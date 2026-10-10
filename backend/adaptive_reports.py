"""Fit source feedback into the ORIGINAL PDF's detected panels, across pages.

Samples inform content roles and body font sizes, never background page design.
No imported records or saved mappings are changed by this rendering path.
"""
from __future__ import annotations

import copy
import io
import re
import statistics
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf as fitz
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from backend.pdf_content import labeled_panels, page_lines
from backend.sample_content import SECTION_REGIONS

MINIMUM_READABLE_SIZE = 7.0
BODY_START_SIZE = 10.5
WEEKLY_TEST_MINIMUM_SIZE = 10.0
CHART_HEIGHT = 150.0
HANGING_PUNCTUATION = set(".,!?;:)]}’”」』…、。")
MISSING_TEST_FEEDBACK = (
    "학생은 이번 위클리 테스트를 응시하지 않았기에 정확한 이해도 판단이 어렵습니다. "
    "위클리 테스트를 나중에 한 번 풀어보시는 것을 추천드립니다."
)


def display_name(name):
    """Display-only: drop an online-class "(ON)" prefix from the student name."""
    return re.sub(r"^\(ON\)\s*", "", str(name or ""), flags=re.I).strip()


def display_class(name):
    """Display-only: drop bracketed tags such as "[25FA]" from the class name."""
    return re.sub(r"\s+", " ", re.sub(r"\s*\[[^\]]*\]", "", str(name or ""))).strip()


def _drawable(text, glyph_maps):
    def usable(value):
        return all(ch.isspace() or all(ord(ch) in glyphs for glyphs in glyph_maps) for ch in value)
    def substitute(ch):
        if usable(ch):
            return ch
        alternative = "–" if ch == "−" else unicodedata.normalize("NFKC", ch)
        return alternative if alternative and usable(alternative) else ch
    return "".join(substitute(ch) for ch in text)


def _near(a, b, tolerance=2):
    return sum(abs(x-y) for x,y in zip(a,b)) < tolerance


def _body_font(observations):
    from backend.report_engine import register_fonts
    fonts = Counter(f for item in observations for f in item.get("body_fonts",[]))
    aliases = {"Arial":"Helvetica", "TimesNewRomanPSMT":"Times-Roman",
               "CourierNewPSMT":"Courier"}
    for font,_ in fonts.most_common():
        candidate = aliases.get(font,font)
        if candidate in {
            "Helvetica", "Helvetica-Bold", "Times-Roman", "Times-Bold",
            "Courier", "Courier-Bold",
        }:
            continue
        if candidate in pdfmetrics.getRegisteredFontNames():
            return candidate
    # Subset fonts cannot safely supply letters absent from the example.
    return register_fonts()


def _frames(doc, mapping, guide):
    frames = []
    regions = {r["key"]:r for r in mapping.get("regions",[])}
    for number,page in enumerate(doc):
        if page.rotation:
            continue
        panels = labeled_panels(page)
        drawings = page.get_drawings()
        for panel in panels:
            section = panel["section"]
            observations = [s for s in guide.get("sections",[]) if s["section"]==section and s.get("reliable",True)]
            rect = fitz.Rect(panel["box"])
            rect = fitz.Rect(rect.x0+8,rect.y0+7,rect.x1-8,rect.y1-7)
            border = next((d for d in drawings if d.get("color") is not None
                           and _near(d["rect"],panel["box"])),None)
            heading_box = fitz.Rect(panel["heading"]["box"])
            bars = [d["rect"] for d in drawings if d.get("fill") is not None
                    and d["rect"].contains(heading_box) and d["rect"].width > page.rect.width*.25
                    and d["rect"].height < 60]
            bar = min(bars,key=lambda r:r.get_area()) if bars else heading_box+(-4,-4,4,4)
            key = SECTION_REGIONS[section]
            mapped = regions.get(key,{})
            sizes = [float(s["preferred_font_size"]) for s in observations if s.get("preferred_font_size")]
            preferred = statistics.median(sizes) if sizes else float(mapped.get("font_size", 9.5))
            target_fills = [float(s["text_fill_ratio"]) for s in observations if s.get("text_fill_ratio") is not None]
            frames.append({"section":section,"page":number,"box":list(rect),
                           "chart":None,"panel":list(panel["box"]),"heading_bar":list(bar),
                           "border_color":list(border["color"]) if border else [0,0,0],
                           "border_width":float(border.get("width") or 1) if border else 1.0,
                           "font":_body_font(observations),"preferred":preferred,
                           "minimum":MINIMUM_READABLE_SIZE,
                           "target_fill":statistics.median(target_fills) if target_fills else .85,
                           "page_width":page.rect.width,
                           "heading":panel["heading"],
                           "heading_box":panel["heading"]["box"],
                           "generated":False})
    frames.sort(key=lambda frame:(frame["section"],frame["page"]))
    return frames


