"""Fit source feedback into the ORIGINAL PDF's detected panels, across pages.

Samples inform content roles and body font sizes, never background page design.
No imported records or saved mappings are changed by this rendering path.
"""
from __future__ import annotations

import copy
import io
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from pdf_content import labeled_panels, page_lines
from sample_content import SECTION_REGIONS

COMPACT_MINIMUM = 5.0
MISSING_TEST_FEEDBACK = (
    "학생은 이번 위클리 테스트를 응시하지 않았기에 정확한 이해도 판단이 어렵습니다. "
    "위클리 테스트를 나중에 한 번 풀어보시는 것을 추천드립니다."
)


def _body_font(observations):
    from report_engine import register_fonts
    fonts = Counter(f for item in observations for f in item.get("body_fonts",[]))
    aliases = {"Arial":"Helvetica", "TimesNewRomanPSMT":"Times-Roman",
               "CourierNewPSMT":"Courier"}
    for font,_ in fonts.most_common():
        candidate = aliases.get(font,font)
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
        for panel in panels:
            section = panel["section"]
            observations = [s for s in guide.get("sections",[]) if s["section"]==section and s.get("reliable",True)]
            if not observations:
                continue
            rect = fitz.Rect(panel["box"])
            rect = fitz.Rect(rect.x0+8,rect.y0+7,rect.x1-8,rect.y1-7)
            chart = None
            if section in {"homework","weekly_test"} and any(s.get("body_lane")=="right" for s in observations):
                chart = fitz.Rect(rect.x0,rect.y0,rect.x0+rect.width*.48,rect.y1)
                rect.x0 += rect.width*.53
            key = SECTION_REGIONS[section]
            mapped = regions.get(key,{})
            sizes = [s["preferred_font_size"] for s in observations if s.get("preferred_font_size")]
            # Small reference typography describes style but does not authorize
            # unreadable output. Grow short comments toward useful occupancy.
            preferred = min(12,max(9.5,max(sizes or [mapped.get("font_size",9.5)])))
            frames.append({"section":section,"page":number,"box":list(rect),
                           "chart":list(chart) if chart else None,
                           "font":_body_font(observations),"preferred":preferred,
                           "minimum":max(8,min(12,float(mapped.get("minimum_font_size",8)))),
                           "heading":panel["heading"],
                           "heading_box":panel["heading"]["box"]})
    return frames


def _wrap_feedback(text, font, size, width):
    """Keep Latin words together; CJK may break between glyphs. No rewriting."""
    from report_engine import ValidationError
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
                if current and pdfmetrics.stringWidth(current+part,font,size)>width:
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


def _section_plan(text, group, frames, page_count, clone_offset, size, allow_extra):
    """Pure trial layout. Rejected font/page trials never leak into the output."""
    from report_engine import ValidationError
    fitted, remaining = _flow_at_size(text, group, size)
    clones = []
    while remaining and allow_extra:
        if clone_offset+len(clones)>=50:
            raise ValidationError("This report exceeds 50 continuation pages. Review the source comments.")
        candidates = [f for f in frames if f.get("heading") and not f.get("chart")]
        source = max(candidates or group,key=lambda f:fitz.Rect(f["box"]).get_area())
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


def layout_comments(values, frames, page_count, *, single_page=False):
    """Keep test feedback in its box; reject sparsely filled final comment pages."""
    from report_engine import ValidationError
    draws, clones, receipt = [], [], []
    for section,key in SECTION_REGIONS.items():
        group = [f for f in frames if f["section"]==section and (not single_page or f["page"]==0)]
        if section == "weekly_test":
            group = group[:1]
        text = values.get(key,"")
        # Imported PDF line endings are usually visual wraps, not paragraphs.
        # Keep blank paragraphs, date entries and bullets; reflow other wraps.
        text = re.sub(r"(?<!\n)\n(?!\s*\n|\s*(?:\d{1,2}[/.-]\d{1,2}|[-•▪]))", " ", text)
        if not text:
            continue
        if not group:
            raise ValidationError(f"The original PDF needs a confirmed {section} text region before automatic fitting.")
        standard_minimum = max(f["minimum"] for f in group)
        preferred = max(standard_minimum,min(f["preferred"] for f in group))
        compact = section in {"weekly_test", "general"} or single_page
        minimum = min(standard_minimum, COMPACT_MINIMUM) if compact else standard_minimum
        allow_extra = not single_page and section != "weekly_test"
        size = preferred
        # First try to use the original pages at normal body sizes. Only use a
        # continuation after this pass; smaller fonts can then eliminate a short
        # final page. All trials start from the complete original wording.
        while size > standard_minimum:
            _,remaining = _flow_at_size(text,group,size)
            if not remaining:break
            size = max(standard_minimum,round(size-.25,2))
        while True:
            fitted, trial_clones, remaining = _section_plan(
                text,group,frames,page_count,len(clones),size,allow_extra)
            # The last teacher-comment page must be at least half occupied.
            # Even an original second template page is optional when sparse.
            sparse = (section == "general" and fitted and fitted[-1]["page"] > 0
                      and _filled_fraction(fitted[-1]) < .5)
            if not remaining and not sparse:break
            if size <= minimum:
                raise ValidationError(
                    "The comment cannot meet the requested page limit at 5 pt. "
                    "Review the source text or enlarge the original comment region; no text was omitted.")
            size = max(minimum,round(size-.25,2))
        draws.extend(fitted)
        clones.extend(trial_clones)
        pages = [f["page"]+1 for f in fitted]
        receipt.append({"section":section,"font_size":size,"pages":pages,
                        "last_page_fill":round(_filled_fraction(fitted[-1]),4) if fitted else 0,
                        "source_characters":len(text),"wording":"preserved"})
    return draws,clones,receipt


