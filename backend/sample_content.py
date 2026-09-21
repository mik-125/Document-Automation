"""Grounded sample-content guidance; never use sample text as student feedback.

Headings establish sample sections. Class-specific content features can then split
mixed source narratives, without permission to replace the source template.
"""
from __future__ import annotations

import re
import unicodedata
import hashlib
from collections import Counter
from typing import Any


SECTION_LABELS = {
    "homework": "Homework / learning feedback",
    "weekly_test": "Weekly test feedback",
    "general": "General teacher feedback",
}
SECTION_REGIONS = {
    "homework": "homework_summary", "weekly_test": "weekly_test_summary",
    "general": "teacher_comment",
}
HEADING_ALIASES = {
    "homework": (
        "Homework", "Homework comments", "Homework completion", "Assignments",
        "Assignment", "Assignment comments", "Learning feedback", "Learning progress",
        "과제", "숙제", "과제 이행도", "과제 코멘트", "숙제 코멘트", "학습 코멘트",
    ),
    "weekly_test": (
        "Weekly test", "Weekly tests", "Weekly exam", "Test comments", "Test feedback",
        "Weekly test comments", "Exam feedback", "위클리 테스트", "주간 테스트",
        "시험 코멘트", "테스트 코멘트", "시험 분석", "테스트 분석",
    ),
    "general": (
        "Teacher comment", "Teacher comments", "General comment", "General comments",
        "Overall feedback", "General feedback", "Strengths and next steps", "Overall",
        "종합 코멘트", "종합 의견", "총평", "선생님 코멘트", "담당 선생님 COMMENT",
        "담당 선생님 코멘트", "선생님 COMMENT",
    ),
}
TOPICS = {
    "completion and consistency": r"과제|숙제|이행|제출|homework|assignment|completion|submitted",
    "concepts and understanding": r"개념|이해|concept|understand|knowledge",
    "scores and performance": r"점수|성적|정답|score|marks?|grade|performance|\d+\s*/\s*\d+",
    "mistakes and reasoning": r"오답|실수|풀이|계산|단위|mistake|error|reasoning|calculation|solution",
    "participation and effort": r"태도|참여|집중|노력|participation|effort|engagement|focus",
    "next steps and practice": r"복습|연습|보완|개선|practice|revise|revision|improve|next step",
}

CONTENT_ROLES = {
    "test_result": r"\d+\s*/\s*\d+|(?:받았|받은|scored|earned|achieved).{0,15}(?:점|grade)|grade\s*[1-7]|\b(?:test|exam)\s+(?:result|score)",
    "question_detail": r"\bQ\s*\d+(?:\([a-z]\))?|\bquestion\s*\d+|\d+\s*번\s*(?:문항|문제)|문항에서는|문항은|문제는|문제도|모범\s*답안|답안에서는",
    "teaching_context": r"수업에서는|수업을|수업에서는|학습하였|지도하였|진행하였|다루었|단원들은|이번.{0,8}특강|during\s+(?:our\s+)?lessons|we\s+(?:studied|covered|learned)",
    "overall_advice": r"전체적으로|앞으로는|앞으로도|꾸준히|종합적으로|overall|going forward|next steps",
}


def content_features(text):
    """Hashed lexical evidence and rhetorical roles; no reusable sample prose."""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC",text).casefold())
    tokens = re.findall(r"[a-z]{3,}|[가-힣]{2,}", compact)
    features = Counter()
    for token in tokens:
        for n in (2,3):
            for i in range(len(token)-n+1):
                features[hashlib.sha256(token[i:i+n].encode()).hexdigest()[:12]] += 1
    return {"lexical":dict(features),"roles":{
        name:len(re.findall(pattern,text,re.I)) for name,pattern in CONTENT_ROLES.items()}}


def _source_units(text):
    # Keep original slices, including punctuation and whitespace, so routing
    # never asks a text generator to reconstruct or shorten teacher comments.
    boundaries = [0]
    for m in re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9가-힣“\"'])|(?<=합니다)\s+(?=[가-힣]{2,8}(?:는|은)\s+(?:이번\s+)?Week)|\n\s*\n",text):
        boundaries.append(m.end())
    boundaries.append(len(text))
    return [text[a:b] for a,b in zip(boundaries,boundaries[1:]) if b>a]


def _question_block_start(unit):
    """A numbered feedback block, not a question mentioned inside an overview.

    Source PDFs may have lost paragraph breaks during import. Sentence starts
    still distinguish 'Q1(a) ...' from 'However, Q4 ...' in an overview.
    Bare subquestion labels are allowed; plain numbers/dates are not.
    """
    return bool(re.match(
        r"\s*(?:Q\s*\d+(?:\s*\([a-z0-9ivx]+\))?\b|"
        r"Question\s+\d+\b|\d+\s*\([a-zivx]+\)|\d+\s*번\s*(?:문항|문제))",
        unit, re.I))


