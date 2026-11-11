"""Split a Sheet that holds several ``WEEK N`` blocks side by side.

Each block repeats the same columns (class, student, optional grade and school,
dated homework marks, one weekly test score) under its own week heading. This
module only finds the blocks; every block is then imported by the existing
single-week path, so header aliases and value validation stay in one place.
"""
from __future__ import annotations

from typing import Any

from backend.database import week_number_from_label
from backend.integrations import HEADER_ALIASES, IntegrationError, normalize_header

_SCAN_ROWS = 50
_MAX_HEADER_ROWS = 3


def _cell(row: list[Any], column: int) -> str:
    return str(row[column]).strip() if 0 <= column < len(row) and row[column] is not None else ""


def split_week_blocks(values: list[list[Any]]) -> list[dict[str, Any]]:
    """Return ``[{"week_number": int, "values": rows}]`` ordered by week number.

    An empty list means the sheet names no week and should be imported as a
    single week. ``values`` in each item holds only that block's columns,
    starting at its first header row.
    """
    rows = [list(row) for row in values]
    student_aliases = HEADER_ALIASES["student_name"]
    header_index = next(
        (
            index
            for index, row in enumerate(rows[:_SCAN_ROWS])
            if any(normalize_header(cell) in student_aliases for cell in row)
        ),
        None,
    )
    if header_index is None:
        return []
    header = rows[header_index]
    student_columns = [
        column for column in range(len(header))
        if normalize_header(header[column]) in student_aliases
    ]
    band_end = header_index + 1
    while (
        band_end < len(rows)
        and band_end - header_index < _MAX_HEADER_ROWS
        and any(_cell(rows[band_end], column) for column in range(len(rows[band_end])))
        and not any(_cell(rows[band_end], column) for column in student_columns)
    ):
        band_end += 1
    band = rows[header_index:band_end]
    data = rows[band_end:]
    heading_rows = ([rows[header_index - 1]] if header_index else []) + band
    width = max(len(row) for row in rows)

    def week_at(column: int) -> list[int]:
        numbers = (week_number_from_label(_cell(row, column)) for row in heading_rows)
        return [number for number in numbers if number is not None]

    if not any(week_at(column) for column in range(width)):
        return []

    def blank_in_band(column: int) -> bool:
        return not any(_cell(row, column) for row in band)

    starts: list[tuple[int, bool]] = []  # (first column, class header was blank)
    for column in student_columns:
        left = column - 1
        blank_class = (
            left >= 0 and blank_in_band(left) and any(_cell(row, left) for row in data)
        )
        named_class = left >= 0 and normalize_header(_cell(header, left)) in HEADER_ALIASES["class_name"]
        starts.append((left if blank_class or named_class else column, blank_class))

    blocks: dict[int, list[list[str]]] = {}
    for position, (start, blank_class) in enumerate(starts):
        end = (starts[position + 1][0] if position + 1 < len(starts) else width) - 1
        while end > start and blank_in_band(end):
            end -= 1  # Spacer columns and trailing empty columns.
        numbers = {number for column in range(start, end + 1) for number in week_at(column)}
        if not numbers:
            raise IntegrationError(
                "A student block has no WEEK heading. Label every block, for example WEEK 3.",
                "week_heading_missing",
            )
        if len(numbers) > 1:
            raise IntegrationError(
                "A student block has more than one WEEK heading. Use one heading per block.",
                "week_heading_ambiguous",
            )
        number = numbers.pop()
        if number in blocks:
            raise IntegrationError(
                f"Week {number} appears more than once in this sheet.", "duplicate_week_number"
            )
        sliced = [[_cell(row, column) for column in range(start, end + 1)] for row in band + data]
        for row in sliced[: len(band)]:
            for index, text in enumerate(row):
                if week_number_from_label(text) is not None:
                    row[index] = ""
        if blank_class:
            sliced[0][0] = "Class"
        blocks[number] = sliced
    return [{"week_number": number, "values": blocks[number]} for number in sorted(blocks)]
