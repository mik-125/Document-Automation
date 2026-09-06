from __future__ import annotations

import atexit
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_SERVER_DATA = tempfile.TemporaryDirectory()
atexit.register(_SERVER_DATA.cleanup)
os.environ.setdefault("BRIGHTLINE_DATA_DIR", _SERVER_DATA.name)

import server as app_server
from database import Database


class WeekUploadWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.original_database = app_server.DB
        app_server.DB = Database(Path(self.folder.name) / "brightline.db")

    def tearDown(self) -> None:
        app_server.DB = self.original_database
        self.folder.cleanup()

    @staticmethod
    def period() -> dict[str, str]:
        return {
            "id": "",
            "label": "Week 1",
            "start_date": "2026-08-17",
            "end_date": "2026-08-23",
        }

    def import_assignment(self) -> str:
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Completed", "Total"],
                ["S-1", "Mark", "Economics", "8", "10"],
                ["S-2", "June", "Economics", "9", "10"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "assignment_completed": "Completed",
                "assignment_total": "Total",
            },
            kind="assignment",
            period=self.period(),
            source_name="Assignment.csv",
            source_provider="manual",
        )
        return app_server.DB.state()["periods"][0]["id"]

    def combined_import(self, scores=("0", "85"), *, confirm=False) -> str:
        raw = [
            ["과목명", "학생", "학년", "학교", "WEEK 1", "", "weekly test\n점수 (%)"],
            ["", "", "", "", "7/13", "7/14", ""],
            ["(2차) 09:00 [26SU] DP1 ESS H+S", "Student One", "10", "J1MS", "A", "F", scores[0]],
            ["(2차) 09:00 [26SU] DP1 ESS H+S", "Student Two", "10", "J1MS", "B", "", scores[1]],
        ]
        values, _, preview = app_server._automatic_sheet_values(raw, "assignment")
        self.assertEqual(preview["row_count"], 2)
        self.assertEqual(preview["suggested_mapping"]["weekly_test_score"], "weekly test\n점수 (%)")
        return app_server.import_mapped_table(
            values=values, mapping=preview["suggested_mapping"],
            kind="assignment", period={**self.period(), "start_date": "2026-07-13", "end_date": "2026-07-17"}, source_name="Combined Sheet",
            source_provider="google_sheets", confirm_replace=confirm,
        )

    def test_combined_sheet_saves_homework_and_test_scores_including_zero(self) -> None:
        summary = self.combined_import()
        state = app_server.DB.state()
        self.assertEqual(len(state["students"]), 2)
        self.assertIn("2 test scores saved", summary)
        for student in state["students"]:
            self.assertTrue(student["assignment_ready"])
            self.assertTrue(student["weekly_test_ready"])
            exam = app_server.DB.get_exams(student["id"], state["periods"][0]["id"])[0]
            self.assertEqual(exam["score"], 0 if student["name"] == "Student One" else 85)
            self.assertEqual(exam["max_score"], 100)
            import json
            saved = app_server.DB.get_student_for_period(student["id"], state["periods"][0]["id"])
            daily = json.loads(saved["homework_json"])
            self.assertEqual([entry["date"] for entry in daily], ["2026-07-13", "2026-07-14"])
            self.assertEqual([entry["value"] for entry in daily], ["A", "F"] if student["name"] == "Student One" else ["B", ""])
        self.assertTrue(app_server.class_period_has_assignment_and_test(
            state["classes"][0]["id"], state["periods"][0]["id"]
        ))

    def test_combined_sheet_blank_scores_preserve_existing_tests(self) -> None:
        self.combined_import()
        summary = self.combined_import(("", ""))
        state = app_server.DB.state()
        self.assertIn("0 test scores saved", summary)
        self.assertTrue(all(student["weekly_test_ready"] for student in state["students"]))
        for student in state["students"]:
            self.assertEqual(len(app_server.DB.get_exams(student["id"], state["periods"][0]["id"])), 1)

    def test_combined_sheet_missing_score_keeps_new_student_unready(self) -> None:
        self.combined_import(("", "85"))
        students = {student["name"]: student for student in app_server.DB.state()["students"]}
        self.assertTrue(students["Student One"]["assignment_ready"])
        self.assertFalse(students["Student One"]["weekly_test_ready"])

    def test_combined_sheet_invalid_score_writes_nothing(self) -> None:
        for value in ("101", "-1", "not a score", "NaN"):
            with self.subTest(value=value):
                with self.assertRaises((ValueError, app_server.IntegrationError)):
                    self.combined_import(("50", value))
                self.assertEqual(app_server.DB.state()["students"], [])
                self.assertEqual(app_server.DB.state()["periods"], [])

    def test_combined_sheet_score_replacement_needs_confirmation(self) -> None:
        self.combined_import()
        with self.assertRaises(app_server.IntegrationError) as raised:
            self.combined_import(("20", "90"))
        self.assertEqual(raised.exception.code, "replacement_confirmation_required")
        self.assertEqual(len(app_server.DB.state()["files"]), 1)
        self.combined_import(("20", "90"), confirm=True)
        state = app_server.DB.state()
        for student in state["students"]:
            exam = app_server.DB.get_exams(student["id"], state["periods"][0]["id"])[0]
            self.assertEqual(exam["score"], 20 if student["name"] == "Student One" else 90)

    def test_combined_sheet_rolls_back_homework_if_test_write_fails(self) -> None:
        with patch.object(app_server.DB, "upsert_exam", side_effect=RuntimeError("test write failed")):
            with self.assertRaisesRegex(RuntimeError, "test write failed"):
                self.combined_import()
        state = app_server.DB.state()
        self.assertEqual(state["students"], [])
        self.assertEqual(state["periods"], [])
        self.assertEqual(state["files"], [])

    def test_comments_are_locked_until_every_roster_student_has_a_test(self) -> None:
        period_id = self.import_assignment()
        period = app_server.DB.get_period(period_id)
        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score"],
                ["S-1", "Mark", "Economics", "91"],
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
        class_id = app_server.DB.state()["classes"][0]["id"]
        self.assertFalse(app_server.class_period_has_assignment_and_test(class_id, period_id))
        status = app_server.period_input_matrix()[0][0]
        self.assertFalse(status["comments_allowed"])

        app_server.import_mapped_table(
            values=[
                ["Student ID", "Student", "Class", "Score"],
                ["S-2", "June", "Economics", "88"],
            ],
            mapping={
                "student_id": "Student ID",
                "student_name": "Student",
                "class_name": "Class",
                "weekly_test_score": "Score",
            },
            kind="weekly_test",
            period=period,
            source_name="Tests-2.csv",
            source_provider="manual",
        )
        self.assertTrue(app_server.class_period_has_assignment_and_test(class_id, period_id))
        self.assertTrue(app_server.period_input_matrix()[0][0]["comments_allowed"])

    def test_all_excluded_assignment_still_defines_week_roster(self) -> None:
        app_server.import_mapped_table(
            values=[
                ["Student", "Class", "8/17", "8/18"],
                ["June", "Economics", "X", "X"],
            ],
            mapping={"student_name": "Student", "class_name": "Class"},
            kind="assignment",
            period=self.period(),
            source_name="Assignment.csv",
            source_provider="manual",
        )
        state = app_server.DB.state()
        self.assertTrue(state["students"][0]["assignment_ready"])
        status = app_server.period_input_matrix()[0][0]
        self.assertEqual(status["student_count"], 1)
        self.assertTrue(status["assignment_ready"])

    def test_comments_csv_must_represent_one_class(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one class"):
            app_server.import_mapped_table(
                values=[
                    ["Student", "Class", "Comment"],
                    ["Mark", "Economics", "Good progress"],
                    ["June", "Mathematics", "Strong work"],
                ],
                mapping={
                    "student_name": "Student",
                    "class_name": "Class",
                    "comment": "Comment",
                },
                kind="comments",
                period=self.period(),
                source_name="Comments.csv",
                source_provider="manual",
            )
        self.assertEqual(app_server.DB.state()["files"], [])


class UploadModalFrontendContractTests(unittest.TestCase):
    """Protect the week picker workflow without coupling tests to browser tooling.

    The project deliberately has no JavaScript test dependency.  These checks keep the
    safety-critical ordering visible in the shipped source; browser QA still covers the
    actual clicks and rendered option labels.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.javascript = (Path(__file__).resolve().parent / "app.js").read_text(
            encoding="utf-8"
        )

    @classmethod
    def section(cls, start: str, end: str) -> str:
        start_index = cls.javascript.index(f"function {start}(")
        end_index = cls.javascript.index(f"function {end}(", start_index)
        return cls.javascript[start_index:end_index]

    def test_staged_source_survives_selecting_another_week(self) -> None:
        open_modal = self.section("openUploadModal", "addUploadWeek")
        stage_manual = self.section("stageManualUpload", "beginGoogleUpload")
        change_handler = self.javascript[
            self.javascript.index('document.addEventListener("change"') :
            self.javascript.index("$$('[data-upload-file-kind]')")
        ]

        # A single session-level stage naturally follows the selected week.  A keyed
        # implementation is also valid when the change handler explicitly carries the
        # current stage to the newly selected key.
        singular_stage = bool(re.search(r"\bstage\s*:\s*null", open_modal)) and (
            "stagesByPeriod" not in open_modal
        )
        carried_keyed_stage = bool(
            re.search(
                r"(?:const|let)\s+(\w+)\s*=\s*currentUploadStage\(\)[\s\S]*"
                r"periodKey\s*=\s*event\.target\.value[\s\S]*"
                r"setUploadStage\(\1",
                change_handler,
            )
        )
        self.assertTrue(
            singular_stage or carried_keyed_stage,
            "Changing Week must keep the one staged source instead of hiding it under the old week.",
        )
        self.assertRegex(
            stage_manual,
            r"(?:session\.stage\s*=|setUploadStage\(|stagesByPeriod\[periodKey\]\s*=)",
            "Manual upload must stage the source before Save.",
        )

    def test_save_is_blocked_and_guarded_without_a_selected_week(self) -> None:
        render = self.section("renderUploadModal", "openUploadModal")
        self.assertRegex(
            render,
            r"saveDisabled\s*=\s*[^;]*!target",
            "The rendered Save button must be disabled until a week is selected.",
        )

        save = self.section("saveUpload", "fieldsForKind")
        first_request = save.index("await api(")
        guard = re.search(
            r"if\s*\(\s*!target\s*\)\s*"
            r"(?P<body>\{[^{}]*\}|return\s+[^;]+;)",
            save,
        )
        self.assertIsNotNone(guard, "saveUpload needs its own missing-week guard.")
        self.assertIn("return", guard.group("body"))
        self.assertLess(
            guard.start(),
            first_request,
            "The missing-week guard must run before any import request.",
        )

    def test_save_confirms_before_the_first_import_request(self) -> None:
        save = self.section("saveUpload", "fieldsForKind")
        first_confirmation = save.find("confirm(")
        first_request = save.find("await api(")
        self.assertGreaterEqual(first_request, 0)
        custom_confirmation = re.search(
            r"if\s*\(\s*!confirmed\s*\)\s*\{[\s\S]*?"
            r"confirming\s*=\s*true[\s\S]*?renderUploadModal\(\)[\s\S]*?return",
            save,
        )
        native_confirmation = 0 <= first_confirmation < first_request
        self.assertTrue(
            native_confirmation or bool(custom_confirmation),
            "The user must confirm the selected source and week before data is imported.",
        )
        if custom_confirmation:
            render = self.section("renderUploadModal", "openUploadModal")
            self.assertIn("data-upload-confirm", render)
            self.assertIn("Source", render)
            self.assertIn("Week", render)

    def test_added_week_options_are_owned_outside_the_modal_session(self) -> None:
        open_modal = self.section("openUploadModal", "addUploadWeek")
        add_week = self.section("addUploadWeek", "stageManualUpload")
        close_modal = self.section("closeModal", "emptyState")
        module_variables = set(
            re.findall(
                r"^(?:let|const)\s+([A-Za-z_$][\w$]*)",
                self.javascript,
                re.MULTILINE,
            )
        )
        shared = {
            name
            for name in module_variables
            if re.search(r"(?:week|draft)", name, re.IGNORECASE)
            and re.search(rf"\b{re.escape(name)}\b", open_modal)
            and re.search(rf"\b{re.escape(name)}\b", add_week)
        }
        self.assertTrue(
            shared,
            "Added week choices must live outside uploadSession so reopening a modal keeps them.",
        )
        for name in shared:
            self.assertNotRegex(
                close_modal,
                rf"\b{re.escape(name)}\s*=",
                "Closing an upload modal must not erase added week choices.",
            )

    def test_google_sheet_source_only_asks_for_the_sheet_url(self) -> None:
        form = self.section("sheetSourceForm", "renderUploadModal")
        self.assertIn("upload-sheet-url", form)
        self.assertNotIn("upload-sheet-tab", form)
        self.assertNotIn("upload-sheet-header", form)
        self.assertNotIn("Worksheet name", form)
        self.assertNotIn("Header row", form)

        preview = self.section("previewUploadSheet", "saveUpload")
        self.assertNotIn("upload-sheet-tab", preview)
        self.assertNotIn("upload-sheet-header", preview)
        self.assertRegex(preview, r"spreadsheet,\s*kind:\s*session\.kind")

    def test_upload_modal_does_not_offer_csv(self) -> None:
        render = self.section("renderUploadModal", "openUploadModal")
        self.assertNotIn("Upload CSV", render)
        self.assertIn("Upload comments PDF", render)

    def test_single_combined_upload_replaces_weekly_test_upload(self) -> None:
        html = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("Homework + test scores", html)
        self.assertNotIn('data-upload-kind="weekly_test"', html)
        self.assertIn('data-upload-kind="assignment"', html)
        self.assertIn('data-upload-kind="comments"', html)
        self.assertIn("Blank scores preserve existing results", self.javascript)

    def test_staged_google_sheet_shows_preview_without_mapping_or_date_fields(self) -> None:
        editor = self.section("uploadStageEditor", "stagedSourceRow")
        google_preview = re.search(
            r'if\s*\(stage\.mode\s*===\s*"google_sheet"\)\s*'
            r"[\s\S]*?return\s+sampleTable\(stage\.preview,\s*\{"
            r"(?P<options>[\s\S]*?)\}\s*\);",
            editor,
        )
        self.assertIsNotNone(google_preview)
        self.assertIn("progressive: true", google_preview.group("options"))
        self.assertLess(google_preview.start(), editor.index("mapping-grid"))
        self.assertLess(
            google_preview.start(), editor.index("Week name", google_preview.end())
        )

        save = self.section("saveUpload", "fieldsForKind")
        google_branch = re.search(
            r'else if\s*\(stage\.mode\s*===\s*"google_sheet"\)\s*\{'
            r"(?P<body>[\s\S]*?)\}\s*else\s*\{",
            save,
        )
        self.assertIsNotNone(google_branch)
        self.assertNotIn("mapping:", google_branch.group("body"))
        self.assertNotIn("header_row", google_branch.group("body"))

    def test_new_week_dates_are_created_automatically(self) -> None:
        dates = self.section("nextWeekDates", "createDraftWeek")
        self.assertIn("Date.UTC", dates)
        self.assertNotIn('start_date: "", end_date: ""', dates)


if __name__ == "__main__":
    unittest.main()
