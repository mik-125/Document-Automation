from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from backend.database import Database
from backend.report_engine import ValidationError, generate_student_pdf, inspect_template
from backend.template_mapping import (
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

    def test_mapping_accepts_teacher_comment_continuation_from_blank_template(self) -> None:
        mapping = default_mapping(letter[0], letter[1])
        mapping["continuation_regions"] = [
            {
                "key": "teacher_page_2",
                "source_key": "teacher_comment",
                "content_type": "text",
                "page": 2,
                "template_page": 1,
                "box": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.82},
                "font_size": 10,
                "minimum_font_size": 8,
                "line_spacing": 1.2,
                "align": "left",
            }
        ]
        normalized = validate_mapping(
            mapping,
            page_width=letter[0],
            page_height=letter[1],
            page_count=1,
        )
        self.assertEqual(normalized["schema_version"], 2)
        self.assertEqual(
            normalized["continuation_regions"][0]["source_key"],
            "teacher_comment",
        )
        self.assertEqual(normalized["continuation_regions"][0]["page"], 2)

    def test_continuation_regions_reject_overlap_and_unknown_sources(self) -> None:
        mapping = default_mapping(letter[0], letter[1])
        base = {
            "content_type": "text",
            "page": 2,
            "box": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.82},
            "font_size": 10,
            "minimum_font_size": 8,
            "line_spacing": 1.2,
            "align": "left",
        }
        mapping["continuation_regions"] = [
            {**base, "key": "teacher_one", "source_key": "teacher_comment"},
            {**base, "key": "teacher_two", "source_key": "teacher_comment"},
        ]
        with self.assertRaisesRegex(TemplateMappingError, "overlap"):
            validate_mapping(
                mapping,
                page_width=letter[0],
                page_height=letter[1],
                page_count=1,
            )
        mapping["continuation_regions"] = [
            {**base, "key": "unknown", "source_key": "student_name"}
        ]
        with self.assertRaisesRegex(TemplateMappingError, "Choose which text field"):
            validate_mapping(
                mapping,
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

    def test_removes_unused_trailing_pages_from_a_multi_page_template(self) -> None:
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

    def test_long_weekly_feedback_is_rejected_instead_of_continued(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            template_path = root / "one-page-template.pdf"
            output_path = root / "student-report.pdf"
            _blank_template(template_path)
            mapping = default_mapping(letter[0], letter[1])
            mapping["continuation_regions"] = [
                {
                    "key": "teacher_page_2",
                    "source_key": "teacher_comment",
                    "content_type": "text",
                    "page": 2,
                    "box": {"x": 0.08, "y": 0.08, "width": 0.84, "height": 0.82},
                    "font_size": 10,
                    "minimum_font_size": 8,
                    "line_spacing": 1.2,
                    "align": "left",
                }
            ]
            tokens = [f"WEEKLY{i:03d}" for i in range(120)]
            student = {
                "name": "Minseo Kim",
                "class_name": "DP1 Chemistry HL",
                "homework": [],
                "exams": [],
                "comments_by_section": {
                    "homework": "",
                    "weekly_test": " ".join(tokens),
                    "general": "",
                },
                "sample_content_guide": {},
            }
            with self.assertRaisesRegex(ValidationError, "Weekly Test Summary does not fit"):
                generate_student_pdf(student, template_path, output_path, mapping=mapping)

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
