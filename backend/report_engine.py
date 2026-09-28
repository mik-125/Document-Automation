from __future__ import annotations

import copy
import csv
import html
import io
import json
import math
import re
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject
from reportlab.lib.colors import Color, HexColor, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from backend.template_mapping import region_lookup
from backend.comment_sections import report_comment_sections
from backend.sample_content import heading_section, page_content_guide
from backend.pdf_content import analyze_content_pdf


BASE_WIDTH = 540.0
BASE_HEIGHT = 780.0
ROYAL_BLUE = HexColor("#3E58C6")
SECTION_BLUE = HexColor("#D8E1F5")
SHADOW_BLUE = HexColor("#213477")
INK = HexColor("#172126")
MUTED = HexColor("#59646A")
GRID = HexColor("#D7DDE8")
GREEN = HexColor("#4B9B79")
RED = HexColor("#C86355")
AMBER = HexColor("#D79B47")

FONT_CANDIDATES = (
    Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
    Path("/System/Library/Fonts/Supplemental/NotoSansGothic-Regular.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
)
APP_FONT = "Helvetica"


class ValidationError(ValueError):
    pass


def register_fonts() -> str:
    global APP_FONT
    if APP_FONT != "Helvetica":
        return APP_FONT
    for path in FONT_CANDIDATES:
        if not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont("RoyalIBUnicode", str(path)))
            APP_FONT = "RoyalIBUnicode"
            return APP_FONT
        except Exception:
            continue
    return APP_FONT


def normalize_spaces(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = re.sub(r"[\t\r\n]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalized_header(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", value or "").lower())


