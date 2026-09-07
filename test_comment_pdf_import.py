from __future__ import annotations

import atexit
import base64
import io
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from reportlab.pdfgen import canvas

_SERVER_DATA = tempfile.TemporaryDirectory()
atexit.register(_SERVER_DATA.cleanup)
os.environ.setdefault("BRIGHTLINE_DATA_DIR", _SERVER_DATA.name)

import server as app_server
from database import Database
from report_engine import ValidationError, _inline_comment_records, parse_comment_pdf, register_fonts


def comments_pdf(pages: list[list[str]]) -> bytes:
    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    for lines in pages:
        pdf.setFont(register_fonts(), 12)
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

    def test_korean_narrative_after_scores_keeps_the_complete_comment(self):
        paragraphs = ["엄현우 학생은 개념을 정확하게 이해했습니다.",
                      "Atomic Structure 내용을 복습하고 답안의 이유를 설명해 주세요.",
                      "엄하진 학생과 함께 연습했습니다. 앞으로도 꾸준히 노력하길 바랍니다."]
        records = parse_comment_pdf(comments_pdf([[
            "총점: 16 / 19", "", "Q1 (Separation Techniques): 5 / 6 (전체 평균: 5.1 / 6)",
            "", "Q2 (Atomic Structure): 7 / 7 (전체 평균: 6.0 / 7)",
            "", "Q3 (Electronic Structure): 4 / 6 (전체 평균: 4.4 / 6)",
            "", paragraphs[0], "", paragraphs[1], "", paragraphs[2],
        ]]), "Week 1", "comments.pdf")
        self.assertEqual(records, [{
            "name": "엄현우", "label": "Week 1", "score": None, "max_score": None,
            "sections": [], "comment": " ".join(paragraphs), "source_name": "comments.pdf",
        }])

    def test_narrative_continues_across_pages_and_retains_same_student_mentions(self):
        records = parse_comment_pdf(comments_pdf([
            ["엄현우 학생은 계산을 잘했습니다."],
            ["다음 단계에서는 단위도 확인해 주세요.", "", "엄현우 학생이 계속 성장하기를 바랍니다."],
        ]), "Week 1", "comments.pdf")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["comment"],
                         "엄현우 학생은 계산을 잘했습니다. 다음 단계에서는 단위도 확인해 주세요. "
                         "엄현우 학생이 계속 성장하기를 바랍니다.")

    def test_narrative_subheading_is_retained_instead_of_treated_as_a_student(self):
        for scores in [[], ["총점: 8/10", ""]]:
            with self.subTest(scores=scores):
                records = parse_comment_pdf(comments_pdf([scores + [
                    "엄현우 학생은 개념을 잘 이해했습니다.", "", "Atomic Structure", "",
                    "원자 구조를 복습해 주세요.",
                ]]), "Week 1", "comments.pdf")
                self.assertEqual([record["name"] for record in records], ["엄현우"])
                self.assertEqual(records[0]["comment"],
                                 "엄현우 학생은 개념을 잘 이해했습니다. Atomic Structure 원자 구조를 복습해 주세요.")

    def test_narrative_rejects_unassigned_preamble_instead_of_dropping_it(self):
        for preamble in ["Unassigned teacher guidance that must not disappear.",
                         "Q1 (Ecology): 8/10 Important feedback."]:
            with self.subTest(preamble=preamble), self.assertRaisesRegex(ValidationError, "Unassigned text"):
                parse_comment_pdf(comments_pdf([
                    [preamble, "", "엄현우 학생은 개념을 이해했습니다."],
                ]), "Week 1", "comments.pdf")

    def test_different_student_introductions_are_rejected_without_guessing_boundaries(self):
        for pages in [
            [["엄현우 학생은 잘했습니다.", "", "엄하진 학생은 복습이 필요합니다."]],
            [["엄현우 학생은 잘했습니다."], ["엄하진 학생이 잘했습니다."]],
        ]:
            with self.subTest(pages=pages), self.assertRaisesRegex(ValidationError, "more than one student"):
                parse_comment_pdf(comments_pdf(pages), "Week 1", "comments.pdf")

    def test_legacy_korean_cover_and_narrative_keep_existing_score_parsing(self):
        records = parse_comment_pdf(comments_pdf([
            ["엄현우 (Class A)"], ["총점: 8/10", "엄현우 학생은 개념을 이해했습니다."],
        ]), "Week 1", "comments.pdf")
        self.assertEqual(records[0]["name"], "엄현우")
        self.assertEqual(records[0]["score"], 8)
        self.assertEqual(records[0]["comment"], "엄현우 학생은 개념을 이해했습니다.")

    def test_explicit_heading_or_cover_must_agree_with_opening_narrative(self):
        for pages in [
            [["엄현우", "", "엄하진 학생은 개념을 이해했습니다."]],
            [["엄현우", "", "총점: 8/10", "Q1 (Ecology): 8/10", "엄하진 학생은 개념을 이해했습니다."]],
            [["엄현우 (Class A)"], ["총점: 8/10", "엄하진 학생은 개념을 이해했습니다."]],
        ]:
            with self.subTest(pages=pages), self.assertRaisesRegex(ValidationError, "heading names.*엄현우.*엄하진"):
                parse_comment_pdf(comments_pdf(pages), "Week 1", "comments.pdf")

    def test_heading_agrees_with_opening_narrative_without_rejecting_later_mentions(self):
        records = parse_comment_pdf(comments_pdf([[
            "엄현우", "", "엄현우 학생은 개념을 이해했습니다.",
            "엄하진 학생은 함께 연습했습니다.",
        ]]), "Week 1", "comments.pdf")
        self.assertEqual(records[0]["name"], "엄현우")
        self.assertIn("엄하진 학생은 함께", records[0]["comment"])


class CommentPdfImportTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.previous_db = app_server.DB
        app_server.DB = Database(Path(self.folder.name) / "test.db")
        app_server.import_mapped_table(
            values=[["ID", "Student", "Class", "Completed", "Total", "Score"],
                    ["S1", "(ON) Mark", "Class A", "4", "5", "80"],
                    ["S2", "June", "Class A", "3", "5", "60"],
                    ["S3", "엄현우", "Class A", "4", "5", "75"],
                    ["S4", "김민준", "Class A", "4", "5", "70"],
                    ["S5", "김민준", "Class A", "3", "5", "65"]],
            mapping={"student_id": "ID", "student_name": "Student", "class_name": "Class",
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
        self.assertEqual(result["summary"], "2 comments matched · 1 need review · 3 students missing comments")
        self.assertEqual(result["matching"]["unmatched_comments"], [{
            "name": "Unknown", "reason": "No matching student in this class and week",
        }])
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

    def test_narrative_import_matches_only_its_student_and_preserves_sheet_scores(self):
        comment = "엄현우 학생은 개념을 이해했습니다. 다음 주에도 복습해 주세요."
        before = {student["id"]: app_server.DB.get_exams(student["id"], self.period_id)
                  for student in app_server.DB.state()["students"]}
        result = self.upload(["총점: 16/19", "Q1 (Ecology): 5/6", "", comment])
        self.assertEqual(result["summary"], "1 comments matched · 0 need review · 4 students missing comments")
        for student in app_server.DB.state()["students"]:
            exams = app_server.DB.get_exams(student["id"], self.period_id)
            if student["name"] == "엄현우":
                imported = [exam for exam in exams if exam.get("comment")]
                self.assertEqual(len(imported), 1)
                self.assertEqual(imported[0]["comment"], comment)
                self.assertIsNone(imported[0]["score"])
                self.assertIsNone(imported[0]["max_score"])
                self.assertEqual([exam for exam in exams if not exam.get("comment")], before[student["id"]])
            else:
                self.assertEqual(exams, before[student["id"]])

    def test_unknown_and_ambiguous_narrative_names_are_queued_without_assignment(self):
        for name, kind, candidates in [("김지우", "unmatched_comment", 0), ("김민준", "ambiguous_comment", 2)]:
            with self.subTest(name=name):
                result = self.upload([f"{name} 학생은 복습이 필요합니다."])
                self.assertEqual(result["summary"], "0 comments matched · 1 need review · 5 students missing comments")
                self.assertEqual(result["matching"]["unmatched_comments"], [{
                    "name": name,
                    "reason": "Multiple students have this name" if candidates
                    else "No matching student in this class and week",
                }])
                issue = next(issue for issue in app_server.DB.state()["issues"] if issue["subject_name"] == name)
                self.assertEqual(issue["kind"], kind)
                self.assertEqual(len(issue["candidates"]), candidates)
        for student in app_server.DB.state()["students"]:
            self.assertFalse(any(exam.get("comment") for exam in
                                 app_server.DB.get_exams(student["id"], self.period_id)))
        self.assertEqual(len(app_server.DB.state()["students"]), 5)

    def test_missing_comments_are_scoped_to_the_selected_class_and_week(self):
        roster = app_server.DB.state()["students"]
        for name, class_name, period in [
            ("Outside", "Class B", app_server.DB.get_period(self.period_id)),
            ("Later", "Class A", {"id": "", "label": "Week 2",
                                   "start_date": "2026-07-20", "end_date": "2026-07-24"}),
        ]:
            app_server.import_mapped_table(
                values=[["Student", "Class", "Completed", "Total", "Score"],
                        [name, class_name, "4", "5", "80"]],
                mapping={"student_name": "Student", "class_name": "Class",
                         "assignment_completed": "Completed", "assignment_total": "Total",
                         "weekly_test_score": "Score"},
                kind="assignment", period=period,
                source_name="Other roster", source_provider="manual",
            )
        other_period = next(period for period in app_server.DB.state()["periods"]
                            if period["id"] != self.period_id)
        mark = app_server.DB.find_students_by_name(self.class_id, "Mark")[0]
        app_server.DB.upsert_exam(mark["id"], self.class_id,
                                 {"label": "Teacher comment", "comment": "A different week's comment."},
                                 other_period["id"])
        june = app_server.DB.find_students_by_name(self.class_id, "June")[0]
        app_server.DB.upsert_exam(june["id"], self.class_id,
                                 {"label": "Teacher comment", "comment": " \t\n\r "}, self.period_id)

        result = self.upload(["Unknown", "", "This comment has no roster match."])
        matching = result["matching"]
        self.assertEqual(matching["class_name"], "Class A")
        self.assertEqual(matching["period_label"], "Week 1")
        self.assertEqual({item["id"] for item in matching["missing_comments"]},
                         {student["id"] for student in roster})
        self.assertEqual(matching["missing_comments"], sorted([
            {key: student[key] for key in ("id", "name", "school", "grade")}
            for student in roster
        ], key=lambda student: (student["name"], student["school"], student["grade"], student["id"])))

    def test_incremental_pdfs_preserve_saved_comments_and_clear_missing_names(self):
        first = self.upload(["Mark", "", "Mark's saved comment."])
        self.assertEqual(len(first["matching"]["missing_comments"]), 4)
        second = self.upload(["June", "", "June's saved comment."])
        missing = second["matching"]["missing_comments"]
        self.assertEqual(len(missing), 3)
        self.assertNotIn("June", [student["name"] for student in missing])
        self.assertNotIn("(ON) Mark", [student["name"] for student in missing])
        # Previously confirmed comments count, even when saved with a different label.
        for student in missing:
            app_server.DB.upsert_exam(student["id"], self.class_id,
                                     {"label": "Teacher comment · Earlier PDF", "comment": "Confirmed feedback."},
                                     self.period_id)
        complete = self.upload(["Mark", "", "Mark's saved comment."])
        self.assertEqual(complete["summary"], "1 comments matched · 0 need review")
        self.assertEqual(complete["matching"]["missing_comments"], [])
        self.assertEqual(complete["matching"]["unmatched_comments"], [])

    def test_missing_comments_block_direct_generation_before_any_renderer(self):
        self.upload(["Unknown", "", "Do not generate a report for this name."])
        with patch.object(app_server, "generate_student_pdf") as render_pdf, \
                patch.object(app_server.GoogleWorkspace, "from_env") as google:
            for student in app_server.DB.state()["students"]:
                for options in ({"archive": False, "preview": True}, {"archive": True}):
                    with self.subTest(student=student["id"], options=options):
                        with self.assertRaisesRegex(ValidationError, "teacher comment"):
                            app_server.generate_report(student["id"], self.period_id, **options)
            render_pdf.assert_not_called()
            google.assert_not_called()
        self.assertEqual(app_server.DB.state()["reports"], [])

    def test_zip_uses_real_readiness_and_generates_only_students_with_matched_comments(self):
        self.upload(["Mark", "", "Mark's saved comment.", "", "Unknown", "",
                     "No report for this name.", "", "김민준", "", "An uncertain comment."])
        mark = app_server.DB.find_students_by_name(self.class_id, "Mark")[0]
        output = Path(self.folder.name)
        report = output / "mark.pdf"
        report.write_bytes(comments_pdf([["Generated report"]]))
        google_state = {
            "google_docs": {"status": "disconnected", "account_name": ""},
            "google_sheets": {"status": "disconnected", "account_name": ""},
        }
        with patch.object(app_server, "google_connection_state", return_value=google_state), \
                patch.object(app_server.DB, "get_class_template_version", return_value={"ready": True}), \
                patch.object(app_server, "generate_report", return_value=report) as generate, \
                patch.object(app_server, "EXPORT_DIR", output / "exports"):
            # Class/week roster and student readiness are computed from the real database.
            state = app_server.app_state()
            self.assertEqual([student["id"] for student in state["students"] if student["report_ready"]],
                             [mark["id"]])
            archive, manifest, _ = app_server.generate_zip(self.class_id)
        generate.assert_called_once_with(mark["id"], self.period_id)
        self.assertEqual([item["student_id"] for item in manifest["generated"]], [mark["id"]])
        self.assertEqual(len(manifest["skipped"]), 4)
        self.assertTrue(all("teacher comment" in item["reason"] for item in manifest["skipped"]))
        self.assertEqual(manifest["failed"], [])
        with zipfile.ZipFile(archive) as bundle:
            self.assertEqual(len([name for name in bundle.namelist() if name.endswith(".pdf")]), 1)


if __name__ == "__main__":
    unittest.main()