def _test_overview_start(unit):
    if _question_block_start(unit):
        return False
    # A student-specific test introduction need not contain a numerical score.
    # Do not mistake a shared lesson description mentioning exams for one.
    named_event = re.match(
        r"\s*[가-힣]{2,8}(?:는|은)\s+(?:이번\s+)?"
        r"(?:Week\s*\d+\s*(?:Test|Exam)|(?:주간|위클리)\s*테스트|시험)",
        unit, re.I)
    if named_event:
        return True
    roles = content_features(unit)["roles"]
    return bool(roles["test_result"] and not roles["teaching_context"]
                and re.search(r"\b(?:test|exam|score|scored|earned|achieved)\b|시험|받았", unit, re.I))


def _sample_routed_chunks(text, selected, guide):
    """Exemplar-informed discourse routing, with explicit headings taking priority.

    Only a class with reliable test AND general sample bodies can split an
    unheaded mixed narrative. Homework imports remain explicitly homework.
    """
    profiles = {}
    for section in guide.get("sections",[]):
        if section.get("reliable",True) and section.get("features"):
            profiles.setdefault(section["section"],[]).append(section["features"])
    if selected == "homework" or not {"weekly_test","general"} <= profiles.keys():
        return None
    if any(heading_section(line) or heading_section(re.split(r"[:：]",line,maxsplit=1)[0])
           for line in text.splitlines()):
        return None
    roles = {section:Counter() for section in profiles}
    for section, items in profiles.items():
        for item in items:
            roles[section].update(item["roles"])
    # A test-overview/long-form-feedback distinction must be observed, not
    # assumed for every subject. Otherwise retain the chosen import category.
    if not roles["weekly_test"]["test_result"] or not (
        roles["general"]["question_detail"] or roles["general"]["teaching_context"]):
        return None
    detail_ratios = {k: roles[k]["question_detail"] / max(1, roles[k]["test_result"])
                     for k in ("general", "weekly_test")}
    if detail_ratios["general"] <= detail_ratios["weekly_test"]:
        # Not every subject uses a short test overview / long teacher-feedback
        # convention. Do not impose this layout when its sample does not agree.
        return None
    units = _source_units(text)
    detail = next((i for i, unit in enumerate(units) if _question_block_start(unit)), len(units))
    start = next((i for i, unit in enumerate(units[:detail]) if _test_overview_start(unit)), None)
    if start is None:
        if content_features(text)["roles"]["teaching_context"] or detail < len(units):
            return [["general", text]]
        return None
    # Keep the complete, contiguous overview. Advice, passing question mentions,
    # and lexical overlap are not boundaries. Once detailed feedback starts,
    # later scores must not pull individual sentences back into the overview.
    return [[section, "".join(chunk)] for section, chunk in (
        ("general", units[:start]), ("weekly_test", units[start:detail]),
        ("general", units[detail:])) if chunk]


def heading_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    value = re.sub(r"^(?:\d+[.)]\s*|[①②③④⑤]\s*)", "", value)
    value = re.sub(r"\s*\(?continued\)?$|\s*\(?계속\)?$", "", value)
    return re.sub(r"[\s#*:：·_\-()\[\]]", "", value)


def heading_section(value: str) -> str | None:
    key = heading_key(value)
    for section, labels in HEADING_ALIASES.items():
        if key in {heading_key(label) for label in labels}:
            return section
    return None


