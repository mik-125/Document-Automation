from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from database import Database
from report_engine import ValidationError, generate_student_pdf, inspect_template
from template_mapping import (
    REQUIRED_REGION_KEYS,
    TemplateMappingError,
    default_mapping,
    validate_mapping,
)


def _blank_template(path: Path, pages: int = 1) -> None:
    document = canvas.Canvas(str(path), pagesize=letter)
    for page_number in range(pages):
        document.setFont("Helvetica-Bold", 14)
        document.drawString(48, 748, f"Company weekly report · page {page_number + 1}")
        document.rect(36, 36, letter[0] - 72, letter[1] - 90)
        document.showPage()
    document.save()


class PdfTemplateMappingTests(unittest.TestCase):
    def test_default_mapping_has_every_required_region_and_validates(self) -> None:
        mapping = validate_mapping(
            default_mapping(letter[0], letter[1]),
            page_width=letter[0],
            page_height=letter[1],
            page_count=1,
        )

        self.assertEqual(
            {region["key"] for region in mapping["regions"]},
            set(REQUIRED_REGION_KEYS),
        )
        self.assertTrue(
            all(
                0 <= region["box"]["x"] < 1
                and 0 <= region["box"]["y"] < 1
                and region["box"]["x"] + region["box"]["width"] <= 1
                and region["box"]["y"] + region["box"]["height"] <= 1
                for region in mapping["regions"]
            )
        )

    def test_mapping_rejects_missing_and_out_of_bounds_regions(self) -> None:
        missing = default_mapping(letter[0], letter[1])
        missing["regions"] = missing["regions"][:-1]
        with self.assertRaisesRegex(TemplateMappingError, "missing required regions"):
            validate_mapping(
                missing,
                page_width=letter[0],
                page_height=letter[1],
                page_count=1,
            )

        outside = default_mapping(letter[0], letter[1])
        outside["regions"][0]["box"]["width"] = 0.9
        with self.assertRaisesRegex(TemplateMappingError, "beyond the page width"):
            validate_mapping(
                outside,
                page_width=letter[0],
                page_height=letter[1],
                page_count=1,
            )

    def test_template_versions_keep_one_published_version_active(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = Database(root / "brightline.sqlite3")
            class_item = database.create_class(name="Chemistry HL")
            source_one = root / "template-v1.pdf"
            source_two = root / "template-v2.pdf"
            _blank_template(source_one)
            _blank_template(source_two)
            mapping = default_mapping(letter[0], letter[1])

            first = database.create_template_version(
                class_id=class_item["id"],
                name="Royal IB",
                source_pdf_path=source_one,
                source_sha256="a" * 64,
                page_width=letter[0],
                page_height=letter[1],
                page_count=1,
                mapping=mapping,
            )
            database.update_template_mapping(first["id"], mapping, publish=True)
            second = database.create_template_version(
                class_id=class_item["id"],
                name="Royal IB revision",
                source_pdf_path=source_two,
                source_sha256="b" * 64,
                page_width=letter[0],
                page_height=letter[1],
                page_count=1,
                mapping=mapping,
            )

            self.assertEqual(
                database.get_class_template_version(class_item["id"])["id"],
                second["id"],
            )
            self.assertEqual(
                database.get_class_template_version(
                    class_item["id"], published_only=True
                )["id"],
                first["id"],
            )
            database.update_template_mapping(second["id"], mapping, publish=True)
            self.assertEqual(
                database.get_class_template_version(
                    class_item["id"], published_only=True
                )["id"],
                second["id"],
            )
            self.assertEqual(database.get_template_version(first["id"])["status"], "superseded")

    def test_generates_a_pdf_on_the_company_template(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "company-template.pdf"
            output_path = root / "student-report.pdf"
            _blank_template(template_path)
            inspection = inspect_template(template_path)
            mapping = validate_mapping(
                default_mapping(
                    inspection["width"], inspection["height"], inspection["pages"]
                ),
                page_width=inspection["width"],
                page_height=inspection["height"],
                page_count=inspection["pages"],
            )
            student = {
                "name": "Minseo Kim",
                "class_name": "DP1 Chemistry HL",
                "school": "SIS",
                "grade": "11",
                "period_label": "Week 3",
                "period_start": "2026-08-24",
                "period_end": "2026-08-30",
                "homework_completed": 4,
                "homework_incomplete": 1,
                "homework_excluded": 0,
                "homework_percent": 80,
                "homework": [
                    {"label": "8/24", "score": 100},
                    {"label": "8/25", "score": 75},
                    {"label": "8/26", "score": 25},
                ],
                "exams": [
                    {
                        "label": "Weekly Test 3",
                        "score": 14,
                        "max_score": 15,
                        "sections": [{"question": "Q1-Q4", "score": 8, "max_score": 8}],
                        "comment": "",
                    },
                    {
                        "label": "Teacher comment",
                        "score": None,
                        "max_score": None,
                        "sections": [],
                        "comment": "Minseo completed the assigned work carefully and showed strong understanding.",
                    },
                ],
            }

            generate_student_pdf(student, template_path, output_path, mapping=mapping)
            output = PdfReader(output_path)

        self.assertEqual(len(output.pages), 1)
        self.assertEqual(output.metadata.title, "Student Report - Minseo Kim")
        page_text = output.pages[0].extract_text()
        self.assertIn("Minseo Kim", page_text)
        self.assertIn("Company weekly report", page_text)
        self.assertIn("Weekly Test 3", page_text)
        self.assertNotIn("Teacher comment: -/-", page_text)

    def test_omits_unused_trailing_pages_in_a_multi_page_template(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "two-page-template.pdf"
            output_path = root / "two-page-report.pdf"
            _blank_template(template_path, pages=2)
            mapping = default_mapping(letter[0], letter[1], page_count=2)
            student = {
                "name": "Minseo Kim",
                "class_name": "DP1 Chemistry HL",
                "homework": [],
                "exams": [{"comment": "Ready for the next reporting period."}],
            }

            generate_student_pdf(student, template_path, output_path, mapping=mapping)
            output = PdfReader(output_path)

        self.assertEqual(len(output.pages), 1)
        self.assertIn("page 1", output.pages[0].extract_text())

    def test_comment_length_selects_one_two_or_three_template_pages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "three-page-template.pdf"
            _blank_template(template_path, pages=3)
            source_bytes = template_path.read_bytes()
            mapping = default_mapping(*letter, page_count=3)
            comment = next(r for r in mapping["regions"] if r["key"] == "teacher_comment")
            for region in [comment, *mapping["continuation_regions"]]:
                region.update(font_size=10, minimum_font_size=10, line_spacing=1.2)
                region["box"] = {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.065}
            for line_count, expected_pages in [(1, 1), (6, 2), (10, 3)]:
                with self.subTest(line_count=line_count):
                    lines = [f"Comment line {index:02d}." for index in range(line_count)]
                    student = {
                        "name": "Minseo Kim", "homework": [], "exams": [],
                        "comments_by_section": {"general": "\n".join(lines)},
                    }
                    output_path = root / f"report-{line_count}.pdf"
                    generate_student_pdf(student, template_path, output_path, mapping=mapping)
                    output = PdfReader(output_path)
                    self.assertEqual(len(output.pages), expected_pages)
                    text = "\n".join(page.extract_text() for page in output.pages)
                    for line in lines:
                        self.assertEqual(text.count(line), 1, line)
                    for page_number, page in enumerate(output.pages, start=1):
                        self.assertIn(f"page {page_number}", page.extract_text())
                    self.assertEqual(template_path.read_bytes(), source_bytes)

    def test_three_page_overflow_never_writes_a_truncated_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "three-page-template.pdf"
            output_path = root / "report.pdf"
            _blank_template(template_path, pages=3)
            mapping = default_mapping(*letter, page_count=3)
            student = {
                "name": "Student", "homework": [], "exams": [],
                "comments_by_section": {
                    "general": "\n".join(f"Unique comment line {i}" for i in range(1000))
                },
            }
            with self.assertRaises(ValidationError):
                generate_student_pdf(student, template_path, output_path, mapping=mapping)
            self.assertFalse(output_path.exists())
            existing_output = b"previous successfully generated report"
            output_path.write_bytes(existing_output)
            with self.assertRaises(ValidationError):
                generate_student_pdf(student, template_path, output_path, mapping=mapping)
            self.assertEqual(output_path.read_bytes(), existing_output)

    def test_required_mapped_content_keeps_later_template_pages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "three-page-template.pdf"
            output_path = root / "report.pdf"
            _blank_template(template_path, pages=3)
            mapping = default_mapping(*letter, page_count=3)
            name_region = next(r for r in mapping["regions"] if r["key"] == "student_name")
            name_region["page"] = 3
            # This layout reserves the last page for identity instead of overflow.
            mapping["continuation_regions"] = [mapping["continuation_regions"][0]]
            student = {"name": "Minseo Kim", "homework": [], "exams": []}
            generate_student_pdf(student, template_path, output_path, mapping=mapping)
            output = PdfReader(output_path)
            self.assertEqual(len(output.pages), 3)
            self.assertIn("Minseo Kim", output.pages[2].extract_text())
            self.assertIn("page 3", output.pages[2].extract_text())

    def test_continuation_mapping_validates_and_rejects_overlap(self) -> None:
        mapping = default_mapping(*letter, page_count=3)
        normalized = validate_mapping(
            mapping, page_width=letter[0], page_height=letter[1], page_count=3,
        )
        self.assertEqual([r["page"] for r in normalized["continuation_regions"]], [2, 3])
        mapping["regions"][0]["page"] = 2
        mapping["regions"][0]["box"] = dict(mapping["continuation_regions"][0]["box"])
        with self.assertRaisesRegex(TemplateMappingError, "overlaps"):
            validate_mapping(
                mapping, page_width=letter[0], page_height=letter[1], page_count=3,
            )

    def test_template_inspection_rejects_more_than_three_pages(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "four-pages.pdf"
            _blank_template(path, pages=4)
            with self.assertRaises(ValidationError):
                inspect_template(path)

    def test_template_inspection_rejects_mixed_page_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "mixed-pages.pdf"
            document = canvas.Canvas(str(path), pagesize=letter)
            document.drawString(40, 700, "First page")
            document.showPage()
            document.setPageSize((500, 700))
            document.drawString(40, 650, "Different size")
            document.showPage()
            document.save()
            with self.assertRaises(ValidationError):
                inspect_template(path)

    def test_generation_refuses_to_clip_an_overflowing_comment(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "company-template.pdf"
            _blank_template(template_path)
            mapping = default_mapping(letter[0], letter[1])
            teacher_comment = next(
                region for region in mapping["regions"] if region["key"] == "teacher_comment"
            )
            teacher_comment["box"]["width"] = 0.05
            teacher_comment["box"]["height"] = 0.01
            output_path = root / "student-report.pdf"
            student = {
                "name": "Student",
                "homework": [],
                "exams": [{"comment": "This comment must never be silently clipped." * 4}],
            }

            with self.assertRaisesRegex(ValidationError, "does not fit"):
                generate_student_pdf(student, template_path, output_path, mapping=mapping)


if __name__ == "__main__":
    unittest.main()
