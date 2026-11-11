"""Importing every WEEK N block of one Sheet in a single step (synthetic data)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_sheet_week_blocks import ROSTER_A, ROSTER_B, ROSTER_C, block, side_by_side
from tests import test_comment_pdf_import as fixtures
from backend.database import Database
from backend.integrations import IntegrationError
from backend.report_engine import ValidationError
from backend.sheet_weeks import split_week_blocks

app = fixtures.app_server


def blocks(*items, **options):
    return split_week_blocks(side_by_side(*items, **options))


def import_blocks(items, **options):
    return app.import_week_blocks(
        blocks=items, year=2026, source_name="Synthetic · Sheet1",
        source_provider="google_sheets", **options)


class FakeWorkspace:
    def __init__(self, values):
        self.values = values

    def read_sheets(self, spreadsheet):
        return {"worksheets": [{"worksheet": "Sheet1", "values": self.values}], "tabs": ["Sheet1"]}

    def read_sheet(self, spreadsheet, worksheet=""):
        return {"values": self.values, "spreadsheet_id": "sheet-id", "spreadsheet_name": "Synthetic",
                "worksheet": worksheet or "Sheet1", "tabs": ["Sheet1"]}


class Handler:
    def _send_json(self, response):
        self.response = response


class MultiWeekSheetImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        previous = app.DB
        self.addCleanup(lambda: setattr(app, "DB", previous))
        app.DB = Database(Path(self.folder.name) / "test.db")
        self.three = blocks(block(1, ROSTER_A), block(2, ROSTER_B), block(4, ROSTER_C))

    def periods(self):
        return {period["week_number"]: period for period in app.DB.state()["periods"]}

    def score(self, name, week):
        student = next(s for s in app.DB.state()["students"] if s["name"] == name)
        exams = app.DB.get_exams(student["id"], self.periods()[week]["id"])
        return next((exam["score"] for exam in exams if exam["score"] is not None), None)

    def test_three_weeks_saved_in_one_call(self):
        summary = import_blocks(self.three)
        periods = self.periods()
        self.assertEqual(sorted(periods), [1, 2, 4])
        self.assertEqual(periods[4]["label"], "Week 4")
        self.assertEqual((periods[1]["start_date"], periods[1]["end_date"]), ("2026-07-13", "2026-07-17"))
        self.assertEqual([self.score("가나다", week) for week in (1, 2, 4)], [80, 90, 70])
        student = next(s for s in app.DB.state()["students"] if s["name"] == "라마바")
        week_1 = app.DB.get_student_for_period(student["id"], periods[1]["id"])
        self.assertEqual((week_1["homework_completed"], week_1["homework_incomplete"]), (4, 1))
        self.assertIn("Week 1: 3 과제이행도 records imported", summary)
        self.assertIn("Week 4: ", summary)
        self.assertEqual(app.DB.selected_period_id(), periods[4]["id"])
        self.assertEqual(len(app.DB.state()["students"]), 3)

    def test_failure_in_last_block_saves_nothing(self):
        broken = [("가나다", "10", "School One", "AAAAA", "140")]
        with self.assertRaisesRegex((ValidationError, IntegrationError), "^Week 4: "):
            import_blocks(blocks(block(1, ROSTER_A), block(4, broken)))
        state = app.DB.state()
        self.assertEqual((state["periods"], state["classes"], state["students"]), ([], [], []))

    def test_identical_reimport_needs_no_confirmation(self):
        import_blocks(self.three)
        import_blocks(self.three)
        self.assertEqual(sorted(self.periods()), [1, 2, 4])

    def test_changed_score_requires_confirmation_and_names_week(self):
        import_blocks(self.three)
        changed = [("가나다", "10", "School One", "AAFAA", "55")] + ROSTER_B[1:]
        again = blocks(block(1, ROSTER_A), block(2, changed), block(4, ROSTER_C))
        with self.assertRaises(IntegrationError) as raised:
            import_blocks(again)
        self.assertEqual(raised.exception.code, "replacement_confirmation_required")
        self.assertIn("Week 2", str(raised.exception))
        self.assertNotIn("Week 1", str(raised.exception))
        self.assertNotIn("Week 4", str(raised.exception))
        self.assertEqual(self.score("가나다", 2), 90)
        import_blocks(again, confirm_replace=True)
        self.assertEqual(self.score("가나다", 2), 55)

    def test_identical_resync_in_a_later_year_keeps_stored_dates(self):
        import_blocks(self.three)
        app.import_week_blocks(blocks=self.three, year=2027, source_name="Synthetic · Sheet1",
                               source_provider="google_sheets")
        periods = self.periods()
        self.assertEqual([periods[n]["start_date"] for n in (1, 2, 4)], ["2026-07-13"] * 3)
        student = next(s for s in app.DB.state()["students"] if s["name"] == "가나다")
        homework = app.DB.get_student_for_period(student["id"], periods[2]["id"])["homework_json"]
        self.assertIn("2026-07-13", homework)
        self.assertNotIn("2027", homework)
        new_week = blocks(block(6, ROSTER_A))
        app.import_week_blocks(blocks=new_week, year=2027, source_name="s", source_provider="google_sheets")
        self.assertEqual(self.periods()[6]["start_date"], "2027-07-13")

    def test_blank_keeps_and_zero_replaces(self):
        import_blocks(self.three)
        blank = [("가나다", "10", "School One", "AAFAA", "")] + ROSTER_B[1:]
        import_blocks(blocks(block(2, blank)))
        self.assertEqual(self.score("가나다", 2), 90)
        zero = [("가나다", "10", "School One", "AAFAA", "0")] + ROSTER_B[1:]
        import_blocks(blocks(block(2, zero)), confirm_replace=True)
        self.assertEqual(self.score("가나다", 2), 0)

    def test_week_2_updates_stored_week_and_keeps_comments(self):
        import_blocks(blocks(block(2, ROSTER_B)))
        week_2 = self.periods()[2]["id"]
        student = next(s for s in app.DB.state()["students"] if s["name"] == "가나다")
        app.DB.upsert_exam(student["id"], student["class_id"], {
            "label": "Teacher comment · weekly_test", "comment": "Kept wording.",
            "comment_section": "weekly_test", "source_name": "exam.pdf"}, week_2)
        import_blocks(self.three)
        comments = [exam["comment"] for exam in app.DB.get_exams(student["id"], week_2) if exam["comment"]]
        self.assertEqual(comments, ["Kept wording."])
        self.assertEqual(self.score("가나다", 2), 90)

    def test_stored_week_with_other_dates_accepts_the_block(self):
        app.DB.ensure_period("2026-07-27", "2026-07-31", "Week 2")
        import_blocks(blocks(block(2, ROSTER_B)))
        week_2 = self.periods()[2]
        self.assertEqual(week_2["start_date"], "2026-07-27")
        self.assertEqual(self.score("가나다", 2), 90)

    def test_blocks_with_different_rosters(self):
        extra = ROSTER_C + [("차카타", "11", "School Two", "AAAAA", "65")]
        import_blocks(blocks(block(1, ROSTER_A), block(2, list(reversed(ROSTER_B))), block(4, extra)))
        self.assertEqual(len(app.DB.state()["students"]), 4)
        self.assertEqual(self.score("가나다", 2), 90)
        self.assertEqual(self.score("사아자", 2), 50)
        self.assertEqual(self.score("차카타", 4), 65)
        self.assertIsNone(self.score("차카타", 1))

    def test_students_absent_from_sheet_untouched(self):
        import_blocks(self.three)
        school_class = app.DB.state()["classes"][0]
        app.DB.create_student(class_id=school_class["id"], name="파하", school="School One", grade="10")
        import_blocks(self.three)
        self.assertIn("파하", [student["name"] for student in app.DB.state()["students"]])

    def test_one_import_entry_per_week(self):
        import_blocks(self.three)
        with app.DB.connect() as db:
            rows = db.execute("SELECT period_id FROM imports WHERE kind = 'assignment_sheet'").fetchall()
        self.assertEqual({row["period_id"] for row in rows}, {p["id"] for p in self.periods().values()})
        self.assertEqual(len(rows), 3)

    def test_preview_lists_weeks(self):
        import_blocks(blocks(block(2, ROSTER_B)))
        previews = app.week_block_previews(
            blocks(block(1, ROSTER_A), block(2, ROSTER_B), block(4, ROSTER_C, scores=False)))
        self.assertEqual(previews[0], {"week_number": 1, "label": "Week 1", "exists": False,
                                       "student_count": 3, "homework_columns": 5, "has_scores": True})
        self.assertTrue(previews[1]["exists"])
        self.assertFalse(previews[2]["has_scores"])

    def invoke(self, method, values, **payload):
        handler = Handler()
        with patch.object(app.GoogleWorkspace, "from_env", return_value=FakeWorkspace(values)), \
                patch.object(app, "mark_google_service_verified"), \
                patch.object(app, "app_state", return_value={}):
            getattr(app.AppHandler, method)(handler, {"kind": "assignment", "spreadsheet": "sheet-id", **payload})
        return handler.response

    def test_preview_handler_returns_weeks_for_block_sheet(self):
        values = side_by_side(block(1, ROSTER_A), block(2, ROSTER_B), block(4, ROSTER_C))
        response = self.invoke("_google_sheet_preview", values)
        self.assertEqual([week["week_number"] for week in response["weeks"]], [1, 2, 4])
        self.assertEqual(response["worksheet"], "Sheet1")
        self.assertNotIn("assignment_period", response)
        self.assertEqual(response["row_count"], 3)

    def test_sync_handler_imports_every_week_and_ignores_selected_week(self):
        stored = app.DB.ensure_period("2026-09-07", "2026-09-11", "Week 9")
        values = side_by_side(block(1, ROSTER_A), block(2, ROSTER_B), block(4, ROSTER_C))
        response = self.invoke("_google_sheet_sync", values, period_id=stored["id"])
        self.assertTrue(response["ok"])
        self.assertEqual(sorted(self.periods()), [1, 2, 4, 9])
        week_9 = self.periods()[9]["id"]
        self.assertTrue(all(not app.DB.get_exams(s["id"], week_9) for s in app.DB.state()["students"]))

    def test_single_week_sheet_without_heading_uses_selected_week(self):
        values = [["Class", "Student", "7/13", "7/14", "Weekly Test 점수"],
                  ["Class A", "가나다", "A", "F", "80"]]
        response = self.invoke("_google_sheet_sync", values, period_label="Week 3",
                               period_start="2026-07-13", period_end="2026-07-17")
        self.assertTrue(response["ok"])
        self.assertEqual(sorted(self.periods()), [3])
        self.assertNotIn("weeks", self.invoke("_google_sheet_preview", values, period_start="2026-07-13"))


if __name__ == "__main__":
    unittest.main()
