import re
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from report_engine import generate_student_pdf
from template_mapping import default_mapping, validate_mapping, TemplateMappingError


class WeeklyReportTests(unittest.TestCase):
    def student(self, comment):
        return {"name": "(ON) 엄세하", "class_name": "DP1 ESS H+S", "school": "J1MS",
                "period_label": "Week 1", "period_start": "2026-07-13", "period_end": "2026-07-17",
                "homework_completed": 1, "homework_incomplete": 1,
                "homework": [{"date": "2026-07-13", "value": "A"}, {"date": "2026-07-14", "value": "F"}],
                "exams": [{"label": "Week 1", "score": 50, "max_score": 100, "comment": comment}]}

    def test_clean_layout_has_correct_identity_and_no_old_template_content(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "report.pdf"
            generate_student_pdf(self.student("과제를 완료했습니다. 주간 테스트에서는 50%를 받았습니다."),
                                 Path("unused-source.pdf"), output, {"layout": "royal_ib_weekly"})
            pages = PdfReader(output).pages
            text = pages[0].extract_text()
            self.assertEqual(len(pages), 1)
            for expected in ["엄세하", "DP1 ESS H+S", "50%", "7/13", "7/14", "과제를 완료했습니다."]:
                self.assertIn(expected, text)
            self.assertNotIn("(ON)", text)
            self.assertNotIn("김로열", text)

    def test_long_comment_continues_without_loss_at_readable_size(self):
        comment = " ".join(f"문장{index:03d} 학습에 대한 구체적인 피드백입니다." for index in range(180))
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "long.pdf"
            generate_student_pdf(self.student(comment), Path("unused.pdf"), output,
                                 {"layout": "royal_ib_weekly", "typography": {"font_size": 11}})
            reader = PdfReader(output)
            self.assertGreater(len(reader.pages), 1)
            content = [page.extract_text() for page in reader.pages]
            # Strip the continuation header before rejoining a word across pages.
            content[1:] = [text.split("종합 코멘트\n", 1)[1] for text in content[1:]]
            text = re.sub(r"\s", "", "".join(content))
            for index in range(180):
                self.assertEqual(text.count(f"문장{index:03d}"), 1)

    def test_exam_continuation_has_no_repeated_masthead_or_lost_text(self):
        comment = "이번 Weekly Exam 에서는 개념을 잘 이해했습니다. " + " ".join(
            f"피드백{index:03d} 계산 과정과 단위도 꼼꼼하게 확인하세요." for index in range(120)
        )
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "continuation.pdf"
            generate_student_pdf(self.student(comment), Path("unused.pdf"), output,
                                 {"layout": "royal_ib_weekly", "typography": {"font_size": 10}})
            pages = PdfReader(output).pages
            self.assertGreater(len(pages), 1)
            content = []
            for index, page in enumerate(pages):
                text = page.extract_text()
                if index:
                    self.assertNotIn("WEEKLY REPORT", text)
                    self.assertNotIn("엄세하", text)
                    self.assertNotIn("DP1 ESS H+S", text)
                content.append(text.split("위클리 테스트\n", 1)[1])
            text = re.sub(r"\s", "", "".join(content))
            self.assertIn(re.sub(r"\s", "", comment), text)
            for index in range(120):
                self.assertEqual(text.count(f"피드백{index:03d}"), 1)

    def test_explicit_categories_render_in_the_selected_sections(self):
        from report_engine import _mapped_values
        from integrations import report_replacements
        student = self.student("")
        student["comments_by_section"] = {
            "homework": "HOMEWORK_MARKER 시험이라는 단어가 있어도 과제 코멘트입니다.",
            "weekly_test": "WEEKLY_MARKER 숙제라는 단어가 있어도 시험 코멘트입니다.",
            "general": "GENERAL_MARKER 과제와 시험 모두 언급하는 종합 의견입니다.",
        }
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "sections.pdf"
            generate_student_pdf(student, Path("unused.pdf"), output, {"layout": "royal_ib_weekly"})
            reader = PdfReader(output)
            self.assertEqual(len(reader.pages), 1)
            text = reader.pages[0].extract_text()
            self.assertLess(text.index("HOMEWORK_MARKER"), text.index("위클리 테스트"))
            self.assertLess(text.index("WEEKLY_MARKER"), text.index("종합 코멘트"))
            self.assertGreater(text.index("GENERAL_MARKER"), text.index("종합 코멘트"))
            positions = []
            reader.pages[0].extract_text(visitor_text=lambda text, cm, tm, font, size:
                                        positions.append((text, cm[4] + tm[4], cm[5] + tm[5])))
            for marker in ("HOMEWORK_MARKER", "WEEKLY_MARKER"):
                self.assertTrue(any(marker in text and x >= 244 for text, x, _ in positions))
        for fields, homework_key, test_key in ((_mapped_values(student), "homework_summary", "weekly_test_summary"),
                                               (report_replacements(student), "assignment_summary", "weekly_test_summary")):
            self.assertIn("HOMEWORK_MARKER", fields[homework_key])
            self.assertIn("WEEKLY_MARKER", fields[test_key])
            self.assertIn("GENERAL_MARKER", fields["teacher_comment"])
            self.assertNotIn("HOMEWORK_MARKER", fields["teacher_comment"])

    def test_general_only_omits_missing_optional_warning_and_keeps_test_graph(self):
        student = self.student("")
        student["comments_by_section"] = {"homework": "과제를 완료했습니다.", "weekly_test": "", "general": "종합 의견입니다."}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "general.pdf"
            generate_student_pdf(student, Path("unused.pdf"), output, {"layout": "royal_ib_weekly"})
            pages = PdfReader(output).pages
            self.assertEqual(len(pages), 1)
            text = pages[0].extract_text()
            self.assertIn("50%", text)
            self.assertIn("종합 코멘트", text)
            self.assertIn("종합 의견입니다.", text)
            self.assertNotIn("테스트 코멘트가 별도로 제공되지 않았습니다", text)

    def test_long_explicit_sections_continue_with_every_marker_once(self):
        student = self.student("")
        student["comments_by_section"] = {key: " ".join(
            f"{key.upper()}{i:03d} 자세한 피드백을 빠짐없이 보존합니다." for i in range(80)
        ) for key in ("homework", "weekly_test", "general")}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "all-long.pdf"
            generate_student_pdf(student, Path("unused.pdf"), output, {"layout": "royal_ib_weekly"})
            pages = PdfReader(output).pages
            text = "".join(page.extract_text() for page in pages)
            for heading in ("과제이행도 (계속)", "위클리 테스트", "종합 코멘트"):
                text = text.replace(heading, "")
            text = re.sub(r"\s+", "", text)
            for key in student["comments_by_section"]:
                for i in range(80):
                    self.assertEqual(text.count(f"{key.upper()}{i:03d}"), 1)

    def test_layout_and_readable_font_choice_survive_validation(self):
        mapping = default_mapping(540, 780)
        mapping.update(layout="royal_ib_weekly", typography={"font_size": 11})
        validated = validate_mapping(mapping, page_width=540, page_height=780, page_count=1)
        self.assertEqual(validated["layout"], "royal_ib_weekly")
        self.assertEqual(validated["typography"]["font_size"], 11)
        mapping["typography"]["font_size"] = 5
        with self.assertRaises(TemplateMappingError):
            validate_mapping(mapping, page_width=540, page_height=780, page_count=1)


if __name__ == "__main__":
    unittest.main()
