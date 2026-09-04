from __future__ import annotations

import atexit
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reportlab.pdfgen import canvas

_SERVER_DATA = tempfile.TemporaryDirectory()
atexit.register(_SERVER_DATA.cleanup)
os.environ.setdefault("BRIGHTLINE_DATA_DIR", _SERVER_DATA.name)

import server as app_server
from database import Database
from report_engine import ValidationError, _inline_comment_records, parse_comment_pdf


def comments_pdf(pages: list[list[str]]) -> bytes:
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    for lines in pages:
        for index, line in enumerate(lines):
            pdf.drawString(60, 760 - index * 24, line)
        pdf.showPage()
    pdf.save()
    return output.getvalue()


class CommentPdfParsingTests(unittest.TestCase):
    def test_names_and_comments_on_same_page(self):
        records = parse_comment_pdf(comments_pdf([
            ["(ON) Mark", "", "First student's comment.", "", "June", "", "Second student's comment."]
        ]), "Week 1", "comments.pdf")
        self.assertEqual([record["name"] for record in records], ["Mark", "June"])
        self.assertEqual(records[0]["comment"], "First student's comment.")
        self.assertEqual(records[1]["comment"], "Second student's comment.")

    def test_continuation_and_heading_at_page_bottom(self):
        records = parse_comment_pdf(comments_pdf([
            ["Mark", "", "His comment starts here."],
            ["His comment continues here.", "", "June"],
            ["Her complete comment is here."],
        ]), "Week 1", "comments.pdf")
        self.assertEqual(records[0]["comment"], "His comment starts here. His comment continues here.")
        self.assertEqual(records[1]["comment"], "Her complete comment is here.")

    def test_korean_headings_ignore_online_prefix_and_keep_mentions(self):
        records = _inline_comment_records([
            "(on) 엄현우\n\n현우는 꾸준히 참여했습니다.\n엄하진 학생과 함께 공부했습니다.\n\n엄하진\n",
            "하진이는 복습이 필요합니다.\n\n엄운비\n\n운비는 잘했습니다.",
        ], "Week 1", "comments.pdf")
        self.assertEqual([record["name"] for record in records], ["엄현우", "엄하진", "엄운비"])
        self.assertIn("엄하진 학생과 함께", records[0]["comment"])
        self.assertEqual(records[1]["comment"], "하진이는 복습이 필요합니다.")

    def test_blank_comment_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "No teacher comment.*Mark"):
            _inline_comment_records(["Mark\n\nJune\n\nHer comment."], "Week 1", "comments.pdf")

    def test_unassigned_leading_text_is_not_silently_dropped(self):
        with self.assertRaisesRegex(ValidationError, "before the first student"):
            _inline_comment_records(["Unassigned text.\n\nMark\n\nHis comment."], "Week 1", "comments.pdf")

    def test_legacy_cover_pages_still_parse_scores(self):
        records = parse_comment_pdf(comments_pdf([
            ["Mark (Class A)"], ["Q1 (Ecology): 8/10", "Good work on the exam."],
        ]), "Week 1", "comments.pdf")
        self.assertEqual(records[0]["name"], "Mark")
        self.assertEqual(records[0]["comment"], "Good work on the exam.")
        self.assertEqual(records[0]["sections"][0]["score"], 8)


class CommentPdfImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.previous_db = app_server.DB
        app_server.DB = Database(Path(self.folder.name) / "test.db")
        app_server.import_mapped_table(
            values=[["Student", "Class", "Completed", "Total", "Score"],
                    ["(ON) Mark", "Class A", "4", "5", "80"],
                    ["June", "Class A", "3", "5", "60"]],
            mapping={"student_name": "Student", "class_name": "Class",
                     "assignment_completed": "Completed", "assignment_total": "Total",
                     "weekly_test_score": "Score"},
            kind="assignment", period={"id": "", "label": "Week 1",
                "start_date": "2026-07-13", "end_date": "2026-07-17"},
            source_name="Combined sheet", source_provider="manual",
        )
        state = app_server.DB.state()
        self.class_id = state["classes"][0]["id"]
        self.period_id = state["periods"][0]["id"]

    def tearDown(self):
        app_server.DB = self.previous_db
        self.folder.cleanup()

    def upload(self, lines):
        class Handler:
            def _send_json(self, payload):
                self.response = payload
        handler = Handler()
        # Parsing and matching are real; only avoid building unrelated UI state.
        with patch.object(app_server, "app_state", return_value={}):
            app_server.AppHandler._import_comments_pdf(handler, {
                "class_id": self.class_id, "period_id": self.period_id,
                "filename": "comments.pdf", "label": "Week 1",
                "pdf_base64": base64.b64encode(comments_pdf([lines])).decode(),
            })
        return handler.response

    def test_real_pdf_matches_ids_keeps_scores_and_reviews_unknown_name(self):
        result = self.upload(["Mark", "", "His first comment.", "", "Unknown", "",
                              "Do not attach this to Mark.", "", "June", "", "Her second comment."])
        self.assertEqual(result["summary"], "2 comments matched · 1 need review")
        for name, text, score in [("Mark", "His first comment.", 80), ("June", "Her second comment.", 60)]:
            student = app_server.DB.find_students_by_name(self.class_id, name)[0]
            payload = app_server.get_ready_student(student["id"], self.period_id)
            from report_engine import _student_comment
            self.assertEqual(_student_comment(payload), text)
            exams = app_server.DB.get_exams(student["id"], self.period_id)
            self.assertIn(score, [exam["score"] for exam in exams])
        issue = app_server.DB.state()["issues"][0]
        self.assertEqual(issue["kind"], "unmatched_comment")
        self.assertEqual(issue["subject_name"], "Unknown")

    def test_duplicate_student_sections_do_not_save(self):
        with self.assertRaisesRegex(ValidationError, "duplicate records"):
            self.upload(["Mark", "", "First comment.", "", "Mark", "", "Second comment."])
        for student in app_server.DB.state()["students"]:
            self.assertFalse(any(exam.get("comment") for exam in
                                 app_server.DB.get_exams(student["id"], self.period_id)))


if __name__ == "__main__":
    unittest.main()