def parse_date_label(value: str, year: int | None = None) -> date | None:
    clean = unicodedata.normalize("NFKC", value or "").strip()
    if not clean:
        return None
    year = year or datetime.now().year
    for pattern, order in (
        (r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$", "ymd"),
        (r"^(\d{1,2})[-./](\d{1,2})$", "md"),
        (r"^(\d{1,2})월\s*(\d{1,2})일?$", "md"),
    ):
        match = re.match(pattern, clean)
        if not match:
            continue
        try:
            if order == "ymd":
                return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            return date(year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


def _find_header_index(row: list[str], names: set[str]) -> int | None:
    for index, value in enumerate(row):
        if normalized_header(value) in names:
            return index
    return None


def inspect_homework_csv(text: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows:
        raise ValidationError("The CSV is empty.")

    header_index = None
    required = {"과목명", "학생", "학년", "학교"}
    for index, row in enumerate(rows[:20]):
        present = {normalized_header(cell) for cell in row}
        if required.issubset(present):
            header_index = index
            break
    if header_index is None or header_index + 1 >= len(rows):
        raise ValidationError("Could not find the class, student, grade, and school columns.")

    headers = rows[header_index]
    date_headers = rows[header_index + 1]
    class_index = _find_header_index(headers, {"과목명", "class", "classname", "course", "subject"})
    teacher_index = _find_header_index(headers, {"담당자이름", "담당자", "teacher", "teachername"})
    name_index = _find_header_index(headers, {"학생", "학생명", "student", "studentname", "name"})
    grade_index = _find_header_index(headers, {"학년", "grade"})
    school_index = _find_header_index(headers, {"학교", "school"})
    if None in (class_index, name_index, grade_index, school_index):
        raise ValidationError("The required student identity columns are incomplete.")

    dates: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for column, label in enumerate(date_headers):
        parsed = parse_date_label(label)
        if not parsed or parsed.isoformat() in seen_dates:
            continue
        seen_dates.add(parsed.isoformat())
        dates.append({"column": column, "label": label.strip(), "iso": parsed.isoformat()})
    if not dates:
        raise ValidationError("No homework dates were found in the CSV header.")

    records = []
    class_names: set[str] = set()
    for row in rows[header_index + 2 :]:
        row = row + [""] * max(0, len(headers) - len(row))
        class_name = row[class_index].strip() if class_index is not None else ""
        student_name = row[name_index].strip() if name_index is not None else ""
        if not class_name or not student_name or normalized_header(class_name) == "과목명":
            continue
        class_names.add(class_name)
        records.append(
            {
                "class_name": class_name,
                "teacher": row[teacher_index].strip() if teacher_index is not None else "",
                "name": student_name,
                "grade": row[grade_index].strip() if grade_index is not None else "",
                "school": row[school_index].strip() if school_index is not None else "",
                "row": row,
            }
        )

    if not records:
        raise ValidationError("No student records were found in the CSV.")
    return {
        "header_index": header_index,
        "indices": {
            "class": class_index,
            "teacher": teacher_index,
            "name": name_index,
            "grade": grade_index,
            "school": school_index,
        },
        "dates": dates,
        "records": records,
        "record_count": len(records),
        "class_count": len(class_names),
    }


def homework_records(text: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    inspected = inspect_homework_csv(text)
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError as exc:
        raise ValidationError("The reporting period is invalid.") from exc
    if start > end:
        raise ValidationError("The reporting period start must be before its end.")

    selected_dates = [
        item for item in inspected["dates"] if start <= date.fromisoformat(item["iso"]) <= end
    ]
    if not selected_dates:
        raise ValidationError("The selected reporting period contains no homework dates.")

    parsed_records: list[dict[str, Any]] = []
    for record in inspected["records"]:
        raw_name = record["name"].strip()
        online_match = re.match(r"^\s*\(\s*ON\s*\)\s*", raw_name, flags=re.IGNORECASE)
        display_name = re.sub(
            r"^\s*\(\s*ON\s*\)\s*", "", raw_name, flags=re.IGNORECASE
        ).strip()
        entries: list[dict[str, Any]] = []
        completed = 0
        incomplete = 0
        excluded = 0
        for date_item in selected_dates:
            column = int(date_item["column"])
            value = record["row"][column].strip() if column < len(record["row"]) else ""
            upper = unicodedata.normalize("NFKC", value).upper().strip()
            category = "excluded"
            chart_score: int | None = None
            if upper.startswith("A"):
                category = "completed"
                chart_score = 100
                completed += 1
            elif upper.startswith("B"):
                category = "completed"
                chart_score = 75
                completed += 1
            elif upper.startswith("F"):
                category = "incomplete"
                chart_score = 25
                incomplete += 1
            else:
                excluded += 1
            entries.append(
                {
                    "date": date_item["iso"],
                    "label": date_item["label"],
                    "value": value,
                    "category": category,
                    "score": chart_score,
                }
            )
        attempted = completed + incomplete
        percent = round(completed / attempted * 100, 1) if attempted else None
        parsed_records.append(
            {
                "class_name": record["class_name"],
                "teacher": record["teacher"],
                "name": display_name,
                "school": record["school"],
                "grade": record["grade"],
                "attendance_mode": "online" if online_match else "in-person",
                "entries": entries,
                "completed": completed,
                "incomplete": incomplete,
                "excluded": excluded,
                "percent": percent,
            }
        )
    return parsed_records


def _cover_name(page_text: str) -> str | None:
    text = normalize_spaces(page_text)
    if not text or len(text) > 110 or "총점" in text or re.search(r"\bQ\d+\b", text):
        return None
    candidate = text.split("(", 1)[0].strip(" -:：")
    candidate = re.sub(r"\s+", " ", candidate)
    if not 2 <= len(candidate) <= 40:
        return None
    if not re.search(r"[A-Za-z가-힣]", candidate):
        return None
    return candidate


def _comment_heading(line: str) -> str | None:
    """Recognize standalone name headings, not names mentioned in sentences."""
    name = normalize_spaces(line)
    if heading_section(name):
        return None
    name = re.sub(r"^\(\s*ON\s*\)\s*", "", name, flags=re.IGNORECASE)
    if re.fullmatch(r"[가-힣]{2,5}", name):
        return name
    if re.fullmatch(r"[A-Z][a-z]+(?:[ '-][A-Z][a-z]+){0,3}", name):
        return name
    return None


def _narrative_name(text: str) -> str | None:
    match = re.match(
        r"^(?:\(ON\)\s*)?([가-힣]{2,5})\s+학생(?:은|는|이)\s+\S",
        normalize_spaces(text), re.IGNORECASE,
    )
    return match.group(1) if match else None


def _is_comment_score_line(line: str) -> bool:
    score = r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?"
    return bool(re.fullmatch(
        rf"(?:총점\s*[:：]\s*{score}|Q\s*\d+\s*\([^()]+\)\s*[:：]\s*{score}"
        rf"(?:\s*\(\s*전체\s*평균\s*[:：]\s*{score}\s*\))?)",
        normalize_spaces(line), re.IGNORECASE,
    ))


def _validate_comment_name(name: str, comment: str) -> None:
    first_content = next((line for line in comment.splitlines()
                          if line.strip() and not _is_comment_score_line(line)), "")
    opening_name = _narrative_name(first_content)
    if opening_name and opening_name != name:
        raise ValidationError(
            f"The heading names {name}, but the comment starts with {opening_name}. "
            "Correct the student name before importing this PDF."
        )


def _inline_comment_records(
    pages: list[str], label: str, source_name: str
) -> list[dict[str, Any]] | None:
    # Layout extraction keeps Korean words on their visual lines. Ordinary
    # extraction can put each word on a separate line, creating false headings.
    lines = "\n\n".join(pages).splitlines()
    headings = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        # Headings must be isolated from body text by whitespace/page breaks.
        if index and lines[index - 1].strip():
            continue
        if index + 1 < len(lines) and lines[index + 1].strip():
            continue
        name = _comment_heading(line)
        if name:
            headings.append((index, name))
    if not headings:
        return None
    if any(line.strip() for line in lines[:headings[0][0]]):
        raise ValidationError(
            "Comments appear before the first student heading. "
            "Start the PDF with a student name on its own line."
        )
    records = []
    for offset, (start, name) in enumerate(headings):
        end = headings[offset + 1][0] if offset + 1 < len(headings) else len(lines)
        raw_comment = "\n".join(lines[start + 1:end])
        comment = _preserve_comment_sections(raw_comment)
        if not comment:
            raise ValidationError(f"No teacher comment could be extracted for {name}.")
        _validate_comment_name(name, raw_comment)
        records.append({
            "name": name, "label": label, "score": None, "max_score": None,
            "sections": [], "comment": comment, "source_name": source_name,
        })
    return records


def _narrative_comment_records(
    pages: list[str], label: str, source_name: str
) -> list[dict[str, Any]] | None:
    """Read one student's narrative without guessing at names inside the prose."""
    lines = [normalize_spaces(line) for page in pages for line in page.splitlines()]
    introductions = [(index, name) for index, line in enumerate(lines)
                     if (name := _narrative_name(line))]
    if not introductions:
        return None
    start, name = introductions[0]
    if any(line and not _is_comment_score_line(line) for line in lines[:start]):
        # Explicit headings and separate legacy covers use the existing paths.
        first_line = next((line for line in lines if line), "")
        if _comment_heading(first_line) or (
            pages and _cover_name(pages[0]) and start >= len(pages[0].splitlines())
        ):
            return None
        raise ValidationError(
            "Unassigned text appears before the student comment. "
            "Start with the student's name and comment, optionally after exam scores."
        )
    if any(other_name != name for _, other_name in introductions[1:]):
        raise ValidationError(
            "This narrative PDF mentions more than one student and cannot be assigned safely. "
            "Use one student's narrative per PDF, or put each student name on its own line "
            "with a blank line before and after it."
        )
    return [{
        "name": name, "label": label, "score": None, "max_score": None,
        "sections": [], "comment": _preserve_comment_sections("\n".join(lines[start:])),
        "source_name": source_name,
    }]


def _preserve_comment_sections(text: str) -> str:
    """Retain explicit section boundaries while joining PDF visual line wraps."""
    blocks: list[str] = []
    body: list[str] = []
    for line in text.splitlines():
        candidate = re.split(r"[:：]", line, maxsplit=1)[0]
        if heading_section(line) or heading_section(candidate):
            if body:
                blocks.append(normalize_spaces(" ".join(body)))
                body = []
            blocks.append(normalize_spaces(line))
        else:
            body.append(line)
    if body:
        blocks.append(normalize_spaces(" ".join(body)))
    return "\n".join(block for block in blocks if block)


def parse_comment_pdf(pdf_bytes: bytes, label: str, source_name: str) -> list[dict[str, Any]]:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise ValidationError("The comments PDF could not be read.") from exc
    if reader.is_encrypted:
        raise ValidationError("Encrypted comments PDFs are not supported.")
    texts = [page.extract_text() or "" for page in reader.pages]
    if not any(text.strip() for text in texts):
        raise ValidationError("The comments PDF has no searchable text. Scanned PDFs need OCR.")

    layout_pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    # An opening student introduction is stronger evidence than an isolated
    # body subheading such as "Atomic Structure", which can resemble a name.
    narrative_records = _narrative_comment_records(layout_pages, label, source_name)
    if narrative_records:
        return narrative_records

    # Keep the legacy cover-page/score format, but recognize name + comment
    # sections on the same page before the old short-page cover heuristic.
    has_inline_heading = any(
        any(_comment_heading(line) for line in page.splitlines())
        and sum(bool(line.strip()) for line in page.splitlines()) > 1
        for page in layout_pages
    )
    if has_inline_heading:
        inline_records = _inline_comment_records(layout_pages, label, source_name)
        if inline_records:
            return inline_records

    covers = [(index, name) for index, text in enumerate(texts) if (name := _cover_name(text))]
    if not covers:
        raise ValidationError(
            "No student headings were found. Put each student name on its own line, "
            "with a blank line before and after it, followed by their comment; "
            "or start a single-student narrative with the student's name followed by 학생은."
        )

    records: list[dict[str, Any]] = []
    for cover_index, (page_index, name) in enumerate(covers):
        next_page = covers[cover_index + 1][0] if cover_index + 1 < len(covers) else len(texts)
        body = _preserve_comment_sections("\n".join(layout_pages[page_index + 1 : next_page]))
        if not body:
            records.append(
                {
                    "name": name,
                    "label": label,
                    "score": None,
                    "max_score": None,
                    "sections": [],
                    "comment": "",
                    "source_name": source_name,
                }
            )
            continue

        score_match = re.search(r"총점\s*[:：]\s*([\d.]+)\s*/\s*([\d.]+)", body)
        score = float(score_match.group(1)) if score_match else None
        max_score = float(score_match.group(2)) if score_match else None
        section_matches = list(
            re.finditer(
                r"Q\s*(\d+)\s*\(([^)]+)\)\s*[:：]\s*([\d.]+)\s*/\s*([\d.]+)(?:\s*\([^)]*\))?",
                body,
                flags=re.IGNORECASE,
            )
        )
        sections = [
            {
                "question": f"Q{match.group(1)}",
                "topic": normalize_spaces(match.group(2)),
                "score": float(match.group(3)),
                "max_score": float(match.group(4)),
            }
            for match in section_matches
        ]
        comment_start = 0
        if section_matches:
            comment_start = section_matches[-1].end()
        elif score_match:
            comment_start = score_match.end()
        comment = body[comment_start:].strip(" -")
        _validate_comment_name(name, comment)
        records.append(
            {
                "name": name,
                "label": label,
                "score": score,
                "max_score": max_score,
                "sections": sections,
                "comment": comment,
                "source_name": source_name,
            }
        )
    return records


def _draw_template_header(c: canvas.Canvas, *, continuation: bool = False) -> None:
    c.setFillColor(ROYAL_BLUE)
    c.rect(0, 0, BASE_WIDTH, BASE_HEIGHT, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 27 if not continuation else 22)
    if continuation:
        c.drawString(45, 716, "STUDENT REPORT CARD")
    else:
        c.drawString(45, 715, "STUDENT")
        c.drawString(45, 682, "REPORT")
        c.drawString(45, 649, "CARD")
    c.setFont("Times-Roman", 13)
    c.drawCentredString(BASE_WIDTH / 2, 17, "ROYAL IB")


def _draw_section(c: canvas.Canvas, x: float, y: float, width: float, height: float, title: str) -> None:
    register_fonts()
    c.setFillColor(white)
    c.rect(x, y, width, height, stroke=0, fill=1)
    c.setFillColor(SECTION_BLUE)
    c.rect(x, y + height - 19, width, 19, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont(APP_FONT, 10)
    c.drawCentredString(x + width / 2, y + height - 14, title)


def _draw_first_page_base(c: canvas.Canvas, *, with_fields: bool) -> None:
    _draw_template_header(c)
    register_fonts()
    labels = (("과 목", 712), ("성 명", 676), ("학교 / 학년", 640))
    for label, y in labels:
        c.setFillColor(SHADOW_BLUE)
        c.rect(271, y - 8, 242, 29, stroke=0, fill=1)
        c.setFillColor(white)
        c.rect(277, y - 3, 230, 24, stroke=0, fill=1)
        c.setFillColor(INK)
        c.setFont(APP_FONT, 8)
        c.drawString(288, y + 5, label)
    _draw_section(c, 31, 399, 478, 173, "과제 이행도")
    _draw_section(c, 31, 218, 478, 165, "WEEKLY TEST")
    _draw_section(c, 31, 38, 478, 164, "담당 선생님 COMMENT")
    c.setStrokeColor(GRID)
    c.rect(43, 420, 218, 125, stroke=1, fill=0)
    c.rect(43, 240, 218, 115, stroke=1, fill=0)

    if not with_fields:
        return
    field_specs = (
        ("class_name", 338, 712, 160, 20, False),
        ("student_name", 338, 676, 160, 20, False),
        ("school_grade", 338, 640, 160, 20, False),
        ("homework_summary", 275, 414, 222, 132, True),
        ("weekly_test_summary", 275, 233, 222, 123, True),
        ("teacher_comment_1", 42, 50, 456, 125, True),
    )
    for name, x, y, width, height, multiline in field_specs:
        c.acroForm.textfield(
            name=name,
            x=x,
            y=y,
            width=width,
            height=height,
            borderWidth=0,
            fillColor=Color(1, 1, 1, alpha=0),
            textColor=INK,
            fontName="Helvetica",
            fontSize=7,
            fieldFlags="multiline" if multiline else "",
            forceBorder=False,
        )


def _draw_continuation_base(c: canvas.Canvas, *, with_fields: bool, page_number: int = 2) -> None:
    _draw_template_header(c, continuation=True)
    _draw_section(c, 35, 50, 470, 635, "담당 선생님 COMMENT")
    c.setFillColor(white)
    c.setFont("Helvetica", 8)
    c.drawRightString(500, 28, f"PAGE {page_number}")
    if with_fields:
        c.acroForm.textfield(
            name="teacher_comment_2",
            x=46,
            y=66,
            width=448,
            height=590,
            borderWidth=0,
            fillColor=Color(1, 1, 1, alpha=0),
            textColor=INK,
            fontName="Helvetica",
            fontSize=8,
            fieldFlags="multiline",
            forceBorder=False,
        )


def create_fillable_template(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=(BASE_WIDTH, BASE_HEIGHT), pageCompression=1)
    c.setTitle("Royal IB Student Report Card - Fillable Template")
    _draw_first_page_base(c, with_fields=True)
    c.showPage()
    _draw_continuation_base(c, with_fields=True)
    c.save()


def inspect_template(path: Path) -> dict[str, Any]:
    try:
        reader = PdfReader(path)
    except Exception as exc:
        raise ValidationError("The report template is not a readable PDF.") from exc
    if reader.is_encrypted:
        raise ValidationError("Encrypted report templates are not supported.")
    if not reader.pages:
        raise ValidationError("The report template has no pages.")
    first = reader.pages[0].mediabox
    width, height = float(first.width), float(first.height)
    if width >= height:
        raise ValidationError("The report template must use a portrait page.")
    fields = sorted((reader.get_fields() or {}).keys())
    return {"pages": len(reader.pages), "fields": fields, "width": width, "height": height}


_EXAMPLE_SECTION_PATTERNS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "student_identity",
        (
            (r"\bstudent(?:\s+(?:name|id|number))?\b", "student"),
            (r"(?:학생(?:명|번호)?|원생(?:명|번호)?|이름)", "학생/이름"),
        ),
    ),
    (
        "class",
        (
            (r"\b(?:class|course|subject)(?:\s+name)?\b", "class/course"),
            (r"(?:과목(?:명)?|수업(?:명)?|(?:^|[\s:])반(?:$|[\s:]))", "과목/수업"),
        ),
    ),
    (
        "reporting_period",
        (
            (r"\b(?:reporting\s+)?(?:week|period)\b", "week/period"),
            (r"(?:주차|보고\s*기간|기간)", "주차/기간"),
        ),
    ),
    (
        "assignment",
        (
            (r"\b(?:assignment|homework)s?\b", "assignment/homework"),
            (r"(?:과제|숙제)", "과제/숙제"),
        ),
    ),
    (
        "weekly_test",
        (
            (r"\b(?:weekly\s+test|test\s+score|exam|quiz)\b", "weekly test/exam"),
            (r"(?:주간\s*테스트|시험|테스트\s*점수)", "주간 테스트/시험"),
        ),
    ),
    (
        "teacher_comment",
        (
            (r"\b(?:(?:teacher|instructor)\s+)?(?:comment|feedback|remark)s?\b", "comment/feedback"),
            (r"(?:선생님?\s*(?:코멘트|의견|평가)|코멘트|의견|총평)", "코멘트/총평"),
        ),
    ),
    (
        "graph_or_chart",
        (
            (r"\b(?:graph|chart|trend)\b", "graph/chart"),
            (r"(?:그래프|차트|추이)", "그래프/차트"),
        ),
    ),
)
_EXAMPLE_CONTINUATION_PATTERN = re.compile(
    r"\b(?:continued|continuation|continue)\b|(?:계속|다음\s*페이지)", re.IGNORECASE
)
_PDF_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")


def _resolved_pdf_object(value: Any) -> Any:
    try:
        return value.get_object()
    except (AttributeError, TypeError, ValueError):
        return value


def _example_font_name(font: Any) -> str:
    font = _resolved_pdf_object(font)
    if not hasattr(font, "get"):
        return ""
    raw_name = str(font.get("/BaseFont") or "").lstrip("/")
    return _PDF_SUBSET_PREFIX.sub("", raw_name)


def _example_text_position(cm: Any, tm: Any) -> tuple[float | None, float | None]:
    try:
        x = float(tm[4]) * float(cm[0]) + float(tm[5]) * float(cm[2]) + float(cm[4])
        y = float(tm[4]) * float(cm[1]) + float(tm[5]) * float(cm[3]) + float(cm[5])
    except (IndexError, TypeError, ValueError):
        return None, None
    if not math.isfinite(x) or not math.isfinite(y):
        return None, None
    return x, y


def _example_section_clues(
    text: str,
    *,
    x: float | None,
    y: float | None,
    width: float,
    height: float,
    seen: set[str],
) -> list[dict[str, Any]]:
    normalized = unicodedata.normalize("NFKC", text or "")
    clues: list[dict[str, Any]] = []
    for section, patterns in _EXAMPLE_SECTION_PATTERNS:
        if section in seen:
            continue
        for pattern, label in patterns:
            if not re.search(pattern, normalized, re.IGNORECASE):
                continue
            clue: dict[str, Any] = {"section": section, "matched_label": label}
            if x is not None and y is not None and width > 0 and height > 0:
                clue["x_ratio"] = round(min(1.0, max(0.0, x / width)), 3)
                clue["top_ratio"] = round(min(1.0, max(0.0, 1.0 - y / height)), 3)
            clues.append(clue)
            seen.add(section)
            break
    return clues


def _example_xobject_counts(resources: Any) -> dict[str, int]:
    counts = {"xobjects": 0, "images": 0, "forms": 0}
    visited: set[tuple[str, int, int] | tuple[str, int]] = set()

    def walk(current_resources: Any) -> None:
        current_resources = _resolved_pdf_object(current_resources)
        if not hasattr(current_resources, "get"):
            return
        xobjects = _resolved_pdf_object(current_resources.get("/XObject"))
        if not hasattr(xobjects, "values"):
            return
        for reference in xobjects.values():
            if hasattr(reference, "idnum"):
                identity: tuple[str, int, int] | tuple[str, int] = (
                    "indirect",
                    int(reference.idnum),
                    int(getattr(reference, "generation", 0)),
                )
            else:
                identity = ("direct", id(reference))
            if identity in visited:
                continue
            visited.add(identity)
            obj = _resolved_pdf_object(reference)
            if not hasattr(obj, "get"):
                continue
            counts["xobjects"] += 1
            subtype = str(obj.get("/Subtype") or "")
            if subtype == "/Image":
                counts["images"] += 1
            elif subtype == "/Form":
                counts["forms"] += 1
                walk(obj.get("/Resources"))

    walk(resources)
    return counts


def inspect_example_report(path: Path) -> dict[str, Any]:
    """Analyze section content and visual evidence without retaining sample prose.

    The original blank PDF remains the only design source. Sample headings and
    aggregate body statistics guide content placement, never student facts.
    """

    try:
        reader = PdfReader(path)
    except Exception as exc:
        raise ValidationError("The example report is not a readable PDF.") from exc
    if reader.is_encrypted:
        raise ValidationError("Encrypted example reports are not supported.")
    if not reader.pages:
        raise ValidationError("The example report has no pages.")

    warnings: list[str] = []
    try:
        all_form_fields = sorted(str(name) for name in (reader.get_fields() or {}))
    except Exception as exc:
        all_form_fields = []
        warnings.append(f"Form fields could not be inspected ({type(exc).__name__}).")
    form_fields = all_form_fields[:50]
    if len(all_form_fields) > len(form_fields):
        warnings.append("Only the first 50 form-field names are included in the analysis.")

    pages: list[dict[str, Any]] = []
    overall_font_names: Counter[str] = Counter()
    overall_font_sizes: Counter[float] = Counter()
    overall_sections: set[str] = set()
    total_characters = 0
    total_text_runs = 0
    total_xobjects = 0
    total_images = 0
    total_forms = 0
    explicit_continuation_pages: list[int] = []
    content_sections: list[dict[str, Any]] = []
    content_warnings: list[str] = []
    content_images: list[dict[str, Any]] = []

    for page_number, page in enumerate(reader.pages, start=1):
        media_box = page.mediabox
        raw_width = float(media_box.width)
        raw_height = float(media_box.height)
        rotation = int(getattr(page, "rotation", 0) or 0) % 360
        display_width, display_height = (
            (raw_height, raw_width) if rotation in {90, 270} else (raw_width, raw_height)
        )
        if math.isclose(display_width, display_height, abs_tol=0.5):
            orientation = "square"
        elif display_width > display_height:
            orientation = "landscape"
        else:
            orientation = "portrait"

        font_runs: Counter[str] = Counter()
        font_sizes_by_name: dict[str, Counter[float]] = {}
        all_sizes: Counter[float] = Counter()
        section_clues: list[dict[str, Any]] = []
        seen_sections: set[str] = set()
        text_run_count = 0
        continuation_label_found = False
        content_runs: list[dict[str, Any]] = []
        image_regions: list[dict[str, Any]] = []

        def visit_operator(operator: bytes, operands: Any, cm: Any, tm: Any) -> None:
            if operator != b"Do" or not operands:
                return
            resources = _resolved_pdf_object(page.get("/Resources"))
            objects = _resolved_pdf_object(resources.get("/XObject")) if hasattr(resources, "get") else None
            obj = _resolved_pdf_object(objects.get(operands[0])) if hasattr(objects, "get") else None
            if not hasattr(obj, "get") or str(obj.get("/Subtype")) != "/Image":
                return
            corners = [(cm[4], cm[5]), (cm[0] + cm[4], cm[1] + cm[5]),
                       (cm[2] + cm[4], cm[3] + cm[5]), (cm[0] + cm[2] + cm[4], cm[1] + cm[3] + cm[5])]
            xs, ys = zip(*corners)
            image_regions.append({"x": round(float(min(xs)) / raw_width, 4),
                                  "y": round(float(min(ys)) / raw_height, 4),
                                  "width": round(float(max(xs) - min(xs)) / raw_width, 4),
                                  "height": round(float(max(ys) - min(ys)) / raw_height, 4),
                                  "kind": "image; graph type unconfirmed"})

        def visit_text(text: Any, cm: Any, tm: Any, font: Any, font_size: Any) -> None:
            nonlocal text_run_count, continuation_label_found
            clean = normalize_spaces(str(text or ""))
            if not clean:
                return
            text_run_count += 1
            name = _example_font_name(font)
            try:
                # PDF text matrices often encode the real size while Tf is 1.
                scale_y = math.hypot(tm[2]*cm[0]+tm[3]*cm[2], tm[2]*cm[1]+tm[3]*cm[3])
                size = round(float(font_size) * scale_y, 2)
            except (TypeError, ValueError):
                size = 0.0
            if not math.isfinite(size) or size <= 0:
                size = 0.0
            if name:
                font_runs[name] += 1
                if size:
                    font_sizes_by_name.setdefault(name, Counter())[size] += 1
            if size:
                all_sizes[size] += 1
            x, y = _example_text_position(cm, tm)
            for line_index, line in enumerate(str(text or "").splitlines()):
                if line.strip():
                    content_runs.append({"text": unicodedata.normalize("NFKC", line).strip(),
                                         "x": x, "y": y - line_index * size * 1.2 if y is not None else None,
                                         "font": name, "size": size})
            section_clues.extend(
                _example_section_clues(
                    clean,
                    x=x,
                    y=y,
                    width=raw_width,
                    height=raw_height,
                    seen=seen_sections,
                )
            )
            continuation_label_found = bool(
                continuation_label_found or _EXAMPLE_CONTINUATION_PATTERN.search(clean)
            )

        try:
            extracted_text = page.extract_text(visitor_text=visit_text, visitor_operand_before=visit_operator) or ""
        except Exception as exc:
            extracted_text = ""
            warnings.append(
                f"Page {page_number} text could not be extracted ({type(exc).__name__})."
            )
        character_count = len(normalize_spaces(extracted_text))
        line_count = len(
            [line for line in str(extracted_text).splitlines() if normalize_spaces(line)]
        )
        xobject_counts = _example_xobject_counts(page.get("/Resources"))
        content_images.extend({**image, "page": page_number} for image in image_regions)
        if rotation:
            content_warnings.append(f"Page {page_number}: rotated content needs placement review.")
        else:
            content_guide = page_content_guide(content_runs, raw_width, raw_height, page_number, image_regions)
            content_sections.extend(content_guide["sections"])
            content_warnings.extend(content_guide["warnings"])
        if not character_count:
            if xobject_counts["images"]:
                warnings.append(
                    f"Page {page_number} may be scanned or image-only; OCR is not performed."
                )
            else:
                warnings.append(f"Page {page_number} has no extractable text.")
        if continuation_label_found:
            explicit_continuation_pages.append(page_number)

        font_entries = [
            {
                "name": name,
                "run_count": font_runs[name],
                "sizes": sorted(font_sizes_by_name.get(name, {})),
            }
            for name in sorted(font_runs, key=lambda item: (-font_runs[item], item))[:24]
        ]
        if len(font_runs) > len(font_entries):
            warnings.append(f"Page {page_number} font details were limited to 24 fonts.")
        page_result = {
            "page": page_number,
            "width_points": round(raw_width, 2),
            "height_points": round(raw_height, 2),
            "display_width_points": round(display_width, 2),
            "display_height_points": round(display_height, 2),
            "rotation": rotation,
            "orientation": orientation,
            "text_character_count": character_count,
            "text_line_count": line_count,
            "text_run_count": text_run_count,
            "fonts": font_entries,
            "font_sizes": sorted(all_sizes)[:30],
            "xobject_resource_count": xobject_counts["xobjects"],
            "image_xobject_count": xobject_counts["images"],
            "form_xobject_count": xobject_counts["forms"],
            "section_label_clues": section_clues,
            "explicit_continuation_label": continuation_label_found,
        }
        pages.append(page_result)
        overall_font_names.update(font_runs)
        overall_font_sizes.update(all_sizes)
        overall_sections.update(seen_sections)
        total_characters += character_count
        total_text_runs += text_run_count
        total_xobjects += xobject_counts["xobjects"]
        total_images += xobject_counts["images"]
        total_forms += xobject_counts["forms"]

    dimension_signatures = {
        (page["display_width_points"], page["display_height_points"]) for page in pages
    }
    orientations = {page["orientation"] for page in pages}
    if len(dimension_signatures) > 1:
        warnings.append("Page dimensions vary within this example report.")
    if len(orientations) > 1:
        warnings.append("Page orientations vary within this example report.")
    if any(page["orientation"] != "portrait" for page in pages):
        warnings.append("At least one page is not portrait; verify that this is intentional.")
    if not overall_font_names:
        warnings.append("Font names could not be extracted from this report.")

    possible_continuation_pages = list(range(2, len(pages) + 1))
    return {
        "analysis_version": 1,
        "page_count": len(pages),
        "multi_page": len(pages) > 1,
        "form_field_count": len(all_form_fields),
        "form_fields": form_fields,
        "pages": pages,
        "summary": {
            "text_character_count": total_characters,
            "text_run_count": total_text_runs,
            "font_names": [
                name
                for name in sorted(
                    overall_font_names,
                    key=lambda item: (-overall_font_names[item], item),
                )[:30]
            ],
            "font_sizes": sorted(overall_font_sizes)[:30],
            "xobject_resource_count": total_xobjects,
            "image_xobject_count": total_images,
            "form_xobject_count": total_forms,
            "section_label_clues": sorted(overall_sections),
        },
        "continuation_clues": {
            "possible_continuation_pages": possible_continuation_pages,
            "explicit_label_pages": explicit_continuation_pages,
            "basis": (
                ["Document contains more than one page; later pages may be continuations."]
                if len(pages) > 1
                else []
            )
            + (
                ["Continuation wording was found in extractable text."]
                if explicit_continuation_pages
                else []
            ),
        },
        "warnings": warnings,
        "content_guide": analyze_content_pdf(Path(path)),
        "limitations": [
            "Section labels are keyword clues, not confirmed field mappings.",
            "Text coordinates are approximate for transformed or rotated PDF content.",
            "PDF font names may be subset aliases and do not prove a font is installed.",
            "Images and form XObjects are counted as resources; graph type and meaning are not inferred.",
            "Image-only text is not recognized because OCR is not performed.",
        ],
    }


def _wrap_lines(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    for raw_paragraph in str(text or "").splitlines() or [""]:
        paragraph = normalize_spaces(raw_paragraph)
        if not paragraph:
            lines.append("")
            continue
        current = ""
        tokens = re.findall(
            r"[A-Za-z0-9]+(?:[._'’/-][A-Za-z0-9]+)*|[ \t]+|.", paragraph
        )
        for token in tokens:
            token = " " if token.isspace() else token
            parts = (
                list(token)
                if pdfmetrics.stringWidth(token, font_name, font_size) > max_width
                else [token]
            )
            for part in parts:
                candidate = current + part
                if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
                    lines.append(current.rstrip())
                    current = part.lstrip()
                else:
                    current = candidate
        lines.append(current.rstrip())
    return lines


def _draw_lines(
    c: canvas.Canvas,
    lines: list[str],
    *,
    x: float,
    top_y: float,
    font_size: float,
    leading: float,
    font_name: str | None = None,
) -> None:
    text = c.beginText(x, top_y)
    text.setFont(font_name or APP_FONT, font_size)
    text.setFillColor(INK)
    text.setLeading(leading)
    for line in lines:
        text.textLine(line)
    c.drawText(text)


def _draw_line_chart(
    c: canvas.Canvas,
    points: list[dict[str, Any]],
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    value_key: str,
    label_key: str,
) -> None:
    c.setStrokeColor(GRID)
    c.setLineWidth(0.5)
    for ratio in (0, 0.25, 0.5, 0.75, 1):
        line_y = y + height * ratio
        c.line(x, line_y, x + width, line_y)
    valid = [item for item in points if item.get(value_key) is not None]
    if not valid:
        c.setFillColor(MUTED)
        c.setFont(APP_FONT, 7)
        c.drawCentredString(x + width / 2, y + height / 2, "No data in this period")
        return
    if len(valid) > 12:
        step = (len(valid) - 1) / 11
        valid = [valid[round(index * step)] for index in range(12)]
    coordinates: list[tuple[float, float]] = []
    for index, item in enumerate(valid):
        point_x = x + (width * index / max(1, len(valid) - 1))
        point_y = y + height * max(0, min(100, float(item[value_key]))) / 100
        coordinates.append((point_x, point_y))
    c.setStrokeColor(GREEN)
    c.setLineWidth(1.5)
    for first, second in zip(coordinates, coordinates[1:]):
        c.line(first[0], first[1], second[0], second[1])
    for (point_x, point_y), item in zip(coordinates, valid):
        c.setFillColor(GREEN)
        c.circle(point_x, point_y, 2.3, stroke=0, fill=1)
        c.setFillColor(MUTED)
        c.setFont(APP_FONT, 5.5)
        label = str(item.get(label_key, ""))[-10:]
        c.saveState()
        c.translate(point_x - 2, y - 7)
        c.rotate(40)
        c.drawString(0, 0, label)
        c.restoreState()


def _format_number(value: Any) -> str:
    if value is None:
        return "-"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.1f}"


def _student_comment(student: dict[str, Any]) -> str:
    exams = student.get("exams", [])
    if "comments_by_section" in student or any("comment_section" in exam for exam in exams):
        parts = student.get("comments_by_section") or report_comment_sections(exams)
        return "\n\n".join(
            f"{label}\n{parts.get(key, '')}" for key, label in (
                ("homework", "과제이행도"), ("weekly_test", "위클리 테스트"), ("general", "종합 코멘트")
            ) if str(parts.get(key) or "").strip()
        )
    comments = [str(exam["comment"]) for exam in exams if str(exam.get("comment") or "").strip()]
    return "\n\n".join(comments) or "No teacher comment was supplied for this reporting period."


def _build_overlay_pages(student: dict[str, Any], width: float, height: float) -> list[bytes]:
    register_fonts()
    scale_x, scale_y = width / BASE_WIDTH, height / BASE_HEIGHT
    comment = _student_comment(student)
    comment_lines = _wrap_lines(comment, APP_FONT, 7.2, 440)
    first_lines = comment_lines[:13]
    remaining = comment_lines[13:]
    continuation_chunks = [remaining[index : index + 55] for index in range(0, len(remaining), 55)]
    pages: list[bytes] = []

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height), pageCompression=1)
    c.saveState()
    c.scale(scale_x, scale_y)
    c.setFillColor(INK)
    c.setFont(APP_FONT, 8.5)
    c.drawString(343, 719, str(student.get("class_name", ""))[:36])
    c.setFont(APP_FONT, 10)
    c.drawString(343, 683, str(student.get("name", ""))[:30])
    c.setFont(APP_FONT, 8.5)
    school_grade = " · ".join(
        value for value in (str(student.get("school", "")), str(student.get("grade", ""))) if value
    )
    c.drawString(343, 647, school_grade[:42])

    homework = student.get("homework", [])
    _draw_line_chart(
        c,
        homework,
        x=51,
        y=441,
        width=200,
        height=87,
        value_key="score",
        label_key="label",
    )
    completed = int(student.get("homework_completed") or 0)
    incomplete = int(student.get("homework_incomplete") or 0)
    excluded = int(student.get("homework_excluded") or 0)
    percent = student.get("homework_percent")
    homework_copy = (
        f"기간: {student.get('period_start') or '-'} ~ {student.get('period_end') or '-'}\n"
        f"과제 이행률: {_format_number(percent)}%\n"
        f"A/B 완료: {completed}   F 미완료: {incomplete}\n"
        f"X 또는 공란 제외: {excluded}"
    )
    _draw_lines(
        c,
        _wrap_lines(homework_copy, APP_FONT, 7.2, 218),
        x=278,
        top_y=535,
        font_size=7.2,
        leading=11,
    )

    exams = student.get("exams", [])
    test_points = [
        {
            "label": exam.get("label", "Exam"),
            "percent": (
                float(exam["score"]) / float(exam["max_score"]) * 100
                if exam.get("score") is not None and exam.get("max_score")
                else None
            ),
        }
        for exam in exams
    ]
    _draw_line_chart(
        c,
        test_points,
        x=51,
        y=260,
        width=200,
        height=78,
        value_key="percent",
        label_key="label",
    )
    latest = next(
        (exam for exam in reversed(exams) if exam.get("score") is not None),
        None,
    )
    if latest:
        section_copy = ", ".join(
            f"{item.get('question')}: {_format_number(item.get('score'))}/{_format_number(item.get('max_score'))}"
            for item in latest.get("sections", [])
        )
        test_copy = (
            f"{latest.get('label', 'Weekly exam')}: "
            f"{_format_number(latest.get('score'))}/{_format_number(latest.get('max_score'))}\n"
            f"{section_copy or 'Section scores were not included.'}"
        )
    else:
        test_copy = "No weekly exam has been matched to this student."
    _draw_lines(
        c,
        _wrap_lines(test_copy, APP_FONT, 7, 218),
        x=278,
        top_y=344,
        font_size=7,
        leading=10,
    )
    _draw_lines(c, first_lines, x=43, top_y=177, font_size=7.2, leading=8.8)
    c.restoreState()
    c.save()
    pages.append(buffer.getvalue())

    for page_number, chunk in enumerate(continuation_chunks, start=2):
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, height), pageCompression=1)
        c.saveState()
        c.scale(scale_x, scale_y)
        _draw_lines(c, chunk, x=47, top_y=650, font_size=7.2, leading=9.5)
        c.setFillColor(white)
        c.setFont("Helvetica", 8)
        c.drawRightString(500, 28, f"PAGE {page_number}")
        c.restoreState()
        c.save()
        pages.append(buffer.getvalue())
    return pages


