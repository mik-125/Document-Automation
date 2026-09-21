"""Royal IB weekly reports with readable comments and compact continuation pages."""
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

from backend.comment_sections import report_comment_sections, split_comment_sections
from backend.report_charts import draw_homework_chart, draw_test_chart

GREEN = HexColor("#00B451")
INK = HexColor("#111111")
WIDTH, HEIGHT = 540, 780
LEFT, RIGHT = 31, 509


def _paragraph(text: str, font: str, size: float) -> Paragraph:
    style = ParagraphStyle("comment", fontName=font, fontSize=size,
                           leading=size * 1.28, textColor=INK, wordWrap="CJK")
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


def _frame(c: Any, student: dict[str, Any], font: str) -> None:
    c.setFillColor(white)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    c.setFillColor(GREEN)
    c.rect(0, HEIGHT - 20, WIDTH, 20, fill=1, stroke=0)
    c.rect(WIDTH - 17, 0, 17, HEIGHT, fill=1, stroke=0)
    _fit_label(c, str(student.get("class_name") or ""), LEFT + 15, 701, 450, font, 17)
    c.setFillColor(GREEN)
    c.rect(LEFT, 652, 337, 40, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 31)
    c.setFillColor(white)
    c.drawString(LEFT + 15, 658, "WEEKLY REPORT")
    period = str(student.get("period_label") or "")
    if student.get("period_start") and student.get("period_end"):
        period += f"  {student['period_start']} - {student['period_end']}"
    _fit_label(c, period, LEFT, 633, 470, font, 8, 7)
    for x, label, value in [
        (LEFT, "성 명", re.sub(r"^\s*\(\s*ON\s*\)\s*", "", str(student.get("name") or ""), flags=re.I)),
        (275, "학 교", str(student.get("school") or "")),
    ]:
        c.setFillColor(HexColor("#999999"))
        c.rect(x - 3, 577, 241, 29, fill=1, stroke=0)
        c.setFillColor(HexColor("#F0F6EB"))
        c.rect(x, 589, 233, 27, fill=1, stroke=0)
        c.setFont(font, 10)
        c.setFillColor(GREEN)
        c.drawString(x + 16, 598, label)
        _fit_label(c, value, x + 79, 598, 144, font, 10)
    c.setFillColor(INK)
    c.setFont("Times-Bold", 14)
    c.drawCentredString(WIDTH / 2, 25, "ROYAL IB")


def _section(c: Any, title: str, top: float, bottom: float, font: str,
             header_height: float = 24) -> None:
    c.setStrokeColor(GREEN)
    c.setLineWidth(.6)
    c.rect(LEFT, bottom, RIGHT - LEFT, top - bottom, fill=0, stroke=1)
    c.setFillColor(GREEN)
    c.rect(LEFT, top - header_height, RIGHT - LEFT, header_height, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont(font, 11)
    c.drawCentredString((LEFT + RIGHT) / 2, top - header_height / 2 - 4, title)


def _continuation(c: Any, pending: list[Paragraph], title: str,
                  font: str) -> list[Paragraph]:
    """Continue near the page top without repeating the first-page masthead."""
    content_top, width = 722, RIGHT - LEFT - 20
    remaining_height = sum(paragraph.wrap(width, HEIGHT)[1] + 7 for paragraph in pending)
    bottom = max(61, content_top - remaining_height - 12)
    c.setFillColor(white)
    c.rect(0, 0, WIDTH, HEIGHT, fill=1, stroke=0)
    _section(c, title, 776, bottom, font, header_height=31)
    return _flow(c, pending, LEFT + 10, content_top, width, bottom + 10)


def generate_weekly_report(student: dict[str, Any], output: Path, *, font: str,
                           font_size: float = 10) -> Path:
    # Prefer a regular sans-serif Korean font close to the supplied sample.
    body_path = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
    if body_path.is_file():
        if "BrightlineBody" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("BrightlineBody", str(body_path)))
        font = "BrightlineBody"
    exams = student.get("exams", [])
    if "comments_by_section" in student or any("comment_section" in exam for exam in exams):
        selected = student.get("comments_by_section") or report_comment_sections(exams)
        parts = {"homework": str(selected.get("homework") or ""),
                 "test": str(selected.get("weekly_test") or ""),
                 "general": str(selected.get("general") or "")}
    else:
        # Retain compatibility for old standalone payloads without section metadata.
        all_sections = [split_comment_sections(str(exam["comment"])) for exam in exams
                        if str(exam.get("comment") or "").strip()]
        parts = {key: "\n\n".join(section[key] for section in all_sections if section[key].strip())
                 for key in ("homework", "test", "general")}
    output.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output), pagesize=(WIDTH, HEIGHT), pageCompression=1)
    c.setTitle(f"Student Report - {student.get('name', '')}")
    c.setAuthor("Brightline Reports")
    _frame(c, student, font)
    _section(c, "과제이행도", 551, 398, font)
    draw_homework_chart(c, student.get("homework", []), x=LEFT + 8, y=414,
                        width=200, height=109, font_name=font)
    completed = int(student.get("homework_completed") or 0)
    missed = int(student.get("homework_incomplete") or 0)
    summary = f"완료 {completed} · 미완료 {missed} · 제외 {int(student.get('homework_excluded') or 0)}"
    _fit_label(c, summary, LEFT + 16, 404, 200, font, 7, 7)
    homework = [_paragraph(parts["homework"], font, font_size)] if parts["homework"].strip() else []
    homework_overflow = _flow(c, homework, 244, 515, 255, 408)
    if not homework:
        _flow(c, [_paragraph("과제 코멘트가 별도로 제공되지 않았습니다.", font, 9)], 244, 515, 255, 408)
    test = [_paragraph(parts["test"], font, font_size)] if parts["test"].strip() else []
    general = [_paragraph(parts["general"], font, font_size)] if parts["general"].strip() else []
    # Short weekly feedback leaves room for a separate general section on page one.
    # Longer sections continue under their own heading, without mixing categories.
    general_on_first = bool(general) and not homework_overflow and (
        not test or test[0].wrap(255, 124)[1] <= 124
    )
    _section(c, "위클리 테스트", 380, 201 if general_on_first else 61, font, header_height=31)
    history = student.get("test_history", [exam for exam in exams if exam.get("score") is not None])
    draw_test_chart(c, history, x=LEFT + 8, y=217, width=200, height=118, font_name=font)
    if len(history) == 1:
        _fit_label(c, "1주 기록 · 다음 주부터 추이 표시", LEFT + 16, 208, 200, font, 7, 7)
    test_pending = _flow(c, test, 244, 335, 255, 211)
    if general_on_first:
        _section(c, "종합 코멘트", 190, 61, font)
        general = _flow(c, general, LEFT + 10, 154, RIGHT - LEFT - 20, 71)
    else:
        test_pending = _flow(c, test_pending, LEFT + 7, 190, RIGHT - LEFT - 14, 71)
    c.showPage()
    for title, pending in (("과제이행도 (계속)", homework_overflow),
                           ("위클리 테스트", test_pending), ("종합 코멘트", general)):
        while pending:
            before = pending
            pending = _continuation(c, pending, title, font)
            if pending == before:
                raise ValueError("The comment cannot fit the report page. Check its formatting.")
            c.showPage()
    c.save()
    return output