def _plan_panels(frames, student, *, general_text, missing_test_feedback):
    """Place charts and hide an empty teacher panel. Returns (frames, charts, hidden)."""
    first = {}
    for frame in frames:
        first.setdefault(frame["section"], frame)
    hw, wt, gen = first.get("homework"), first.get("weekly_test"), first.get("general")
    hidden = None
    if (wt and gen and not general_text.strip() and gen["page"] == wt["page"]
            and gen["panel"][1] > wt["panel"][3]):
        # Like the academy sample: one tall Weekly Test panel, no empty comment box.
        hidden = {"page": gen["page"], "heading_box": gen["heading_box"],
                  "panel": [min(wt["panel"][0], gen["panel"][0]), wt["panel"][1],
                            max(wt["panel"][2], gen["panel"][2]), gen["panel"][3]],
                  "cover": list(fitz.Rect(gen["panel"]) | fitz.Rect(gen["heading_bar"])),
                  "border_color": wt["border_color"], "border_width": wt["border_width"]}
        wt["panel"] = hidden["panel"]
        wt["box"][3] = gen["box"][3]
        frames = [f for f in frames if not (f["section"] == "general" and f["page"] == gen["page"])]
    charts = []
    if hw and student.get("homework"):
        rect = fitz.Rect(hw["box"])
        charts.append({"section": "homework", "page": hw["page"],
                       "box": [rect.x0, rect.y0, rect.x0+rect.width*.48, rect.y1]})
        hw["box"][0] = rect.x0+rect.width*.53
    if wt and student.get("test_series") and not missing_test_feedback:
        rect = fitz.Rect(wt["box"])
        chart_bottom = rect.y0+min(CHART_HEIGHT, rect.height)
        charts.append({"section": "weekly_test", "page": wt["page"],
                       "box": [rect.x0, rect.y0, rect.x0+rect.width*.48, chart_bottom]})
        lanes = [{**wt, "box": [rect.x0+rect.width*.53, rect.y0, rect.x1, chart_bottom]}]
        if rect.y1-(chart_bottom+6) >= 12:
            lanes.append({**wt, "box": [rect.x0, chart_bottom+6, rect.x1, rect.y1]})
        index = frames.index(wt)
        frames = frames[:index]+lanes+frames[index+1:]
    return frames, charts, hidden


def _weekly_continuation_frame(doc, frames):
    """A full-width page built from the original page artwork and WT heading style."""
    wt = next((f for f in frames if f["section"] == "weekly_test"), None)
    if not wt:
        return None
    page = doc[wt["page"]]
    height = page.rect.height
    # The new panel ends where the original page's last panel ended, which the
    # template already keeps clear of its footer artwork.
    bottoms = [panel["box"][3] for panel in labeled_panels(page)]
    footer_top = min(max(bottoms) if bottoms else height*.9, height*.95)
    bar = fitz.Rect(wt["heading_bar"])
    original_panel = fitz.Rect(wt["panel"])
    top = height*.05
    bar_dest = fitz.Rect(bar.x0, top, bar.x1, top+bar.height)
    panel = fitz.Rect(original_panel.x0, bar_dest.y1+(original_panel.y0-bar.y1),
                      original_panel.x1, footer_top)
    return {**wt, "box": [panel.x0+8, panel.y0+10, panel.x1-8, panel.y1-7], "chart": None,
            "weekly_continuation": {"bar_source": list(bar), "bar": list(bar_dest),
                                    "panel": list(panel), "clear_bottom": footer_top+1}}