def _generated_continuation_page(width: float, height: float, page_number: int) -> Any:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height), pageCompression=1)
    c.saveState()
    c.scale(width / BASE_WIDTH, height / BASE_HEIGHT)
    _draw_continuation_base(c, with_fields=False, page_number=page_number)
    c.restoreState()
    c.save()
    return PdfReader(io.BytesIO(buffer.getvalue())).pages[0]


def _mapped_font(region: dict[str, Any]) -> str:
    requested = str(region.get("font_name") or "auto")
    if requested == "auto":
        return register_fonts()
    if requested in pdfmetrics.getRegisteredFontNames() or requested in {
        "Helvetica",
        "Helvetica-Bold",
        "Times-Roman",
        "Times-Bold",
        "Courier",
        "Courier-Bold",
    }:
        return requested
    return register_fonts()


def _mapped_box(region: dict[str, Any], width: float, height: float) -> tuple[float, float, float, float]:
    box = region["box"]
    return (
        float(box["x"]) * width,
        float(box["y"]) * height,
        float(box["width"]) * width,
        float(box["height"]) * height,
    )


def _fit_mapped_text(
    value: str,
    region: dict[str, Any],
    width: float,
    height: float,
) -> tuple[str, float, float, list[str]]:
    font_name = _mapped_font(region)
    preferred = float(region.get("font_size") or 8.5)
    minimum = max(7.0, float(region.get("minimum_font_size") or min(7.0, preferred)))
    spacing = float(region.get("line_spacing") or 1.2)
    size = preferred
    while size + 0.001 >= minimum:
        leading = size * spacing
        lines = _wrap_lines(value, font_name, size, max(1, width))
        if len(lines) * leading <= height + 0.01:
            return font_name, size, leading, lines
        size = round(size - 0.25, 2)
    raise ValidationError(
        f"{region['key'].replace('_', ' ').title()} does not fit its template region. "
        "Increase the region or shorten the source text before generating the report."
    )


