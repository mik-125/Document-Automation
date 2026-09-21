"""Small PDF charts drawn from actual imported observations."""
from __future__ import annotations

import math
import unicodedata
from collections import Counter
from typing import Any

from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth


INK = HexColor("#364850")
GRID = HexColor("#DDE4E6")
BLUE = HexColor("#4389BE")
GRADES = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}


def homework_points(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep every date, including blanks; grades are categories, not percentages."""
    points = []
    for entry in entries:
        grade = unicodedata.normalize("NFKC", str(entry.get("value") or "")).strip().upper()
        label = str(entry.get("date") or entry.get("label") or "")
        if len(label) == 10 and label[4] == "-" and label[7] == "-":
            label = f"{int(label[5:7])}/{int(label[8:10])}"
        points.append({"label": label, "value": GRADES.get(grade) if grade in {"A", "B", "F"} else None,
                       "display": grade if grade in {"A", "B", "F"} else ""})
    return points


def test_points(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Retain missing observations as gaps, and never turn them into zero scores."""
    points = []
    period_counts = Counter(str(entry.get("period_label") or "") for entry in entries)
    for entry in entries:
        value = None
        try:
            if entry.get("percent") is not None:
                value = float(entry["percent"])
            elif entry.get("score") is not None and float(entry.get("max_score") or 0) > 0:
                value = float(entry["score"]) / float(entry["max_score"]) * 100
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 100):
                value = None
        except (TypeError, ValueError, OverflowError):
            value = None
        label = str(entry.get("period_label") or entry.get("label") or "Test")
        if entry.get("period_label") and period_counts[str(entry["period_label"])] > 1:
            label += " · " + str(entry.get("label") or "Test")
        points.append({"label": label,
                       "value": value, "display": f"{value:g}%" if value is not None else ""})
    return points


def _draw_chart(c: Any, points: list[dict[str, Any]], *, x: float, y: float,
                width: float, height: float, font_name: str, ticks: list[tuple[float, str]],
                maximum: float, empty: str) -> None:
    if width < 70 or height < 65:
        raise ValueError("Chart area is too small. Increase it to at least 70 by 65 PDF points.")
    size = max(6, min(8, width / 31))
    left, right = x + 27, x + width - 14
    bottom, top = y + 22, y + height - 16
    c.saveState()
    c.setFont(font_name, size)
    for value, label in ticks:
        ordinate = bottom + (top - bottom) * value / maximum
        c.setStrokeColor(GRID)
        c.setLineWidth(0.5)
        c.line(left, ordinate, right, ordinate)
        c.setFillColor(INK)
        c.drawRightString(left - 5, ordinate - size / 3, label)
    if not any(point["value"] is not None for point in points):
        c.setFillColor(INK)
        c.drawCentredString((left + right) / 2, (bottom + top) / 2 + size, empty)
        c.restoreState()
        return

    slot = (right - left) / max(1, len(points) - 1)
    previous = None
    label_right = -math.inf
    value_right = -math.inf
    for index, point in enumerate(points):
        abscissa = (left + right) / 2 if len(points) == 1 else left + slot * index
        label = point["label"]
        label_width = stringWidth(label, font_name, size)
        if abscissa - label_width / 2 > label_right + 4:
            c.setFillColor(INK)
            c.drawCentredString(abscissa, bottom - size - 5, label)
            label_right = abscissa + label_width / 2
        if point["value"] is None:
            previous = None
            continue
        ordinate = bottom + (top - bottom) * point["value"] / maximum
        c.setStrokeColor(BLUE)
        c.setLineWidth(1)
        if previous is not None:
            c.line(*previous, abscissa, ordinate)
        c.setFillColor(BLUE)
        c.circle(abscissa, ordinate, 2.2, stroke=0, fill=1)
        display_width = stringWidth(point["display"], font_name, size)
        if abscissa - display_width / 2 > value_right + 4:
            c.setFillColor(INK)
            c.drawCentredString(abscissa, ordinate + 5, point["display"])
            value_right = abscissa + display_width / 2
        previous = (abscissa, ordinate)
    c.restoreState()


def draw_homework_chart(c: Any, entries: list[dict[str, Any]], *, x: float, y: float,
                        width: float, height: float, font_name: str = "Helvetica") -> None:
    _draw_chart(c, homework_points(entries), x=x, y=y, width=width, height=height,
                font_name=font_name, ticks=[(value, grade) for grade, value in GRADES.items()],
                maximum=4, empty="No homework grades recorded")


def draw_test_chart(c: Any, entries: list[dict[str, Any]], *, x: float, y: float,
                    width: float, height: float, font_name: str = "Helvetica") -> None:
    _draw_chart(c, test_points(entries), x=x, y=y, width=width, height=height,
                font_name=font_name, ticks=[(value, str(value)) for value in (0, 25, 50, 75, 100)],
                maximum=100, empty="No test scores recorded")