def _wrap_feedback(text, font, size, width):
    """Keep Latin words together; CJK may break between glyphs. No rewriting."""
    from backend.report_engine import ValidationError
    lines=[]
    for paragraph in text.splitlines() or [""]:
        current=""
        for token in re.findall(r"[A-Za-z0-9]+(?:[._'’/-][A-Za-z0-9]+)*|[ \t]+|.",paragraph):
            if token.isspace():token=" "
            if pdfmetrics.stringWidth(token,font,size)>width:
                tokens=list(token)
            else:
                tokens=[token]
            for part in tokens:
                if pdfmetrics.stringWidth(part,font,size)>width:
                    raise ValidationError("The original comment panel is too narrow for readable text.")
                # Closing punctuation may hang slightly past the margin rather
                # than start a line on its own (Korean typesetting practice).
                if (current and part not in HANGING_PUNCTUATION
                        and pdfmetrics.stringWidth(current+part,font,size)>width):
                    lines.append(current.rstrip());current=part.lstrip()
                else:
                    current+=part
        lines.append(current.rstrip())
    return lines


def _flow_at_size(text, frames, size):
    """Line-wrap exact remaining text and consume slices once, never summarize."""
    remaining = text
    draws = []
    for frame in frames:
        rect = fitz.Rect(frame["box"])
        leading = size*1.18
        capacity = max(0,int((rect.height-size)//leading)+1)
        if not capacity:
            continue
        lines = _wrap_feedback(remaining,frame["font"],size,rect.width)
        used = lines[:capacity]
        draws.append({**frame,"lines":used,"size":size,"leading":leading})
        # Reflow unconsumed lines into the next box, which may have a different
        # width. Keep paragraph boundaries; line breaks are whitespace only.
        if len(lines) <= capacity:
            remaining = ""
        else:
            # Match consumed visible characters back into source, ignoring only
            # wrapping whitespace. This preserves every source word/punctuation.
            count = sum(len(re.sub(r"\s","",line)) for line in used)
            end = 0
            for end,ch in enumerate(remaining,1):
                if not ch.isspace():count-=1
                if count<=0:break
            remaining = remaining[end:].lstrip() if count<=0 else remaining
        if not remaining:
            break
    return draws,remaining


def _filled_fraction(draw):
    used = max(0, (len(draw["lines"])-1)*draw["leading"] + draw["size"])
    return used / max(1, fitz.Rect(draw["box"]).height)


def _section_plan(text, group, frames, page_count, clone_offset, size, allow_extra,
                  weekly_continuation=None):
    """Pure trial layout. Rejected font/page trials never leak into the output."""
    from backend.report_engine import ValidationError
    fitted, remaining = _flow_at_size(text, group, size)
    if group and group[0]["section"] == "general":
        label = group[0].get("heading", {}).get("text", group[0]["section"])
        continued = label + (" (계속)" if re.search(r"[가-힣]", label) else " (continued)")
        for draw in fitted:
            if draw["page"] > group[0]["page"] and draw.get("heading"):
                draw["continuation_heading"] = continued
    clones = []
    while remaining and allow_extra:
        if clone_offset+len(clones)>=50:
            raise ValidationError("This report exceeds 50 continuation pages. Review the source comments.")
        if group[0]["section"] == "weekly_test":
            if not weekly_continuation:
                raise ValidationError(
                    "The blank template has no Weekly Test heading to use for continuation pages. "
                    "Review the template layout; no text was omitted.")
            continuation = copy.deepcopy(weekly_continuation)
            clones.append(continuation["page"])
            continuation["page"] = page_count+clone_offset+len(clones)-1
            added,left = _flow_at_size(remaining,[continuation],size)
            if left==remaining:
                raise ValidationError("The Weekly Test continuation panel is too small for readable text.")
            fitted.extend(added)
            remaining = left
            continue
        candidates = [
            frame for frame in frames
            if frame["section"] == "general"
            and frame.get("heading")
            and not frame.get("chart")
            and fitz.Rect(frame["box"]).width >= float(frame.get("page_width") or 1) * .70
        ]
        if not candidates:
            raise ValidationError(
                "The blank template has no full-width teacher-comment page to use for continuation. "
                "Review the template layout; no text was omitted."
            )
        source = max(candidates,key=lambda f:fitz.Rect(f["box"]).get_area())
        continuation = copy.deepcopy(source)
        if continuation.get("heading"):
            label = group[0].get("heading",{}).get("text",group[0]["section"])
            continuation["continuation_heading"] = label + (" (계속)" if re.search(r"[가-힣]",label) else " (continued)")
        continuation["section"] = group[0]["section"]
        continuation["font"] = group[0]["font"]
        clones.append(continuation["page"])
        continuation["page"] = page_count+clone_offset+len(clones)-1
        continuation["chart"] = None
        added,left = _flow_at_size(remaining,[continuation],size)
        if left==remaining:
            raise ValidationError("The original comment panel is too small for readable text.")
        fitted.extend(added)
        remaining = left
    return fitted, clones, remaining


def layout_comments(values, frames, page_count, *, single_page=False, weekly_continuation=None):
    """Fit each section; Weekly Test text stays >= 10 pt and continues on new pages."""
    from backend.report_engine import ValidationError
    draws, clones, receipt = [], [], []
    for section,key in SECTION_REGIONS.items():
        group = [f for f in frames if f["section"]==section and (not single_page or f["page"]==0)]
        if section == "weekly_test" and group:
            group = [f for f in group if f.get("panel") == group[0].get("panel")
                     and f["page"] == group[0]["page"]]
        text = values.get(key,"")
        # Imported PDF line endings are usually visual wraps, not paragraphs.
        # Keep blank paragraphs, date entries and bullets; reflow other wraps.
        text = re.sub(r"(?<!\n)\n(?!\s*\n|\s*(?:\d{1,2}[/.-]\d{1,2}|[-•▪]))", " ", text)
        if not text:
            continue
        if not group:
            raise ValidationError(f"The original PDF needs a confirmed {section} text region before automatic fitting.")
        minimum = WEEKLY_TEST_MINIMUM_SIZE if section == "weekly_test" else MINIMUM_READABLE_SIZE
        preferred = max(minimum,min(f["preferred"] for f in group))
        allow_extra = not single_page and section in {"general", "weekly_test"}
        size = (BODY_START_SIZE if section in {"homework", "weekly_test"}
                else min(36.0, round(preferred + .75, 2)))
        # First try to use the original pages at normal body sizes. Only use a
        # continuation after this pass; smaller fonts can then eliminate a short
        # final page. All trials start from the complete original wording.
        while size > minimum:
            _,remaining = _flow_at_size(text,group,size)
            if not remaining:break
            size = max(minimum,round(size-.25,2))
        if section == "weekly_test" and allow_extra and _flow_at_size(text,group,size)[1]:
            size = BODY_START_SIZE  # Continuation pages keep the normal body size.
        while True:
            fitted, trial_clones, remaining = _section_plan(
                text,group,frames,page_count,len(clones),size,allow_extra,weekly_continuation)
            # The last teacher-comment page must be at least half occupied.
            # Even an original second template page is optional when sparse.
            sparse = (section == "general" and fitted and fitted[-1]["page"] > 0
                      and _filled_fraction(fitted[-1]) < .5)
            if not remaining and not sparse:break
            if size <= minimum:
                raise ValidationError(
                    f"The {section.replace('_', ' ')} comment cannot fit under the page rules at {minimum:g} pt. "
                    "Review the source text or enlarge the original comment region; no text was omitted.")
            size = max(minimum,round(size-.25,2))
        draws.extend(fitted)
        clones.extend(trial_clones)
        pages = [f["page"]+1 for f in fitted]
        receipt.append({"section":section,"font_size":size,"pages":pages,
                        "page_fills":[round(_filled_fraction(item),4) for item in fitted],
                        "last_page_fill":round(_filled_fraction(fitted[-1]),4) if fitted else 0,
                        "source_characters":len(text),"wording":"preserved"})
    return draws,clones,receipt


def _identity_fields(page, student):
    """Replace explicit placeholder values without changing labels/backgrounds."""
    lines = page_lines(page)
    replacements = []
    labels = {"성명":"name","이름":"name","student name":"name", "학교":"school","school":"school",
              "과목":"class_name","class":"class_name","subject":"class_name"}
    shown = {"name": display_name(student.get("name")),
             "class_name": display_class(student.get("class_name")),
             "school": str(student.get("school") or "")}
    for line in lines:
        key=labels.get(line["text"].strip().casefold())
        if not key:continue
        box=fitz.Rect(line["box"])
        neighbors=[r for r in lines if abs(r["box"][1]-box.y0)<6 and r["box"][0]>box.x1+3
                   and r["text"].strip().casefold() not in labels]
        if neighbors:
            target=min(neighbors,key=lambda r:r["box"][0])
            replacements.append((target,shown[key]))
    # Common original templates contain a demonstration class above the title.
    # Restrict this to a schedule-style class line, never arbitrary narrative.
    for line in lines:
        if line["box"][1]<page.rect.height*.2 and re.search(r"\(\d+차\).*\b(?:DP\d|IB)\b",line["text"]):
            replacements.append((line,shown["class_name"]))
    return replacements


def _rgb(c, color, stroke=False):
    values = [float(v) for v in (list(color or [0,0,0])+[0,0,0])[:3]]
    (c.setStrokeColorRGB if stroke else c.setFillColorRGB)(*values)


def _draw_chart(c, chart, student, height):
    from backend.report_charts import draw_homework_status_chart, draw_weekly_test_chart
    from backend.report_engine import register_fonts
    box = fitz.Rect(chart["box"])
    name, class_name = str(student.get("name") or ""), str(student.get("class_name") or "")
    geometry = (box.x0+2, height-box.y1+2, box.width-4, box.height-4)
    if chart["section"] == "homework":
        draw_homework_status_chart(
            c, *geometry, list(student.get("homework") or []), f"{name} (과제 - {class_name})",
            f"기간: {student.get('period_start') or ''} ~ {student.get('period_end') or ''}",
            font_name=register_fonts())
    else:
        series = list(student.get("test_series") or [])
        draw_weekly_test_chart(
            c, *geometry, series, f"{name} (WT - {class_name})",
            f"기간: {series[0].get('start_date') or ''} ~ {series[-1].get('end_date') or ''}",
            font_name=register_fonts())


def _weekly_continuation_page(background_bytes, source_index, layout):
    """Original page artwork minus its content area, plus the copied WT heading bar."""
    with fitz.open(stream=background_bytes,filetype="pdf") as source, \
            fitz.open(stream=background_bytes,filetype="pdf") as page_doc:
        page = page_doc[source_index]
        # Remove text, images, and drawings wholly inside the title/identity/panel
        # area; full-page backgrounds, edge bars, and the footer logo remain.
        page.add_redact_annot(fitz.Rect(0,0,page.rect.width,layout["clear_bottom"]),fill=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS,
                              graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED)
        page.draw_rect(fitz.Rect(layout["panel"]),color=None,fill=(1,1,1),overlay=True)
        page.show_pdf_page(fitz.Rect(layout["bar"]),source,source_index,
                           clip=fitz.Rect(layout["bar_source"]))
        return page_doc.tobytes()


def generate_adaptive_pdf(student, template_path, output_path, mapping):
    """Return False for unsupported original geometry, allowing mapped fallback."""
    from backend.report_engine import (_mapped_values, _draw_lines,
                               _fit_mapped_text, INK, register_fonts)
    guide=student.get("sample_content_guide") or {}
    if not guide.get("sections"):return False
    register_fonts()
    doc=fitz.open(template_path)
    frames=_frames(doc,mapping,guide)
    # Never partially infer some panels while silently dropping other content.
    values=_mapped_values(student)
    parts=student.get("comments_by_section")
    missing_test_feedback = parts is not None and not str(parts.get("weekly_test") or "").strip()
    if missing_test_feedback:
        # Academy-requested reporting assumption, NOT an attendance inference
        # or an update to the student's imported score / source feedback.
        values["weekly_test_summary"] = MISSING_TEST_FEEDBACK
    needed={s for s,k in SECTION_REGIONS.items() if values.get(k)}
    if not needed <= {f["section"] for f in frames}:
        doc.close()
        return False
    # A sample-aware comment box contains teacher wording, not an extra English
    # numeric summary that the sample did not contain. Charts retain numeric data.
    for section,key in SECTION_REGIONS.items():
        comment=(student.get("comments_by_section") or {}).get(section)
        if comment:values[key]=comment
        # A missing comment stays blank; never print a generated summary instead.
        elif not (section=="weekly_test" and missing_test_feedback):values[key]=""
    if (parts or {}).get("homework"):
        # Teachers leave "___" for the student's name in shared comments.
        shown_name = display_name(student.get("name"))
        values["homework_summary"] = re.sub(r"_{3,}", lambda _: shown_name, values["homework_summary"])
    # Display-only: a character the body font cannot draw would render blank,
    # so draw its plain look-alike (ₓ → x, − → –) when the font has that.
    glyph_maps = [getattr(getattr(pdfmetrics.getFont(name), "face", None), "charToGlyph", None)
                  for name in {frame["font"] for frame in frames}]
    glyph_maps = [glyphs for glyphs in glyph_maps if glyphs is not None]
    if glyph_maps:
        for key in SECTION_REGIONS.values():
            if values.get(key):
                values[key] = _drawable(values[key], glyph_maps)
    frames,charts,hidden=_plan_panels(frames,student,general_text=str(values.get("teacher_comment") or ""),
                                      missing_test_feedback=missing_test_feedback)
    weekly_continuation=_weekly_continuation_frame(doc,frames)
    draws,clones,receipt=layout_comments(values,frames,len(doc),single_page=missing_test_feedback,
                                         weekly_continuation=weekly_continuation)
    # Omit unused trailing pages and remap generated continuation indexes.
    original_count=len(doc)
    used_original_count=max([d["page"]+1 for d in draws if d["page"]<original_count] or [1])
    sources=list(range(used_original_count))+clones
    def output_page(index):
        return index if index<original_count else index-original_count+used_original_count
    for draw in draws:draw["page"]=output_page(draw["page"])
    for item in receipt:item["pages"]=[output_page(page-1)+1 for page in item["pages"]]
    replacements={}
    for i,page in enumerate(doc):
        replacements[i]=_identity_fields(page,student)
        for target,value in replacements[i]:
            page.add_redact_annot(fitz.Rect(target["box"]),fill=False)
        if hidden and hidden["page"]==i:
            page.add_redact_annot(fitz.Rect(hidden["heading_box"]),fill=False)
        if replacements[i] or (hidden and hidden["page"]==i):
            page.apply_redactions(images=0,graphics=0)
    background_bytes=doc.tobytes()
    doc.close()
    writer=PdfWriter()
    # Retain readers and isolate each source page: merging overlays can mutate
    # shared PDF streams/resources even when a PageObject is deep-copied.
    readers=[]
    for output_index,source_index in enumerate(sources):
        page_draws=[d for d in draws if d["page"]==output_index]
        continuation_draws=[d for d in page_draws if d.get("continuation_heading")]
        weekly_page=next((d["weekly_continuation"] for d in page_draws if d.get("weekly_continuation")),None)
        page_bytes=background_bytes
        if weekly_page:
            page_bytes=_weekly_continuation_page(background_bytes,source_index,weekly_page)
        elif continuation_draws:
            with fitz.open(stream=background_bytes,filetype="pdf") as copy_doc:
                page=copy_doc[source_index]
                for draw in continuation_draws:
                    page.add_redact_annot(fitz.Rect(draw["heading_box"]),fill=False)
                page.apply_redactions(images=0,graphics=0)
                page_bytes=copy_doc.tobytes()
        source_reader=PdfReader(io.BytesIO(page_bytes));readers.append(source_reader)
        background=source_reader.pages[source_index]
        width,height=float(background.mediabox.width),float(background.mediabox.height)
        stream=io.BytesIO(); c=canvas.Canvas(stream,pagesize=(width,height))
        original_page=output_index<used_original_count
        if weekly_page:
            panel=fitz.Rect(weekly_page["panel"])
            first=next(d for d in page_draws if d.get("weekly_continuation"))
            _rgb(c,first["border_color"],stroke=True);c.setLineWidth(first["border_width"])
            c.rect(panel.x0,height-panel.y1,panel.width,panel.height,stroke=1,fill=0)
        if original_page and hidden and hidden["page"]==output_index:
            cover=fitz.Rect(hidden["cover"])|fitz.Rect(hidden["panel"])
            top,bottom=cover.y0+hidden["border_width"],cover.y1+1.5
            c.setFillColorRGB(1,1,1)
            c.rect(cover.x0-1.5,height-bottom,cover.width+3,bottom-top,stroke=0,fill=1)
            panel=fitz.Rect(hidden["panel"])
            _rgb(c,hidden["border_color"],stroke=True);c.setLineWidth(hidden["border_width"])
            c.rect(panel.x0,height-panel.y1,panel.width,panel.height,stroke=1,fill=0)
        c.setFillColor(INK)
        for target,value in (replacements.get(source_index, []) if original_page else []):
            box=fitz.Rect(target["box"])
            max_width=min(width-38-box.x0,max(box.width,150))
            region={"key":"identity","font_size":min(16,max(9,target["size"])),
                    "minimum_font_size":8,"line_spacing":1.1}
            font,size,leading,lines=_fit_mapped_text(value,region,max_width,max(30,box.height))
            _draw_lines(c,lines,x=box.x0,top_y=height-box.y0-size,font_size=size,leading=leading,font_name=font)
        for draw in page_draws:
            rect=fitz.Rect(draw["box"])
            if draw.get("continuation_heading"):
                heading=draw["heading"]
                color=int(heading.get("color",0))
                c.setFillColorRGB(((color>>16)&255)/255,((color>>8)&255)/255,(color&255)/255)
                c.setFont(register_fonts(),heading["size"])
                c.drawCentredString((rect.x0+rect.x1)/2,height-heading["box"][1]-heading["size"],
                                    draw["continuation_heading"])
                c.setFillColor(INK)
            _draw_lines(c,draw["lines"],x=rect.x0,top_y=height-rect.y0-draw["size"],
                        font_size=draw["size"],leading=draw["leading"],font_name=draw["font"])
        # Only original pages receive graphs; continuation pages receive text.
        if original_page:
            for chart in charts:
                if chart["page"]==output_index:
                    _draw_chart(c,chart,student,height)
        c.setFont(register_fonts(),8)
        c.setFillColor(INK)
        c.drawRightString(width-38,24,f"{student.get('period_label','')} · {output_index+1}/{len(sources)}")
        c.showPage();c.save()
        writer.add_page(background)
        overlay_reader=PdfReader(io.BytesIO(stream.getvalue()));readers.append(overlay_reader)
        writer.pages[-1].merge_page(overlay_reader.pages[0])
    output_path=Path(output_path);output_path.parent.mkdir(parents=True,exist_ok=True)
    with output_path.open("wb") as stream:writer.write(stream)
    # Non-sensitive diagnostics; teacher text is never written to logs.
    continuation_pages = sum(max(0, len(set(item["pages"])) - 1) for item in receipt)
    student["report_fit"]={"sections":receipt,"continuation_pages":continuation_pages,
                           "missing_test_feedback_assumption":missing_test_feedback,
                           "general_panel_hidden":bool(hidden)}
    return True
