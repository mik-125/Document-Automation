from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import tempfile
import urllib.parse
import uuid
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from database import Database
from report_engine import (
    ValidationError,
    create_fillable_template,
    generate_student_pdf,
    homework_records,
    inspect_homework_csv,
    inspect_template,
    parse_comment_pdf,
    student_payload,
)


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets"
DEFAULT_TEMPLATE = ASSETS_DIR / "royal-ib-report-template.pdf"
ACTIVE_TEMPLATE = DATA_DIR / "templates" / "active-template.pdf"
REPORT_DIR = DATA_DIR / "reports"
EXPORT_DIR = DATA_DIR / "exports"
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_REQUEST_BYTES = 60_000_000
DB = Database(DATA_DIR / "brightline.db")


def ensure_template() -> None:
    if not DEFAULT_TEMPLATE.exists():
        create_fillable_template(DEFAULT_TEMPLATE)
    if not ACTIVE_TEMPLATE.exists():
        ACTIVE_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_TEMPLATE, ACTIVE_TEMPLATE)
    details = inspect_template(ACTIVE_TEMPLATE)
    if not DB.get_setting("template_path"):
        DB.set_setting("template_path", str(ACTIVE_TEMPLATE))
        DB.set_setting("template_name", "Royal IB report template")
        DB.set_setting("template_pages", str(details["pages"]))
        DB.set_setting("template_fields", json.dumps(details["fields"]))


def safe_filename(value: str, fallback: str = "report") -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "-", value).strip(" .-")
    return value[:100] or fallback


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
    if not attempted or not exams or not any(exam.get("comment") for exam in exams):
        raise ValidationError("This student still needs homework and a matched teacher comment.")
    return payload


def generate_report(
    student_id: str, period_id: str | None = None, *, archive: bool = True
) -> Path:
    student = get_ready_student(student_id, period_id)
    template_path = Path(DB.get_setting("template_path", str(ACTIVE_TEMPLATE)))
    period_folder = safe_filename(student.get("period_label", "period"), "period")
    output = REPORT_DIR / period_folder / f"{student_id}-{uuid.uuid4().hex[:8]}.pdf"
    generate_student_pdf(student, template_path, output)
    if archive:
        DB.archive_report(student, student["period_id"], output)
    return output


def generate_zip(class_id: str | None = None) -> Path:
    state = DB.state()
    students = [
        student
        for student in state["students"]
        if student["ready"] and (class_id is None or student["class_id"] == class_id)
    ]
    if not students:
        raise ValidationError("There are no ready reports in this selection.")
    if class_id:
        selected_class = next((item for item in state["classes"] if item["id"] == class_id), None)
        if not selected_class:
            raise ValidationError("Class not found.")
        archive_name = safe_filename(selected_class["name"], "class-reports")
    else:
        archive_name = "all-class-reports"
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = EXPORT_DIR / f"{archive_name}.zip"
    class_names = {item["id"]: item["name"] for item in state["classes"]}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for student in students:
            report_path = generate_report(student["id"], student.get("period_id"))
            report_name = f"{safe_filename(student['name'], student['id'])}.pdf"
            if class_id is None:
                report_name = f"{safe_filename(class_names.get(student['class_id'], 'Unassigned'))}/{report_name}"
            archive.write(report_path, report_name)
    return archive_path


