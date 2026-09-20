from __future__ import annotations

import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from report_engine import generate_student_pdf, inspect_example_report, parse_comment_pdf, ValidationError
from sample_content import merge_content_guides, organize_comments, page_content_guide
from template_mapping import default_mapping, validate_mapping


def make_sample(path):
    c = canvas.Canvas(str(path), pagesize=(600, 800))
    # Deliberately different from the original: general feedback appears first.
    c.setFillColorRGB(.8, .05, .05)
    for title, body, y in [
        ("General comments", "SAMPLE_ONLY_PERSON: Excellent participation and effort.", 720),
        ("Weekly Test", "SAMPLE_ONLY_SCORE: Improve calculation and reasoning.", 500),
        ("Homework", "SAMPLE_ONLY_FACT: All assignments submitted; practice concepts.", 280),
    ]:
        c.setFont("Courier-Bold", 16)
        c.drawString(40, y, title)
        c.setFont("Courier", 10)
        c.drawString(40, y - 30, body)
    c.save()


def original_mapping():
    mapping = default_mapping(600, 800, 2)
    regions = {r["key"]: r for r in mapping["regions"]}
    regions["reporting_week"]["box"] = {"x": .1, "y": .83, "width": .3, "height": .04}
    for key, y in [("homework_summary", .65), ("weekly_test_summary", .38), ("teacher_comment", .10)]:
        regions[key]["box"] = {"x": .1, "y": y, "width": .8, "height": .15}
        regions[key]["font_name"] = "Helvetica"
        regions[key]["font_size"] = 10
    regions["weekly_test_chart"]["page"] = 2
    regions["homework_chart"]["page"] = 2
    return validate_mapping(mapping, page_width=600, page_height=800, page_count=2)


