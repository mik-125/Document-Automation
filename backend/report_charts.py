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


MUTED = HexColor("#8A8F94")
WEEKLY_SERIES = (
    ("student", "학생점수", HexColor("#4389BE"), "circle"),
    ("average", "평균", HexColor("#3BA55C"), "square"),
    ("highest", "최고점", HexColor("#D9534F"), "triangle"),
    ("lowest", "최저점", HexColor("#9AA0A6"), "diamond"),
)


def _bounded(value: Any, maximum: float) -> float | None:
    """Missing or invalid observations become gaps, never zero."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= maximum else None


def _marker(c: Any, kind: str, x: float, y: float, r: float = 2.2) -> None:
    if kind == "circle":
        c.circle(x, y, r, stroke=0, fill=1)
    elif kind == "square":
        c.rect(x - r, y - r, 2 * r, 2 * r, stroke=0, fill=1)
    else:
        path = c.beginPath()
        if kind == "triangle":
            path.moveTo(x, y + r * 1.2); path.lineTo(x - r * 1.1, y - r * .9); path.lineTo(x + r * 1.1, y - r * .9)
        else:
            path.moveTo(x, y + r * 1.3); path.lineTo(x + r, y); path.lineTo(x, y - r * 1.3); path.lineTo(x - r, y)
        path.close()
        c.drawPath(path, stroke=0, fill=1)


def _fitted_size(text: str, font_name: str, size: float, width: float) -> float:
    while size > 4 and stringWidth(text, font_name, size) > width:
        size -= .25
    return size


def _draw_series_chart(c: Any, *, x: float, y: float, width: float, height: float,
                       labels: list[str], series: list[dict[str, Any]], ticks: list[tuple[float, str]],
                       maximum: float, title: str, subtitle: str, font_name: str | None,
                       legend: bool) -> None:
    if font_name is None:
        from backend.report_engine import register_fonts
        font_name = register_fonts()
    if width < 90 or height < 80:
        raise ValueError("Chart area is too small. Increase it to at least 90 by 80 PDF points.")
    size = 5.8
    top_y = y + height
    c.saveState()
    c.setFillColor(MUTED)
    title_size = _fitted_size(title, font_name, 6.5, width)
    c.setFont(font_name, title_size)
    c.drawCentredString(x + width / 2, top_y - title_size, title)
    c.setFont(font_name, 5.5)
    c.drawCentredString(x + width / 2, top_y - title_size - 8, subtitle)
    left, right = x + 20, x + width - 12
    bottom = y + size + 6 + (12 if legend else 0)
    top = top_y - title_size - 20
    c.setFont(font_name, size)
    for value, label in ticks:
        ordinate = bottom + (top - bottom) * value / maximum
        c.setStrokeColor(GRID)
        c.setLineWidth(0.5)
        c.line(left, ordinate, right, ordinate)
        c.setFillColor(MUTED)
        c.drawRightString(left - 4, ordinate - size / 3, label)
    count = len(labels)
    slot = (right - left - 12) / max(1, count - 1)

    def abscissa(index: int) -> float:
        return (left + right) / 2 if count == 1 else left + 6 + slot * index

    label_right = -math.inf
    c.setFillColor(MUTED)
    for index, label in enumerate(labels):
        label_width = stringWidth(label, font_name, size)
        if abscissa(index) - label_width / 2 > label_right + 3:
            c.drawCentredString(abscissa(index), bottom - size - 4, label)
            label_right = abscissa(index) + label_width / 2
    for item in reversed(series):  # Student line drawn last, on top.
        previous = None
        c.setStrokeColor(item["color"])
        c.setLineWidth(1)
        for index, value in enumerate(item["values"][:count]):
            if value is None:
                previous = None
                continue
            point = (abscissa(index), bottom + (top - bottom) * value / maximum)
            if previous is not None:
                c.line(*previous, *point)
            c.setFillColor(item["color"])
            _marker(c, item["marker"], *point)
            display = (item.get("displays") or [""] * count)[index]
            if display:
                c.setFillColor(INK)
                c.setFont(font_name, 5.5)
                if item.get("label_beside"):
                    # Beside the point, so it never covers other series' markers.
                    if index == count - 1 and count > 1:
                        c.drawRightString(point[0] - 4.5, point[1] - 2, display)
                    else:
                        c.drawString(point[0] + 4.5, point[1] - 2, display)
                else:
                    c.drawCentredString(point[0], point[1] + 4.5, display)
                c.setFont(font_name, size)
            previous = point
    if legend:
        widths = [stringWidth(item["name"], font_name, size) + 12 for item in series]
        cursor = x + (width - sum(widths) - 6 * (len(widths) - 1)) / 2
        for item, item_width in zip(series, widths):
            c.setFillColor(item["color"])
            _marker(c, item["marker"], cursor + 3, y + 3 + size / 3, 2)
            c.setFillColor(INK)
            c.drawString(cursor + 8, y + 3, item["name"])
            cursor += item_width + 6
    c.restoreState()


def draw_homework_status_chart(c: Any, x: float, y: float, width: float, height: float,
                               entries: list[dict[str, Any]], title: str, subtitle: str,
                               *, font_name: str | None = None) -> None:
    """Daily A/B/F statuses; X, blank, and other values are gaps."""
    letters = [unicodedata.normalize("NFKC", str(e.get("value") or "")).strip().upper() for e in entries]
    letters = [letter if letter in {"A", "B", "F"} else "" for letter in letters]
    labels = [str(e.get("date") or e.get("label") or "") for e in entries]
    series = [{"name": "과제", "color": BLUE, "marker": "circle",
               "values": [GRADES[letter] if letter else None for letter in letters],
               "displays": letters}]
    _draw_series_chart(c, x=x, y=y, width=width, height=height, labels=labels, series=series,
                       ticks=[(4, "A"), (3, "B"), (0, "F")], maximum=4, title=title,
                       subtitle=subtitle, font_name=font_name, legend=False)


def draw_weekly_test_chart(c: Any, x: float, y: float, width: float, height: float,
                           series: list[dict[str, Any]], title: str, subtitle: str,
                           *, font_name: str | None = None) -> None:
    """Student, average, highest, and lowest percentages per reporting period."""
    labels = [str(item.get("end_date") or item.get("period_label") or "") for item in series]
    lines = []
    for key, name, color, marker in WEEKLY_SERIES:
        values = [_bounded(item.get(key), 100) for item in series]
        displays = [f"{round(v, 1):g}" if v is not None and key == "student" else "" for v in values]
        lines.append({"name": name, "color": color, "marker": marker, "values": values, "displays": displays,
                      "label_beside": True})
    _draw_series_chart(c, x=x, y=y, width=width, height=height, labels=labels, series=lines,
                       ticks=[(v, str(v)) for v in (0, 25, 50, 75, 100)], maximum=100, title=title,
                       subtitle=subtitle, font_name=font_name, legend=True)
