from __future__ import annotations

import atexit
import base64
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
import re
from unittest.mock import patch

_SERVER_DATA = tempfile.TemporaryDirectory()
atexit.register(_SERVER_DATA.cleanup)
os.environ["BRIGHTLINE_DATA_DIR"] = _SERVER_DATA.name
from backend import server as app_server
from backend.database import Database
from backend.integrations import (
    GoogleWorkspace,
    IntegrationError,
    google_file_id,
    mapped_records,
    preview_table,
    report_replacements,
    template_placeholders,
)


class GoogleReferenceTests(unittest.TestCase):
    def test_oauth_error_message_exposes_codes_without_response_secrets(self) -> None:
        class Error(Exception):
            error = "invalid_grant"
            description = "Authorization code expired"

        self.assertEqual(
            app_server.google_oauth_error(Error()),
            "Google OAuth error: invalid_grant · Authorization code expired",
        )
        self.assertEqual(
            app_server.google_oauth_error(RuntimeError("secret response body")),
            "Google sign-in failed (RuntimeError). Check the OAuth client settings.",
        )

    def test_oauth_accepts_extra_scopes_but_requires_every_brightline_scope(self) -> None:
        class Credentials:
            scopes = app_server.GOOGLE_SCOPES
            granted_scopes = (*app_server.GOOGLE_SCOPES, "extra")

        class Flow:
            credentials = Credentials()

            @staticmethod
            def fetch_token(**kwargs) -> None:
                self.assertEqual(os.environ.get("OAUTHLIB_INSECURE_TRANSPORT"), "1")
                self.assertEqual(os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE"), "1")
                self.assertIn("authorization_response", kwargs)

        self.assertIs(
            app_server.complete_google_oauth(Flow(), "http://127.0.0.1/callback"),
            Flow.credentials,
        )
        Credentials.granted_scopes = app_server.GOOGLE_SCOPES[:-1]
        with self.assertRaisesRegex(IntegrationError, "approve every requested permission"):
            app_server.complete_google_oauth(Flow(), "http://127.0.0.1/callback")

    def test_oauth_client_and_token_stay_in_explicit_local_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            client = root / "oauth-client.json"
            token = root / "oauth-token.json"
            client.write_text(
                json.dumps(
                    {
                        "web": {
                            "client_id": "client-id.apps.googleusercontent.com",
                            "project_id": "brightline",
                            "client_secret": "test-secret",
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": "https://oauth2.googleapis.com/token",
                            "redirect_uris": [app_server.GOOGLE_OAUTH_REDIRECT_URI],
                        }
                    }
                ),
                encoding="utf-8",
            )

            class Credentials:
                @staticmethod
                def to_json() -> str:
                    return '{"refresh_token":"local-test-token"}'

            with patch.dict(
                os.environ,
                {
                    "GOOGLE_OAUTH_CLIENT_FILE": str(client),
                    "GOOGLE_OAUTH_TOKEN_FILE": str(token),
                },
                clear=False,
            ):
                identity = GoogleWorkspace.configured_identity()
                self.assertTrue(identity["configured"])
                self.assertFalse(identity["authorized"])
                flow = GoogleWorkspace.authorization_flow(
                    app_server.GOOGLE_OAUTH_REDIRECT_URI
                )
                authorization_url, state = flow.authorization_url()
                self.assertTrue(state)
                self.assertIn("accounts.google.com", authorization_url)
                GoogleWorkspace.save_oauth_credentials(Credentials())
                self.assertEqual(token.stat().st_mode & 0o777, 0o600)
                self.assertTrue(GoogleWorkspace.configured_identity()["authorized"])
                GoogleWorkspace.disconnect()
                self.assertFalse(token.exists())

    def test_service_account_json_is_not_accepted_as_an_oauth_client(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            client = Path(folder) / "service-account.json"
            client.write_text(
                '{"type":"service_account","client_email":"old@example.test"}',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"GOOGLE_OAUTH_CLIENT_FILE": str(client)},
                clear=False,
            ):
                identity = GoogleWorkspace.configured_identity()
            self.assertFalse(identity["configured"])
            self.assertIn("Web application", identity["error"])

    def test_extracts_supported_google_ids(self) -> None:
        identifier = "1AbCdEfGhIjKlMnOpQrStUvWxYz_12345"
        self.assertEqual(
            google_file_id(f"https://docs.google.com/spreadsheets/d/{identifier}/edit#gid=0"),
            identifier,
        )
        self.assertEqual(
            google_file_id(f"https://docs.google.com/document/d/{identifier}/edit"),
            identifier,
        )
        self.assertEqual(
            google_file_id(f"https://drive.google.com/drive/folders/{identifier}"),
            identifier,
        )
        self.assertEqual(google_file_id(identifier), identifier)

    def test_rejects_arbitrary_urls(self) -> None:
        with self.assertRaises(IntegrationError):
            google_file_id("https://example.com/document/secret")

    def test_accepts_writable_personal_drive_output_folder(self) -> None:
        class Workspace(GoogleWorkspace):
            def __init__(self) -> None:
                self.project_id = "brightline"

            def account_email(self) -> str:
                return "owner@example.test"

            def file_metadata(self, *_args, **_kwargs):
                return {
                    "id": "folder_identifier_123",
                    "name": "Reports",
                    "capabilities": {"canAddChildren": True},
                }

        result = Workspace().test_connection("folder_identifier_123")
        self.assertEqual(result["account"], "owner@example.test")
        self.assertEqual(result["output_folder_id"], "folder_identifier_123")

    def test_rejects_an_unwritable_personal_output_folder(self) -> None:
        class Workspace(GoogleWorkspace):
            def __init__(self) -> None:
                self.project_id = "brightline"

            def account_email(self) -> str:
                return "owner@example.test"

            def file_metadata(self, *_args, **_kwargs):
                return {
                    "id": "folder_identifier_123",
                    "name": "Reports",
                    "capabilities": {"canAddChildren": False},
                }

        with self.assertRaisesRegex(IntegrationError, "cannot add reports"):
            Workspace().test_connection("folder_identifier_123")

    def test_creates_a_brightline_folder_in_personal_drive(self) -> None:
        class Execute:
            def __init__(self, result) -> None:
                self.result = result

            def execute(self, **_kwargs):
                return self.result

        class Files:
            def list(self, **_kwargs):
                return Execute({"files": []})

            def create(self, **kwargs):
                self.created = kwargs["body"]
                return Execute(
                    {
                        "id": "personal_folder_123",
                        "name": self.created["name"],
                        "capabilities": {"canAddChildren": True},
                    }
                )

        class Drive:
            files_api = Files()

            def files(self):
                return self.files_api

        class Workspace(GoogleWorkspace):
            def __init__(self) -> None:
                self.project_id = "brightline"
                self.drive = Drive()

            def account_email(self) -> str:
                return "owner@example.test"

            def _service(self, *_args):
                return self.drive

        result = Workspace().test_connection(ensure_output_folder=True)
        self.assertEqual(result["output_folder_id"], "personal_folder_123")
        self.assertEqual(result["output_folder_name"], "Brightline Reports")

    def test_classifies_rate_limit_hidden_inside_google_403(self) -> None:
        class Response:
            status = 403

        class Error(Exception):
            resp = Response()
            content = b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}'

        with self.assertRaises(IntegrationError) as raised:
            GoogleWorkspace._raise_google_error(Error())
        self.assertEqual(raised.exception.code, "google_rate_limited")

    def test_failed_preview_cleanup_removes_the_local_pdf(self) -> None:
        class Execute:
            def __init__(self, result=None, error: Exception | None = None) -> None:
                self.result = result
                self.error = error

            def execute(self, **_kwargs):
                if self.error:
                    raise self.error
                return self.result

        class Files:
            def copy(self, **_kwargs):
                return Execute({"id": "generated_document_123"})

            def export_media(self, **_kwargs):
                return object()

            def update(self, **_kwargs):
                return Execute(error=RuntimeError("trash failed"))

        class Drive:
            files_api = Files()

            def files(self):
                return self.files_api

        class Documents:
            def batchUpdate(self, **kwargs):
                return Execute(
                    {
                        "replies": [
                            {"replaceAllText": {"occurrencesChanged": 1}}
                            for _ in kwargs["body"]["requests"]
                        ]
                    }
                )

            def get(self, **_kwargs):
                return Execute({"body": {"content": []}})

        class Docs:
            documents_api = Documents()

            def documents(self):
                return self.documents_api

        class Workspace(GoogleWorkspace):
            def __init__(self) -> None:
                self.drive = Drive()
                self.docs = Docs()

            def file_metadata(self, _identifier, expected_mime=None):
                if expected_mime == "application/vnd.google-apps.folder":
                    return {
                        "driveId": "shared_drive_123",
                        "capabilities": {
                            "canAddChildren": True,
                            "canTrashChildren": True,
                        },
                    }
                return {"capabilities": {"canCopy": True}}

            def _service(self, name, _version):
                return self.drive if name == "drive" else self.docs

        class Downloader:
            def __init__(self, buffer, _request) -> None:
                self.buffer = buffer

            def next_chunk(self, **_kwargs):
                self.buffer.write(b"%PDF-test")
                return None, True

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "preview.pdf"
            with patch("googleapiclient.http.MediaIoBaseDownload", Downloader):
                with self.assertRaises(IntegrationError) as raised:
                    Workspace().create_report_pdf(
                        template_id="template_identifier_123",
                        output_folder_id="folder_identifier_123",
                        copy_name="Preview",
                        replacements={
                            name: "safe value"
                            for name in app_server.REQUIRED_PLACEHOLDERS
                        },
                        output_path=output,
                        preview=True,
                    )
            self.assertEqual(raised.exception.code, "google_cleanup_failed")
            self.assertFalse(output.exists())


