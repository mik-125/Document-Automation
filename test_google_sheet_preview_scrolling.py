from __future__ import annotations

import re
import unittest
from pathlib import Path

from integrations import preview_table


class GoogleSheetPreviewPayloadTests(unittest.TestCase):
    """Keep the preview truthful while giving the UI more than a tiny sample."""

    def test_total_row_count_is_independent_of_the_render_window(self) -> None:
        values = [["Student", "Class", "Score"]] + [
            [f"Student {number}", "Economics", str(number)]
            for number in range(1, 258)
        ]

        preview = preview_table(values)

        self.assertEqual(
            preview["row_count"],
            257,
            "The total must describe every populated data row, not only rows sent to the first DOM window.",
        )

    def test_preview_payload_makes_rows_after_the_old_twenty_row_sample_reachable(self) -> None:
        values = [["Student", "Class", "Score"]] + [
            [f"Student {number}", "Economics", str(number)]
            for number in range(1, 76)
        ]

        preview = preview_table(values)
        row_collections = [
            value
            for key, value in preview.items()
            if key.endswith("rows") and isinstance(value, list)
        ]

        self.assertTrue(
            any(len(rows) > 20 for rows in row_collections),
            "The response needs a preview-row collection beyond the legacy 20-row sample so scrolling/load-more can reveal later rows.",
        )
        self.assertTrue(
            any(
                any("Student 21" in [str(cell) for cell in row] for row in rows)
                for rows in row_collections
            ),
            "At least the first row after the old sample boundary must be present in the preview data.",
        )

    def test_preview_payload_caps_rows_while_preserving_a_large_total(self) -> None:
        values = [["Student", "Class", "Score"]] + [
            [f"Student {number}", "Economics", str(number)]
            for number in range(1, 1_001)
        ]

        preview = preview_table(values)
        row_collections = [
            value
            for key, value in preview.items()
            if key.endswith("rows") and isinstance(value, list)
        ]

        self.assertEqual(preview["row_count"], 1_000)
        self.assertTrue(row_collections)
        self.assertLessEqual(
            max(len(rows) for rows in row_collections),
            250,
            "A large Sheet must not put thousands of rows in the preview payload and eventually accumulate them all in the DOM.",
        )