def _draw_mapped_text(
    c: canvas.Canvas,
    value: str,
    region: dict[str, Any],
    *,
    width: float,
    height: float,
) -> None:
    x, y, box_width, box_height = _mapped_box(region, width, height)
    font_name, font_size, leading, lines = _fit_mapped_text(
        str(value or ""), region, box_width, box_height
    )
    align = str(region.get("align") or "left")
    c.setFont(font_name, font_size)
    c.setFillColor(INK)
    top_y = y + box_height - font_size
    if align == "left":
        _draw_lines(
            c,
            lines,
            x=x,
            top_y=top_y,
            font_size=font_size,
            leading=leading,
            font_name=font_name,
        )
        return
    for index, line in enumerate(lines):
        line_y = top_y - index * leading
        if align == "center":
            c.drawCentredString(x + box_width / 2, line_y, line)
        else:
            c.drawRightString(x + box_width, line_y, line)


def _mapped_values(student: dict[str, Any]) -> dict[str, str]:
    completed = int(student.get("homework_completed") or 0)
    incomplete = int(student.get("homework_incomplete") or 0)
    excluded = int(student.get("homework_excluded") or 0)
    percent = student.get("homework_percent")
    exams = list(student.get("exams") or [])
    latest = next(
        (exam for exam in reversed(exams) if exam.get("score") is not None),
        None,
    )
    if latest:
        section_copy = ", ".join(
            f"{item.get('question')}: {_format_number(item.get('score'))}/{_format_number(item.get('max_score'))}"
            for item in latest.get("sections", [])
        )
        weekly_summary = (
            f"{latest.get('label', 'Weekly test')}: "
            f"{_format_number(latest.get('score'))}/{_format_number(latest.get('max_score'))}"
            + (f"\n{section_copy}" if section_copy else "")
        )
    else:
        weekly_summary = "No weekly-test score was supplied."
    school_grade = " · ".join(
        value
        for value in (str(student.get("school") or ""), str(student.get("grade") or ""))
        if value
    )
    period = str(student.get("period_label") or "")
    if student.get("period_start") and student.get("period_end"):
        period = f"{period} · {student['period_start']}–{student['period_end']}".strip(" ·")
    parts = student.get("comments_by_section")
    return {
        "student_name": str(student.get("name") or ""),
        "class_name": str(student.get("class_name") or ""),
        "school_grade": school_grade,
        "reporting_week": period,
        "homework_summary": (
            f"Homework completion: {_format_number(percent)}%\n"
            f"Completed: {completed} · Incomplete: {incomplete} · Excluded: {excluded}"
            + (f"\n\n{parts['homework']}" if parts and parts.get("homework") else "")
        ),
        "weekly_test_summary": weekly_summary + (
            f"\n\n{parts['weekly_test']}" if parts and parts.get("weekly_test") else ""
        ),
        "teacher_comment": str(parts.get("general") or "") if parts is not None else _student_comment(student),
    }