class SampleContentTests(unittest.TestCase):
    def test_sample_guides_content_without_retaining_student_prose(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.pdf"
            make_sample(path)
            analysis = inspect_example_report(path)
        guide = merge_content_guides([{"id": "class-a-sample", "analysis": analysis}])
        sections = {s["section"]: s for s in guide["sections"]}
        self.assertEqual(set(sections), {"homework", "weekly_test", "general"})
        self.assertIn("participation and effort", sections["general"]["topics"])
        self.assertIn("mistakes and reasoning", sections["weekly_test"]["topics"])
        self.assertIn("completion and consistency", sections["homework"]["topics"])
        self.assertNotIn("SAMPLE_ONLY_PERSON", str(analysis))
        self.assertEqual(guide["design_authority"], "original_template")
        self.assertFalse(guide["sample_text_is_output"])

    def test_comment_pdf_headings_survive_import_and_route_to_sample_sections(self):
        cbytes = io.BytesIO()
        c = canvas.Canvas(cbytes, pagesize=(600, 800))
        lines = ["Alice Smith", "", "Homework", "Keep ALL_ORIGINAL_HOMEWORK words.", "",
                 "Weekly Test", "Retain ALL_ORIGINAL_TEST exactly.", "",
                 "General comments", "Retain ALL_ORIGINAL_GENERAL exactly."]
        c.setFont("Helvetica", 11)
        for i, line in enumerate(lines):
            c.drawString(40, 750 - i * 20, line)
        c.save()
        records = parse_comment_pdf(cbytes.getvalue(), "Comments", "synthetic.pdf")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["name"], "Alice Smith")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.pdf"
            make_sample(path)
            guide = merge_content_guides([{"analysis": inspect_example_report(path)}])
        organized = organize_comments(records, guide)
        for section, marker in [("homework", "ALL_ORIGINAL_HOMEWORK"), ("weekly_test", "ALL_ORIGINAL_TEST"), ("general", "ALL_ORIGINAL_GENERAL")]:
            self.assertIn(marker, organized["sections"][section])
            self.assertEqual("".join(organized["sections"].values()).count(marker), 1)

    def test_unheaded_narrative_retains_explicit_category_and_wording(self):
        text = "The weekly exam included a question about homework.\nKeep its context and 7/10 score."
        guide = {"sections": [{"section": s} for s in ("homework", "weekly_test", "general")]}
        for section in ("homework", "weekly_test", "general"):
            result = organize_comments([{"comment": text, "comment_section": section}], guide)
            self.assertEqual(result["sections"][section], text)
            self.assertEqual(sum(bool(v) for v in result["sections"].values()), 1)

    def test_classes_do_not_share_sample_headings_or_reuse_sample_words(self):
        exam = {"comment": "General comments:\nKeep this source text.", "comment_section": "weekly_test"}
        class_a = {"sections": [{"section": "general"}]}
        class_b = {"sections": [{"section": "homework"}]}
        self.assertEqual(organize_comments([exam], class_a)["sections"]["general"], exam["comment"])
        self.assertEqual(organize_comments([exam], class_b)["sections"]["weekly_test"], exam["comment"])

    def test_ambiguous_or_rotated_samples_request_review(self):
        guide = page_content_guide([{"text": "Unrecognized block", "x": 20, "y": 600}], 600, 800, 1, [])
        self.assertFalse(guide["sections"])
        self.assertTrue(guide["warnings"])

    def test_fragmented_heading_is_rejoined_without_using_narrative_as_heading(self):
        runs = [{"text": text, "x": x, "y": y, "size": 10} for text, x, y in [
            ("General", 40, 700), ("comments", 82, 700),
            ("Discuss the", 40, 660), ("Weekly Test", 100, 660), ("during revision.", 160, 660),
        ]]
        guide = page_content_guide(runs, 600, 800, 1, [])
        self.assertEqual([s["section"] for s in guide["sections"]], ["general"])
        self.assertGreater(guide["sections"][0]["text_characters"], 0)

    def test_repeated_uncertain_headings_do_not_reroute_teacher_text(self):
        runs = [{"text": "Weekly Test", "x": 40, "y": y} for y in (700, 500)]
        guide = page_content_guide(runs, 600, 800, 1, [])
        self.assertTrue(guide["warnings"])
        comment = "Weekly Test\nPreserve this homework narrative."
        result = organize_comments([{"comment": comment, "comment_section": "homework"}], guide)
        self.assertEqual(result["sections"]["homework"], comment)

    def test_server_prepares_class_specific_sections_for_actual_report_generation(self):
        import server
        from contextlib import nullcontext
        student = {"id": "student", "class_id": "class", "period_id": "week",
                   "period_end": "2026-09-20", "homework_json": "[]", "homework_completed": 1}
        database = Mock()
        database.get_student_for_period.return_value = student
        database.get_exams.return_value = [
            {"score": 8, "max_score": 10, "comment_section": "weekly_test",
             "comment": "Homework\nDo the assigned practice.\nWeekly Test\nCheck units."},
        ]
        database.connect.return_value = nullcontext(Mock(execute=Mock(return_value=[])))
        example = {"analysis": {"content_guide": {"sections": [{"section": s}
                                                                  for s in ("homework", "weekly_test")]}}}
        with patch.object(server, "DB", database), patch.object(server, "class_example_manifest", return_value=[example]) as manifest:
            payload = server.get_ready_student("student", "week")
        manifest.assert_called_once_with("class")
        self.assertIn("Do the assigned practice.", payload["comments_by_section"]["homework"])
        self.assertIn("Check units.", payload["comments_by_section"]["weekly_test"])

    def test_missing_test_feedback_policy_allows_pdf_without_score_but_requires_homework(self):
        import server
        from contextlib import nullcontext
        database=Mock()
        database.get_student_for_period.return_value={
            'id':'student','class_id':'class','period_id':'week','period_end':'2026-09-20',
            'homework_json':'[]','homework_completed':1}
        database.get_exams.return_value=[{'comment_section':'homework','comment':'Practice was assigned.'}]
        database.get_class_template_version.return_value={'ready':True}
        database.connect.return_value=nullcontext(Mock(execute=Mock(return_value=[])))
        sample={'analysis':{'content_guide':{'sections':[{'section':'weekly_test','reliable':True}]}}}
        with patch.object(server,'DB',database),patch.object(server,'class_example_manifest',return_value=[sample]):
            payload=server.get_ready_student('student','week')
            self.assertTrue(payload['missing_test_feedback_assumption'])
            self.assertEqual(payload['comments_by_section']['weekly_test'],'')
            database.get_exams.return_value=[]
            with self.assertRaisesRegex(ValidationError,'homework comment'):
                server.get_ready_student('student','week')
            database.get_exams.return_value=[{'comment_section':'homework','comment':'Practice was assigned.'}]
            database.get_class_template_version.return_value=None
            with self.assertRaisesRegex(ValidationError,'Weekly Test score'):
                server.get_ready_student('student','week')

    def test_different_sample_design_cannot_change_original_pdf_or_mapped_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample = root / "sample.pdf"
            make_sample(sample)
            guide = merge_content_guides([{"analysis": inspect_example_report(sample)}])
            template = root / "original.pdf"
            c = canvas.Canvas(str(template), pagesize=(600, 800))
            c.setStrokeColorRGB(0, .15, .4)
            c.setLineWidth(5)
            c.rect(15, 15, 570, 770)
            c.setFont("Helvetica-Bold", 16)
            c.drawString(40, 760, "ORIGINAL DESIGN")
            c.showPage()
            c.setPageSize((500, 700))
            c.drawString(40, 660, "ORIGINAL SECOND PAGE")
            c.save()
            original_bytes = template.read_bytes()
            student = {"name": "New Student", "class_name": "New Class", "period_label": "Week 2",
                       "homework": [], "homework_percent": 100, "homework_completed": 1,
                       "exams": [{"score": 8, "max_score": 10}],
                       "comments_by_section": organize_comments([{
                           "comment": "Homework\nHOMEWORK_CURRENT\nWeekly Test\nTEST_CURRENT\nGeneral comments\nGENERAL_CURRENT"
                       }], guide)["sections"]}
            mapping = original_mapping()
            mapping_before = copy.deepcopy(mapping)
            result = root / "generated.pdf"
            generate_student_pdf(student, template, result, mapping)
            reader = PdfReader(result)
            self.assertEqual(len(reader.pages), 2)
            self.assertEqual(float(reader.pages[1].mediabox.width), 500)
            self.assertEqual(float(reader.pages[1].mediabox.height), 700)
            text = "\n".join(page.extract_text() for page in reader.pages)
            self.assertIn("ORIGINAL DESIGN", text)
            self.assertIn("ORIGINAL SECOND PAGE", text)
            self.assertNotIn("SAMPLE_ONLY", text)
            for marker in ("HOMEWORK_CURRENT", "TEST_CURRENT", "GENERAL_CURRENT"):
                self.assertEqual(text.count(marker), 1)
            positions = {}
            def visit(text, cm, tm, font, size):
                for marker in ("HOMEWORK_CURRENT", "TEST_CURRENT", "GENERAL_CURRENT"):
                    if marker in text:
                        positions[marker] = tm[5] + cm[5]
            reader.pages[0].extract_text(visitor_text=visit)
            self.assertGreater(positions["HOMEWORK_CURRENT"], positions["TEST_CURRENT"])
            self.assertGreater(positions["TEST_CURRENT"], positions["GENERAL_CURRENT"])
            self.assertEqual(template.read_bytes(), original_bytes)
            self.assertEqual(mapping, mapping_before)

    def test_server_blocks_legacy_design_override_and_unpublished_pdf_fallback(self):
        import server
        student = {"id": "s", "class_id": "c", "period_label": "Week 1"}
        for template in ({"ready": True, "mapping": {"layout": "royal_ib_weekly"}}, None):
            database = Mock()
            database.get_class_template_version.side_effect = [template, {"id": "original"}]
            with patch.object(server, "DB", database), patch.object(server, "get_ready_student", return_value=student), patch.object(server, "generate_student_pdf") as generate:
                with self.assertRaisesRegex(ValidationError, "original PDF"):
                    server.generate_report("s", archive=False)
                generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
