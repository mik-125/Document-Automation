from __future__ import annotations

import re
import unittest
from pathlib import Path


class TemplateTableFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent
        cls.html = (root / "index.html").read_text(encoding="utf-8")
        cls.javascript = (root / "app.js").read_text(encoding="utf-8")

    def render_templates(self) -> str:
        start = self.javascript.index("function renderTemplates(")
        end = self.javascript.index("function reportReason(", start)
        return self.javascript[start:end]

    def test_templates_table_has_only_the_requested_columns(self) -> None:
        table = re.search(
            r'<table class="templates-table">(?P<table>[\s\S]*?)</table>',
            self.html,
        )
        self.assertIsNotNone(table)
        headings = re.findall(r"<th>(.*?)</th>", table.group("table"))
        self.assertEqual(headings, ["Classes", "Sample Report"])

    def test_each_automatic_class_row_keeps_sample_upload_available(self) -> None:
        render = self.render_templates()
        self.assertIn("state.classes.map", render)
        self.assertIn('colspan="2"', render)
        self.assertIn("data-template-example", render)
        self.assertIn("Upload sample report", render)

        row = render[render.index("return `<tr>") :]
        self.assertEqual(row.count("<td"), 2)
        self.assertNotIn("data-template-connect", row)
        self.assertNotIn("data-template-map", row)
        self.assertNotIn("status-pill", row)

    def test_sample_picker_accepts_pdf_and_preserves_multi_sample_support(self) -> None:
        picker = re.search(r'<input[^>]+id="example-report-input"[^>]*>', self.html)
        self.assertIsNotNone(picker)
        self.assertIn('accept=".pdf,application/pdf"', picker.group(0))
        self.assertIn("multiple", picker.group(0))


if __name__ == "__main__":
    unittest.main()
