from __future__ import annotations

import io
import hashlib
import json
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
)
GOOGLE_OUTPUT_FOLDER_NAME = "Brightline Reports"
SUPPORTED_PLACEHOLDERS = (
    "student_name",
    "student_id",
    "class_name",
    "grade_or_year",
    "school",
    "reporting_week",
    "assignment_summary",
    "assignment_percent",
    "weekly_test_score",
    "weekly_test_summary",
    "teacher_comment",
)
REQUIRED_PLACEHOLDERS = (
    "student_name",
    "class_name",
    "reporting_week",
    "assignment_summary",
    "weekly_test_summary",
    "teacher_comment",
)
MAX_SHEET_ROWS = 10_000
PREVIEW_SAMPLE_ROW_LIMIT = 100
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,256}$")
_PLACEHOLDER_PATTERN = re.compile(r"\{\{([a-zA-Z][a-zA-Z0-9_-]*)\}\}")
_ANY_PLACEHOLDER_PATTERN = re.compile(r"\{\{.*?\}\}", re.DOTALL)


class IntegrationError(RuntimeError):
    def __init__(self, message: str, code: str = "integration_error") -> None:
        super().__init__(message)
        self.code = code


def normalize_header(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", value)


HEADER_ALIASES: dict[str, set[str]] = {
    "student_id": {"studentid", "studentnumber", "id", "학생id", "학생번호", "원생번호"},
    "student_name": {
        "student",
        "studentname",
        "name",
        "학생",
        "학생명",
        "학생이름",
        "이름",
        "원생명",
        "원생이름",
    },
    "class_name": {
        "class",
        "classname",
        "course",
        "subject",
        "과목",
        "과목명",
        "과목이름",
        "수업",
        "수업명",
        "수업이름",
        "반",
        "반이름",
        "강좌",
        "강좌명",
    },
    "grade": {"grade", "year", "schoolyear", "학년"},
    "school": {"school", "schoolname", "학교", "학교명"},
    "teacher": {"teacher", "teachername", "instructor", "담당자", "담당자이름", "강사", "선생님"},
    "assignment_completed": {"assignmentcompleted", "homeworkcompleted", "completed", "과제완료", "숙제완료", "완료수"},
    "assignment_total": {"assignmenttotal", "homeworktotal", "totalassignments", "과제수", "숙제수", "전체수"},
    "assignment_percent": {"assignmentpercent", "homeworkpercent", "completionrate", "과제완료율", "숙제완료율", "완료율"},
    "weekly_test_label": {"testlabel", "testname", "exam", "examname", "시험", "시험명", "테스트명"},
    "weekly_test_score": {"testscore", "score", "weeklytestscore", "점수", "시험점수"},
    "weekly_test_max": {"maxscore", "totalscore", "maximum", "만점", "총점"},
    "comment": {"comment", "comments", "teachercomment", "feedback", "코멘트", "댓글", "선생님코멘트", "평가"},
    "reporting_week": {"reportingweek", "week", "period", "주차", "보고주차", "기간"},
}


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    normalized = {normalize_header(header): header for header in headers if str(header).strip()}
    suggestions: dict[str, str] = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                suggestions[field] = normalized[alias]
                break
    return suggestions


def preview_table(values: list[list[Any]]) -> dict[str, Any]:
    rows = [[str(cell).strip() for cell in row] for row in values]
    rows = [row for row in rows if any(row)]
    if not rows:
        raise IntegrationError("The selected table is empty.", "empty_table")
    headers = rows[0]
    if not any(headers):
        raise IntegrationError("The header row is empty.", "empty_headers")
    normalized = [normalize_header(header) for header in headers]
    duplicates = sorted({header for header in normalized if header and normalized.count(header) > 1})
    if duplicates:
        raise IntegrationError(
            "The header row contains duplicate column names. Rename the duplicate columns first.",
            "duplicate_headers",
        )
    width = len(headers)
    sample = [
        (row + [""] * width)[:width]
        for row in rows[1 : PREVIEW_SAMPLE_ROW_LIMIT + 1]
    ]
    return {
        "headers": headers,
        "sample_rows": sample,
        "row_count": max(0, len(rows) - 1),
        "suggested_mapping": suggest_mapping(headers),
    }


def mapped_records(values: list[list[Any]], mapping: dict[str, Any]) -> list[dict[str, str]]:
    preview = preview_table(values)
    headers = preview["headers"]
    header_indices = {header: index for index, header in enumerate(headers)}
    clean_mapping = {
        str(field): str(header)
        for field, header in mapping.items()
        if str(header).strip()
    }
    for required in ("student_name", "class_name"):
        if required not in clean_mapping:
            raise IntegrationError(
                f"Map the {required.replace('_', ' ')} column before importing.",
                "missing_mapping",
            )
    missing = [header for header in clean_mapping.values() if header not in header_indices]
    if missing:
        raise IntegrationError(
            f"A mapped column no longer exists: {missing[0]}",
            "stale_mapping",
        )
    records: list[dict[str, str]] = []
    for raw_row in values[1:]:
        row = [str(cell).strip() for cell in raw_row]
        record = {
            field: row[index] if index < len(row) else ""
            for field, header in clean_mapping.items()
            for index in (header_indices[header],)
        }
        # Google Sheets often retain formatting or assignment marks on a trailing
        # row after its student cells have been cleared. That is not a student
        # record, so ignore it when both identity cells are empty. A row with only
        # one identity value remains an error because importing it would be unsafe.
        if not record.get("student_name") and not record.get("class_name"):
            continue
        if not any(record.values()):
            continue
        if not record.get("student_name") or not record.get("class_name"):
            raise IntegrationError(
                "Every imported row must include both a student name and class name.",
                "incomplete_identity",
            )
        records.append(record)
    if not records:
        raise IntegrationError("No student records were found below the header row.", "empty_table")
    if len(records) > MAX_SHEET_ROWS:
        raise IntegrationError(
            f"This MVP accepts at most {MAX_SHEET_ROWS:,} rows per synchronization.",
            "too_many_rows",
        )
    return records


def parse_number(value: Any, label: str, *, required: bool = False) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if text.endswith("%"):
        text = text[:-1].strip()
    if not text:
        if required:
            raise IntegrationError(f"{label} is required.", "missing_number")
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise IntegrationError(f"{label} must be a number.", "invalid_number") from exc
    if not math.isfinite(number):
        raise IntegrationError(f"{label} must be a finite number.", "invalid_number")
    return number


def google_file_id(value: str) -> str:
    value = str(value or "").strip()
    if _ID_PATTERN.fullmatch(value):
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {"docs.google.com", "drive.google.com"}:
        raise IntegrationError("Enter a valid Google Sheets, Docs, or Drive URL.", "invalid_google_url")
    segments = [segment for segment in parsed.path.split("/") if segment]
    candidate = ""
    for marker in ("d", "folders"):
        if marker in segments:
            index = segments.index(marker)
            if index + 1 < len(segments):
                candidate = segments[index + 1]
                break
    if not candidate:
        candidate = parse_qs(parsed.query).get("id", [""])[0]
    if not _ID_PATTERN.fullmatch(candidate):
        raise IntegrationError("The Google file ID could not be read from that URL.", "invalid_google_url")
    return candidate


def _document_text(document: dict[str, Any]) -> str:
    parts: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            text_run = value.get("textRun")
            if isinstance(text_run, dict) and isinstance(text_run.get("content"), str):
                parts.append(text_run["content"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)
    return "".join(parts)


def template_placeholders(text: str) -> tuple[list[str], list[str]]:
    placeholders: set[str] = set()
    invalid: set[str] = set()
    consumed = [False] * len(text)
    for match in _ANY_PLACEHOLDER_PATTERN.finditer(text):
        token = match.group(0)
        exact = _PLACEHOLDER_PATTERN.fullmatch(token)
        extra_left = match.start() > 0 and text[match.start() - 1] == "{"
        extra_right = match.end() < len(text) and text[match.end()] == "}"
        if exact and exact.group(1) in SUPPORTED_PLACEHOLDERS and not extra_left and not extra_right:
            placeholders.add(exact.group(1))
        else:
            start = match.start() - int(extra_left)
            end = match.end() + int(extra_right)
            invalid.add(text[start:end])
        for index in range(match.start(), match.end()):
            consumed[index] = True
    residue = "".join(character if not consumed[index] else " " for index, character in enumerate(text))
    if "{{" in residue:
        invalid.add("unclosed {{")
    if "}}" in residue:
        invalid.add("unopened }}")
    return sorted(placeholders), sorted(invalid)


def report_replacements(student: dict[str, Any]) -> dict[str, str]:
    exams = list(student.get("exams") or [])
    scored = [exam for exam in exams if exam.get("score") is not None]
    commented = [exam for exam in exams if str(exam.get("comment") or "").strip()]
    latest_test = scored[-1] if scored else {}
    attempted = int(student.get("homework_completed") or 0) + int(
        student.get("homework_incomplete") or 0
    )
    missed = int(student.get("homework_incomplete") or 0)
    excluded = int(student.get("homework_excluded") or 0)
    percent = student.get("homework_percent")
    assignment_summary = (
        f"{missed} assignment{'s' if missed != 1 else ''} missed"
        if attempted or excluded
        else (
            "Missed assignment count unavailable"
            if percent is not None
            else "No assignment data"
        )
    )
    score = latest_test.get("score")
    maximum = latest_test.get("max_score")
    score_text = "No weekly test score"
    if score is not None:
        score_text = f"{float(score):g}%" if maximum == 100 else f"{float(score):g}"
        if maximum is not None and maximum != 100:
            score_text += f" / {float(maximum):g}"
    test_summary = " · ".join(
        f"{exam.get('label') or 'Weekly test'}: {float(exam['score']):g}"
        + (
            "%"
            if exam.get("max_score") == 100
            else f"/{float(exam['max_score']):g}"
            if exam.get("max_score") is not None
            else ""
        )
        for exam in scored
    ) or "No weekly test data"
    comment = "\n\n".join(
        str(exam.get("comment") or "").strip() for exam in commented
    ) or "No teacher comment"
    external_id = str(student.get("external_student_id") or student.get("id") or "")
    return {
        "student_name": str(student.get("name") or ""),
        "student_id": external_id,
        "class_name": str(student.get("class_name") or ""),
        "grade_or_year": str(student.get("grade") or ""),
        "school": str(student.get("school") or ""),
        "reporting_week": str(student.get("period_label") or ""),
        "assignment_summary": assignment_summary,
        "assignment_percent": "" if percent is None else f"{float(percent):g}%",
        "weekly_test_score": score_text,
        "weekly_test_summary": test_summary,
        "teacher_comment": comment,
    }


class GoogleWorkspace:
    def __init__(self, client_path: Path, token_path: Path) -> None:
        self.client_path = client_path
        self.token_path = token_path
        try:
            payload = json.loads(client_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrationError(
                "The Google OAuth client file cannot be read.",
                "google_credentials_invalid",
            ) from exc
        client = payload.get("web")
        if not isinstance(client, dict) or not client.get("client_id"):
            raise IntegrationError(
                "Create a Google OAuth client of type Web application and download its JSON file.",
                "google_credentials_invalid",
            )
        self.client_id = str(client["client_id"])
        self.project_id = str(client.get("project_id") or "")
        self.identity_fingerprint = hashlib.sha256(self.client_id.encode()).hexdigest()
        self._credentials: Any = None
        self._account_email = ""

    @staticmethod
    def _client_path() -> Path:
        configured = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE", "")
        if not configured:
            raise IntegrationError(
                "Set GOOGLE_OAUTH_CLIENT_FILE before connecting your Google account.",
                "google_not_configured",
            )
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise IntegrationError(
                "The configured Google OAuth client file does not exist.",
                "google_credentials_missing",
            )
        return path

    @staticmethod
    def _token_path() -> Path:
        configured = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "")
        if configured:
            return Path(configured).expanduser().resolve()
        data_dir = Path(
            os.environ.get("BRIGHTLINE_DATA_DIR", Path(__file__).resolve().parent / "data")
        ).expanduser().resolve()
        return data_dir / "google-oauth-token.json"

    @classmethod
    def from_env(cls) -> "GoogleWorkspace":
        workspace = cls(cls._client_path(), cls._token_path())
        if not workspace.token_path.is_file():
            raise IntegrationError(
                "Connect your Google account from the Connections page.",
                "google_oauth_required",
            )
        return workspace

    @staticmethod
    def configured_identity() -> dict[str, Any]:
        try:
            workspace = GoogleWorkspace(
                GoogleWorkspace._client_path(), GoogleWorkspace._token_path()
            )
        except IntegrationError as exc:
            return {
                "configured": False,
                "authorized": False,
                "fingerprint": "",
                "project": "",
                "error": str(exc),
            }
        return {
            "configured": True,
            "authorized": workspace.token_path.is_file(),
            "fingerprint": workspace.identity_fingerprint,
            "project": workspace.project_id,
            "error": "" if workspace.token_path.is_file() else "Connect your Google account.",
        }

    @classmethod
    def authorization_flow(cls, redirect_uri: str) -> Any:
        client_path = cls._client_path()
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as exc:
            raise IntegrationError(
                "Install the Google API packages from requirements.txt.",
                "google_dependencies_missing",
            ) from exc
        try:
            return Flow.from_client_secrets_file(
                str(client_path),
                scopes=GOOGLE_SCOPES,
                redirect_uri=redirect_uri,
                autogenerate_code_verifier=True,
            )
        except Exception as exc:
            raise IntegrationError(
                "Google rejected the OAuth client configuration.",
                "google_credentials_invalid",
            ) from exc

    @classmethod
    def save_oauth_credentials(cls, credentials: Any) -> None:
        token_path = cls._token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = token_path.with_suffix(".tmp")
        temporary.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(token_path)

    @classmethod
    def disconnect(cls) -> None:
        cls._token_path().unlink(missing_ok=True)

    def _load_credentials(self) -> Any:
        if self._credentials is not None:
            return self._credentials
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise IntegrationError(
                "Install the Google API packages from requirements.txt.",
                "google_dependencies_missing",
            ) from exc
        try:
            credentials = Credentials.from_authorized_user_file(
                str(self.token_path), scopes=GOOGLE_SCOPES
            )
            if credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
                self.save_oauth_credentials(credentials)
            if not credentials.valid:
                raise ValueError("OAuth token is not valid")
            self._credentials = credentials
        except Exception as exc:
            raise IntegrationError(
                "Your Google authorization expired. Reconnect your account.",
                "google_oauth_required",
            ) from exc
        return self._credentials

    def account_email(self) -> str:
        if self._account_email:
            return self._account_email
        try:
            about = (
                self._service("drive", "v3")
                .about()
                .get(fields="user(emailAddress)")
                .execute(num_retries=2)
            )
        except IntegrationError:
            raise
        except Exception as exc:
            self._raise_google_error(exc)
        self._account_email = str(about.get("user", {}).get("emailAddress") or "")
        if not self._account_email:
            raise IntegrationError(
                "Google did not return the connected account email.",
                "google_account_missing",
            )
        return self._account_email

    def _service(self, name: str, version: str) -> Any:
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise IntegrationError(
                "Install the Google API packages from requirements.txt.",
                "google_dependencies_missing",
            ) from exc
        try:
            return build(
                name,
                version,
                credentials=self._load_credentials(),
                cache_discovery=False,
            )
        except Exception as exc:
            self._raise_google_error(exc)

    @staticmethod
    def _raise_google_error(exc: Exception) -> None:
        status = getattr(getattr(exc, "resp", None), "status", None)
        raw_content = getattr(exc, "content", b"")
        if isinstance(raw_content, bytes):
            reason_text = raw_content.decode("utf-8", "ignore").casefold()
        else:
            reason_text = str(raw_content).casefold()
        if status == 429 or any(
            reason in reason_text
            for reason in (
                "ratelimitexceeded",
                "userratelimitexceeded",
                "sharingratelimitexceeded",
                "dailylimitexceeded",
            )
        ):
            message, code = "Google is rate limiting requests. Try again shortly.", "google_rate_limited"
        elif any(
            reason in reason_text
            for reason in ("accessnotconfigured", "servicedisabled", "api_disabled")
        ):
            message, code = (
                "Enable the Google Sheets, Docs, and Drive APIs in this Cloud project.",
                "google_api_disabled",
            )
        elif "storagequotaexceeded" in reason_text:
            message, code = (
                "The Google Drive destination has no available storage.",
                "google_storage_limit",
            )
        elif any(
            reason in reason_text
            for reason in ("cannotdownloadfile", "downloadrestrictedforrevision")
        ):
            message, code = (
                "Google does not allow this file to be copied or exported.",
                "google_export_not_allowed",
            )
        elif status == 400:
            message, code = "Google rejected the request. Check the file URL and sheet contents.", "invalid_google_request"
        elif status == 401:
            message, code = "Your Google authorization expired. Reconnect your account.", "google_oauth_required"
        elif status == 403:
            message, code = (
                "Google denied access. Confirm that the connected account can open this file.",
                "google_access_denied",
            )
        elif status == 404:
            message, code = (
                "The Google file was not found or is not available to the connected account.",
                "google_file_missing_or_not_shared",
            )
        elif status and status >= 500:
            message, code = "Google Workspace is temporarily unavailable.", "google_temporarily_unavailable"
        else:
            message, code = "The Google Workspace request failed.", "google_request_failed"
        raise IntegrationError(message, code) from exc

    def file_metadata(self, file_or_url: str, expected_mime: str | None = None) -> dict[str, Any]:
        file_id = google_file_id(file_or_url)
        try:
            result = (
                self._service("drive", "v3")
                .files()
                .get(
                    fileId=file_id,
                    fields="id,name,mimeType,webViewLink,driveId,capabilities(canCopy,canDownload,canEdit,canAddChildren,canTrashChildren)",
                    supportsAllDrives=True,
                )
                .execute(num_retries=2)
            )
        except IntegrationError:
            raise
        except Exception as exc:
            self._raise_google_error(exc)
        if expected_mime and result.get("mimeType") != expected_mime:
            raise IntegrationError("The selected Google file has the wrong file type.", "wrong_google_file_type")
        return result

    def _ensure_output_folder(self, output_folder: str = "") -> dict[str, Any]:
        if output_folder:
            folder = self.file_metadata(output_folder, GOOGLE_FOLDER_MIME)
        else:
            drive = self._service("drive", "v3")
            try:
                existing = (
                    drive.files()
                    .list(
                        q=(
                            "appProperties has { key='brightline' and value='report-output' } and "
                            f"mimeType = '{GOOGLE_FOLDER_MIME}' and trashed = false"
                        ),
                        spaces="drive",
                        pageSize=10,
                        fields="files(id,name,mimeType,webViewLink,capabilities(canAddChildren))",
                    )
                    .execute(num_retries=2)
                    .get("files", [])
                )
                folder = existing[0] if existing else (
                    drive.files()
                    .create(
                        body={
                            "name": GOOGLE_OUTPUT_FOLDER_NAME,
                            "mimeType": GOOGLE_FOLDER_MIME,
                            "appProperties": {"brightline": "report-output"},
                        },
                        fields="id,name,mimeType,webViewLink,capabilities(canAddChildren)",
                    )
                    .execute(num_retries=2)
                )
            except IntegrationError:
                raise
            except Exception as exc:
                self._raise_google_error(exc)
        if not folder.get("capabilities", {}).get("canAddChildren"):
            raise IntegrationError(
                "The connected Google account cannot add reports to that Drive folder.",
                "output_folder_not_writable",
            )
        return folder

    def test_connection(
        self, output_folder: str = "", *, ensure_output_folder: bool = False
    ) -> dict[str, Any]:
        folder: dict[str, Any] | None = None
        if output_folder or ensure_output_folder:
            folder = self._ensure_output_folder(output_folder)
        return {
            "account": self.account_email(),
            "project": self.project_id,
            "output_folder_id": folder.get("id", "") if folder else "",
            "output_folder_name": folder.get("name", "") if folder else "",
        }

    def read_sheet(self, spreadsheet_or_url: str, worksheet: str = "") -> dict[str, Any]:
        spreadsheet_id = google_file_id(spreadsheet_or_url)
        self.file_metadata(spreadsheet_id, GOOGLE_SHEET_MIME)
        sheets = self._service("sheets", "v4")
        try:
            metadata = (
                sheets.spreadsheets()
                .get(
                    spreadsheetId=spreadsheet_id,
                    includeGridData=False,
                    fields="properties(title),sheets(properties(sheetId,title,index))",
                )
                .execute(num_retries=2)
            )
            tabs = sorted(
                (item["properties"] for item in metadata.get("sheets", [])),
                key=lambda tab: int(tab.get("index") or 0),
            )
            if not tabs:
                raise IntegrationError("The spreadsheet has no worksheets.", "empty_spreadsheet")
            selected = worksheet.strip() or str(tabs[0].get("title") or "")
            if selected not in {str(tab.get("title") or "") for tab in tabs}:
                raise IntegrationError("The requested worksheet does not exist.", "worksheet_missing")
            safe_title = selected.replace("'", "''")
            result = (
                sheets.spreadsheets()
                .values()
                .get(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{safe_title}'",
                    majorDimension="ROWS",
                    valueRenderOption="FORMATTED_VALUE",
                )
                .execute(num_retries=2)
            )
        except IntegrationError:
            raise
        except Exception as exc:
            self._raise_google_error(exc)
        values = result.get("values", [])
        if len(values) > MAX_SHEET_ROWS + 1:
            raise IntegrationError(
                f"This MVP accepts at most {MAX_SHEET_ROWS:,} spreadsheet rows per synchronization.",
                "too_many_rows",
            )
        return {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": str(metadata.get("properties", {}).get("title") or "Spreadsheet"),
            "worksheet": selected,
            "tabs": [str(tab.get("title") or "") for tab in tabs],
            "values": values,
        }

    def read_sheets(
        self, spreadsheet_or_url: str, *, row_limit: int = 60
    ) -> dict[str, Any]:
        """Read a bounded detection sample from every worksheet in a spreadsheet.

        ``read_sheet`` remains the full-table API used once a worksheet has been
        selected.  Sampling all tabs here prevents a workbook with several large tabs
        from being downloaded in full merely to locate the table to preview.
        """

        spreadsheet_id = google_file_id(spreadsheet_or_url)
        self.file_metadata(spreadsheet_id, GOOGLE_SHEET_MIME)
        sheets = self._service("sheets", "v4")
        try:
            metadata = (
                sheets.spreadsheets()
                .get(
                    spreadsheetId=spreadsheet_id,
                    includeGridData=False,
                    fields="properties(title),sheets(properties(sheetId,title,index))",
                )
                .execute(num_retries=2)
            )
            tabs = sorted(
                (item["properties"] for item in metadata.get("sheets", [])),
                key=lambda tab: int(tab.get("index") or 0),
            )
            if not tabs:
                raise IntegrationError("The spreadsheet has no worksheets.", "empty_spreadsheet")
            worksheets: list[dict[str, Any]] = []
            for tab in tabs:
                title = str(tab.get("title") or "")
                safe_title = title.replace("'", "''")
                row_range = f"'{safe_title}'!1:{max(1, int(row_limit))}"
                result = (
                    sheets.spreadsheets()
                    .values()
                    .get(
                        spreadsheetId=spreadsheet_id,
                        range=row_range,
                        majorDimension="ROWS",
                        valueRenderOption="FORMATTED_VALUE",
                    )
                    .execute(num_retries=2)
                )
                worksheets.append(
                    {
                        "worksheet": title,
                        "index": int(tab.get("index") or 0),
                        "values": result.get("values", []),
                    }
                )
        except IntegrationError:
            raise
        except Exception as exc:
            self._raise_google_error(exc)
        return {
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": str(
                metadata.get("properties", {}).get("title") or "Spreadsheet"
            ),
            "tabs": [str(tab.get("title") or "") for tab in tabs],
            "worksheets": worksheets,
        }

    def inspect_document(self, document_or_url: str) -> dict[str, Any]:
        document_id = google_file_id(document_or_url)
        metadata = self.file_metadata(document_id, GOOGLE_DOC_MIME)
        try:
            document = (
                self._service("docs", "v1")
                .documents()
                .get(documentId=document_id, includeTabsContent=True)
                .execute(num_retries=2)
            )
        except IntegrationError:
            raise
        except Exception as exc:
            self._raise_google_error(exc)
        placeholders, invalid = template_placeholders(_document_text(document))
        missing = [name for name in REQUIRED_PLACEHOLDERS if name not in placeholders]
        return {
            "document_id": document_id,
            "name": str(metadata.get("name") or "Google Docs template"),
            "url": str(metadata.get("webViewLink") or f"https://docs.google.com/document/d/{document_id}/edit"),
            "placeholders": placeholders,
            "missing_required": missing,
            "invalid_placeholders": invalid,
            "can_copy": bool(metadata.get("capabilities", {}).get("canCopy")),
        }

    def create_report_pdf(
        self,
        *,
        template_id: str,
        output_folder_id: str,
        copy_name: str,
        replacements: dict[str, str],
        output_path: Path,
        preview: bool = False,
    ) -> tuple[Path, str]:
        unsafe_fields = [
            name
            for name, value in replacements.items()
            if "{{" in str(value) or "}}" in str(value)
        ]
        if unsafe_fields:
            raise IntegrationError(
                "Report data contains reserved template braces in: "
                + ", ".join(unsafe_fields),
                "unsafe_template_value",
            )
        if not output_folder_id:
            raise IntegrationError(
                "Configure a writable Google Drive output folder first.",
                "output_folder_required",
            )
        template = self.file_metadata(template_id, GOOGLE_DOC_MIME)
        folder = self.file_metadata(output_folder_id, GOOGLE_FOLDER_MIME)
        if not template.get("capabilities", {}).get("canCopy"):
            raise IntegrationError("The Google Docs template cannot be copied.", "template_copy_not_allowed")
        if not folder.get("capabilities", {}).get("canAddChildren"):
            raise IntegrationError("The Drive output folder is not writable.", "output_folder_not_writable")
        drive = self._service("drive", "v3")
        document_id = ""
        succeeded = False

        def trash_copy() -> None:
            try:
                drive.files().update(
                    fileId=document_id,
                    body={"trashed": True},
                    supportsAllDrives=True,
                ).execute(num_retries=2)
            except Exception as exc:
                raise IntegrationError(
                    "Google created a temporary report but could not remove it. "
                    f"Delete Drive file {document_id} and reconnect your Google account.",
                    "google_cleanup_failed",
                ) from exc

        try:
            copied = (
                drive.files()
                .copy(
                    fileId=google_file_id(template_id),
                    body={"name": copy_name, "parents": [google_file_id(output_folder_id)]},
                    fields="id,name",
                    supportsAllDrives=True,
                )
                .execute()
            )
            document_id = str(copied["id"])
            requests = [
                {
                    "replaceAllText": {
                        "containsText": {"text": "{{" + name + "}}", "matchCase": True},
                        "replaceText": str(value),
                    }
                }
                for name, value in replacements.items()
            ]
            update_result = (
                self._service("docs", "v1")
                .documents()
                .batchUpdate(documentId=document_id, body={"requests": requests})
                .execute(num_retries=2)
            )
            changed = {
                name: int(
                    update_result.get("replies", [])[index]
                    .get("replaceAllText", {})
                    .get("occurrencesChanged", 0)
                )
                for index, name in enumerate(replacements)
                if index < len(update_result.get("replies", []))
            }
            missing_required = [name for name in REQUIRED_PLACEHOLDERS if not changed.get(name)]
            if missing_required:
                raise IntegrationError(
                    "The copied template is missing required placeholders: "
                    + ", ".join(missing_required),
                    "template_placeholders_missing",
                )
            rendered_document = (
                self._service("docs", "v1")
                .documents()
                .get(documentId=document_id, includeTabsContent=True)
                .execute(num_retries=2)
            )
            remaining, invalid = template_placeholders(_document_text(rendered_document))
            if remaining or invalid:
                unresolved = [f"{{{{{name}}}}}" for name in remaining] + invalid
                raise IntegrationError(
                    "The generated report still contains unresolved placeholders: "
                    + ", ".join(unresolved),
                    "template_placeholders_unresolved",
                )
            try:
                from googleapiclient.http import MediaIoBaseDownload
            except ImportError as exc:
                raise IntegrationError(
                    "Install the Google API packages from requirements.txt.",
                    "google_dependencies_missing",
                ) from exc
            request = drive.files().export_media(fileId=document_id, mimeType=PDF_MIME)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=2)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(buffer.getvalue())
            if preview:
                trash_copy()
                document_id = ""
            succeeded = True
            return output_path, document_id
        except IntegrationError as exc:
            if document_id:
                try:
                    trash_copy()
                except IntegrationError as cleanup_error:
                    raise cleanup_error from exc
            raise
        except Exception as exc:
            if document_id:
                try:
                    trash_copy()
                except IntegrationError as cleanup_error:
                    raise cleanup_error from exc
            self._raise_google_error(exc)
        finally:
            if preview and not succeeded:
                output_path.unlink(missing_ok=True)