def _mapped_text_piece(
    value: str, region: dict[str, Any], width: float, height: float,
) -> tuple[tuple[str, float, float, list[str]], str]:
    """Fit what is safe in one region and return the exact remaining source."""
    _, _, box_width, box_height = _mapped_box(region, width, height)
    try:
        return _fit_mapped_text(value, region, box_width, box_height), ""
    except ValidationError:
        font_name = _mapped_font(region)
        size = max(7.0, float(region.get("minimum_font_size") or 7.0))
        leading = size * float(region.get("line_spacing") or 1.2)
        lines = _wrap_lines(value, font_name, size, max(1, box_width))
        capacity = max(0, int((box_height + 0.01) / leading))
        used = lines[:capacity]
        if not used:
            return (font_name, size, leading, []), value
        count = sum(len(re.sub(r"\s", "", line)) for line in used)
        end = 0
        for end, character in enumerate(value, 1):
            if not character.isspace():
                count -= 1
            if count <= 0:
                break
        remaining = value[end:].lstrip() if count <= 0 else value
        return (font_name, size, leading, used), remaining


def _draw_mapped_piece(
    c: canvas.Canvas,
    piece: tuple[str, float, float, list[str]],
    region: dict[str, Any],
    width: float,
    height: float,
) -> None:
    font_name, size, leading, lines = piece
    x, y, box_width, box_height = _mapped_box(region, width, height)
    c.setFont(font_name, size)
    c.setFillColor(INK)
    for index, line in enumerate(lines):
        baseline = y + box_height - size - index * leading
        align = str(region.get("align") or "left")
        if align == "center":
            c.drawCentredString(x + box_width / 2, baseline, line)
        elif align == "right":
            c.drawRightString(x + box_width, baseline, line)
        else:
            c.drawString(x, baseline, line)