class AppHandler(BaseHTTPRequestHandler):
    server_version = "BrightlineLocal"
    sys_version = ""

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; "
            "img-src 'self' data: blob:; frame-src 'self' blob:; object-src 'self'; base-uri 'none'",
        )

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
            ascii_name = safe_filename(download_name.encode("ascii", "ignore").decode() or "download")
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

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def do_GET(self) -> None:
        try:
            path = self._path()
            if path == "/api/health":
                self._send_json({"ok": True})
                return
            if path == "/api/state":
                self._send_json(DB.state())
                return
            if path == "/api/template/download":
                template_path = Path(DB.get_setting("template_path", str(ACTIVE_TEMPLATE)))
                self._send_file(template_path, "application/pdf", "royal-ib-report-template.pdf")
                return
            if path == "/api/template/preview":
                template_path = Path(DB.get_setting("template_path", str(ACTIVE_TEMPLATE)))
                self._send_file(template_path, "application/pdf")
                return
            import_match = re.fullmatch(r"/api/imports/([a-f0-9]+)/download", path)
            if import_match:
                imported = DB.get_import(import_match.group(1))
                stored_path = Path(str(imported.get("stored_path") or "")) if imported else None
                if not imported or imported.get("kind") != "generic_csv" or not stored_path:
                    raise ValidationError("Stored CSV not found.")
                resolved_path = stored_path.resolve()
                if not resolved_path.is_relative_to(UPLOAD_DIR.resolve()):
                    raise ValidationError("Stored CSV path is invalid.")
                self._send_file(resolved_path, "text/csv; charset=utf-8", str(imported["name"]))
                return
            student_match = re.fullmatch(r"/api/reports/student/([a-f0-9]+)", path)
            if student_match:
                student_id = student_match.group(1)
                student = DB.get_student(student_id)
                period_id = self._query().get("period_id", [None])[0]
                report_path = generate_report(student_id, period_id)
                filename = f"{safe_filename(student['name'] if student else 'student')}-report.pdf"
                self._send_file(report_path, "application/pdf", filename)
                return
            student_preview = re.fullmatch(r"/api/reports/student/([a-f0-9]+)/preview", path)
            if student_preview:
                period_id = self._query().get("period_id", [None])[0]
                report_path = generate_report(student_preview.group(1), period_id, archive=False)
                self._send_file(report_path, "application/pdf")
                return
            archived_match = re.fullmatch(r"/api/reports/archive/([a-f0-9]+)", path)
            if archived_match:
                report = DB.get_archived_report(archived_match.group(1))
                if not report:
                    raise ValidationError("Archived report not found.")
                filename = f"{safe_filename(report['student_name'])}-{safe_filename(report['period_label'])}.pdf"
                self._send_file(Path(report["pdf_path"]), "application/pdf", filename)
                return
            class_match = re.fullmatch(r"/api/reports/class/([a-f0-9]+)\.zip", path)
            if class_match:
                class_id = class_match.group(1)
                archive = generate_zip(class_id)
                self._send_file(archive, "application/zip", archive.name)
                return
            if path == "/api/reports/all.zip":
                archive = generate_zip()
                self._send_file(archive, "application/zip", archive.name)
                return
            self._serve_static(path)
        except (ValidationError, ValueError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"GET {self.path} failed: {exc}")
            self._send_json({"error": "The request could not be completed."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in ("", "/") else urllib.parse.unquote(path).lstrip("/")
        candidate = (ROOT / relative).resolve()
        if not candidate.is_relative_to(ROOT) or any(part.startswith(".") for part in Path(relative).parts):
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_file(candidate, content_type)

    def do_POST(self) -> None:
        try:
            path = self._path()
            payload = self._read_json()
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
                self._import_csv(payload)
                return
            if path == "/api/csv/upload":
                text = payload.get("text")
                filename = str(payload.get("filename") or "upload.csv")
                if not isinstance(text, str) or not text.strip():
                    raise ValidationError("The CSV is empty.")
                rows = [row for row in csv.reader(io.StringIO(text.lstrip("\ufeff"))) if any(cell.strip() for cell in row)]
                if not rows:
                    raise ValidationError("The CSV is empty.")
                UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
                stored_name = f"{uuid.uuid4().hex}-{safe_filename(filename, 'upload.csv')}"
                if not stored_name.lower().endswith(".csv"):
                    stored_name += ".csv"
                stored_path = UPLOAD_DIR / stored_name
                stored_path.write_text(text, encoding="utf-8")
                import_id = DB.add_import(
                    filename,
                    "generic_csv",
                    "complete",
                    f"{max(0, len(rows) - 1)} data rows stored · no student fields mapped",
                    stored_path=str(stored_path),
                )
                self._send_json({"ok": True, "import_id": import_id, "state": DB.state()}, HTTPStatus.CREATED)
                return
            if path == "/api/comments/import":
                self._import_comments(payload)
                return
            if path == "/api/imports/delete":
                raw_ids = payload.get("import_ids")
                if not isinstance(raw_ids, list) or not all(
                    isinstance(import_id, str) and re.fullmatch(r"[a-f0-9]+", import_id)
                    for import_id in raw_ids
                ):
                    raise ValidationError("Choose valid uploaded data to delete.")
                deletion = DB.delete_imports(raw_ids)
                deleted_imports = deletion["imports"]
                upload_root = UPLOAD_DIR.resolve()
                for imported in deleted_imports:
                    if imported.get("kind") != "generic_csv" or not imported.get("stored_path"):
                        continue
                    stored_path = Path(str(imported["stored_path"])).resolve()
                    if stored_path.is_relative_to(upload_root) and stored_path.is_file():
                        try:
                            stored_path.unlink()
                        except OSError:
                            pass
                if any(imported.get("kind") == "report_template" for imported in deleted_imports):
                    ACTIVE_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(DEFAULT_TEMPLATE, ACTIVE_TEMPLATE)
                    details = inspect_template(ACTIVE_TEMPLATE)
                    DB.set_setting("template_path", str(ACTIVE_TEMPLATE))
                    DB.set_setting("template_name", "Royal IB report template")
                    DB.set_setting("template_pages", str(details["pages"]))
                    DB.set_setting("template_fields", json.dumps(details["fields"]))
                self._send_json(
                    {
                        "ok": True,
                        "deleted": len(deleted_imports),
                        "deleted_data": {key: deletion[key] for key in ("classes", "students", "exams", "issues")},
                        "state": DB.state(),
                    }
                )
                return
            if path == "/api/periods/select":
                DB.select_period(str(payload.get("period_id") or ""))
                self._send_json({"ok": True, "state": DB.state()})
                return
            if path == "/api/classes":
                DB.create_class(
                    name=str(payload.get("name") or ""),
                    subject=str(payload.get("subject") or ""),
                    teacher=str(payload.get("teacher") or ""),
                )
                self._send_json({"ok": True, "state": DB.state()}, HTTPStatus.CREATED)
                return
            if path == "/api/classes/delete":
                raw_ids = payload.get("class_ids")
                if not isinstance(raw_ids, list) or not all(
                    isinstance(class_id, str) and re.fullmatch(r"[a-f0-9]+", class_id)
                    for class_id in raw_ids
                ):
                    raise ValidationError("Choose valid classes to delete.")
                deleted = DB.delete_classes(raw_ids)
                self._send_json({"ok": True, "deleted": deleted, "state": DB.state()})
                return
            if path == "/api/students":
                DB.create_student(
                    class_id=str(payload.get("class_id") or ""),
                    name=str(payload.get("name") or ""),
                    school=str(payload.get("school") or ""),
                    grade=str(payload.get("grade") or ""),
                )
                self._send_json({"ok": True, "state": DB.state()}, HTTPStatus.CREATED)
                return
            student_match = re.fullmatch(r"/api/students/([a-f0-9]+)", path)
            if student_match:
                DB.update_student_profile(
                    student_match.group(1),
                    class_id=str(payload.get("class_id") or ""),
                    name=str(payload.get("name") or ""),
                    school=str(payload.get("school") or ""),
                    grade=str(payload.get("grade") or ""),
                )
                self._send_json({"ok": True, "state": DB.state()})
                return
            if path == "/api/students/delete":
                raw_ids = payload.get("student_ids")
                if not isinstance(raw_ids, list) or not all(
                    isinstance(student_id, str) and re.fullmatch(r"[a-f0-9]+", student_id)
                    for student_id in raw_ids
                ):
                    raise ValidationError("Choose valid students to delete.")
                deleted = DB.delete_students(raw_ids)
                self._send_json({"ok": True, "deleted": deleted, "state": DB.state()})
                return
            class_period_match = re.fullmatch(r"/api/classes/([a-f0-9]+)/period", path)
            if class_period_match:
                DB.select_class_period(
                    class_period_match.group(1), str(payload.get("period_id") or "")
                )
                self._send_json({"ok": True, "state": DB.state()})
                return
            if path == "/api/template/upload":
                self._upload_template(payload)
                return
            issue_match = re.fullmatch(r"/api/issues/([a-f0-9]+)/resolve", path)
            if issue_match:
                self._resolve_issue(issue_match.group(1), payload)
                return
            if path == "/api/reset":
                if payload.get("confirm") is not True:
                    raise ValidationError("Reset confirmation is required.")
                DB.clear_all()
                shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
                self._send_json({"ok": True})
                return
            self._send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except (ValidationError, ValueError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            print(f"POST {self.path} failed: {exc}")
            self._send_json({"error": "The request could not be completed."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _import_csv(self, payload: dict[str, Any]) -> None:
        text = payload.get("text")
        filename = str(payload.get("filename") or "homework.csv")
        period_start = str(payload.get("period_start") or "")
        period_end = str(payload.get("period_end") or "")
        period_label = str(payload.get("period_label") or "").strip()
        if not isinstance(text, str):
            raise ValidationError("CSV text is required.")
        records = homework_records(text, period_start, period_end)
        period = DB.ensure_period(period_start, period_end, period_label)
        DB.select_period(period["id"])
        created = 0
        updated = 0
        class_ids: set[str] = set()
        student_ids: set[str] = set()
        for record in records:
            class_record = DB.upsert_class(record["class_name"], record["teacher"])
            class_ids.add(class_record["id"])
            student, was_created = DB.upsert_student(
                class_id=class_record["id"],
                class_name=class_record["name"],
                name=record["name"],
                school=record["school"],
                grade=record["grade"],
                attendance_mode=record["attendance_mode"],
            )
            student_ids.add(student["id"])
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
            )
            created += int(was_created)
            updated += int(not was_created)
        for class_id in class_ids:
            DB.select_class_period(class_id, period["id"])
        summary = f"{len(records)} records matched · {created} added · {updated} updated"
        import_id = DB.add_import(filename, "homework_csv", "complete", summary, period["id"])
        DB.link_import_entities(import_id, {"class": class_ids, "student": student_ids})
        self._send_json({"ok": True, "summary": summary, "state": DB.state()})

    def _import_comments(self, payload: dict[str, Any]) -> None:
        filename = str(payload.get("filename") or "teacher-comments.pdf")
        class_id = str(payload.get("class_id") or "")
        period_id = str(payload.get("period_id") or DB.period_id_for_class(class_id) or "")
        label = str(payload.get("label") or "").strip()
        if not DB.get_class(class_id):
            raise ValidationError("Choose the class represented by this PDF.")
        if not label:
            raise ValidationError("Enter a weekly exam label.")
        if not DB.get_period(period_id):
            raise ValidationError("Choose the reporting period represented by this PDF.")
        pdf_bytes = decode_base64(payload.get("pdf_base64"), "Comments PDF")
        records = parse_comment_pdf(pdf_bytes, label, filename)
        matched = 0
        review = 0
        exam_ids: set[str] = set()
        issue_ids: set[str] = set()
        for record in records:
            candidates = DB.find_students_by_name(class_id, record["name"])
            if len(candidates) == 1:
                exam_ids.add(DB.upsert_exam(candidates[0]["id"], class_id, record, period_id))
                matched += 1
            else:
                issue_ids.add(DB.add_issue(
                    kind="ambiguous_comment" if candidates else "unmatched_comment",
                    class_id=class_id,
                    source_name=filename,
                    subject_name=record["name"],
                    payload=record,
                    candidate_ids=[candidate["id"] for candidate in candidates],
                    period_id=period_id,
                ))
                review += 1
        state = "review" if review else "complete"
        summary = f"{matched} comments matched · {review} need review"
        import_id = DB.add_import(filename, "comments_pdf", state, summary, period_id)
        DB.link_import_entities(import_id, {"exam": exam_ids, "issue": issue_ids})
        self._send_json({"ok": True, "summary": summary, "state": DB.state()})

    def _resolve_issue(self, issue_id: str, payload: dict[str, Any]) -> None:
        issue = DB.get_issue(issue_id)
        if not issue or issue["status"] != "pending":
            raise ValidationError("This review item is no longer available.")
        student_id = str(payload.get("student_id") or "")
        student = DB.get_student(student_id)
        if not student or student["class_id"] != issue["class_id"]:
            raise ValidationError("Choose a student from the selected class.")
        import_id = DB.import_id_for_entity("issue", issue_id)
        exam_id = DB.upsert_exam(student_id, issue["class_id"], issue["payload"], issue.get("period_id"))
        if import_id:
            DB.link_import_entities(import_id, {"exam": {exam_id}})
        DB.resolve_issue(issue_id)
        self._send_json({"ok": True, "state": DB.state()})

    def _upload_template(self, payload: dict[str, Any]) -> None:
        filename = str(payload.get("filename") or "report-template.pdf")
        pdf_bytes = decode_base64(payload.get("pdf_base64"), "Template PDF")
        ACTIVE_TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
        temporary = ACTIVE_TEMPLATE.with_name(f"template-{uuid.uuid4().hex}.pdf")
        temporary.write_bytes(pdf_bytes)
        try:
            details = inspect_template(temporary)
            os.replace(temporary, ACTIVE_TEMPLATE)
        finally:
            temporary.unlink(missing_ok=True)
        DB.set_setting("template_path", str(ACTIVE_TEMPLATE))
        DB.set_setting("template_name", filename)
        DB.set_setting("template_pages", str(details["pages"]))
        DB.set_setting("template_fields", json.dumps(details["fields"]))
        DB.add_import(filename, "report_template", "complete", f"{details['pages']} page template activated")
        self._send_json({"ok": True, "template": DB.state()["template"]})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    ensure_template()
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"Brightline Reports: http://{host}:{port}")
    print("Student data stays in the local data directory. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