class TableMappingTests(unittest.TestCase):
    def test_suggests_and_maps_student_rows(self) -> None:
        values = [
            ["학생번호", "학생명", "과목명", "점수", "만점"],
            ["S-1", "Mark", "Economics", "18", "20"],
            ["S-2", "June", "Economics", "19", "20"],
        ]
        preview = preview_table(values)
        self.assertEqual(preview["suggested_mapping"]["student_name"], "학생명")
        self.assertEqual(preview["suggested_mapping"]["class_name"], "과목명")
        records = mapped_records(values, preview["suggested_mapping"])
        self.assertEqual(records[0]["student_id"], "S-1")
        self.assertEqual(records[1]["weekly_test_score"], "19")

    def test_ignores_trailing_assessment_row_without_student_identity(self) -> None:
        values = [
            ["과목명", "학생", "학년", "학교", "7/13", "7/14"],
            ["(2차) 09:00 [26SU] DP1 ESS H+S", "엄하진", "10", "J1MS", "A", "F"],
            ["", "", "", "", "F", "A"],
        ]
        mapping = preview_table(values)["suggested_mapping"]
        records = mapped_records(values, mapping)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["student_name"], "엄하진")
        self.assertEqual(records[0]["class_name"], "(2차) 09:00 [26SU] DP1 ESS H+S")

    def test_rejects_row_with_only_one_identity_value(self) -> None:
        values = [
            ["Class", "Student", "Score"],
            ["Economics", "", "91"],
        ]
        with self.assertRaisesRegex(IntegrationError, "student name and class name"):
            mapped_records(values, preview_table(values)["suggested_mapping"])

    def test_duplicate_headers_are_rejected(self) -> None:
        with self.assertRaises(IntegrationError):
            preview_table([["Student", "Student"], ["Mark", "Mark"]])

    def test_selects_the_requested_sheet_header_row(self) -> None:
        values = [
            ["", "", "Staff note"],
            ["Course", "Teacher", "Student"],
            ["과목명", "담당자 이름", "학생", "학년", "학교"],
            ["Economics", "Teacher", "Mark", "11", "School"],
        ]
        selected, header_row = app_server._values_from_header_row(values, 3)
        preview = preview_table(selected)
        self.assertEqual(header_row, 3)
        self.assertEqual(preview["suggested_mapping"]["student_name"], "학생")
        self.assertEqual(preview["suggested_mapping"]["class_name"], "과목명")
        self.assertEqual(preview["row_count"], 1)
        with self.assertRaisesRegex(ValueError, "positive whole number"):
            app_server._values_from_header_row(values, 0)

    def test_google_sheet_header_and_mapping_are_detected_automatically(self) -> None:
        values = [
            ["Assignment export", "", ""],
            ["Course", "Teacher", "Student"],
            ["과목명", "담당자 이름", "학생", "학년", "학교", "8/17", "8/18"],
            ["Economics", "Teacher", "Mark", "11", "School", "A", "F"],
        ]
        selected, header_row, preview = app_server._automatic_sheet_values(
            values, "assignment"
        )
        self.assertEqual(header_row, 3)
        self.assertEqual(selected[0][0], "과목명")
        self.assertEqual(preview["suggested_mapping"]["student_name"], "학생")
        self.assertEqual(preview["suggested_mapping"]["class_name"], "과목명")

    def test_automatic_sheet_detection_uses_kind_specific_fields(self) -> None:
        values = [
            ["Student", "Class"],
            ["Ignore", "Ignore"],
            ["Student", "Class", "Score"],
            ["Mark", "Economics", "91"],
        ]
        _, header_row, preview = app_server._automatic_sheet_values(
            values, "weekly_test"
        )
        self.assertEqual(header_row, 3)
        self.assertEqual(preview["suggested_mapping"]["weekly_test_score"], "Score")

    def test_automatic_sheet_detection_rejects_unrecognized_columns(self) -> None:
        with self.assertRaisesRegex(IntegrationError, "could not identify") as raised:
            app_server._automatic_sheet_values(
                [["Person", "Group"], ["Mark", "Economics"]],
                "comments",
            )
        self.assertEqual(raised.exception.code, "automatic_sheet_mapping_failed")

    def test_counts_missed_assignments_from_dated_columns(self) -> None:
        values, mapping, dated = app_server._assignment_values_from_dated_columns(
            [
                ["과목명", "학생", "학교", "7/13", "7/14", "7/15", "7/16"],
                ["Economics", "Mark", "School", "A", "F", "X", "B"],
                ["Economics", "June", "School", "X", "", "X", ""],
            ],
            {"class_name": "과목명", "student_name": "학생", "school": "학교"},
        )
        records = mapped_records(values, mapping)
        self.assertEqual(dated, ["7/13", "7/14", "7/15", "7/16"])
        self.assertEqual(records[0]["assignment_completed"], "2")
        self.assertEqual(records[0]["assignment_total"], "3")
        self.assertEqual(records[0]["assignment_excluded"], "1")
        self.assertEqual(records[1]["assignment_completed"], "0")
        self.assertEqual(records[1]["assignment_total"], "0")
        self.assertEqual(records[1]["assignment_excluded"], "4")

    def test_dated_assignment_counts_stay_aligned_when_trailing_cells_are_blank(self) -> None:
        values, mapping, _ = app_server._assignment_values_from_dated_columns(
            [
                ["과목명", "학생", "학년", "학교", "7/13", "7/14", "7/15", "7/16", "7/17"],
                ["(2차) 09:00 [26SU] DP1 ESS H+S", "엄지아", "10", "J1MS", "A", "A", "F"],
            ],
            {
                "class_name": "과목명",
                "student_name": "학생",
                "grade": "학년",
                "school": "학교",
            },
        )

        records = mapped_records(values, mapping)
        self.assertEqual(records[0]["assignment_completed"], "2")
        self.assertEqual(records[0]["assignment_total"], "3")
        self.assertEqual(records[0]["assignment_excluded"], "2")