def _build_mapped_overlay_pages(
    student: dict[str, Any],
    width: float,
    height: float,
    page_count: int,
    mapping: dict[str, Any],
    page_sizes: list[tuple[float, float]] | None = None,
) -> list[bytes]:
    register_fonts()
    regions = region_lookup(mapping)
    values = _mapped_values(student)
    flow_keys = ("teacher_comment",)
    pieces: dict[str, tuple[str, float, float, list[str]]] = {}
    continuation_pieces: dict[str, tuple[str, float, float, list[str]]] = {}
    continuation_body_regions: dict[str, dict[str, Any]] = {}
    continuation_regions = list(mapping.get("continuation_regions") or [])
    used_pages = {1}
    for key in flow_keys:
        region = regions.get(key)
        value = str(values.get(key) or "")
        if not region or not value.strip():
            continue
        pieces[key], remaining = _mapped_text_piece(value, region, width, height)
        used_pages.add(int(region.get("page") or 1))
        for continuation in sorted(
            (item for item in continuation_regions if item.get("source_key") == key),
            key=lambda item: (int(item.get("page") or 0), str(item.get("key") or "")),
        ):
            if not remaining.strip():
                break
            page = int(continuation.get("page") or 0)
            template_page = int(continuation.get("template_page") or 1)
            page_width, page_height = (
                page_sizes[template_page - 1] if page_sizes else (width, height)
            )
            body_region = copy.deepcopy(continuation)
            body_box = body_region["box"]
            heading_height = min(float(body_box["height"]) * .12, 28 / page_height)
            body_box["height"] = float(body_box["height"]) - heading_height
            piece, remaining = _mapped_text_piece(
                remaining, body_region, page_width, page_height
            )
            continuation_key = str(continuation["key"])
            continuation_pieces[continuation_key] = piece
            continuation_body_regions[continuation_key] = body_region
            used_pages.add(page)
        if continuation_pieces:
            last_key = next(reversed(continuation_pieces))
            last_piece = continuation_pieces[last_key]
            last_region = next(
                item for item in continuation_regions if str(item.get("key")) == last_key
            )
            source_page = int(last_region.get("template_page") or 1)
            source_width, source_height = page_sizes[source_page - 1] if page_sizes else (width, height)
            last_body_region = continuation_body_regions.get(last_key, last_region)
            _, _, _, box_height = _mapped_box(last_body_region, source_width, source_height)
            used_height = len(last_piece[3]) * last_piece[2]
            if used_height / max(1, box_height) < .5:
                raise ValidationError(
                    "The remaining teacher comment would fill less than half of its continuation panel. "
                    "It cannot fit the earlier page at 7 pt, so this report needs review."
                )
        if remaining.strip():
            raise ValidationError(
                f"{key.replace('_', ' ').title()} does not fit its configured continuation pages. "
                "Add another continuation area, enlarge the mapped area, or shorten the source text. "
                "No text was discarded."
            )
    homework_points = list(student.get("homework") or [])
    test_points = [
        {
            "label": exam.get("label", "Test"),
            "percent": (
                float(exam["score"]) / float(exam["max_score"]) * 100
                if exam.get("score") is not None and exam.get("max_score")
                else None
            ),
        }
        for exam in student.get("exams", [])
    ]
    for key, region in regions.items():
        if region["content_type"] == "chart" or str(values.get(key) or "").strip():
            used_pages.add(int(region.get("page") or 1))
    overlays: list[bytes] = []
    for page_number in range(1, max(used_pages) + 1):
        page_width, page_height = (
            page_sizes[page_number - 1]
            if page_sizes and page_number <= len(page_sizes)
            else next(
                (
                    page_sizes[int(region.get("template_page") or 1) - 1]
                    for region in continuation_regions
                    if int(region.get("page") or 0) == page_number and page_sizes
                ),
                (width, height),
            )
        )
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(page_width, page_height), pageCompression=1)
        for key, region in regions.items():
            if int(region.get("page") or 1) != page_number:
                continue
            if region["content_type"] == "text":
                if key in pieces:
                    _draw_mapped_piece(c, pieces[key], region, page_width, page_height)
                else:
                    _draw_mapped_text(
                        c, values.get(key, ""), region,
                        width=page_width, height=page_height,
                    )
                continue
            x, y, chart_width, chart_height = _mapped_box(
                region, page_width, page_height
            )
            padding = min(chart_width, chart_height) * 0.04
            if key == "homework_chart":
                _draw_line_chart(
                    c,
                    homework_points,
                    x=x + padding,
                    y=y + padding,
                    width=max(1, chart_width - padding * 2),
                    height=max(1, chart_height - padding * 2),
                    value_key="score",
                    label_key="label",
                )
            elif key == "weekly_test_chart":
                _draw_line_chart(
                    c,
                    test_points,
                    x=x + padding,
                    y=y + padding,
                    width=max(1, chart_width - padding * 2),
                    height=max(1, chart_height - padding * 2),
                    value_key="percent",
                    label_key="label",
                )
        for continuation in continuation_regions:
            if int(continuation.get("page") or 0) != page_number:
                continue
            piece = continuation_pieces.get(str(continuation.get("key") or ""))
            if piece:
                continuation_key = str(continuation.get("key") or "")
                body_region = continuation_body_regions.get(continuation_key, continuation)
                _draw_mapped_piece(
                    c, piece, body_region, page_width, page_height
                )
                x, y, box_width, box_height = _mapped_box(
                    body_region, page_width, page_height
                )
                c.setFillColor(INK)
                c.setFont(register_fonts(), 9)
                c.drawString(x, y + box_height + 8, "Teacher comment (continued)")
        # ReportLab omits a page entirely when it has no drawing operations. A
        # mapped multi-page template may intentionally place every region on
        # page one, so explicitly finalize every overlay page.
        c.showPage()
        c.save()
        overlays.append(buffer.getvalue())
    return overlays