def _visual_lines(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rejoin adjacent PDF text fragments before interpreting any headings."""
    rows: list[list[dict[str, Any]]] = []
    for run in sorted((r for r in runs if r.get("x") is not None and r.get("y") is not None),
                      key=lambda r: (-r["y"], r["x"])):
        if rows and abs(rows[-1][0]["y"] - run["y"]) <= max(2, (run.get("size") or 10) * .25):
            rows[-1].append(run)
        else:
            rows.append([run])
    lines = []
    for row in rows:
        current = None
        right = 0
        for run in sorted(row, key=lambda r: r["x"]):
            size = run.get("size") or 10
            combined = heading_key(current["text"] + run["text"]) if current else ""
            heading_fragment = bool(combined and any(heading_key(label).startswith(combined)
                                    for labels in HEADING_ALIASES.values() for label in labels))
            allowed_gap = max(12, size * 1.5, 80 if heading_fragment else 0)
            if current is not None and -size * 2 <= run["x"] - right <= allowed_gap:
                current["text"] += " " + run["text"]
            else:
                current = dict(run)
                lines.append(current)
            right = run["x"] + sum(size if unicodedata.east_asian_width(ch) in {"W", "F"}
                                   else size * .5 for ch in run["text"])
    return lines


def page_content_guide(runs: list[dict[str, Any]], width: float, height: float,
                       page: int, image_regions: list[dict[str, Any]]) -> dict[str, Any]:
    """Group short text runs under standalone section headings on the same column.

    Ambiguous side-by-side headings and rotated text are not silently interpreted.
    Raw body text stays in memory only; the persisted guide contains statistics.
    """
    runs = _visual_lines(runs)
    headings = []
    for run in runs:
        section = heading_section(run["text"])
        if section and run.get("x") is not None and run.get("y") is not None:
            headings.append({**run, "section": section})
    headings.sort(key=lambda run: (-run["y"], run["x"]))
    sections = []
    heading_counts = Counter(h["section"] for h in headings)
    for heading in headings:
        # Section headings aligned to the left form a column. A heading to the
        # right bounds this column, while a lower heading ends its vertical span.
        right = min([h["x"] for h in headings if h["x"] > heading["x"] + width * .2
                     and abs(h["y"] - heading["y"]) < height * .04] or [width])
        bottom = max([h["y"] for h in headings if h["y"] < heading["y"] - 4
                      and abs(h["x"] - heading["x"]) < width * .2] or [0])
        body = [r for r in runs if not heading_section(r["text"])
                and r.get("x") is not None and r.get("y") is not None
                and heading["x"] - 8 <= r["x"] < right and bottom < r["y"] < heading["y"] - 2]
        text = "\n".join(r["text"] for r in body)
        fonts = Counter(r.get("font") for r in body if r.get("font"))
        chart_labels = sorted({kind for kind, pattern in {
            "line": r"line\s+(?:chart|graph)|꺾은선", "bar": r"bar\s+(?:chart|graph)|막대",
            "pie": r"pie\s+chart|원형\s*차트",
        }.items() if re.search(pattern, text, re.I)})
        sections.append({
            "section": heading["section"], "label": heading["text"].strip(),
            "page": page, "target_region": SECTION_REGIONS[heading["section"]],
            "evidence": "recognized heading and text position",
            "reliable": heading_counts[heading["section"]] == 1,
            "heading_position": {"x": round(heading["x"] / width, 4), "y": round(heading["y"] / height, 4)},
            "text_characters": len(text), "text_runs": len(body),
            "topics": [name for name, pattern in TOPICS.items() if re.search(pattern, text, re.I)],
            "body_fonts": [name for name, _ in fonts.most_common(6)],
            "body_sizes": sorted({r["size"] for r in body if r.get("size")})[:10],
            "bullet_runs": sum(bool(re.match(r"\s*(?:[-•▪]|\d+[.)])", r["text"])) for r in body),
            "graph_type_labels": chart_labels,
            "image_regions": [image for image in image_regions if bottom <= image["y"] * height < heading["y"]
                              and heading["x"] - 8 <= image["x"] * width < right],
        })
    warnings = []
    if not headings:
        warnings.append(f"Page {page}: no reliable section headings found; content placement needs review.")
    for section, count in heading_counts.items():
        if count > 1:
            warnings.append(f"Page {page}: repeated {SECTION_LABELS[section]} headings; confirm which blocks belong together.")
    return {"sections": sections, "warnings": warnings}


def merge_content_guides(examples: list[dict[str, Any]]) -> dict[str, Any]:
    sections = []
    warnings = []
    for example in examples:
        guide = (example.get("analysis") or {}).get("content_guide") or {}
        for section in guide.get("sections", []):
            sections.append({**section, "sample_id": example.get("id", "")})
        warnings.extend(guide.get("warnings", []))
    return {
        "version": 1, "design_authority": "original_template", "wording_policy": "preserve_source",
        "sections": sections, "warnings": list(dict.fromkeys(warnings)),
        "requires_review": bool(examples) and (not sections or bool(warnings)),
        "sample_text_is_output": False,
    }


def organize_comments(exams: list[dict[str, Any]], guide: dict[str, Any]) -> dict[str, Any]:
    """Route source spans by explicit headings or supported class sample roles.

    Without sufficient sample evidence, retain the selected import category.
    Homework imports stay homework; no source wording is rewritten.
    """
    parts = {section: [] for section in SECTION_LABELS}
    decisions = []
    observed = {s["section"] for s in guide.get("sections", []) if s.get("reliable", True)}
    for exam in exams:
        text = str(exam.get("comment") or "")
        if not text.strip():
            continue
        selected = exam.get("comment_section") or "weekly_test"
        if selected not in parts:
            raise ValueError("Choose a report section for the imported comment.")
        active = selected
        inferred = _sample_routed_chunks(text, selected, guide)
        chunks = inferred or []
        for line in ([] if inferred else text.splitlines(keepends=True)):
            # A heading must match the entire line or the prefix before a colon.
            # Narrative keywords alone do not establish a section boundary.
            section = heading_section(line) or heading_section(re.split(r"[:：]", line, maxsplit=1)[0])
            if section in observed:
                active = section
            if chunks and chunks[-1][0] == active:
                chunks[-1][1] += line
            else:
                chunks.append([active, line])
        for destination, content in chunks:
            parts[destination].append(content)
        decisions.append({
            "source_section": selected,
            "destinations": list(dict.fromkeys(destination for destination, _ in chunks)),
            "basis": "class sample roles and contiguous source blocks" if inferred else
                     "sample section headings" if any(d != selected for d, _ in chunks) else "selected import section",
        })
    return {"sections": {key: "\n\n".join(value) for key, value in parts.items()}, "decisions": decisions}