def _identity_fields(page, student):
    """Replace explicit placeholder values without changing labels/backgrounds."""
    lines = page_lines(page)
    replacements = []
    labels = {"성명":"name","이름":"name","student name":"name", "학교":"school","school":"school",
              "과목":"class_name","class":"class_name","subject":"class_name"}
    for line in lines:
        key=labels.get(line["text"].strip().casefold())
        if not key:continue
        box=fitz.Rect(line["box"])
        neighbors=[r for r in lines if abs(r["box"][1]-box.y0)<6 and r["box"][0]>box.x1+3
                   and r["text"].strip().casefold() not in labels]
        if neighbors:
            target=min(neighbors,key=lambda r:r["box"][0])
            replacements.append((target,str(student.get(key) or "")))
    # Common original templates contain a demonstration class above the title.
    # Restrict this to a schedule-style class line, never arbitrary narrative.
    for line in lines:
        if line["box"][1]<page.rect.height*.2 and re.search(r"\(\d+차\).*\b(?:DP\d|IB)\b",line["text"]):
            replacements.append((line,str(student.get("class_name") or "")))
    return replacements


def generate_adaptive_pdf(student, template_path, output_path, mapping):
    """Return False for unsupported original geometry, allowing mapped fallback."""
    from report_engine import (_mapped_values, _draw_lines, _draw_line_chart,
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
    draws,clones,receipt=layout_comments(values,frames,len(doc),single_page=missing_test_feedback)
    # Omit unused trailing template pages. Remap generated continuation indexes
    # after trimming so overlays, receipts and page numbers stay aligned.
    original_count=len(doc)
    used_original_count=max([d["page"]+1 for d in draws if d["page"]<original_count] or [1])
    sources=list(range(used_original_count))+clones
    def output_page(index):
        return index if index<original_count else index-original_count+used_original_count
    for draw in draws:draw["page"]=output_page(draw["page"])
    for item in receipt:item["pages"]=[output_page(p-1)+1 for p in item["pages"]]
    replacements={}
    for i,page in enumerate(doc):
        replacements[i]=_identity_fields(page,student)
        for target,value in replacements[i]:
            page.add_redact_annot(fitz.Rect(target["box"]),fill=False)
        if replacements[i]:page.apply_redactions(images=0,graphics=0)
    background_bytes=doc.tobytes()
    doc.close()
    writer=PdfWriter()
    # Retain readers and isolate each source page: merging overlays can mutate
    # shared PDF streams/resources even when a PageObject is deep-copied.
    readers=[]
    for output_index,source_index in enumerate(sources):
        page_bytes=background_bytes
        continuation_draws=[d for d in draws if d["page"]==output_index and d.get("continuation_heading")]
        if continuation_draws:
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
        c.setFillColor(INK)
        for target,value in replacements[source_index]:
            box=fitz.Rect(target["box"])
            max_width=min(width-38-box.x0,max(box.width,150))
            region={"key":"identity","font_size":min(16,max(9,target["size"])),
                    "minimum_font_size":8,"line_spacing":1.1}
            font,size,leading,lines=_fit_mapped_text(value,region,max_width,max(30,box.height))
            _draw_lines(c,lines,x=box.x0,top_y=height-box.y0-size,font_size=size,leading=leading,font_name=font)
        for draw in draws:
            if draw["page"]!=output_index:continue
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
        if output_index<used_original_count:
            for frame in frames:
                if frame["page"]!=output_index or not frame["chart"]:continue
                if frame["section"]=="weekly_test" and missing_test_feedback:continue
                chart=fitz.Rect(frame["chart"])
                if frame["section"]=="homework":
                    points=list(student.get("homework") or []); value_key="score"
                else:
                    points=[{"label":e.get("label","Test"),"percent":float(e["score"])/float(e["max_score"])*100
                             if e.get("score") is not None and e.get("max_score") else None} for e in student.get("exams",[])]
                    value_key="percent"
                _draw_line_chart(c,points,x=chart.x0+4,y=height-chart.y1+4,width=chart.width-8,height=chart.height-8,
                                 value_key=value_key,label_key="label")
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
    student["report_fit"]={"sections":receipt,"continuation_pages":len(clones),
                           "missing_test_feedback_assumption":missing_test_feedback}
    return True
