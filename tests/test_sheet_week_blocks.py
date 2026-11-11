"""Splitting a Sheet with side-by-side WEEK N blocks (synthetic data)."""
import os
import tempfile
import unittest

_DATA = tempfile.TemporaryDirectory()
os.environ.setdefault("BRIGHTLINE_DATA_DIR", _DATA.name)

from backend.integrations import IntegrationError
from backend.sheet_weeks import split_week_blocks

# (name, grade, school, five daily marks, weekly test score)
ROSTER_A = [("가나다", "10", "School One", "AAAAA", "80"),
            ("라마바", "11", "School Two", "ABAFA", "60"),
            ("사아자", "10", "School One", "XXXXX", "100")]
ROSTER_B = [("가나다", "10", "School One", "AAFAA", "90"),
            ("라마바", "11", "School Two", "AAAAA", "70"),
            ("사아자", "10", "School One", "AAAAA", "50")]
ROSTER_C = [("가나다", "10", "School One", "FAAAA", "70"),
            ("라마바", "11", "School Two", "AAAAB", "40"),
            ("사아자", "10", "School One", "AAAAA", "75")]


def block(week, roster, heading=None, class_header="", scores=True, class_name="Class A"):
    """One week block in the academy layout: class column with a blank header."""
    heading = f"WEEK {week}" if heading is None else heading
    rows = [[class_header, "학생", "학년", "학교", heading, "", "", "", ""],
            ["", "", "", "", "7/13", "7/14", "7/15", "7/16", "7/17"]]
    if scores:
        rows[0].append("weekly test 점수 (%)")
        rows[1].append("")
    for name, grade, school, marks, score in roster:
        rows.append([class_name, name, grade, school, *marks] + ([score] if scores else []))
    return rows


def side_by_side(*blocks, spacer=0):
    height = max(len(item) for item in blocks)
    rows = []
    for index in range(height):
        row = []
        for position, item in enumerate(blocks):
            width = len(item[0])
            row += (item[index] if index < len(item) else [""] * width)
            if position < len(blocks) - 1:
                row += [""] * spacer
        rows.append(row)
    return rows


class SheetWeekBlockTests(unittest.TestCase):
    def test_three_blocks_non_consecutive(self):
        result = split_week_blocks(side_by_side(block(1, ROSTER_A), block(2, ROSTER_B), block(4, ROSTER_C)))
        self.assertEqual([item["week_number"] for item in result], [1, 2, 4])
        self.assertEqual(result[2]["values"][0][:2], ["Class", "학생"])
        self.assertEqual(len(result[0]["values"][0]), 10)
        self.assertEqual(result[2]["values"][2][1], "가나다")
        self.assertEqual(result[2]["values"][2][-1], "70")

    def test_blocks_feed_existing_detection(self):
        from backend import server
        for item in split_week_blocks(side_by_side(block(1, ROSTER_A), block(2, ROSTER_B))):
            values, _, preview = server._automatic_sheet_values(item["values"], "assignment")
            self.assertTrue({"student_name", "class_name", "weekly_test_score"}
                            <= set(preview["suggested_mapping"]))
            self.assertEqual(preview["row_count"], 3)
            self.assertEqual(sum(server._is_assignment_date_header(h) for h in values[0]), 5)

    def test_single_block_with_heading_is_returned(self):
        result = split_week_blocks(block(3, ROSTER_A))
        self.assertEqual([item["week_number"] for item in result], [3])

    def test_no_week_heading_returns_empty(self):
        self.assertEqual(split_week_blocks(block(1, ROSTER_A, heading="과제")), [])

    def test_sheet_without_student_header_returns_empty(self):
        self.assertEqual(split_week_blocks([["WEEK 1", "notes"], ["a", "b"]]), [])

    def test_heading_spellings(self):
        for heading in ("Week1", "week 01", "1주차", "WEEK 1"):
            result = split_week_blocks(block(9, ROSTER_A, heading=heading))
            self.assertEqual([item["week_number"] for item in result], [1], heading)

    def test_spacer_and_trailing_blank_columns_ignored(self):
        values = side_by_side(block(1, ROSTER_A), block(2, ROSTER_B), spacer=2)
        values = [row + ["", "", ""] for row in values]
        result = split_week_blocks(values)
        self.assertEqual([item["week_number"] for item in result], [1, 2])
        self.assertEqual([len(item["values"][0]) for item in result], [10, 10])

    def test_block_without_score_column(self):
        result = split_week_blocks(side_by_side(block(1, ROSTER_A, scores=False), block(2, ROSTER_B)))
        self.assertEqual([len(item["values"][0]) for item in result], [9, 10])

    def test_named_class_header_kept(self):
        result = split_week_blocks(block(1, ROSTER_A, class_header="수업"))
        self.assertEqual(result[0]["values"][0][:2], ["수업", "학생"])

    def test_block_without_heading_raises(self):
        with self.assertRaises(IntegrationError) as raised:
            split_week_blocks(side_by_side(block(1, ROSTER_A), block(2, ROSTER_B, heading="")))
        self.assertEqual(raised.exception.code, "week_heading_missing")

    def test_duplicate_week_number_raises(self):
        with self.assertRaises(IntegrationError) as raised:
            split_week_blocks(side_by_side(block(2, ROSTER_A), block(2, ROSTER_B)))
        self.assertEqual(raised.exception.code, "duplicate_week_number")
        self.assertIn("Week 2", str(raised.exception))

    def test_title_rows_above_header_are_skipped(self):
        values = [["Summer term homework"], [""]] + side_by_side(block(1, ROSTER_A), block(2, ROSTER_B))
        self.assertEqual([item["week_number"] for item in split_week_blocks(values)], [1, 2])

    def test_week_heading_on_row_above_header(self):
        rows = block(1, ROSTER_A, heading="")
        above = ["", "", "", "", "WEEK 6", "", "", "", "", ""]
        self.assertEqual([item["week_number"] for item in split_week_blocks([above] + rows)], [6])

    def test_ragged_rows_are_tolerated(self):
        values = side_by_side(block(1, ROSTER_A), block(2, ROSTER_B))
        values[3] = values[3][:13]  # Google omits trailing empty cells.
        result = split_week_blocks(values)
        self.assertEqual(len(result[1]["values"][3]), 10)


if __name__ == "__main__":
    unittest.main()
