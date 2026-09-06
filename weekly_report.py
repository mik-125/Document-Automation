"""Clean Royal IB weekly reports with readable, bounded text and real data."""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from reportlab.lib.colors import HexColor, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from comment_sections import split_comment_sections
from report_charts import draw_homework_chart, draw_test_chart

GREEN = HexColor("#00B451")
INK = HexColor("#111111")
WIDTH, HEIGHT = 540, 780
LEFT, RIGHT = 31, 509


def _paragraph(text: str, font: str, size: float) -> Paragraph:
    style = ParagraphStyle("comment", fontName=font, fontSize=size,
                           leading=size * 1.45, textColor=INK, wordWrap="CJK")
    return Paragraph(html.escape(text.strip()).replace("\n", "<br/>"), style)


def _flow(c: Any, paragraphs: list[Paragraph], x: float, top: float,
          width: float, bottom: float) -> list[Paragraph]:
    """Draw only what fits, returning complete overflow for the next area/page."""
    pending = list(paragraphs)
    while pending and top - bottom >= 15:
        paragraph = pending.pop(0)
        _, height = paragraph.wrap(width, top - bottom)
        if height > top - bottom:
            parts = paragraph.split(width, top - bottom)
            if not parts:
                pending.insert(0, paragraph)
                break
            paragraph, *rest = parts
            pending[0:0] = rest
            _, height = paragraph.wrap(width, top - bottom)
        paragraph.drawOn(c, x, top - height)
        top -= height + 7
    return pending


def _fit_label(c: Any, text: str, x: float, y: float, width: float,
               font: str, preferred: float, minimum: float = 9) -> None:
    size = preferred
    while stringWidth(text, font, size) > width and size > minimum:
        size -= .25
    if stringWidth(text, font, size) > width:
        raise ValueError("The class or student label is too long for this report layout.")
    c.setFont(font, size)
    c.setFillColor(INK)
    c.drawString(x, y, text)


def _frame(c: Any, student: dict[str, Any], font: str, number: int) -> None:
    c.setFillColor(white)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    c.setFillColor(GREEN)
    c.rect(0, HEIGHT - 14, WIDTH, 14, fill=1, stroke=0)
    c.rect(WIDTH - 13, 0, 13, HEIGHT, fill=1, stroke=0)
    _fit_label(c, str(student.get("class_name") or ""), LEFT + 15, 708, 450, font, 17)
    c.setFillColor(GREEN)
    c.rect(LEFT, 659, 337, 39, fill=1, stroke=0)
    bold_path = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
    title_font = "Helvetica-Bold"
    if bold_path.is_file():
        if "BrightlineHeading" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("BrightlineHeading", str(bold_path)))
        title_font = "BrightlineHeading"
    c.setFont(title_font, 31)
    c.setFillColor(white)
    c.drawString(LEFT + 10, 670, "WEEKLY REPORT")
    period = str(student.get("period_label") or "")
    if student.get("period_start") and student.get("period_end"):
        period += f"  {student['period_start']} - {student['period_end']}"
    _fit_label(c, period, LEFT, 639, 470, font, 8, 7)
    for x, label, value in [
        (LEFT, "성 명", re.sub(r"^\s*\(\s*ON\s*\)\s*", "", str(student.get("name") or ""), flags=re.I)),
        (275, "학 교", str(student.get("school") or "")),
    ]:
        c.setFillColor(HexColor("#999999"))
        c.rect(x - 3, 601, 237, 28, fill=1, stroke=0)
        c.setFillColor(HexColor("#F0F6EB"))
        c.rect(x, 608, 233, 27, fill=1, stroke=0)
        c.setFont(font, 10)
        c.setFillColor(GREEN)
        c.drawString(x + 12, 617, label)
        _fit_label(c, value, x + 79, 617, 144, font, 10)
    c.setFillColor(INK)
    c.setFont("Times-Bold", 14)
    c.drawCentredString(WIDTH / 2, 16, "ROYAL IB")
    c.setFont(font, 7)
    c.drawRightString(RIGHT, 17, str(number))


def _section(c: Any, title: str, top: float, bottom: float, font: str) -> None:
    c.setStrokeColor(GREEN)
    c.setLineWidth(.6)
    c.rect(LEFT, bottom, RIGHT - LEFT, top - bottom, fill=0, stroke=1)
    c.setFillColor(GREEN)
    c.rect(LEFT, top - 23, RIGHT - LEFT, 23, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(font, 11)
    c.drawCentredString((LEFT + RIGHT) / 2, top - 16, title)


def generate_weekly_report(student: dict[str, Any], output: Path, *, font: str,
                           font_size: float = 10) -> Path:
    all_sections = [split_comment_sections(str(exam["comment"])) for exam in student.get("exams", [])
                    if str(exam.get("comment") or "").strip()]
    parts = {key: "\n\n".join(section[key] for section in all_sections if section[key].strip())
             for key in ("homework", "test", "general")}
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output), pagesize=(WIDTH, HEIGHT), pageCompression=1)
    c.setTitle(f"Student Report - {student.get('name', '')}")
    c.setAuthor("Brightline Reports")
    _frame(c, student, font, 1)
    _section(c, "과제이행도", 574, 405, font)
    draw_homework_chart(c, student.get("homework", []), x=LEFT + 8, y=421,
                        width=200, height=120, font_name=font)
    completed = int(student.get("homework_completed") or 0)
    missed = int(student.get("homework_incomplete") or 0)
    summary = f"완료 {completed} · 미완료 {missed} · 제외 {int(student.get('homework_excluded') or 0)}"
    _fit_label(c, summary, LEFT + 16, 413, 200, font, 7, 7)
    homework = [_paragraph(parts["homework"], font, font_size)] if parts["homework"].strip() else []
    if homework:
        homework.insert(0, _paragraph("학습·과제 코멘트", font, 9))
    homework_overflow = _flow(c, homework, 249, 541, 250, 415)
    if not homework:
        _flow(c, [_paragraph("과제 코멘트가 별도로 제공되지 않았습니다.", font, 9)], 249, 541, 250, 415)
    _section(c, "위클리 테스트", 379, 35, font)
    history = student.get("test_history", [exam for exam in student.get("exams", []) if exam.get("score") is not None])
    draw_test_chart(c, history, x=LEFT + 8, y=217, width=200, height=128, font_name=font)
    if len(history) == 1:
        _fit_label(c, "1주 기록 · 다음 주부터 추이 표시", LEFT + 16, 208, 200, font, 7, 7)
    test = [_paragraph(parts["test"], font, font_size)] if parts["test"].strip() else []
    pending = _flow(c, test, 249, 345, 250, 211)
    if not test:
        _flow(c, [_paragraph("테스트 코멘트가 별도로 제공되지 않았습니다.", font, 9)], 249, 345, 250, 211)
    if homework_overflow:
        pending += [_paragraph("과제 코멘트 (계속)", font, 10)] + homework_overflow
    if parts["general"].strip():
        pending += [_paragraph("종합 코멘트", font, 10), _paragraph(parts["general"], font, font_size)]
    pending = _flow(c, pending, LEFT + 10, 195, RIGHT - LEFT - 20, 45)
    c.showPage()
    number = 2
    while pending:
        _frame(c, student, font, number)
        _section(c, "코멘트 (계속)", 574, 35, font)
        before = pending
        pending = _flow(c, pending, LEFT + 12, 539, RIGHT - LEFT - 24, 47)
        if pending == before:
            raise ValueError("The comment cannot fit the report page. Check its formatting.")
        c.showPage()
        number += 1
    c.save()
    return output