class GoogleSheetPreviewFrontendContractTests(unittest.TestCase):
    """Protect bounded rendering without adding a JavaScript DOM dependency."""

    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parent
        cls.javascript = (root / "app.js").read_text(encoding="utf-8")
        cls.styles = (root / "styles.css").read_text(encoding="utf-8")

    @classmethod
    def function_section(cls, start: str, end: str) -> str:
        start_index = cls.javascript.index(f"function {start}(")
        end_index = cls.javascript.index(f"function {end}(", start_index)
        return cls.javascript[start_index:end_index]

    @classmethod
    def style_rule(cls, selector: str) -> str:
        match = re.search(
            rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{(?P<body>[^}}]*)\}}",
            cls.styles,
        )
        if not match:
            raise AssertionError(f"Missing CSS rule for {selector}")
        return match.group("body")

    def test_preview_shows_the_accurate_total_and_renders_a_bounded_chunk(self) -> None:
        table = self.function_section("sampleTable", "openMappingModal")

        self.assertRegex(
            table,
            r"\brow_count\b",
            "The scrollable preview itself must show the backend's accurate total row count.",
        )
        self.assertNotRegex(
            table,
            r"sample_rows\.slice\(\s*0\s*,\s*4\s*\)",
            "The old four-row-only table cannot support a scrollable Sheet preview.",
        )

        limits = {
            name: int(value)
            for name, value in re.findall(
                r"\bconst\s+([A-Z_$][A-Z0-9_$]*PREVIEW[A-Z0-9_$]*)\s*=\s*(\d+)\b",
                self.javascript,
            )
        }
        used_limits = [
            (name, value)
            for name, value in limits.items()
            if name in table and re.search(r"\.slice\(", table)
        ]
        virtual_window = bool(
            re.search(
                r"IntersectionObserver|(?:scrollTop|visibleStart|visibleEnd|rowHeight)",
                table,
            )
        )
        self.assertTrue(
            used_limits or virtual_window,
            "Render a named, bounded preview chunk (or a virtual row window) instead of mapping all 10,000 rows into the DOM.",
        )
        for name, limit in used_limits:
            self.assertGreater(limit, 0, f"{name} must render at least one row.")
            self.assertLessEqual(
                limit,
                250,
                f"{name} is too large for a bounded preview DOM window.",
            )

    def test_preview_has_a_way_to_reveal_later_chunks(self) -> None:
        table = self.function_section("sampleTable", "openMappingModal")
        control_names = re.findall(
            r"data-([a-z0-9-]*(?:preview|row)[a-z0-9-]*(?:more|next|load)[a-z0-9-]*)",
            table,
            re.IGNORECASE,
        )
        wired_control = any(
            re.search(rf"\[data-{re.escape(name)}\]", self.javascript)
            for name in control_names
        )
        scroll_window = bool(
            re.search(
                r"(?:addEventListener\(\s*['\"]scroll['\"]|\.onscroll\s*=|IntersectionObserver)",
                self.javascript,
            )
        )

        self.assertTrue(
            wired_control or scroll_window,
            "A bounded preview needs a wired load-more/next control or scroll-driven virtual window so rows after the first chunk remain reachable.",
        )

    def test_google_sheet_stage_activates_and_binds_the_progressive_window(self) -> None:
        editor = self.function_section("uploadStageEditor", "stagedSourceRow")
        render_modal = self.function_section("renderUploadModal", "openUploadModal")
        scroll_binding = self.function_section(
            "bindSamplePreviewScroll", "openMappingModal"
        )

        google_branch = re.search(
            r'if\s*\(stage\.mode\s*===\s*["\']google_sheet["\']\)\s*\{'
            r"(?P<body>[\s\S]*?)\n\s*\}",
            editor,
        )
        self.assertIsNotNone(
            google_branch,
            "The staged Google Sheet needs its own preview-rendering branch.",
        )
        self.assertRegex(
            google_branch.group("body"),
            r"sampleTable\([\s\S]*?progressive\s*:\s*true",
            "The actual Google Sheet stage must opt into the bounded progressive renderer.",
        )
        self.assertRegex(
            render_modal,
            r"\bbindSamplePreviewScroll\(\)",
            "Every upload-modal rerender must reconnect the preview scroll listener.",
        )
        self.assertRegex(
            scroll_binding,
            r"\[data-preview-scroll\]",
            "The scroll listener must target the progressive preview region.",
        )
        self.assertRegex(
            scroll_binding,
            r"addEventListener\(\s*[\"\']scroll[\"\']",
        )
        self.assertRegex(
            scroll_binding,
            r"\bappendSamplePreviewRows\(",
            "Reaching the preview boundary must append the next bounded chunk.",
        )

    def test_preview_container_confines_vertical_and_horizontal_scrolling(self) -> None:
        container = self.style_rule(".sample-table")
        table = self.style_rule(".sample-table table")

        self.assertRegex(
            container,
            r"(?:max-)?height\s*:\s*[^;]+",
            "The preview needs a bounded height so rows scroll inside it.",
        )
        shorthand = bool(re.search(r"overflow\s*:\s*(?:auto|scroll)\b", container))
        vertical = bool(
            re.search(r"overflow-y\s*:\s*(?:auto|scroll)\b", container)
        )
        horizontal = bool(
            re.search(r"overflow-x\s*:\s*(?:auto|scroll)\b", container)
        )
        self.assertTrue(
            shorthand or (vertical and horizontal),
            "Both axes must scroll inside the preview instead of expanding the modal or page.",
        )
        self.assertRegex(
            container,
            r"overscroll-behavior(?:-[xy])?\s*:\s*(?:contain|none)\b",
            "Scrolling at a preview boundary must remain contained within the modal.",
        )
        self.assertRegex(
            table,
            r"(?:min-)?width\s*:\s*[^;]+",
            "The inner table needs an intrinsic/minimum width so wide Sheets use horizontal scrolling.",
        )


if __name__ == "__main__":
    unittest.main()
