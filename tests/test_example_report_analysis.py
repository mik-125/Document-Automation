from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from backend.report_engine import ValidationError, inspect_example_report

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is an indirect ReportLab dependency.
    Image = None


class ExampleReportAnalysisTests(unittest.TestCase):
    def test_reports_structure_fonts_labels_and_continuation_clues(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "two-page-example.pdf"
            document = canvas.Canvas(str(path), pagesize=letter)
            document.setFont("Helvetica-Bold", 12)
            document.drawString(72, 720, "Student Name  Class  Week 1")
            document.setFont("Helvetica", 10)
            document.drawString(
                72, 680, "Assignment  Weekly Test  Teacher Comment: Alice Confidential"
            )
            document.showPage()
            document.setPageSize(landscape(letter))
            document.setFont("Helvetica", 9)
            document.drawString(72, 500, "Teacher Comment continued")
            document.save()

            result = inspect_example_report(path)

        self.assertEqual(result["analysis_version"], 1)
        self.assertEqual(result["page_count"], 2)
        self.assertTrue(result["multi_page"])
        self.assertEqual(
            [page["orientation"] for page in result["pages"]],
            ["portrait", "landscape"],
        )
        self.assertGreater(result["summary"]["text_character_count"], 0)
        self.assertIn("Helvetica", result["summary"]["font_names"])
        self.assertIn("Helvetica-Bold", result["summary"]["font_names"])
        self.assertEqual(result["continuation_clues"]["possible_continuation_pages"], [2])
        self.assertEqual(result["continuation_clues"]["explicit_label_pages"], [2])
        sections = set(result["summary"]["section_label_clues"])
        self.assertTrue(
            {
                "student_identity",
                "class",
                "reporting_period",
                "assignment",
                "weekly_test",
                "teacher_comment",
            }.issubset(sections)
        )
        # The analyzer records only fixed label clues, not arbitrary student report text.
        self.assertNotIn("Alice Confidential", json.dumps(result))
        json.dumps(result)

    @unittest.skipUnless(Image is not None, "Pillow is required to build the image fixture")
    def test_counts_image_xobjects_without_interpreting_them(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "image-example.pdf"
            image_buffer = io.BytesIO()
            Image.new("RGB", (8, 8), (30, 120, 180)).save(image_buffer, format="PNG")
            image_buffer.seek(0)
            document = canvas.Canvas(str(path), pagesize=letter)
            document.drawImage(ImageReader(image_buffer), 72, 500, width=100, height=80)
            document.save()

            result = inspect_example_report(path)

        self.assertGreaterEqual(result["summary"]["xobject_resource_count"], 1)
        self.assertGreaterEqual(result["summary"]["image_xobject_count"], 1)
        self.assertTrue(any("OCR" in warning for warning in result["warnings"]))
        self.assertTrue(any("graph type" in item for item in result["limitations"]))

    def test_rejects_unreadable_and_empty_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            unreadable = Path(folder) / "unreadable.pdf"
            unreadable.write_bytes(b"not a pdf")
            with self.assertRaisesRegex(ValidationError, "not a readable PDF"):
                inspect_example_report(unreadable)

            empty = Path(folder) / "empty.pdf"
            with empty.open("wb") as stream:
                PdfWriter().write(stream)
            with self.assertRaisesRegex(ValidationError, "has no pages"):
                inspect_example_report(empty)


if __name__ == "__main__":
    unittest.main()