def generate_student_pdf(
    student: dict[str, Any],
    template_path: Path,
    output_path: Path,
    mapping: dict[str, Any] | None = None,
) -> Path:
    if mapping and mapping.get("layout", "uploaded_pdf") == "uploaded_pdf":
        from backend.adaptive_reports import generate_adaptive_pdf
        if generate_adaptive_pdf(student, template_path, output_path, mapping):
            return output_path
    if mapping and mapping.get("layout") == "royal_ib_weekly":
        from backend.weekly_report import generate_weekly_report
        return generate_weekly_report(
            student, output_path, font=register_fonts(),
            font_size=float((mapping.get("typography") or {}).get("font_size", 10)),
        )
    template_reader = PdfReader(template_path)
    if template_reader.is_encrypted or not template_reader.pages:
        raise ValidationError("The active report template is unavailable.")
    first_box = template_reader.pages[0].mediabox
    width, height = float(first_box.width), float(first_box.height)
    overlays = (
        _build_mapped_overlay_pages(
            student,
            width,
            height,
            len(template_reader.pages),
            mapping,
            page_sizes=[(float(page.mediabox.width), float(page.mediabox.height)) for page in template_reader.pages],
        )
        if mapping
        else _build_overlay_pages(student, width, height)
    )
    writer = PdfWriter()
    for index, overlay_bytes in enumerate(overlays):
        if mapping and index < len(template_reader.pages):
            background = copy.deepcopy(template_reader.pages[index])
        elif mapping:
            continuation = next(
                (
                    region
                    for region in mapping.get("continuation_regions") or []
                    if int(region.get("page") or 0) == index + 1
                ),
                None,
            )
            if not continuation:
                raise ValidationError(
                    f"Report page {index + 1} has no configured continuation area."
                )
            source_page = int(continuation.get("template_page") or 1) - 1
            if source_page < 0 or source_page >= len(template_reader.pages):
                raise ValidationError(
                    "The configured continuation source page is not in the blank template."
                )
            background = copy.deepcopy(template_reader.pages[source_page])
        elif index == 0:
            background = copy.deepcopy(template_reader.pages[0])
        elif len(template_reader.pages) > 1:
            background = copy.deepcopy(template_reader.pages[min(index, len(template_reader.pages) - 1)])
        else:
            background = _generated_continuation_page(width, height, index + 1)
        writer.add_page(background)
        overlay = PdfReader(io.BytesIO(overlay_bytes)).pages[0]
        writer.pages[-1].merge_page(overlay)
        writer.pages[-1].pop(NameObject("/Annots"), None)
    writer.add_metadata(
        {
            "/Title": f"Student Report - {student.get('name', '')}",
            "/Author": "Brightline Reports",
            "/Subject": str(student.get("class_name", "")),
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as stream:
        writer.write(stream)
    return output_path


def student_payload(student: dict[str, Any], exams: list[dict[str, Any]]) -> dict[str, Any]:
    payload = dict(student)
    payload["homework"] = json.loads(payload.pop("homework_json") or "[]")
    payload["exams"] = exams
    payload["comments_by_section"] = report_comment_sections(exams)
    return payload
