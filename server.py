from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import shutil
import time
import urllib.parse
import uuid
import zipfile
from datetime import UTC, date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from database import Database, normalize_student_name, normalize_value
from integrations import (
    GoogleWorkspace,
    IntegrationError,
    REQUIRED_PLACEHOLDERS,
    SUPPORTED_PLACEHOLDERS,
    mapped_records,
    parse_number,
    preview_table,
    report_replacements,
)
from report_engine import (
    ValidationError,
    homework_records,
    inspect_homework_csv,
    inspect_template,
    parse_comment_pdf,
    student_payload,
)


ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("BRIGHTLINE_DATA_DIR", ROOT / "data")).expanduser().resolve()
REPORT_DIR = DATA_DIR / "reports"
EXPORT_DIR = DATA_DIR / "exports"
EXAMPLE_DIR = DATA_DIR / "examples"
MAX_REQUEST_BYTES = 60_000_000
STATIC_FILES = {
    "": "index.html",
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DB = Database(DATA_DIR / "brightline.db")
GOOGLE_OAUTH_REDIRECT_URI = "http://127.0.0.1:8000/api/google/oauth/callback"
GOOGLE_OAUTH_FLOWS: dict[str, tuple[float, Any]] = {}


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def safe_filename(value: str, fallback: str = "report") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", str(value or "")).strip(" .-")
    return value[:100] or fallback


def report_zip_member_name(student: dict[str, Any]) -> str:
    internal_id = safe_filename(str(student["id"])[:8], "student")
    external_id = safe_filename(student.get("external_student_id") or "", "")[:80]
    identifier = f"{external_id}-{internal_id}" if external_id else internal_id
    return "_".join(
        (
            safe_filename(student["class_name"], "Class"),
            safe_filename(student.get("period_label", "Period"), "Period"),
            safe_filename(student["name"], str(student["id"])),
            identifier,
        )
    ) + ".pdf"


def local_request_context(host: str, origin: str = "", fetch_site: str = "") -> bool:
    if fetch_site.casefold() == "cross-site":
        return False
    try:
        host_parts = urllib.parse.urlsplit(f"//{host}")
        host_name = (host_parts.hostname or "").casefold()
        if (
            host_name not in LOOPBACK_HOSTS
            or host_parts.username
            or host_parts.password
            or host_parts.path
            or host_parts.query
            or host_parts.fragment
        ):
            return False
        if not origin:
            return True
        origin_parts = urllib.parse.urlsplit(origin)
        origin_name = (origin_parts.hostname or "").casefold()
        if origin_parts.scheme != "http" or origin_name != host_name:
            return False
        host_port = host_parts.port or 80
        origin_port = origin_parts.port or 80
        return host_port == origin_port
    except ValueError:
        return False


def local_get_request_context(
    path: str, host: str, origin: str = "", fetch_site: str = ""
) -> bool:
    if path in STATIC_FILES:
        return local_request_context(host)
    return local_request_context(host, origin, fetch_site)


def is_json_content_type(value: str) -> bool:
    return value.partition(";")[0].strip().casefold() == "application/json"


def error_status(exc: Exception) -> HTTPStatus:
    code = getattr(exc, "code", "")
    if code == "google_oauth_required":
        return HTTPStatus.UNAUTHORIZED
    if code == "google_rate_limited":
        return HTTPStatus.TOO_MANY_REQUESTS
    if code in {"google_temporarily_unavailable", "google_api_disabled"}:
        return HTTPStatus.SERVICE_UNAVAILABLE
    if code in {
        "google_file_not_shared",
        "google_access_denied",
        "google_export_not_allowed",
    }:
        return HTTPStatus.FORBIDDEN
    if code == "google_file_missing_or_not_shared":
        return HTTPStatus.NOT_FOUND
    if code == "google_storage_limit":
        return HTTPStatus.INSUFFICIENT_STORAGE
    if code == "json_content_type_required":
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    if code == "replacement_confirmation_required":
        return HTTPStatus.CONFLICT
    return HTTPStatus.BAD_REQUEST


def decode_base64(value: Any, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{label} is required.")
    try:
        result = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError(f"{label} is invalid.") from exc
    if not result:
        raise ValidationError(f"{label} is empty.")
    return result


def json_setting(key: str, default: Any) -> Any:
    raw = DB.get_setting(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def set_json_setting(key: str, value: Any) -> None:
    DB.set_setting(key, json.dumps(value, ensure_ascii=False))


def class_template(class_id: str) -> dict[str, Any]:
    template = json_setting(f"class_template_{class_id}", {})
    return template if isinstance(template, dict) else {}


def save_class_template(class_id: str, template: dict[str, Any]) -> None:
    set_json_setting(f"class_template_{class_id}", template)


def sheet_source(kind: str) -> dict[str, Any]:
    source = json_setting(f"sheet_source_{kind}", {})
    return source if isinstance(source, dict) else {}


def google_connection_state() -> dict[str, Any]:
    identity = GoogleWorkspace.configured_identity()
    disabled = DB.get_setting("google_connection_disabled") == "1"
    tested_status = DB.get_setting("google_connection_status")
    stored_account = DB.get_setting("google_connection_account")
    stored_identity = DB.get_setting("google_connection_identity")
    credentials_verified = bool(
        tested_status == "connected"
        and identity["authorized"]
        and stored_account
        and stored_identity == identity.get("fingerprint")
    )
    active_verified = credentials_verified and not disabled
    if disabled:
        base_status = "disconnected"
    elif tested_status == "error":
        base_status = "error"
    elif credentials_verified:
        base_status = "configured"
    elif identity["configured"] and not identity["authorized"]:
        base_status = "needs_auth"
    elif identity["configured"]:
        base_status = "configured"
    else:
        base_status = "disconnected"
    account = stored_account
    error = DB.get_setting("google_connection_error") or identity.get("error", "")
    common: dict[str, Any] = {
        "available": True,
        "configured": bool(identity["authorized"]),
        "oauth_client_configured": bool(identity["configured"]),
        "authorized": bool(identity["authorized"]),
        "auth_mode": "oauth",
        "connect_url": "/api/google/oauth/start",
        "account_name": account,
        "last_tested_at": DB.get_setting("google_connection_last_tested"),
    }
    output_folder_id = DB.get_setting("google_output_folder_id")
    output_folder_verified = DB.get_setting("google_output_folder_verified") == "1"
    sheets_status = base_status
    docs_status = base_status
    if active_verified and DB.get_setting("google_sheets_verified") == "1":
        sheets_status = "connected"
    if active_verified and (not output_folder_id or not output_folder_verified):
        docs_status = "needs_setup"
    elif active_verified and DB.get_setting("google_docs_verified") == "1":
        docs_status = "connected"
    return {
        "google_sheets": {
            **common,
            "status": sheets_status,
            "error": error if sheets_status not in {"connected", "needs_setup"} else "",
            "last_synced_at": DB.get_setting("google_sheets_last_sync"),
            "resource_name": DB.get_setting("google_sheet_name"),
        },
        "google_docs": {
            **common,
            "status": docs_status,
            "error": error if docs_status not in {"connected", "needs_setup"} else "",
            "last_synced_at": DB.get_setting("google_docs_last_sync"),
            "resource_name": DB.get_setting("google_output_folder_name"),
            "output_folder_configured": bool(output_folder_id),
            "output_folder_verified": output_folder_verified,
        },
    }


def mark_google_service_verified(service: str, workspace: GoogleWorkspace) -> None:
    prepare_google_identity(workspace)
    DB.set_setting("google_connection_status", "connected")
    DB.set_setting("google_connection_error", "")
    DB.set_setting(f"google_{service}_verified", "1")


def prepare_google_identity(workspace: GoogleWorkspace) -> None:
    account = workspace.account_email()
    identity_changed = (
        DB.get_setting("google_connection_account") != account
        or DB.get_setting("google_connection_identity") != workspace.identity_fingerprint
    )
    if identity_changed:
        for key in (
            "google_sheets_verified",
            "google_docs_verified",
            "google_output_folder_verified",
        ):
            DB.set_setting(key, "0")
        DB.set_setting("google_output_folder_id", "")
        DB.set_setting("google_output_folder_name", "")
    DB.set_setting("google_connection_account", account)
    DB.set_setting("google_connection_identity", workspace.identity_fingerprint)


def app_state() -> dict[str, Any]:
    state = DB.state()
    google = google_connection_state()
    google_docs_ready = google["google_docs"]["status"] == "connected"
    state["connections"] = {
        "makeedu": {
            "available": False,
            "configured": False,
            "status": "unavailable",
            "account_name": "",
            "last_synced_at": "",
            "error": "Official MakeEdu API documentation and academy access are required.",
            "support_url": "https://makeedu.co.kr/faq.jsp",
        },
        **google,
    }
    templates: list[dict[str, Any]] = []
    template_lookup: dict[str, dict[str, Any]] = {}
    active_google_account = str(google["google_docs"].get("account_name") or "")
    for class_item in state["classes"]:
        template = class_template(class_item["id"])
        example_folder = EXAMPLE_DIR / class_item["id"]
        example_count = len(list(example_folder.glob("*.pdf"))) if example_folder.exists() else 0
        missing = list(template.get("missing_required") or [])
        invalid = list(template.get("invalid_placeholders") or [])
        template_account = str(template.get("verified_account") or "")
        account_verified = bool(
            not active_google_account or template_account == active_google_account
        )
        ready = bool(
            template.get("document_id")
            and not missing
            and not invalid
            and account_verified
        )
        item = {
            "class_id": class_item["id"],
            "document_id": str(template.get("document_id") or ""),
            "name": str(template.get("name") or ""),
            "url": str(template.get("url") or ""),
            "placeholders": list(template.get("placeholders") or []),
            "missing_required": missing,
            "invalid_placeholders": invalid,
            "status": (
                "ready"
                if ready
                else (
                    "needs_test"
                    if template.get("document_id") and not account_verified
                    else ("needs_mapping" if template else "missing")
                )
            ),
            "ready": ready,
            "example_count": example_count,
            "last_tested_at": str(template.get("last_tested_at") or ""),
            "error": str(
                template.get("error")
                or (
                    "Retest this template with the connected Google account."
                    if template.get("document_id") and not account_verified
                    else ""
                )
            ),
        }
        templates.append(item)
        template_lookup[class_item["id"]] = item
    state["class_templates"] = templates
    for student in state["students"]:
        student["template_ready"] = bool(
            template_lookup.get(student["class_id"], {}).get("ready")
        )
        student["report_ready"] = bool(student.get("ready") and student["template_ready"])
        student["google_ready"] = google_docs_ready
        student["report_ready"] = bool(student["report_ready"] and google_docs_ready)

    data_status: list[dict[str, Any]] = []
    for class_item in state["classes"]:
        period_id = class_item.get("selected_period_id") or state.get("selected_period_id")
        students = [
            student
            for student in state["students"]
            if student["class_id"] == class_item["id"] and student.get("period_id") == period_id
        ]
        data_status.append(
            {
                "class_id": class_item["id"],
                "period_id": period_id,
                "student_count": len(students),
                "assignment_count": sum(bool(item.get("assignment_ready")) for item in students),
                "weekly_test_count": sum(bool(item.get("weekly_test_ready")) for item in students),
                "comment_count": sum(bool(item.get("comments_ready")) for item in students),
                "assignment_ready": bool(students) and all(item.get("assignment_ready") for item in students),
                "weekly_test_ready": bool(students) and all(item.get("weekly_test_ready") for item in students),
                "comments_ready": bool(students) and all(item.get("comments_ready") for item in students),
                "template_ready": bool(template_lookup.get(class_item["id"], {}).get("ready")),
                "google_ready": google_docs_ready,
            }
        )
    state["data_status"] = data_status
    state["sheet_sources"] = {
        kind: sheet_source(kind) for kind in ("assignment", "weekly_test", "comments")
    }
    state["supported_placeholders"] = list(SUPPORTED_PLACEHOLDERS)
    return state


def period_from_payload(
    payload: dict[str, Any], *, allow_create: bool = True
) -> dict[str, Any]:
    period_id = str(payload.get("period_id") or "")
    if period_id:
        period = DB.get_period(period_id)
        if not period:
            raise ValidationError("The selected reporting period no longer exists.")
        return period
    if not allow_create:
        raise ValidationError("Choose an existing reporting period for this import.")
    start = str(payload.get("period_start") or "")
    end = str(payload.get("period_end") or "")
    label = str(payload.get("period_label") or "").strip()
    if not start or not end:
        raise ValidationError("Choose an existing reporting period or enter its start and end dates.")
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise ValidationError("Reporting period dates are invalid.") from exc
    if start_date > end_date:
        raise ValidationError("The reporting period start must be before its end.")
    return {
        "id": "",
        "label": label or f"{start} to {end}",
        "start_date": start,
        "end_date": end,
    }


def _values_from_csv(text: str) -> list[list[str]]:
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("CSV text is required.")
    return list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))


def _existing_class_by_name(name: str) -> dict[str, Any] | None:
    normalized = normalize_value(name)
    return next(
        (item for item in DB.state()["classes"] if normalize_value(item["name"]) == normalized),
        None,
    )


def _validate_source_week(
    records: list[dict[str, Any]], mapping: dict[str, Any], period: dict[str, Any]
) -> None:
    if "reporting_week" not in mapping:
        return
    weeks = [str(record.get("reporting_week") or "").strip() for record in records]
    if any(not week for week in weeks):
        raise ValidationError("Every row needs a reporting week when that column is mapped.")
    normalized_weeks = {normalize_value(week) for week in weeks}
    if len(normalized_weeks) != 1:
        raise ValidationError(
            "This file contains multiple reporting weeks. Filter it to one week before importing."
        )
    if normalized_weeks != {normalize_value(str(period["label"]))}:
        raise ValidationError(
            f"The source week does not match the selected period, {period['label']}."
        )


def _reuse_existing_period(period: dict[str, Any]) -> dict[str, Any]:
    if period.get("id"):
        return period
    existing = next(
        (
            item
            for item in DB.state()["periods"]
            if item["start_date"] == period.get("start_date")
            and item["end_date"] == period.get("end_date")
        ),
        None,
    )
    return existing or period


def _import_identity(record: dict[str, Any]) -> tuple[str, ...]:
    external_id = normalize_value(str(record.get("student_id") or ""))
    if external_id:
        return (normalize_value(record["class_name"]), "id", external_id)
    return (
        normalize_value(record["class_name"]),
        "identity",
        normalize_student_name(record["student_name"]),
        normalize_value(str(record.get("school") or "")),
        normalize_value(str(record.get("grade") or "")),
    )


def _record_label(kind: str, record: dict[str, Any], period: dict[str, Any]) -> str:
    if kind == "comments":
        return _comment_label(record.get("weekly_test_label"))
    return str(
        record.get("weekly_test_label")
        or record.get("reporting_week")
        or period["label"]
    ).strip()


def _comment_label(value: Any) -> str:
    source_label = str(value or "").strip()
    if not source_label or normalize_value(source_label) == "teacher comment":
        return "Teacher comment"
    if normalize_value(source_label).startswith("teacher comment ·"):
        return source_label
    return f"Teacher comment · {source_label}"


def _validate_duplicate_rows(
    kind: str, records: list[dict[str, Any]], period: dict[str, Any]
) -> None:
    seen: set[tuple[str, ...]] = set()
    for record in records:
        key = _import_identity(record)
        if kind != "assignment":
            key += (normalize_value(_record_label(kind, record, period)),)
        if key in seen:
            raise ValidationError(
                "This import contains duplicate rows for the same student and destination. "
                "Remove the duplicate before importing."
            )
        seen.add(key)


def _resolve_existing_targets(
    kind: str, records: list[dict[str, Any]], period: dict[str, Any]
) -> None:
    state = DB.state()
    classes = {normalize_value(item["name"]): item for item in state["classes"]}
    students_by_class: dict[str, list[dict[str, Any]]] = {}
    for student in state["students"]:
        students_by_class.setdefault(student["class_id"], []).append(student)
    for record in records:
        class_item = classes.get(normalize_value(record["class_name"]))
        if not class_item:
            raise ValidationError(
                f"Import Assignment data before {kind.replace('_', ' ')} data for {record['class_name']}."
            )
        candidates = students_by_class.get(class_item["id"], [])
        external_id = str(record.get("student_id") or "").strip()
        if external_id:
            matches = [
                student
                for student in candidates
                if str(student.get("external_student_id") or "").strip() == external_id
            ]
        else:
            matches = [
                student
                for student in candidates
                if normalize_student_name(student["name"])
                == normalize_student_name(record["student_name"])
            ]
            school = normalize_value(str(record.get("school") or ""))
            grade = normalize_value(str(record.get("grade") or ""))
            if school:
                matches = [
                    student for student in matches if normalize_value(student["school"]) == school
                ]
            if grade:
                matches = [
                    student for student in matches if normalize_value(student["grade"]) == grade
                ]
        if not matches:
            raise ValidationError(
                f"No Assignment roster match was found for {record['student_name']} in {class_item['name']}. "
                "Correct the name or map the same Student ID used by Assignment."
            )
        if len(matches) > 1:
            raise ValidationError(
                f"Multiple students match {record['student_name']} in {class_item['name']}. "
                "Map a unique Student ID, school, or grade."
            )
        student = DB.get_student_for_period(matches[0]["id"], period["id"])
        attempted = int(student.get("homework_completed") or 0) + int(
            student.get("homework_incomplete") or 0
        )
        if not attempted and student.get("homework_percent") is None:
            raise ValidationError(
                f"Assignment data for {record['student_name']} is missing in {period['label']}."
            )
        if kind == "comments" and not any(
            exam.get("score") is not None
            for exam in DB.get_exams(matches[0]["id"], period["id"])
        ):
            raise ValidationError(
                f"Weekly Test data for {record['student_name']} is missing in {period['label']}."
            )
        record["_class"] = class_item
        record["_student"] = matches[0]


def _same_number(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return first is None and second is None
    return abs(float(first) - float(second)) < 0.0001


def _existing_assignment_student(
    record: dict[str, Any], state: dict[str, Any]
) -> dict[str, Any] | None:
    class_item = next(
        (
            item
            for item in state["classes"]
            if normalize_value(item["name"]) == normalize_value(record["class_name"])
        ),
        None,
    )
    if not class_item:
        return None
    candidates = [
        student
        for student in state["students"]
        if student["class_id"] == class_item["id"]
    ]
    external_id = str(record.get("student_id") or "").strip()
    if external_id:
        exact_id = [
            student
            for student in candidates
            if str(student.get("external_student_id") or "").strip() == external_id
        ]
        if len(exact_id) == 1:
            return exact_id[0]
        candidates = [
            student
            for student in candidates
            if not student.get("external_student_id")
            and normalize_student_name(student["name"])
            == normalize_student_name(record.get("student_name") or record.get("name") or "")
            and normalize_value(student["school"])
            == normalize_value(str(record.get("school") or ""))
            and normalize_value(student["grade"])
            == normalize_value(str(record.get("grade") or ""))
        ]
    else:
        candidates = [
            student
            for student in candidates
            if normalize_student_name(student["name"])
            == normalize_student_name(record.get("student_name") or record.get("name") or "")
            and normalize_value(student["school"])
            == normalize_value(str(record.get("school") or ""))
            and normalize_value(student["grade"])
            == normalize_value(str(record.get("grade") or ""))
        ]
    return candidates[0] if len(candidates) == 1 else None


def _mapped_replacement_conflicts(
    kind: str, records: list[dict[str, Any]], period: dict[str, Any]
) -> list[str]:
    if not period.get("id"):
        return []
    conflicts: list[str] = []
    state = DB.state()
    for record in records:
        student = (
            _existing_assignment_student(record, state)
            if kind == "assignment"
            else record["_student"]
        )
        if not student:
            continue
        label = f"{record['student_name']} · {record['class_name']}"
        if kind == "assignment":
            current = DB.get_student_for_period(student["id"], period["id"])
            has_value = (
                int(current.get("homework_completed") or 0)
                + int(current.get("homework_incomplete") or 0)
                > 0
                or current.get("homework_percent") is not None
            )
            differs = (
                int(current.get("homework_completed") or 0) != int(record["_completed"])
                or int(current.get("homework_incomplete") or 0)
                != int(record["_total"]) - int(record["_completed"])
                or not _same_number(current.get("homework_percent"), record["_percent"])
            )
            if has_value and differs:
                conflicts.append(label)
            continue
        destination = _record_label(kind, record, period)
        existing = next(
            (
                exam
                for exam in DB.get_exams(student["id"], period["id"])
                if exam["label"] == destination
            ),
            None,
        )
        if not existing:
            continue
        if kind == "weekly_test" and (
            not _same_number(existing.get("score"), record.get("_score"))
            or not _same_number(existing.get("max_score"), record.get("_maximum"))
        ):
            conflicts.append(label)
        if kind == "comments" and str(existing.get("comment") or "").strip() not in {
            "",
            str(record.get("comment") or "").strip(),
        }:
            conflicts.append(label)
    return conflicts


def import_mapped_table(
    *,
    values: list[list[Any]],
    mapping: dict[str, Any],
    kind: str,
    period: dict[str, Any],
    source_name: str,
    source_provider: str,
    confirm_replace: bool = False,
) -> str:
    if kind not in {"assignment", "weekly_test", "comments"}:
        raise ValidationError("Choose Assignment, Weekly Test, or Comments as the import type.")
    records = mapped_records(values, mapping)
    if kind == "assignment" and not (
        {"assignment_completed", "assignment_total"}.issubset(mapping)
        or "assignment_percent" in mapping
    ):
        raise ValidationError(
            "Assignment imports require completed and total columns, or an assignment percent column."
        )
    if kind == "weekly_test" and "weekly_test_score" not in mapping:
        raise ValidationError("Weekly Test imports require a score column.")
    if kind == "comments" and "comment" not in mapping:
        raise ValidationError("Comments imports require a comment column.")

    prepared: list[dict[str, Any]] = []
    for record in records:
        item: dict[str, Any] = dict(record)
        if kind == "assignment":
            percent_value = parse_number(item.get("assignment_percent"), "Assignment percent")
            if percent_value is not None:
                if not 0 <= percent_value <= 100:
                    raise ValidationError("Assignment percent must be between 0 and 100.")
            has_count_value = bool(
                str(item.get("assignment_completed") or "").strip()
                or str(item.get("assignment_total") or "").strip()
            )
            if has_count_value:
                completed_number = parse_number(
                    item.get("assignment_completed"),
                    "Assignments completed",
                    required=True,
                )
                total_number = parse_number(
                    item.get("assignment_total"), "Total assignments", required=True
                )
                if not completed_number.is_integer() or not total_number.is_integer():
                    raise IntegrationError(
                        "Assignment counts must be whole numbers.", "invalid_number"
                    )
                completed = int(completed_number)
                total = int(total_number)
                if completed < 0 or total <= 0 or completed > total:
                    raise ValidationError(
                        "Assignment totals must be positive and completed cannot exceed total."
                    )
                calculated_percent = round(completed / total * 100, 1)
                if (
                    percent_value is not None
                    and abs(percent_value - calculated_percent) > 0.11
                ):
                    raise ValidationError(
                        "Assignment percent does not match the completed and total counts."
                    )
                percent_value = calculated_percent
            elif percent_value is not None:
                completed = 0
                total = 0
            else:
                raise ValidationError(
                    "Every Assignment row needs completed and total counts, or a percent."
                )
            item.update(
                {
                    "_completed": completed,
                    "_total": total,
                    "_percent": percent_value,
                }
            )
        elif kind == "weekly_test":
            score = parse_number(
                item.get("weekly_test_score"), "Weekly Test score", required=True
            )
            maximum = parse_number(item.get("weekly_test_max"), "Weekly Test maximum")
            if score is not None and score < 0:
                raise ValidationError("Weekly Test scores cannot be negative.")
            if maximum is not None and (maximum <= 0 or (score is not None and score > maximum)):
                raise ValidationError(
                    "Weekly Test maximum must be positive and not below the score."
                )
            item.update({"_score": score, "_maximum": maximum})
        elif not str(item.get("comment") or "").strip():
            raise ValidationError("Every Comments row must include a teacher comment.")
        prepared.append(item)

    period = _reuse_existing_period(period)
    _validate_source_week(prepared, mapping, period)
    _validate_duplicate_rows(kind, prepared, period)
    if kind != "assignment":
        _resolve_existing_targets(kind, prepared, period)
    conflicts = _mapped_replacement_conflicts(kind, prepared, period)
    if conflicts and not confirm_replace:
        examples = ", ".join(conflicts[:3])
        raise IntegrationError(
            f"This import would replace different existing data for {examples}. Confirm replacement to continue.",
            "replacement_confirmation_required",
        )

    class_ids: set[str] = set()
    created = 0
    updated = 0
    with DB.connect() as transaction:
        if not period.get("id"):
            period = DB.ensure_period(
                period["start_date"],
                period["end_date"],
                period["label"],
                connection=transaction,
            )
        if kind == "assignment":
            DB.select_period(period["id"], connection=transaction)
        for record in prepared:
            if kind == "assignment":
                class_record = DB.upsert_class(
                    record["class_name"],
                    record.get("teacher", ""),
                    source_provider,
                    connection=transaction,
                )
                student, was_created = DB.upsert_student(
                    class_id=class_record["id"],
                    class_name=class_record["name"],
                    name=record["student_name"],
                    school=record.get("school", ""),
                    grade=record.get("grade", ""),
                    attendance_mode="",
                    external_student_id=record.get("student_id", ""),
                    external_source=source_provider,
                    connection=transaction,
                )
                created += int(was_created)
                updated += int(not was_created)
            else:
                class_record = record["_class"]
                student = record["_student"]
                updated += 1
            class_ids.add(class_record["id"])
            if kind == "assignment":
                completed = int(record["_completed"])
                total = int(record["_total"])
                percent_value = float(record["_percent"])
                incomplete = total - completed
                DB.update_homework(
                    student["id"],
                    [
                        {
                            "date": period["end_date"],
                            "label": "Assignment summary",
                            "value": (
                                f"{completed}/{total}"
                                if total
                                else f"{percent_value:g}%"
                            ),
                            "category": (
                                "completed"
                                if not total or not incomplete
                                else "incomplete"
                            ),
                            "score": percent_value,
                        }
                    ],
                    completed,
                    incomplete,
                    0,
                    percent_value,
                    period["start_date"],
                    period["end_date"],
                    period["id"],
                    source_provider,
                    connection=transaction,
                )
            else:
                DB.upsert_exam(
                    student["id"],
                    class_record["id"],
                    {
                        "label": _record_label(kind, record, period),
                        "score": record.get("_score") if kind == "weekly_test" else None,
                        "max_score": record.get("_maximum")
                        if kind == "weekly_test"
                        else None,
                        "sections": [],
                        "comment": record.get("comment", "") if kind == "comments" else "",
                        "source_name": source_name,
                    },
                    period["id"],
                    source_provider,
                    connection=transaction,
                )
        for class_id in class_ids:
            DB.select_class_period(class_id, period["id"], connection=transaction)
        label = {
            "assignment": "Assignment",
            "weekly_test": "Weekly Test",
            "comments": "Comments",
        }[kind]
        summary = (
            f"{len(records)} {label.lower()} records imported · "
            + (
                f"{created} students added · {updated} updated"
                if kind == "assignment"
                else f"{updated} existing students matched"
            )
        )
        DB.add_import(
            source_name,
            f"{kind}_{'sheet' if source_provider == 'google_sheets' else 'csv'}",
            "complete",
            summary,
            period["id"],
            source_provider,
            connection=transaction,
        )
    return summary


def import_assignment_csv(
    text: str,
    filename: str,
    period_start: str,
    period_end: str,
    period_label: str,
    confirm_replace: bool = False,
) -> str:
    records = homework_records(text, period_start, period_end)
    seen: set[tuple[str, ...]] = set()
    for record in records:
        key = (
            normalize_value(record["class_name"]),
            normalize_student_name(record["name"]),
            normalize_value(record["school"]),
            normalize_value(record["grade"]),
        )
        if key in seen:
            raise ValidationError(
                "The Assignment CSV contains duplicate rows for the same student and class."
            )
        seen.add(key)

    state = DB.state()
    existing_period = next(
        (
            item
            for item in state["periods"]
            if item["start_date"] == period_start and item["end_date"] == period_end
        ),
        None,
    )
    conflicts: list[str] = []
    if existing_period:
        for record in records:
            student = _existing_assignment_student(record, state)
            if not student:
                continue
            current = DB.get_student_for_period(student["id"], existing_period["id"])
            has_value = (
                int(current.get("homework_completed") or 0)
                + int(current.get("homework_incomplete") or 0)
                > 0
                or current.get("homework_percent") is not None
            )
            differs = (
                int(current.get("homework_completed") or 0) != int(record["completed"])
                or int(current.get("homework_incomplete") or 0) != int(record["incomplete"])
                or int(current.get("homework_excluded") or 0) != int(record["excluded"])
                or not _same_number(current.get("homework_percent"), record["percent"])
            )
            if has_value and differs:
                conflicts.append(f"{record['name']} · {record['class_name']}")
    if conflicts and not confirm_replace:
        raise IntegrationError(
            "This Assignment CSV would replace different existing data for "
            + ", ".join(conflicts[:3])
            + ". Confirm replacement to continue.",
            "replacement_confirmation_required",
        )

    created = 0
    updated = 0
    class_ids: set[str] = set()
    with DB.connect() as transaction:
        period = DB.ensure_period(
            period_start,
            period_end,
            period_label,
            connection=transaction,
        )
        DB.select_period(period["id"], connection=transaction)
        for record in records:
            class_record = DB.upsert_class(
                record["class_name"],
                record["teacher"],
                "manual",
                connection=transaction,
            )
            class_ids.add(class_record["id"])
            student, was_created = DB.upsert_student(
                class_id=class_record["id"],
                class_name=class_record["name"],
                name=record["name"],
                school=record["school"],
                grade=record["grade"],
                attendance_mode=record["attendance_mode"],
                connection=transaction,
            )
            DB.update_homework(
                student["id"],
                record["entries"],
                record["completed"],
                record["incomplete"],
                record["excluded"],
                record["percent"],
                period_start,
                period_end,
                period["id"],
                "manual",
                connection=transaction,
            )
            created += int(was_created)
            updated += int(not was_created)
        for class_id in class_ids:
            DB.select_class_period(class_id, period["id"], connection=transaction)
        summary = (
            f"{len(records)} assignment records matched · "
            f"{created} students added · {updated} updated"
        )
        DB.add_import(
            filename,
            "assignment_csv",
            "complete",
            summary,
            period["id"],
            "manual",
            connection=transaction,
        )
    return summary


def get_ready_student(student_id: str, period_id: str | None = None) -> dict[str, Any]:
    student = DB.get_student_for_period(student_id, period_id)
    if not student:
        raise ValidationError("Student not found.")
    if not student.get("period_id"):
        raise ValidationError("Choose a reporting period for this class.")
    exams = DB.get_exams(student_id, student["period_id"])
    payload = student_payload(student, exams)
    attempted = int(payload.get("homework_completed") or 0) + int(
        payload.get("homework_incomplete") or 0
    )
    if not attempted and payload.get("homework_percent") is None:
        raise ValidationError("This student still needs Assignment data.")
    if not any(exam.get("score") is not None for exam in exams):
        raise ValidationError("This student still needs a Weekly Test score.")
    if not any(str(exam.get("comment") or "").strip() for exam in exams):
        raise ValidationError("This student still needs a teacher comment.")
    return payload


def student_period_has_assignment_and_test(student_id: str, period_id: str) -> bool:
    student = DB.get_student_for_period(student_id, period_id)
    if not student:
        return False
    has_assignment = bool(
        int(student.get("homework_completed") or 0)
        + int(student.get("homework_incomplete") or 0)
        > 0
        or student.get("homework_percent") is not None
    )
    has_weekly_test = any(
        exam.get("score") is not None for exam in DB.get_exams(student_id, period_id)
    )
    return has_assignment and has_weekly_test


def class_period_has_assignment_and_test(class_id: str, period_id: str) -> bool:
    return any(
        student_period_has_assignment_and_test(student["id"], period_id)
        for student in DB.state()["students"]
        if student["class_id"] == class_id
    )


def generate_report(
    student_id: str,
    period_id: str | None = None,
    *,
    archive: bool = True,
    preview: bool = False,
) -> Path:
    student = get_ready_student(student_id, period_id)
    template = class_template(student["class_id"])
    if not template.get("document_id"):
        raise ValidationError("Connect a Google Docs template for this class first.")
    if template.get("missing_required"):
        raise ValidationError(
            "The class template is missing required placeholders: "
            + ", ".join(template["missing_required"])
        )
    if template.get("invalid_placeholders"):
        raise ValidationError(
            "The class template contains unsupported placeholders: "
            + ", ".join(template["invalid_placeholders"])
        )
    if DB.get_setting("google_connection_disabled") == "1":
        raise ValidationError("Reconnect Google Workspace before generating reports.")
    output_folder_id = DB.get_setting("google_output_folder_id") or ""
    period_folder = safe_filename(student.get("period_label", "period"), "period")
    output = REPORT_DIR / period_folder / f"{student_id}-{uuid.uuid4().hex[:8]}.pdf"
    report_name = " - ".join(
        (
            safe_filename(student.get("class_name", "Class"), "Class"),
            safe_filename(student.get("period_label", "Period"), "Period"),
            safe_filename(student.get("name", "Student"), "Student"),
        )
    )
    workspace = GoogleWorkspace.from_env()
    output, document_id = workspace.create_report_pdf(
        template_id=str(template["document_id"]),
        output_folder_id=output_folder_id,
        copy_name=report_name,
        replacements=report_replacements(student),
        output_path=output,
        preview=preview,
    )
    if archive:
        DB.archive_report(
            student,
            student["period_id"],
            output,
            google_doc_id=document_id,
            google_template_id=str(template["document_id"]),
        )
        DB.set_setting("google_docs_last_sync", now_iso())
    return output


def generate_zip(class_id: str | None = None) -> tuple[Path | None, dict[str, Any], str]:
    state = app_state()
    students = [
        student
        for student in state["students"]
        if student.get("report_ready") and (class_id is None or student["class_id"] == class_id)
    ]
    if not students:
        raise ValidationError("There are no report-ready students in this selection.")
    selected_class = None
    if class_id:
        selected_class = next((item for item in state["classes"] if item["id"] == class_id), None)
        if not selected_class:
            raise ValidationError("Class not found.")
    archive_name = safe_filename(
        selected_class["name"] if selected_class else "all-class-reports", "class-reports"
    )
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = EXPORT_DIR / f"{uuid.uuid4().hex}.zip"
    class_names = {item["id"]: item["name"] for item in state["classes"]}
    manifest: dict[str, Any] = {"generated": [], "failed": []}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for student in students:
            try:
                report_path = generate_report(student["id"], student.get("period_id"))
                report_name = report_zip_member_name(student)
                if class_id is None:
                    report_name = f"{safe_filename(class_names.get(student['class_id'], 'Unassigned'))}/{report_name}"
                archive.write(report_path, report_name)
                manifest["generated"].append(
                    {
                        "student_id": student["id"],
                        "student": student["name"],
                        "class_name": student["class_name"],
                    }
                )
            except (ValidationError, IntegrationError, ValueError) as exc:
                manifest["failed"].append(
                    {
                        "student_id": student["id"],
                        "student": student["name"],
                        "class_name": student["class_name"],
                        "error": str(exc),
                    }
                )
        archive.writestr(
            "generation-summary.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    if not manifest["generated"]:
        archive_path.unlink(missing_ok=True)
        archive_path = None
    return archive_path, manifest, f"{archive_name}.zip"


class AppHandler(BaseHTTPRequestHandler):
    server_version = "BrightlineLocal"
    sys_version = ""

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; "
            "img-src 'self' data: blob:; frame-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'self'",
        )

    def _authorized(self) -> bool:
        expected_password = os.environ.get("BRIGHTLINE_PASSWORD", "")
        if not expected_password:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            username, password = base64.b64decode(header[6:], validate=True).decode().split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return False
        expected_username = os.environ.get("BRIGHTLINE_USERNAME", "admin")
        return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
            password, expected_password
        )

    def _require_safe_context(self, *, allow_static_navigation: bool = False) -> bool:
        check = local_get_request_context if allow_static_navigation else local_request_context
        arguments = (
            (self._path(), self.headers.get("Host", ""))
            if allow_static_navigation
            else (self.headers.get("Host", ""),)
        )
        if check(
            *arguments,
            self.headers.get("Origin", ""),
            self.headers.get("Sec-Fetch-Site", ""),
        ):
            return True
        self._send_json({"error": "Request origin is not allowed."}, HTTPStatus.FORBIDDEN)
        return False

    def _require_authorized(self) -> bool:
        if self._authorized():
            return True
        payload = json.dumps({"error": "Authentication required."}).encode()
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Brightline Reports", charset="UTF-8"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(payload)
        return False

    def _send_bytes(
        self,
        payload: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        disposition: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send_bytes(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()

    def _send_file(self, path: Path, content_type: str, download_name: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "File not found."}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if download_name:
            ascii_name = safe_filename(
                download_name.encode("ascii", "ignore").decode() or "download"
            )
            encoded_name = urllib.parse.quote(download_name)
            self.send_header(
                "Content-Disposition",
                f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}",
            )
        self.end_headers()
        with path.open("rb") as stream:
            while chunk := stream.read(128 * 1024):
                self.wfile.write(chunk)

    def _read_json(self) -> dict[str, Any]:
        if not is_json_content_type(self.headers.get("Content-Type", "")):
            raise IntegrationError(
                "POST requests require application/json.", "json_content_type_required"
            )
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValidationError("Invalid request size.") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValidationError("The request is empty or exceeds 60 MB.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("The request is not valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValidationError("The request must be an object.")
        return payload

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def do_GET(self) -> None:
        path = self._path()
        if path == "/api/google/oauth/callback":
            if not local_request_context(self.headers.get("Host", "")):
                self._send_json({"error": "Request host is not allowed."}, HTTPStatus.FORBIDDEN)
                return
            try:
                self._google_oauth_callback()
            except (ValidationError, IntegrationError, ValueError) as exc:
                self._send_json(
                    {"error": str(exc), "code": getattr(exc, "code", "validation_error")},
                    error_status(exc),
                )
            return
        if not self._require_safe_context(allow_static_navigation=True):
            return
        if not self._require_authorized():
            return
        try:
            if path == "/api/health":
                self._send_json({"ok": True})
                return
            if path == "/api/state":
                self._send_json(app_state())
                return
            if path == "/api/google/oauth/start":
                self._start_google_oauth()
                return
            archived_match = re.fullmatch(r"/api/reports/archive/([a-f0-9]+)", path)
            if archived_match:
                report = DB.get_archived_report(archived_match.group(1))
                if not report:
                    raise ValidationError("Archived report not found.")
                filename = (
                    f"{safe_filename(report['class_name'])}_"
                    f"{safe_filename(report['period_label'])}_"
                    f"{safe_filename(report['student_name'])}.pdf"
                )
                self._send_file(Path(report["pdf_path"]), "application/pdf", filename)
                return
            export_match = re.fullmatch(r"/api/exports/([a-f0-9]{32})\.zip", path)
            if export_match:
                archive = EXPORT_DIR / f"{export_match.group(1)}.zip"
                try:
                    self._send_file(archive, "application/zip")
                finally:
                    archive.unlink(missing_ok=True)
                return
            self._serve_static(path)
        except (ValidationError, IntegrationError, ValueError) as exc:
            self._send_json(
                {"error": str(exc), "code": getattr(exc, "code", "validation_error")},
                error_status(exc),
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"GET {self.path} failed: {type(exc).__name__}")
            self._send_json(
                {"error": "The request could not be completed."},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _serve_static(self, path: str) -> None:
        relative = STATIC_FILES.get(path)
        if not relative:
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        candidate = ROOT / relative
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_file(candidate, content_type)

    def do_POST(self) -> None:
        if not self._require_safe_context():
            return
        if not self._require_authorized():
            return
        try:
            path = self._path()
            payload = self._read_json()
            student_report_match = re.fullmatch(
                r"/api/reports/student/([a-f0-9]+)/(preview|download)", path
            )
            if student_report_match:
                student_id, action = student_report_match.groups()
                period_id = str(payload.get("period_id") or "") or None
                student = DB.get_student(student_id)
                report_path = generate_report(
                    student_id,
                    period_id,
                    archive=action == "download",
                    preview=action == "preview",
                )
                try:
                    filename = (
                        None
                        if action == "preview"
                        else f"{safe_filename(student['name'] if student else 'student')}-report.pdf"
                    )
                    self._send_file(report_path, "application/pdf", filename)
                finally:
                    if action == "preview":
                        report_path.unlink(missing_ok=True)
                return
            class_report_match = re.fullmatch(
                r"/api/reports/class/([a-f0-9]+)/download", path
            )
            if class_report_match:
                archive, manifest, filename = generate_zip(class_report_match.group(1))
                self._send_archive_result(archive, manifest, filename)
                return
            if path == "/api/reports/all/download":
                archive, manifest, filename = generate_zip()
                self._send_archive_result(archive, manifest, filename)
                return
            if path == "/api/csv/inspect":
                text = payload.get("text")
                if not isinstance(text, str):
                    raise ValidationError("CSV text is required.")
                inspected = inspect_homework_csv(text)
                self._send_json(
                    {
                        "record_count": inspected["record_count"],
                        "class_count": inspected["class_count"],
                        "dates": inspected["dates"],
                    }
                )
                return
            if path == "/api/csv/import":
                self._import_assignment_csv(payload)
                return
            if path == "/api/tabular/preview":
                self._send_json(preview_table(_values_from_csv(payload.get("text"))))
                return
            if path == "/api/tabular/import":
                kind = str(payload.get("kind") or "")
                period = period_from_payload(payload, allow_create=kind == "assignment")
                summary = import_mapped_table(
                    values=_values_from_csv(payload.get("text")),
                    mapping=dict(payload.get("mapping") or {}),
                    kind=kind,
                    period=period,
                    source_name=str(payload.get("filename") or "import.csv"),
                    source_provider="manual",
                    confirm_replace=payload.get("confirm_replace") is True,
                )
                self._send_json({"ok": True, "summary": summary, "state": app_state()})
                return
            if path == "/api/comments/pdf/import":
                self._import_comments_pdf(payload)
                return
            if path == "/api/google/sheets/preview":
                self._google_sheet_preview(payload)
                return
            if path == "/api/google/sheets/sync":
                self._google_sheet_sync(payload)
                return
            if path == "/api/connections/google/test":
                self._test_google(payload)
                return
            if path == "/api/connections/google/disconnect":
                GoogleWorkspace.disconnect()
                DB.set_setting("google_connection_disabled", "1")
                DB.set_setting("google_connection_status", "disconnected")
                DB.set_setting("google_connection_error", "")
                DB.set_setting("google_connection_account", "")
                DB.set_setting("google_connection_identity", "")
                DB.set_setting("google_sheets_verified", "0")
                DB.set_setting("google_docs_verified", "0")
                DB.set_setting("google_output_folder_verified", "0")
                self._send_json({"ok": True, "state": app_state()})
                return
            if path == "/api/connections/makeedu/test":
                raise ValidationError(
                    "Official MakeEdu API documentation and academy credentials have not been supplied."
                )
            if path == "/api/periods/select":
                DB.select_period(str(payload.get("period_id") or ""))
                self._send_json({"ok": True, "state": app_state()})
                return
            class_period_match = re.fullmatch(r"/api/classes/([a-f0-9]+)/period", path)
            if class_period_match:
                DB.select_class_period(
                    class_period_match.group(1), str(payload.get("period_id") or "")
                )
                self._send_json({"ok": True, "state": app_state()})
                return
            template_match = re.fullmatch(
                r"/api/templates/([a-f0-9]+)/(connect|test|disconnect|example)", path
            )
            if template_match:
                self._template_action(template_match.group(1), template_match.group(2), payload)
                return
            issue_match = re.fullmatch(r"/api/issues/([a-f0-9]+)/resolve", path)
            if issue_match:
                self._resolve_issue(issue_match.group(1), payload)
                return
            if path == "/api/reset":
                if payload.get("confirm") is not True:
                    raise ValidationError("Reset confirmation is required.")
                DB.clear_all()
                for generated_folder in (REPORT_DIR, EXPORT_DIR, EXAMPLE_DIR):
                    shutil.rmtree(generated_folder, ignore_errors=True)
                self._send_json({"ok": True, "state": app_state()})
                return
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except (ValidationError, IntegrationError, ValueError) as exc:
            self._send_json(
                {"error": str(exc), "code": getattr(exc, "code", "validation_error")},
                error_status(exc),
            )
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"POST {self.path} failed: {type(exc).__name__}")
            self._send_json(
                {"error": "The request could not be completed."},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _send_archive_result(
        self, archive: Path | None, manifest: dict[str, Any], filename: str
    ) -> None:
        payload = {
            "ok": bool(archive) and not manifest["failed"],
            "partial": bool(archive) and bool(manifest["failed"]),
            "generated": manifest["generated"],
            "failed": manifest["failed"],
            "generated_count": len(manifest["generated"]),
            "failed_count": len(manifest["failed"]),
            "download_url": f"/api/exports/{archive.name}" if archive else "",
            "filename": filename,
        }
        if not archive:
            self._send_json(
                {
                    **payload,
                    "error": "No reports were generated. Review the student errors below.",
                    "code": "all_reports_failed",
                },
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
            return
        self._send_json(payload)

    def _import_assignment_csv(self, payload: dict[str, Any]) -> None:
        text = payload.get("text")
        filename = str(payload.get("filename") or "assignment.csv")
        period_start = str(payload.get("period_start") or "")
        period_end = str(payload.get("period_end") or "")
        period_label = str(payload.get("period_label") or "").strip()
        if not isinstance(text, str):
            raise ValidationError("CSV text is required.")
        summary = import_assignment_csv(
            text,
            filename,
            period_start,
            period_end,
            period_label,
            payload.get("confirm_replace") is True,
        )
        self._send_json({"ok": True, "summary": summary, "state": app_state()})

    def _import_comments_pdf(self, payload: dict[str, Any]) -> None:
        filename = str(payload.get("filename") or "teacher-comments.pdf")
        class_id = str(payload.get("class_id") or "")
        period_id = str(payload.get("period_id") or DB.period_id_for_class(class_id) or "")
        label = _comment_label(payload.get("label"))
        selected_class = DB.get_class(class_id)
        if not selected_class:
            raise ValidationError("Choose the class represented by this PDF.")
        if not DB.get_period(period_id):
            raise ValidationError("Choose the reporting period represented by this PDF.")
        if not class_period_has_assignment_and_test(class_id, period_id):
            raise ValidationError(
                "Import Assignment and Weekly Test data for this class before Comments."
            )
        pdf_bytes = decode_base64(payload.get("pdf_base64"), "Comments PDF")
        records = parse_comment_pdf(pdf_bytes, label, filename)
        matched = 0
        review = 0
        prepared: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        conflicts: list[str] = []
        destinations: set[tuple[str, str]] = set()
        for record in records:
            if not str(record.get("comment") or "").strip():
                raise ValidationError(
                    f"No teacher comment could be extracted for {record.get('name') or 'a student'}."
                )
            candidates = [
                student
                for student in DB.find_students_by_name(class_id, record["name"])
                if student_period_has_assignment_and_test(student["id"], period_id)
            ]
            record["score"] = None
            record["max_score"] = None
            prepared.append((record, candidates))
            if len(candidates) == 1:
                destination = (candidates[0]["id"], record["label"])
                if destination in destinations:
                    raise ValidationError(
                        "This Comments PDF contains duplicate records for the same student."
                    )
                destinations.add(destination)
                existing = next(
                    (
                        exam
                        for exam in DB.get_exams(candidates[0]["id"], period_id)
                        if exam["label"] == record["label"]
                    ),
                    None,
                )
                if existing and str(existing.get("comment") or "").strip() not in {
                    "",
                    str(record.get("comment") or "").strip(),
                }:
                    conflicts.append(record["name"])
        if conflicts and payload.get("confirm_replace") is not True:
            raise IntegrationError(
                "This Comments PDF would replace different existing comments for "
                + ", ".join(conflicts[:3])
                + ". Confirm replacement to continue.",
                "replacement_confirmation_required",
            )
        with DB.connect() as transaction:
            for record, candidates in prepared:
                if len(candidates) == 1:
                    DB.upsert_exam(
                        candidates[0]["id"],
                        class_id,
                        record,
                        period_id,
                        "manual",
                        connection=transaction,
                    )
                    matched += 1
                else:
                    DB.add_issue(
                        kind="ambiguous_comment" if candidates else "unmatched_comment",
                        class_id=class_id,
                        source_name=filename,
                        subject_name=record["name"],
                        payload=record,
                        candidate_ids=[candidate["id"] for candidate in candidates],
                        period_id=period_id,
                        connection=transaction,
                    )
                    review += 1
            import_state = "review" if review else "complete"
            summary = f"{matched} comments matched · {review} need review"
            DB.add_import(
                filename,
                "comments_pdf",
                import_state,
                summary,
                period_id,
                "manual",
                connection=transaction,
            )
        self._send_json({"ok": True, "summary": summary, "state": app_state()})

    def _google_sheet_preview(self, payload: dict[str, Any]) -> None:
        if DB.get_setting("google_connection_disabled") == "1":
            raise ValidationError("Reconnect Google Workspace before reading Sheets.")
        workspace = GoogleWorkspace.from_env()
        result = workspace.read_sheet(
            str(payload.get("spreadsheet") or ""), str(payload.get("worksheet") or "")
        )
        mark_google_service_verified("sheets", workspace)
        preview = preview_table(result["values"])
        self._send_json(
            {
                **preview,
                "spreadsheet_id": result["spreadsheet_id"],
                "spreadsheet_name": result["spreadsheet_name"],
                "worksheet": result["worksheet"],
                "tabs": result["tabs"],
            }
        )

    def _google_sheet_sync(self, payload: dict[str, Any]) -> None:
        if DB.get_setting("google_connection_disabled") == "1":
            raise ValidationError("Reconnect Google Workspace before reading Sheets.")
        kind = str(payload.get("kind") or "")
        period = period_from_payload(payload, allow_create=kind == "assignment")
        workspace = GoogleWorkspace.from_env()
        result = workspace.read_sheet(
            str(payload.get("spreadsheet") or ""), str(payload.get("worksheet") or "")
        )
        mark_google_service_verified("sheets", workspace)
        mapping = dict(payload.get("mapping") or {})
        summary = import_mapped_table(
            values=result["values"],
            mapping=mapping,
            kind=kind,
            period=period,
            source_name=f"{result['spreadsheet_name']} · {result['worksheet']}",
            source_provider="google_sheets",
            confirm_replace=payload.get("confirm_replace") is True,
        )
        source = {
            "spreadsheet_id": result["spreadsheet_id"],
            "spreadsheet_name": result["spreadsheet_name"],
            "worksheet": result["worksheet"],
            "mapping": mapping,
            "last_sync_at": now_iso(),
        }
        set_json_setting(f"sheet_source_{kind}", source)
        DB.set_setting("google_sheet_name", result["spreadsheet_name"])
        DB.set_setting("google_sheets_last_sync", source["last_sync_at"])
        self._send_json({"ok": True, "summary": summary, "state": app_state()})

    def _start_google_oauth(self) -> None:
        flow = GoogleWorkspace.authorization_flow(GOOGLE_OAUTH_REDIRECT_URI)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        cutoff = time.monotonic() - 600
        for pending_state, (created_at, _) in list(GOOGLE_OAUTH_FLOWS.items()):
            if created_at < cutoff:
                GOOGLE_OAUTH_FLOWS.pop(pending_state, None)
        GOOGLE_OAUTH_FLOWS[state] = (time.monotonic(), flow)
        self._redirect(authorization_url)

    def _google_oauth_callback(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        state = str(query.get("state", [""])[0])
        pending = GOOGLE_OAUTH_FLOWS.pop(state, None)
        if not state or not pending or pending[0] < time.monotonic() - 600:
            raise IntegrationError(
                "The Google sign-in session expired. Start the connection again.",
                "google_oauth_required",
            )
        if query.get("error"):
            DB.set_setting("google_connection_status", "error")
            DB.set_setting("google_connection_error", "Google sign-in was cancelled.")
            self._redirect("/#connections")
            return
        _, flow = pending
        try:
            flow.fetch_token(
                authorization_response=f"{GOOGLE_OAUTH_REDIRECT_URI}?{urllib.parse.urlparse(self.path).query}"
            )
            GoogleWorkspace.save_oauth_credentials(flow.credentials)
            workspace = GoogleWorkspace.from_env()
            result = workspace.test_connection(ensure_output_folder=True)
            prepare_google_identity(workspace)
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, IntegrationError)
                else "Google sign-in could not be completed. Check the OAuth client settings."
            )
            DB.set_setting("google_connection_status", "error")
            DB.set_setting("google_connection_error", error)
            DB.set_setting("google_connection_last_tested", now_iso())
            self._redirect("/#connections")
            return
        DB.set_setting("google_connection_disabled", "0")
        DB.set_setting("google_connection_status", "connected")
        DB.set_setting("google_connection_error", "")
        DB.set_setting("google_connection_account", result["account"])
        DB.set_setting("google_connection_last_tested", now_iso())
        DB.set_setting("google_sheets_verified", "1")
        DB.set_setting("google_docs_verified", "1")
        DB.set_setting("google_output_folder_id", result["output_folder_id"])
        DB.set_setting("google_output_folder_name", result["output_folder_name"])
        DB.set_setting("google_output_folder_verified", "1")
        self._redirect("/#connections")

    def _test_google(self, payload: dict[str, Any]) -> None:
        service = str(payload.get("service") or "google_sheets")
        if service not in {"google_sheets", "google_docs"}:
            raise ValidationError("Choose a valid Google service.")
        output_folder = str(
            payload.get("output_folder")
            or DB.get_setting("google_output_folder_id")
            or ""
        )
        try:
            workspace = GoogleWorkspace.from_env()
            result = workspace.test_connection(
                output_folder,
                ensure_output_folder=service == "google_docs",
            )
            prepare_google_identity(workspace)
        except IntegrationError as exc:
            DB.set_setting("google_connection_status", "error")
            DB.set_setting("google_connection_error", str(exc))
            DB.set_setting("google_connection_last_tested", now_iso())
            if output_folder:
                DB.set_setting("google_output_folder_verified", "0")
            raise
        DB.set_setting("google_connection_disabled", "0")
        DB.set_setting("google_connection_status", "connected")
        DB.set_setting("google_connection_error", "")
        DB.set_setting("google_connection_account", result["account"])
        DB.set_setting("google_connection_last_tested", now_iso())
        DB.set_setting(f"google_{'sheets' if service == 'google_sheets' else 'docs'}_verified", "1")
        if result.get("output_folder_id"):
            DB.set_setting("google_output_folder_id", result["output_folder_id"])
            DB.set_setting("google_output_folder_name", result["output_folder_name"])
            DB.set_setting("google_output_folder_verified", "1")
        self._send_json(
            {
                "ok": True,
                "message": "Google account access verified.",
                "state": app_state(),
            }
        )

    def _template_action(self, class_id: str, action: str, payload: dict[str, Any]) -> None:
        if not DB.get_class(class_id):
            raise ValidationError("Class not found.")
        if action == "disconnect":
            save_class_template(class_id, {})
            self._send_json({"ok": True, "state": app_state()})
            return
        if action == "example":
            filename = safe_filename(str(payload.get("filename") or "example-report.pdf"))
            pdf_bytes = decode_base64(payload.get("pdf_base64"), "Example report")
            folder = EXAMPLE_DIR / class_id
            folder.mkdir(parents=True, exist_ok=True)
            temporary = folder / f"{uuid.uuid4().hex}-{filename}"
            temporary.write_bytes(pdf_bytes)
            try:
                details = inspect_template(temporary)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise ValidationError("The example report is not a readable PDF.")
            DB.add_import(
                filename,
                "example_report",
                "complete",
                f"{details['pages']} page reference saved",
                source_provider="manual",
            )
            self._send_json({"ok": True, "state": app_state()})
            return
        current = class_template(class_id)
        document = str(payload.get("document") or current.get("document_id") or "")
        if not document:
            raise ValidationError("Enter the Google Docs template URL.")
        if DB.get_setting("google_connection_disabled") == "1":
            raise ValidationError("Reconnect Google Workspace before checking templates.")
        workspace = GoogleWorkspace.from_env()
        inspected = workspace.inspect_document(document)
        mark_google_service_verified("docs", workspace)
        if not inspected["can_copy"]:
            raise ValidationError("The connected Google account cannot copy this template.")
        template = {
            **inspected,
            "verified_account": workspace.account_email(),
            "last_tested_at": now_iso(),
            "error": "",
        }
        save_class_template(class_id, template)
        self._send_json(
            {
                "ok": True,
                "message": (
                    "Template connected."
                    if not template["missing_required"]
                    and not template["invalid_placeholders"]
                    else "Template connected, but its placeholders need attention."
                ),
                "missing_required": template["missing_required"],
                "invalid_placeholders": template["invalid_placeholders"],
                "state": app_state(),
            }
        )

    def _resolve_issue(self, issue_id: str, payload: dict[str, Any]) -> None:
        issue = DB.get_issue(issue_id)
        if not issue or issue["status"] != "pending":
            raise ValidationError("This review item is no longer available.")
        student_id = str(payload.get("student_id") or "")
        student = DB.get_student(student_id)
        if not student or student["class_id"] != issue["class_id"]:
            raise ValidationError("Choose a student from the selected class.")
        period_id = str(issue.get("period_id") or "")
        if not student_period_has_assignment_and_test(student_id, period_id):
            raise ValidationError(
                "This student still needs Assignment and Weekly Test data for the selected period."
            )
        record = dict(issue["payload"])
        record["label"] = _comment_label(record.get("label"))
        if not str(record.get("comment") or "").strip():
            raise ValidationError("This review item does not contain a teacher comment.")
        existing = next(
            (
                exam
                for exam in DB.get_exams(student_id, period_id)
                if exam["label"] == record["label"]
            ),
            None,
        )
        if (
            existing
            and str(existing.get("comment") or "").strip()
            not in {"", str(record.get("comment") or "").strip()}
            and payload.get("confirm_replace") is not True
        ):
            raise IntegrationError(
                "This match would replace a different existing comment. Confirm replacement to continue.",
                "replacement_confirmation_required",
            )
        DB.upsert_exam(
            student_id,
            issue["class_id"],
            record,
            period_id,
            "manual",
        )
        DB.resolve_issue(issue_id)
        self._send_json({"ok": True, "state": app_state()})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    if host not in LOOPBACK_HOSTS:
        raise RuntimeError(
            "This MVP is local-only. Put it behind authenticated HTTPS before sharing it."
        )
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Brightline Reports: http://{host}:{port}")
    if os.environ.get("BRIGHTLINE_PASSWORD"):
        print("Password protection is enabled.")
    else:
        print("Local-only mode: set BRIGHTLINE_PASSWORD before sharing this service.")
    print("Student data stays in the ignored local data directory. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
