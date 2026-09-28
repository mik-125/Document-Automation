"""Generate private report diagnostics and PNG previews from local Brightline data."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import server
from backend.report_engine import generate_student_pdf


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value).strip("-.")
    return cleaned[:80] or "student"


def render_pages(pdf_path: Path, render_dir: Path) -> list[str]:
    render_dir.mkdir(parents=True, exist_ok=True)
    rendered = []
    with fitz.open(pdf_path) as document:
        for index, page in enumerate(document, start=1):
            output = render_dir / f"page-{index}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(output)
            rendered.append(str(output))
    return rendered


def inspect_student(student: dict, output_dir: Path) -> dict:
    decisions = list(student.get("comment_placement") or [])
    flags = []
    if student.get("routing_review_required"):
        flags.append("low-confidence comment routing")
    if student.get("missing_test_score_warning"):
        flags.append("stored score with missed-test notice")
    result = {
        "student_id": student["id"],
        "student": student["name"],
        "class": student.get("class_name", ""),
        "page_count": 0,
        "passages": decisions,
        "sections": [],
        "review_flags": flags,
        "rendered_pages": [],
    }
    if not student.get("report_ready"):
        result["review_flags"].append("report not ready")
        return result
    payload = server.get_ready_student(student["id"], student.get("period_id"))
    template = server.DB.get_class_template_version(student["class_id"], published_only=True)
    if not template or not template.get("ready"):
        result["review_flags"].append("published PDF template unavailable")
        return result
    template_path = Path(str(template.get("source_pdf_path") or "")).resolve()
    student_dir = output_dir / safe_name(student["name"])
    student_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = student_dir / "report.pdf"
    generate_student_pdf(payload, template_path, pdf_path, dict(template.get("mapping") or {}))
    with fitz.open(pdf_path) as document:
        result["page_count"] = len(document)
    fit = payload.get("report_fit") or {}
    result["sections"] = fit.get("sections") or []
    result["rendered_pages"] = render_pages(pdf_path, student_dir / "pages")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=server.DATA_DIR / "report-checks",
        help="Private ignored directory for PDFs, PNGs, and summary.json.",
    )
    parser.add_argument("--class-id", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    state = server.app_state()
    students = [
        student for student in state["students"]
        if not args.class_id or student["class_id"] == args.class_id
    ]
    summary = [inspect_student(student, output_dir) for student in students]
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.quiet:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Summary: {summary_path}")
    return 1 if any(item["review_flags"] for item in summary) else 0


if __name__ == "__main__":
    raise SystemExit(main())
