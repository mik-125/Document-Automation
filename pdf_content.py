"""Local, geometry-aware PDF evidence. No sample prose is persisted in guides."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import statistics
import pymupdf as fitz

from sample_content import heading_section, SECTION_REGIONS, TOPICS, content_features
import re


def page_lines(page):
    rows = []
    spans = [s for b in page.get_text("dict")["blocks"] if "lines" in b
             for line in b["lines"] for s in line["spans"] if s["text"].strip()]
    for span in sorted(spans, key=lambda s: (s["origin"][1], s["bbox"][0])):
        baseline = span["origin"][1]
        if rows and abs(rows[-1][0]["origin"][1] - baseline) < 1.5:
            rows[-1].append(span)
        else:
            rows.append([span])
    result = []
    for row in rows:
        groups = []
        for s in sorted(row, key=lambda s: s["bbox"][0]):
            if groups and s["bbox"][0] - groups[-1][-1]["bbox"][2] < max(12, s["size"] * 1.5):
                groups[-1].append(s)
            else:
                groups.append([s])
        for group in groups:
            box = fitz.Rect(group[0]["bbox"])
            text = ""
            for i, span in enumerate(group):
                if i and span["bbox"][0] - group[i-1]["bbox"][2] > span["size"] * .15:
                    text += " "
                text += span["text"]
                box |= fitz.Rect(span["bbox"])
            result.append({"text": text.strip(), "box": list(box),
                           "size": statistics.median(s["size"] for s in group),
                           "color": group[0].get("color", 0),
                           "font": Counter(s["font"] for s in group).most_common(1)[0][0]})
    return sorted(result, key=lambda r: (r["box"][1], r["box"][0]))


def labeled_panels(page, lines=None):
    """Only an unambiguous drawn panel beneath a recognized heading is automatic."""
    lines = lines or page_lines(page)
    rectangles = []
    for drawing in page.get_drawings():
        r = drawing["rect"]
        if r.width > page.rect.width * .25 and r.height > 35 and r.get_area() < page.rect.get_area() * .8:
            if not any(abs(r.x0-b.x0)+abs(r.y0-b.y0)+abs(r.x1-b.x1)+abs(r.y1-b.y1) < 2 for b in rectangles):
                rectangles.append(r)
    panels = []
    for line in lines:
        section = heading_section(line["text"])
        if not section:
            continue
        h = fitz.Rect(line["box"])
        matches = [r for r in rectangles if -2 <= r.y0 - h.y1 <= 24
                   and r.x0 <= (h.x0+h.x1)/2 <= r.x1]
        if len(matches) == 1:
            panels.append({"section": section, "box": list(matches[0]), "heading": line})
    return panels


def analyze_content_pdf(path: Path):
    sections, warnings = [], []
    with fitz.open(path) as doc:
        for number, page in enumerate(doc, 1):
            if page.rotation:
                warnings.append(f"Page {number}: rotated sample needs content-placement review.")
                continue
            lines = page_lines(page)
            headings = [r for r in lines if heading_section(r["text"])]
            panels = labeled_panels(page, lines)
            counts = Counter(heading_section(h["text"]) for h in headings)
            if not headings:
                warnings.append(f"Page {number}: no readable section headings; review content placement.")
            for heading in headings:
                section = heading_section(heading["text"])
                h = fitz.Rect(heading["box"])
                panel = next((p for p in panels if p["heading"] == heading), None)
                lower = min([r["box"][1] for r in headings if r["box"][1] > h.y1] or [page.rect.height*.95])
                box = fitz.Rect(panel["box"]) if panel else fitz.Rect(0,h.y1,page.rect.width,lower)
                body = [r for r in lines if box.contains(fitz.Rect(r["box"]))
                        and r not in headings and not re.search(r"그래프.*입력|insert.*(?:graph|chart)",r["text"],re.I)]
                # Ignore graph labels: narrative text lies in the lane with the
                # greatest character mass, except full-width teacher feedback.
                if section != "general" and body:
                    left = [r for r in body if r["box"][0] < box.x0+box.width*.48]
                    right = [r for r in body if r["box"][0] >= box.x0+box.width*.48]
                    if sum(len(r["text"]) for r in right) > sum(len(r["text"]) for r in left)*2:
                        body = right
                text = "\n".join(r["text"] for r in body)
                sizes = Counter(round(r["size"],2) for r in body for _ in range(max(1,len(r["text"]))))
                fonts = Counter(r["font"] for r in body)
                reliable = counts[section] == 1 and bool(body)
                if not reliable:
                    warnings.append(f"Page {number}: {heading['text']} needs review (empty or repeated section).")
                sections.append({"section":section,"label":heading["text"],"page":number,
                    "target_region":SECTION_REGIONS[section],"reliable":reliable,
                    "evidence":"PDF text geometry, section panel and content features",
                    "heading_position":{"x":h.x0/page.rect.width,"y":1-h.y1/page.rect.height},
                    "text_characters":len(text),"text_runs":len(body),
                    "body_fonts":[f for f,_ in fonts.most_common(5)],"body_sizes":sorted(sizes),
                    "preferred_font_size":sizes.most_common(1)[0][0] if sizes else None,
                    "features":content_features(text),
                    "topics":[name for name,pattern in TOPICS.items() if re.search(pattern,text,re.I)],
                    "graph_type_labels":[],"image_regions":[],
                    "body_lane":"right" if body and min(r["box"][0] for r in body)>box.x0+box.width*.45 else "full",
                })
    return {"version":2,"sections":sections,"warnings":warnings,"requires_review":bool(warnings),
            "design_authority":"original_template","wording_policy":"preserve_source"}