class ReportReplacementTests(unittest.TestCase):
    def test_uses_scored_test_and_separate_comment(self) -> None:
        replacements = report_replacements(
            {
                "id": "internal",
                "external_student_id": "S-42",
                "name": "Mark",
                "class_name": "Economics",
                "grade": "11",
                "school": "Brightline School",
                "period_label": "Week 1",
                "homework_completed": 8,
                "homework_incomplete": 2,
                "homework_percent": 80,
                "exams": [
                    {"label": "Weekly Test 1", "score": 18, "max_score": 20, "comment": ""},
                    {"label": "Teacher comment", "score": None, "max_score": None, "comment": "Strong analysis."},
                ],
            }
        )
        self.assertEqual(replacements["student_id"], "S-42")
        self.assertEqual(replacements["assignment_summary"], "2 assignments missed")
        self.assertEqual(replacements["weekly_test_score"], "18 / 20")
        self.assertEqual(replacements["teacher_comment"], "Strong analysis.")

    def test_template_fields_must_be_exact_and_supported(self) -> None:
        fields, invalid = template_placeholders(
            "{{student_name}} {{ student_name }} {{made_up_field}} "
            "{{{class_name}}} {{reporting_week}}} {{}} {{unclosed"
        )
        self.assertEqual(fields, ["student_name"])
        self.assertEqual(
            invalid,
            [
                "unclosed {{",
                "{{ student_name }}",
                "{{made_up_field}}",
                "{{reporting_week}}}",
                "{{{class_name}}}",
                "{{}}",
            ],
        )

    def test_report_values_cannot_inject_another_placeholder(self) -> None:
        workspace = object.__new__(GoogleWorkspace)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(
            IntegrationError
        ) as raised:
            workspace.create_report_pdf(
                template_id="template_identifier_123",
                output_folder_id="folder_identifier_123",
                copy_name="Report",
                replacements={
                    "student_name": "{{teacher_comment}}",
                    "teacher_comment": "Private feedback",
                },
                output_path=Path(folder) / "report.pdf",
            )
        self.assertEqual(raised.exception.code, "unsafe_template_value")


class ImportSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.original_database = app_server.DB
        app_server.DB = Database(Path(self.folder.name) / "brightline.db")

    def tearDown(self) -> None:
        app_server.DB = self.original_database
        self.folder.cleanup()

    @staticmethod
    def assignment_mapping() -> dict[str, str]:
        return {
            "student_id": "Student ID",
            "student_name": "Student",
            "class_name": "Class",
            "assignment_completed": "Completed",
            "assignment_total": "Total",
            "reporting_week": "Week",
        }

    @staticmethod
    def new_period() -> dict[str, str]:
        return {
            "id": "",
            "label": "Week 1",
            "start_date": "2026-08-17",
            "end_date": "2026-08-23",
        }

    def test_dated_assignment_import_counts_missed_per_student(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["학생", "과목명", "학교", "8/17", "8/18", "8/19"],
                ["Mark", "Economics", "School", "A", "F", "B"],
                ["June", "Economics", "School", "X", "", "X"],
            ],
            mapping={
                "student_name": "학생",
                "class_name": "과목명",
                "school": "학교",
            },
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        students = {item["name"]: item for item in app_server.DB.state()["students"]}
        self.assertEqual(students["Mark"]["homework_incomplete"], 1)
        self.assertEqual(students["Mark"]["homework_excluded"], 0)
        self.assertEqual(students["June"]["homework_incomplete"], 0)
        self.assertEqual(students["June"]["homework_excluded"], 3)
        self.assertEqual(
            report_replacements(students["June"])["assignment_summary"],
            "0 assignments missed",
        )

    def test_mixed_weeks_and_duplicate_rows_write_nothing(self) -> None:
        headers = ["Student ID", "Student", "Class", "Completed", "Total", "Week"]
        with self.assertRaisesRegex(ValueError, "multiple reporting weeks"):
            app_server.import_mapped_table(
                values=[
                    headers,
                    ["S-1", "Mark", "Economics", "8", "10", "Week 1"],
                    ["S-2", "June", "Economics", "9", "10", "Week 2"],
                ],
                mapping=self.assignment_mapping(),
                kind="assignment",
                period=self.new_period(),
                source_name="Assignments.csv",
                source_provider="manual",
            )
        self.assertEqual(app_server.DB.state()["periods"], [])

        with self.assertRaisesRegex(ValueError, "duplicate rows"):
            app_server.import_mapped_table(
                values=[
                    headers,
                    ["S-1", "Mark", "Economics", "8", "10", "Week 1"],
                    ["S-1", "Mark", "Economics", "9", "10", "Week 1"],
                ],
                mapping=self.assignment_mapping(),
                kind="assignment",
                period=self.new_period(),
                source_name="Assignments.csv",
                source_provider="manual",
            )
        self.assertEqual(app_server.DB.state()["periods"], [])

    def test_test_import_cannot_create_an_unmatched_student(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total", "Week"],
                ["S-1", "Mark", "Economics", "8", "10", "Week 1"],
            ],
            mapping=self.assignment_mapping(),
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        period = app_server.DB.state()["periods"][0]
        with self.assertRaisesRegex(ValueError, "No Assignment roster match"):
            app_server.import_mapped_table(
                values=[
                    ["Student ID", "Student", "Class", "Score"],
                    ["S-999", "Mark typo", "Economics", "18"],
                ],
                mapping={
                    "student_id": "Student ID",
                    "student_name": "Student",
                    "class_name": "Class",
                    "weekly_test_score": "Score",
                },
                kind="weekly_test",
                period=period,
                source_name="Tests.csv",
                source_provider="manual",
            )
        self.assertEqual(len(app_server.DB.state()["students"]), 1)

    def test_reimport_requires_confirmation_and_keeps_period_label(self) -> None:
        values = [
            ["Student ID", "Student", "Class", "Completed", "Total"],
            ["S-1", "Mark", "Economics", "8", "10"],
        ]
        mapping = {
            key: value
            for key, value in self.assignment_mapping().items()
            if key != "reporting_week"
        }
        app_server.import_mapped_table(
            values=values,
            mapping=mapping,
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        changed_period = {**self.new_period(), "label": "Week 2"}
        changed_values = [values[0], ["S-1", "Mark", "Economics", "9", "10"]]
        with self.assertRaises(IntegrationError) as raised:
            app_server.import_mapped_table(
                values=changed_values,
                mapping=mapping,
                kind="assignment",
                period=changed_period,
                source_name="Assignments.csv",
                source_provider="manual",
            )
        self.assertEqual(raised.exception.code, "replacement_confirmation_required")
        state = app_server.DB.state()
        self.assertEqual(state["periods"][0]["label"], "Week 1")
        self.assertEqual(state["students"][0]["homework_completed"], 8)

        app_server.import_mapped_table(
            values=changed_values,
            mapping=mapping,
            kind="assignment",
            period=changed_period,
            source_name="Assignments.csv",
            source_provider="manual",
            confirm_replace=True,
        )
        state = app_server.DB.state()
        self.assertEqual(state["periods"][0]["label"], "Week 1")
        self.assertEqual(state["students"][0]["homework_completed"], 9)

    def test_percent_only_assignment_does_not_invent_counts(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Percent"],
                ["S-1", "Mark", "Economics", "78.4%"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "assignment_percent": "Percent",
            },
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        student = app_server.DB.state()["students"][0]
        self.assertEqual(student["homework_completed"], 0)
        self.assertEqual(student["homework_incomplete"], 0)
        self.assertEqual(student["homework_percent"], 78.4)
        self.assertTrue(student["assignment_ready"])
        self.assertEqual(
            report_replacements(student)["assignment_summary"],
            "Missed assignment count unavailable",
        )

    def test_assignment_counts_win_when_percent_is_also_mapped(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total", "Percent"],
                ["S-1", "Mark", "Economics", "8", "10", "80%"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "assignment_completed": "Completed",
                "assignment_total": "Total",
                "assignment_percent": "Percent",
            },
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        student = app_server.DB.state()["students"][0]
        self.assertEqual(student["homework_completed"], 8)
        self.assertEqual(student["homework_incomplete"], 2)
        self.assertEqual(student["homework_percent"], 80)

    def test_assignment_rejects_a_percent_that_conflicts_with_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            app_server.import_mapped_table(
                values=[
                    ["Student", "Class", "Completed", "Total", "Percent"],
                    ["Mark", "Economics", "8", "10", "70%"],
                ],
                mapping={
                    "student_name": "Student",
                    "class_name": "Class",
                    "assignment_completed": "Completed",
                    "assignment_total": "Total",
                    "assignment_percent": "Percent",
                },
                kind="assignment",
                period=self.new_period(),
                source_name="Assignments.csv",
                source_provider="manual",
            )
        self.assertEqual(app_server.DB.state()["students"], [])

    def test_non_finite_numbers_and_fractional_counts_are_rejected(self) -> None:
        base_values = [
            ["Student ID", "Student", "Class", "Completed", "Total"],
            ["S-1", "Mark", "Economics", "8", "10"],
        ]
        mapping = {
            key: value
            for key, value in self.assignment_mapping().items()
            if key != "reporting_week"
        }
        for completed in ("NaN", "Infinity", "8.9"):
            values = [base_values[0], ["S-1", "Mark", "Economics", completed, "10"]]
            with self.subTest(completed=completed), self.assertRaises(IntegrationError):
                app_server.import_mapped_table(
                    values=values,
                    mapping=mapping,
                    kind="assignment",
                    period=self.new_period(),
                    source_name="Assignments.csv",
                    source_provider="manual",
                )
        self.assertEqual(app_server.DB.state()["students"], [])

    def test_non_finite_weekly_test_scores_are_rejected(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-1", "Mark", "Economics", "8", "10"],
            ],
            mapping={
                key: value
                for key, value in self.assignment_mapping().items()
                if key != "reporting_week"
            },
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        period = app_server.DB.state()["periods"][0]
        for score in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(score=score), self.assertRaises(IntegrationError):
                app_server.import_mapped_table(
                    values=[
                        ["Student ID", "Student", "Class", "Score"],
                        ["S-1", "Mark", "Economics", score],
                    ],
                    mapping={
                        "student_id": "Student ID",
                        "student_name": "Student",
                        "class_name": "Class",
                        "weekly_test_score": "Score",
                    },
                    kind="weekly_test",
                    period=period,
                    source_name="Scores.csv",
                    source_provider="manual",
                )
        student = app_server.DB.state()["students"][0]
        self.assertEqual(app_server.DB.get_exams(student["id"], period["id"]), [])

    def test_comments_keep_separate_label_and_source_from_weekly_test(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total", "Week"],
                ["S-1", "Mark", "Economics", "8", "10", "Week 1"],
            ],
            mapping=self.assignment_mapping(),
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        state = app_server.DB.state()
        period = state["periods"][0]
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score", "Week"],
                ["S-1", "Mark", "Economics", "18", "Week 1"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "weekly_test_score": "Score",
                "reporting_week": "Week",
            },
            kind="weekly_test",
            period=period,
            source_name="Scores sheet",
            source_provider="google_sheets",
        )
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Comment", "Week"],
                ["S-1", "Mark", "Economics", "Strong analysis.", "Week 1"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "comment": "Comment",
                "reporting_week": "Week",
            },
            kind="comments",
            period=period,
            source_name="Comments.csv",
            source_provider="manual",
        )
        student = app_server.DB.state()["students"][0]
        exams = app_server.DB.get_exams(student["id"], period["id"])
        self.assertEqual(len(exams), 2)
        score = next(item for item in exams if item["score"] is not None)
        comment = next(item for item in exams if item["comment"])
        self.assertEqual(score["label"], "Week 1")
        self.assertEqual(score["source_provider"], "google_sheets")
        self.assertEqual(comment["label"], "Teacher comment")
        self.assertEqual(comment["source_provider"], "manual")

    def test_comments_readiness_uses_the_requested_period(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-1", "Mark", "Economics", "8", "10"],
            ],
            mapping={
                key: value
                for key, value in self.assignment_mapping().items()
                if key != "reporting_week"
            },
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        first_period = app_server.DB.state()["periods"][0]
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score"],
                ["S-1", "Mark", "Economics", "18"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "weekly_test_score": "Score",
            },
            kind="weekly_test",
            period=first_period,
            source_name="Scores.csv",
            source_provider="manual",
        )
        class_id = app_server.DB.state()["classes"][0]["id"]
        second_period = app_server.DB.ensure_period(
            "2026-08-24", "2026-08-30", "Week 2"
        )
        app_server.DB.select_class_period(class_id, second_period["id"])
        self.assertTrue(
            app_server.class_period_has_assignment_and_test(
                class_id, first_period["id"]
            )
        )

    def test_comments_pdf_namespaces_labels_and_reviews_unready_students(self) -> None:
        mapping = {
            key: value
            for key, value in self.assignment_mapping().items()
            if key != "reporting_week"
        }
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-1", "Mark", "Economics", "8", "10"],
            ],
            mapping=mapping,
            kind="assignment",
            period=self.new_period(),
            source_name="Week 1 assignments.csv",
            source_provider="manual",
        )
        first_period = app_server.DB.state()["periods"][0]
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score"],
                ["S-1", "Mark", "Economics", "18"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "weekly_test_score": "Score",
            },
            kind="weekly_test",
            period=first_period,
            source_name="Week 1 scores.csv",
            source_provider="manual",
        )
        second_period = {
            "id": "",
            "label": "Week 2",
            "start_date": "2026-08-24",
            "end_date": "2026-08-30",
        }
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-2", "June", "Economics", "9", "10"],
            ],
            mapping=mapping,
            kind="assignment",
            period=second_period,
            source_name="Week 2 assignments.csv",
            source_provider="manual",
        )
        state = app_server.DB.state()
        class_id = state["classes"][0]["id"]
        students = {student["name"]: student for student in state["students"]}

        class Handler:
            response: dict[str, object] = {}

            def _send_json(self, payload: dict[str, object]) -> None:
                self.response = payload

        def parsed_comments(_pdf: bytes, label: str, filename: str):
            self.assertEqual(label, "Teacher comment · Week 1")
            return [
                {
                    "name": name,
                    "label": label,
                    "comment": comment,
                    "sections": [],
                    "source_name": filename,
                }
                for name, comment in (
                    ("Mark", "Strong analysis."),
                    ("June", "Good effort."),
                )
            ]

        handler = Handler()
        with patch.object(app_server, "parse_comment_pdf", side_effect=parsed_comments):
            app_server.AppHandler._import_comments_pdf(
                handler,
                {
                    "filename": "comments.pdf",
                    "class_id": class_id,
                    "period_id": first_period["id"],
                    "label": "Week 1",
                    "pdf_base64": base64.b64encode(b"fake-pdf").decode(),
                },
            )
        mark_exams = app_server.DB.get_exams(students["Mark"]["id"], first_period["id"])
        self.assertEqual(
            {exam["label"] for exam in mark_exams},
            {"Week 1", "Teacher comment · Week 1"},
        )
        self.assertEqual(
            app_server.DB.get_exams(students["June"]["id"], first_period["id"]), []
        )
        issue = app_server.DB.state()["issues"][0]
        with self.assertRaisesRegex(ValueError, "still needs Assignment"):
            app_server.AppHandler._resolve_issue(
                None, issue["id"], {"student_id": students["June"]["id"]}
            )

        import_count = len(app_server.DB.state()["files"])
        with patch.object(
            app_server,
            "parse_comment_pdf",
            return_value=[
                {
                    "name": "Mark",
                    "label": "Teacher comment · Blank",
                    "comment": "",
                    "sections": [],
                    "source_name": "blank.pdf",
                }
            ],
        ), self.assertRaisesRegex(ValueError, "No teacher comment"):
            app_server.AppHandler._import_comments_pdf(
                handler,
                {
                    "filename": "blank.pdf",
                    "class_id": class_id,
                    "period_id": first_period["id"],
                    "label": "Blank",
                    "pdf_base64": base64.b64encode(b"fake-pdf").decode(),
                },
            )
        self.assertEqual(len(app_server.DB.state()["files"]), import_count)

    def test_disconnect_cannot_be_bypassed_by_sync_or_template_routes(self) -> None:
        class_item = app_server.DB.upsert_class("Economics")
        app_server.DB.set_setting("google_connection_disabled", "1")
        with self.assertRaisesRegex(ValueError, "Reconnect Google Workspace"):
            app_server.AppHandler._google_sheet_sync(None, {})
        with self.assertRaisesRegex(ValueError, "Reconnect Google Workspace"):
            app_server.AppHandler._template_action(
                None,
                class_item["id"],
                "test",
                {"document": "document_identifier_123"},
            )

    def test_changed_oauth_client_requires_a_new_connection_test(self) -> None:
        app_server.DB.set_setting("google_connection_status", "connected")
        app_server.DB.set_setting("google_connection_account", "old@example.test")
        app_server.DB.set_setting("google_connection_identity", "old-client")
        app_server.DB.set_setting("google_sheets_verified", "1")
        with patch.object(
            app_server.GoogleWorkspace,
            "configured_identity",
            return_value={
                "configured": True,
                "authorized": True,
                "fingerprint": "new-client",
                "project": "brightline",
                "error": "",
            },
        ):
            connection = app_server.google_connection_state()["google_sheets"]
        self.assertEqual(connection["account_name"], "old@example.test")
        self.assertNotEqual(connection["status"], "connected")

    def test_new_oauth_account_resets_other_google_verification(self) -> None:
        app_server.DB.set_setting("google_connection_account", "old@example.test")
        app_server.DB.set_setting("google_connection_identity", "old-client")
        app_server.DB.set_setting("google_output_folder_id", "old-folder")
        for key in (
            "google_sheets_verified",
            "google_docs_verified",
            "google_output_folder_verified",
        ):
            app_server.DB.set_setting(key, "1")

        class Workspace:
            identity_fingerprint = "new-client"

            @staticmethod
            def account_email() -> str:
                return "new@example.test"

        app_server.mark_google_service_verified("sheets", Workspace())
        self.assertEqual(
            app_server.DB.get_setting("google_connection_account"), "new@example.test"
        )
        self.assertEqual(app_server.DB.get_setting("google_sheets_verified"), "1")
        self.assertEqual(app_server.DB.get_setting("google_docs_verified"), "0")
        self.assertEqual(
            app_server.DB.get_setting("google_output_folder_verified"), "0"
        )
        self.assertEqual(app_server.DB.get_setting("google_output_folder_id"), "")

    def test_each_template_must_match_the_active_oauth_account(self) -> None:
        first_class = app_server.DB.upsert_class("Economics")
        second_class = app_server.DB.upsert_class("History")
        for class_item in (first_class, second_class):
            app_server.save_class_template(
                class_item["id"],
                {
                    "document_id": f"document_{class_item['id']}",
                    "name": f"{class_item['name']} template",
                    "missing_required": [],
                    "invalid_placeholders": [],
                    "verified_account": "old@example.test",
                },
            )
        app_server.DB.set_setting("google_connection_status", "connected")
        app_server.DB.set_setting("google_connection_account", "new@example.test")
        app_server.DB.set_setting("google_connection_identity", "oauth-client")
        app_server.DB.set_setting("google_docs_verified", "1")
        app_server.DB.set_setting("google_output_folder_id", "folder_identifier_123")
        app_server.DB.set_setting("google_output_folder_verified", "1")
        identity = {
            "configured": True,
            "authorized": True,
            "fingerprint": "oauth-client",
            "project": "brightline",
            "error": "",
        }
        with patch.object(
            app_server.GoogleWorkspace, "configured_identity", return_value=identity
        ):
            templates = app_server.app_state()["class_templates"]
        self.assertEqual({item["status"] for item in templates}, {"needs_test"})

        first_template = app_server.class_template(first_class["id"])
        app_server.save_class_template(
            first_class["id"],
            {**first_template, "verified_account": "new@example.test"},
        )
        with patch.object(
            app_server.GoogleWorkspace, "configured_identity", return_value=identity
        ):
            templates = {
                item["class_id"]: item for item in app_server.app_state()["class_templates"]
            }
        self.assertEqual(templates[first_class["id"]]["status"], "ready")
        self.assertEqual(templates[second_class["id"]]["status"], "needs_test")

    def test_partial_zip_keeps_successes_and_reports_failures(self) -> None:
        students = [
            {
                "id": "a" * 32,
                "external_student_id": "S-1",
                "name": "Mark",
                "class_id": "c" * 32,
                "class_name": "Economics",
                "period_id": "p" * 32,
                "period_label": "Week 1",
                "report_ready": True,
            },
            {
                "id": "b" * 32,
                "external_student_id": "S-2",
                "name": "June",
                "class_id": "c" * 32,
                "class_name": "Economics",
                "period_id": "p" * 32,
                "period_label": "Week 1",
                "report_ready": True,
            },
        ]
        state = {
            "students": students,
            "classes": [{"id": "c" * 32, "name": "Economics"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)

            def fake_report(student_id: str, _period_id: str) -> Path:
                if student_id == "b" * 32:
                    raise app_server.ValidationError("Template value is missing.")
                report = output / "mark.pdf"
                report.write_bytes(b"%PDF-test")
                return report

            with (
                patch.object(app_server, "app_state", return_value=state),
                patch.object(app_server, "generate_report", side_effect=fake_report),
                patch.object(app_server, "EXPORT_DIR", output / "exports"),
            ):
                archive, manifest, filename = app_server.generate_zip("c" * 32)

            self.assertIsNotNone(archive)
            self.assertEqual(filename, "Economics.zip")
            self.assertEqual(len(manifest["generated"]), 1)
            self.assertEqual(manifest["failed"][0]["student"], "June")
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.namelist()
                self.assertIn("generation-summary.json", members)
                self.assertEqual(len([name for name in members if name.endswith(".pdf")]), 1)

    def test_batch_rolls_back_when_a_late_write_fails(self) -> None:
        class FailingDatabase(Database):
            writes = 0

            def update_homework(self, *args, **kwargs):
                self.writes += 1
                if self.writes == 2:
                    raise RuntimeError("simulated disk failure")
                return super().update_homework(*args, **kwargs)

        app_server.DB = FailingDatabase(Path(self.folder.name) / "rollback.db")
        with self.assertRaisesRegex(RuntimeError, "simulated disk failure"):
            app_server.import_mapped_table(
                values=[
                    ["Student ID", "Student", "Class", "Completed", "Total"],
                    ["S-1", "Mark", "Economics", "8", "10"],
                    ["S-2", "June", "Economics", "9", "10"],
                ],
                mapping={
                    key: value
                    for key, value in self.assignment_mapping().items()
                    if key != "reporting_week"
                },
                kind="assignment",
                period=self.new_period(),
                source_name="Assignments.csv",
                source_provider="manual",
            )
        state = app_server.DB.state()
        self.assertEqual(state["periods"], [])
        self.assertEqual(state["classes"], [])
        self.assertEqual(state["students"], [])

    def test_legacy_assignment_batch_is_atomic(self) -> None:
        class FailingDatabase(Database):
            writes = 0

            def update_homework(self, *args, **kwargs):
                self.writes += 1
                if self.writes == 2:
                    raise RuntimeError("simulated legacy failure")
                return super().update_homework(*args, **kwargs)

        app_server.DB = FailingDatabase(Path(self.folder.name) / "legacy-rollback.db")
        csv_text = "\n".join(
            (
                "과목명,담당자 이름,학생,학년,학교,과제",
                ",,,,,2026-08-17",
                "Economics,Jane,Mark,11,Brightline School,A",
                "Economics,Jane,June,11,Brightline School,B",
            )
        )
        with self.assertRaisesRegex(RuntimeError, "simulated legacy failure"):
            app_server.import_assignment_csv(
                csv_text,
                "homework.csv",
                "2026-08-17",
                "2026-08-17",
                "Week 1",
            )
        state = app_server.DB.state()
        self.assertEqual(state["periods"], [])
        self.assertEqual(state["classes"], [])
        self.assertEqual(state["students"], [])

    def test_reports_stay_disabled_until_google_output_is_ready(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-1", "Mark", "Economics", "8", "10"],
            ],
            mapping={
                key: value
                for key, value in self.assignment_mapping().items()
                if key != "reporting_week"
            },
            kind="assignment",
            period=self.new_period(),
            source_name="Assignments.csv",
            source_provider="manual",
        )
        period = app_server.DB.state()["periods"][0]
        for kind, header, field, value in (
            ("weekly_test", "Score", "weekly_test_score", "18"),
            ("comments", "Comment", "comment", "Strong analysis."),
        ):
            app_server.import_mapped_table(
                values=[
                    ["Student ID", "Student", "Class", header],
                    ["S-1", "Mark", "Economics", value],
                ],
                mapping={
                    "student_id": "Student ID",
                    "student_name": "Student",
                    "class_name": "Class",
                    field: header,
                },
                kind=kind,
                period=period,
                source_name=f"{kind}.csv",
                source_provider="manual",
            )
        class_id = app_server.DB.state()["classes"][0]["id"]
        app_server.save_class_template(
            class_id,
            {
                "document_id": "document_identifier_123",
                "name": "Economics template",
                "missing_required": [],
                "invalid_placeholders": [],
            },
        )
        app_server.DB.set_setting("google_connection_disabled", "1")
        state = app_server.app_state()
        self.assertTrue(state["students"][0]["template_ready"])
        self.assertFalse(state["students"][0]["google_ready"])
        self.assertFalse(state["students"][0]["report_ready"])


class DatabaseReadinessTests(unittest.TestCase):
    def test_existing_period_label_is_not_silently_renamed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "brightline.db")
            first = database.ensure_period("2026-08-17", "2026-08-23", "Week 1")
            second = database.ensure_period("2026-08-17", "2026-08-23", "Week 2")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(second["label"], "Week 1")

    def test_clear_data_preserves_google_connection_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "brightline.db")
            preserved = {
                "google_connection_status": "connected",
                "google_connection_account": "reports@example.test",
                "google_output_folder_id": "folder_identifier_123",
                "google_output_folder_name": "Academy Reports",
                "google_output_folder_verified": "1",
                "google_sheets_verified": "1",
                "google_docs_verified": "1",
            }
            for key, value in preserved.items():
                database.set_setting(key, value)
            database.set_setting("google_sheets_last_sync", "old-import")
            database.clear_all()
            for key, value in preserved.items():
                self.assertEqual(database.get_setting(key), value)
            self.assertEqual(database.get_setting("google_sheets_last_sync"), "")

    def test_external_ids_distinguish_identical_names(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "brightline.db")
            class_item = database.upsert_class("Economics")
            first, first_created = database.upsert_student(
                class_id=class_item["id"],
                class_name=class_item["name"],
                name="Alex Kim",
                school="Brightline School",
                grade="11",
                attendance_mode="",
                external_student_id="S-100",
                external_source="google_sheets",
            )
            second, second_created = database.upsert_student(
                class_id=class_item["id"],
                class_name=class_item["name"],
                name="Alex Kim",
                school="Brightline School",
                grade="11",
                attendance_mode="",
                external_student_id="S-101",
                external_source="google_sheets",
            )
            self.assertTrue(first_created and second_created)
            self.assertNotEqual(first["id"], second["id"])

    def test_external_id_is_stable_and_readiness_uses_separate_records(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Database(Path(folder) / "brightline.db")
            period = database.ensure_period("2026-08-17", "2026-08-23", "Week 1")
            database.select_period(period["id"])
            class_item = database.upsert_class("Economics", "Jane", "google_sheets")
            student, created = database.upsert_student(
                class_id=class_item["id"],
                class_name=class_item["name"],
                name="Mark",
                school="Brightline School",
                grade="11",
                attendance_mode="",
                external_student_id="S-42",
                external_source="google_sheets",
            )
            self.assertTrue(created)
            renamed, created_again = database.upsert_student(
                class_id=class_item["id"],
                class_name=class_item["name"],
                name="Mark Lee",
                school="Brightline School",
                grade="11",
                attendance_mode="",
                external_student_id="S-42",
                external_source="google_sheets",
            )
            self.assertFalse(created_again)
            self.assertEqual(renamed["id"], student["id"])
            mixed_source, mixed_created = database.upsert_student(
                class_id=class_item["id"],
                class_name=class_item["name"],
                name="Mark Lee",
                school="",
                grade="",
                attendance_mode="",
                external_student_id="S-42",
                external_source="manual",
            )
            self.assertFalse(mixed_created)
            self.assertEqual(mixed_source["id"], student["id"])
            self.assertEqual(mixed_source["school"], "Brightline School")
            self.assertEqual(mixed_source["grade"], "11")
            database.update_homework(
                student["id"],
                [{"label": "Assignments", "value": "8/10"}],
                8,
                2,
                0,
                80,
                period["start_date"],
                period["end_date"],
                period["id"],
                "google_sheets",
            )
            database.select_class_period(class_item["id"], period["id"])
            database.upsert_exam(
                student["id"],
                class_item["id"],
                {
                    "label": "Weekly Test 1",
                    "score": 18,
                    "max_score": 20,
                    "comment": "",
                    "sections": [],
                    "source_name": "Scores",
                },
                period["id"],
                "google_sheets",
            )
            database.upsert_exam(
                student["id"],
                class_item["id"],
                {
                    "label": "Teacher comment",
                    "score": None,
                    "max_score": None,
                    "comment": "Strong analysis.",
                    "sections": [],
                    "source_name": "Comments",
                },
                period["id"],
                "google_sheets",
            )
            self.assertFalse(database.state()["students"][0]["comments_ready"])
            database.upsert_exam(student["id"], class_item["id"], {"label": "Homework feedback", "comment_section": "homework", "comment": "Complete homework."}, period["id"])
            ready = database.state()["students"][0]
            self.assertTrue(ready["assignment_ready"])
            self.assertTrue(ready["weekly_test_ready"])
            self.assertTrue(ready["comments_ready"])
            self.assertTrue(ready["ready"])
            self.assertEqual(ready["latest_exam"]["label"], "Weekly Test 1")
            report_path = Path(folder) / "private-report.pdf"
            report_path.write_bytes(b"%PDF-test")
            database.archive_report(ready, period["id"], report_path)
            public_report = database.state()["reports"][0]
            self.assertNotIn("pdf_path", public_report)
            self.assertNotIn("snapshot_json", public_report)
            self.assertNotIn("google_doc_id", public_report)
            self.assertNotIn("google_template_id", public_report)


class FrontendContractTests(unittest.TestCase):
    def test_connections_expose_only_supported_google_services(self) -> None:
        self.assertEqual(
            set(app_server.app_state()["connections"]),
            {"google_sheets", "google_docs"},
        )
        root = Path(__file__).resolve().parent.parent
        for path in (root / "frontend" / "app.js", root / "backend" / "server.py"):
            self.assertNotIn("makeedu", path.read_text(encoding="utf-8").lower())

    def test_pdf_mapping_exposes_teacher_comment_continuation_pages(self) -> None:
        root = Path(__file__).resolve().parent.parent
        source = (root / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("pdfContinuationRegions", source)
        self.assertIn("data-continuation-region", source)
        self.assertIn('source_key: sourceKey', source)
        self.assertIn("teacher_comment", source)
        self.assertNotIn('"weekly_test_summary", "Weekly test"', source)

    def test_report_review_shows_routing_confidence_and_score_warning(self) -> None:
        root = Path(__file__).resolve().parent.parent
        source = (root / "frontend" / "app.js").read_text(encoding="utf-8")
        self.assertIn("showRoutingReview", source)
        self.assertIn("decision.confidence", source)
        self.assertIn("decision.signals", source)
        self.assertIn("missing_test_score_warning", source)
        self.assertIn("A Weekly Test score is stored", source)

    def test_static_javascript_ids_exist_once_in_html(self) -> None:
        root = (Path(__file__).resolve().parent.parent / "frontend")
        html = (root / "index.html").read_text(encoding="utf-8")
        javascript = (root / "app.js").read_text(encoding="utf-8")
        html_ids = re.findall(r'\bid="([A-Za-z][A-Za-z0-9_-]*)"', html)
        self.assertEqual(len(html_ids), len(set(html_ids)), "HTML IDs must be unique")
        dynamic_ids = set(
            re.findall(r'\bid=\\?"([A-Za-z][A-Za-z0-9_-]*)\\?"', javascript)
        )
        referenced_ids = set(re.findall(r'\$\("#([A-Za-z][A-Za-z0-9_-]*)"\)', javascript))
        self.assertEqual(referenced_ids - set(html_ids) - dynamic_ids, set())

    def test_sidebar_exposes_only_the_four_mvp_sections(self) -> None:
        html = (Path(__file__).resolve().parent.parent / "frontend" / "index.html").read_text(encoding="utf-8")
        navigation = re.findall(r'data-nav="([a-z_]+)"', html)
        self.assertEqual(set(navigation), {"inbox", "templates", "reports", "connections"})

    def test_web_server_allows_only_public_frontend_files(self) -> None:
        self.assertEqual(
            set(app_server.STATIC_FILES.values()), {"index.html", "app.js", "styles.css"}
        )

    def test_local_request_guards_reject_cross_site_and_plain_text_posts(self) -> None:
        self.assertTrue(
            app_server.local_request_context(
                "127.0.0.1:8000", "http://127.0.0.1:8000", "same-origin"
            )
        )
        self.assertFalse(
            app_server.local_request_context(
                "127.0.0.1:8000", "https://malicious.example", "cross-site"
            )
        )
        self.assertFalse(app_server.local_request_context("malicious.example"))
        self.assertTrue(app_server.is_json_content_type("application/json; charset=utf-8"))
        self.assertFalse(app_server.is_json_content_type("text/plain"))

    def test_oauth_return_can_load_static_app_without_opening_cross_site_apis(self) -> None:
        self.assertTrue(
            app_server.local_get_request_context(
                "/", "127.0.0.1:8000", "https://accounts.google.com", "cross-site"
            )
        )
        self.assertFalse(
            app_server.local_get_request_context(
                "/api/state",
                "127.0.0.1:8000",
                "https://malicious.example",
                "cross-site",
            )
        )

    def test_same_name_zip_members_include_a_unique_student_id(self) -> None:
        common = {
            "class_name": "Economics",
            "period_label": "Week 1",
            "name": "Alex Kim",
        }
        first = app_server.report_zip_member_name(
            {**common, "id": "a" * 32, "external_student_id": "S-100"}
        )
        second = app_server.report_zip_member_name(
            {**common, "id": "b" * 32, "external_student_id": "S-101"}
        )
        self.assertNotEqual(first, second)

    def test_zip_members_stay_unique_when_external_ids_sanitize_the_same(self) -> None:
        common = {
            "class_name": "Economics",
            "period_label": "Week 1",
            "name": "Alex Kim",
        }
        first = app_server.report_zip_member_name(
            {**common, "id": "a" * 32, "external_student_id": "S/1"}
        )
        second = app_server.report_zip_member_name(
            {**common, "id": "b" * 32, "external_student_id": "S:1"}
        )
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
