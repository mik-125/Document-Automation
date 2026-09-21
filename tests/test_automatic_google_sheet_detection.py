from __future__ import annotations

import unittest

from backend import server as app_server
from backend.integrations import IntegrationError


class AutomaticAssignmentSheetDetectionTests(unittest.TestCase):
    @staticmethod
    def assignment_sheet(date_heading: str) -> list[list[str]]:
        return [
            ["Brightline assignment export"],
            [],
            ["과목명", "학생", "학교", ""],
            ["", "", "", date_heading],
            ["IB Math AA", "Minseo Kim", "KIS", "A"],
            ["IB Math AA", "Seojun Lee", "KIS", "F"],
        ]

    def test_flattens_two_row_assignment_headers_and_recognizes_date_formats(self) -> None:
        for date_heading in ("8-17", "8.17", "8월 17일"):
            with self.subTest(date_heading=date_heading):
                values, header_row, preview = app_server._automatic_sheet_values(
                    self.assignment_sheet(date_heading), "assignment"
                )

                self.assertEqual(header_row, 3)
                self.assertEqual(
                    values[0], ["과목명", "학생", "학교", date_heading]
                )
                self.assertEqual(preview["headers"], values[0])
                self.assertEqual(preview["row_count"], 2)
                self.assertEqual(preview["suggested_mapping"]["student_name"], "학생")
                self.assertEqual(preview["suggested_mapping"]["class_name"], "과목명")

                transformed, mapping, dated = (
                    app_server._assignment_values_from_dated_columns(
                        values, preview["suggested_mapping"]
                    )
                )
                self.assertEqual(dated, [date_heading])
                self.assertEqual(
                    mapping["assignment_completed"],
                    "__brightline_assignments_completed",
                )
                self.assertEqual(transformed[1][-3:], ["1", "1", "0"])
                self.assertEqual(transformed[2][-3:], ["0", "1", "0"])

    def test_infers_draft_period_from_dated_headers_using_proposed_year(self) -> None:
        period = app_server._assignment_period_from_headers(
            ["과목명", "학생", "7/13", "7/14", "7/15", "7/16", "7/17"],
            "2026-09-07",
        )

        self.assertEqual(
            period,
            {"start_date": "2026-07-13", "end_date": "2026-07-17"},
        )

    def test_does_not_infer_a_period_without_a_valid_draft_date(self) -> None:
        self.assertIsNone(
            app_server._assignment_period_from_headers(["7/13", "7/17"], "")
        )

    def test_selects_a_later_valid_assignment_worksheet(self) -> None:
        worksheets = [
            {
                "worksheet": "Overview",
                "values": [["Assignment workbook"], ["Updated", "2026-08-17"]],
            },
            {
                "worksheet": "Roster only",
                "values": [
                    ["학생", "과목명", "학교"],
                    ["Minseo Kim", "IB Math AA", "KIS"],
                ],
            },
            {
                "worksheet": "Week 1 Assignments",
                "values": self.assignment_sheet("8월 17일"),
            },
        ]

        selected, values, header_row, preview = (
            app_server._automatic_workbook_sheet(worksheets, "assignment")
        )

        self.assertEqual(selected["worksheet"], "Week 1 Assignments")
        self.assertEqual(header_row, 3)
        self.assertEqual(values[0][-1], "8월 17일")
        self.assertEqual(preview["row_count"], 2)

    def test_ignores_assessment_only_trailing_rows_in_preview(self) -> None:
        values = self.assignment_sheet("8/17")
        values.append(["", "", "", "F"])

        selected, _, preview = app_server._automatic_sheet_values(
            values, "assignment"
        )

        self.assertEqual(preview["row_count"], 2)
        self.assertEqual(len(selected), 3)

    def test_rejects_a_workbook_when_no_assignment_tab_is_valid(self) -> None:
        worksheets = [
            {
                "worksheet": "Overview",
                "values": [["Assignment workbook"], ["Updated", "2026-08-17"]],
            },
            {
                "worksheet": "Roster only",
                "values": [
                    ["학생", "과목명", "학교"],
                    ["Minseo Kim", "IB Math AA", "KIS"],
                ],
            },
            {
                "worksheet": "Scores without identities",
                "values": [["8-17"], ["A"], ["F"]],
            },
        ]

        with self.assertRaises(IntegrationError) as raised:
            app_server._automatic_workbook_sheet(worksheets, "assignment")

        self.assertEqual(raised.exception.code, "automatic_sheet_mapping_failed")


if __name__ == "__main__":
    unittest.main()
