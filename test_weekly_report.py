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
            text = re.sub(r"\s", "", "".join(page.extract_text() for page in reader.pages))
            for index in range(180):
                self.assertEqual(text.count(f"문장{index:03d}"), 1)

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
