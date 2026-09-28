from __future__ import annotations

import copy
import math
from typing import Any


class TemplateMappingError(ValueError):
    pass


REGION_TYPES = {
    "student_name": "text",
    "class_name": "text",
    "school_grade": "text",
    "reporting_week": "text",
    "homework_chart": "chart",
    "homework_summary": "text",
    "weekly_test_chart": "chart",
    "weekly_test_summary": "text",
    "teacher_comment": "text",
}

REQUIRED_REGION_KEYS = tuple(REGION_TYPES)
CONTINUATION_SOURCE_KEYS = (
    "teacher_comment",
)
MAX_GENERATED_CONTINUATION_PAGES = 50


def _region(
    key: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    font_size: float = 8.5,
    minimum_font_size: float = 7.0,
    align: str = "left",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": key,
        "content_type": REGION_TYPES[key],
        "page": 1,
        "box": {"x": x, "y": y, "width": width, "height": height},
        "required": True,
    }
    if REGION_TYPES[key] == "text":
        result.update(
            {
                "font_name": "auto",
                "font_size": font_size,
                "minimum_font_size": minimum_font_size,
                "line_spacing": 1.2,
                "align": align,
                "overflow_policy": "require_review",
            }
        )
    else:
        result.update({"fit": "contain", "show_grid": True})
    return result


def default_mapping(page_width: float, page_height: float, page_count: int = 1) -> dict[str, Any]:
    """Return a safe first-page mapping based on the proven Royal IB layout.

    Coordinates are normalized PDF coordinates with the origin at the bottom-left.
    They remain portable across portrait page sizes and are always editable before publish.
    """

    return {
        "schema_version": 2,
        "name": "Suggested report layout",
        "page_width": round(float(page_width), 2),
        "page_height": round(float(page_height), 2),
        "page_count": max(1, int(page_count)),
        "continuation_regions": [],
        "regions": [
            _region("class_name", 0.63, 0.90, 0.30, 0.035),
            _region("student_name", 0.63, 0.855, 0.30, 0.04, font_size=10),
            _region("school_grade", 0.63, 0.81, 0.30, 0.04),
            _region("reporting_week", 0.08, 0.755, 0.48, 0.035, font_size=8),
            _region("homework_chart", 0.08, 0.545, 0.40, 0.16),
            _region("homework_summary", 0.51, 0.525, 0.42, 0.18, font_size=7.5),
            _region("weekly_test_chart", 0.08, 0.305, 0.40, 0.15),
            _region("weekly_test_summary", 0.51, 0.285, 0.42, 0.18, font_size=7.2),
            _region("teacher_comment", 0.08, 0.065, 0.85, 0.13, font_size=7.4),
        ],
    }


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise TemplateMappingError(f"{label} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TemplateMappingError(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise TemplateMappingError(f"{label} must be finite.")
    return number


def validate_mapping(
    payload: Any,
    *,
    page_width: float,
    page_height: float,
    page_count: int,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise TemplateMappingError("Template mapping must be an object.")
    layout = str(payload.get("layout") or "uploaded_pdf")
    if layout not in {"uploaded_pdf", "royal_ib_weekly"}:
        raise TemplateMappingError("Choose a supported report layout.")
    typography = payload.get("typography") or {}
    body_font_size = _finite_number(typography.get("font_size", 10), "Body font size")
    if not 9 <= body_font_size <= 12:
        raise TemplateMappingError("Body font size must be between 9 and 12 points.")
    raw_regions = payload.get("regions")
    if not isinstance(raw_regions, list):
        raise TemplateMappingError("Template mapping needs a regions list.")
    raw_continuations = payload.get("continuation_regions") or []
    if not isinstance(raw_continuations, list):
        raise TemplateMappingError("Continuation areas must be a list.")

    normalized_regions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_regions:
        if not isinstance(raw, dict):
            raise TemplateMappingError("Every template region must be an object.")
        key = str(raw.get("key") or "").strip()
        if key not in REGION_TYPES:
            raise TemplateMappingError(f"Unsupported template region: {key or 'unnamed'}.")
        if key in seen:
            raise TemplateMappingError(f"Template region {key} appears more than once.")
        seen.add(key)
        content_type = str(raw.get("content_type") or REGION_TYPES[key])
        if content_type != REGION_TYPES[key]:
            raise TemplateMappingError(f"Template region {key} must be {REGION_TYPES[key]}.")
        try:
            page = int(raw.get("page") or 1)
        except (TypeError, ValueError) as exc:
            raise TemplateMappingError(f"Template region {key} has an invalid page.") from exc
        if page < 1 or page > max(1, int(page_count)):
            raise TemplateMappingError(f"Template region {key} points outside the template pages.")
        raw_box = raw.get("box")
        if not isinstance(raw_box, dict):
            raise TemplateMappingError(f"Template region {key} needs a box.")
        box = {
            field: _finite_number(raw_box.get(field), f"{key} {field}")
            for field in ("x", "y", "width", "height")
        }
        if box["width"] <= 0 or box["height"] <= 0:
            raise TemplateMappingError(f"Template region {key} must have positive dimensions.")
        if box["x"] < 0 or box["y"] < 0 or box["x"] + box["width"] > 1:
            raise TemplateMappingError(f"Template region {key} extends beyond the page width.")
        if box["y"] + box["height"] > 1:
            raise TemplateMappingError(f"Template region {key} extends beyond the page height.")

        region: dict[str, Any] = {
            "key": key,
            "content_type": content_type,
            "page": page,
            "box": {field: round(value, 5) for field, value in box.items()},
            "required": bool(raw.get("required", True)),
        }
        if content_type == "text":
            font_size = _finite_number(raw.get("font_size", 8.5), f"{key} font size")
            minimum = _finite_number(
                raw.get("minimum_font_size", min(font_size, 7.0)),
                f"{key} minimum font size",
            )
            if not 7 <= minimum <= font_size <= 36:
                raise TemplateMappingError(
                    f"Template region {key} needs font sizes between 7 and 36 points."
                )
            line_spacing = _finite_number(
                raw.get("line_spacing", 1.2), f"{key} line spacing"
            )
            if not 0.8 <= line_spacing <= 2:
                raise TemplateMappingError(f"Template region {key} has invalid line spacing.")
            align = str(raw.get("align") or "left")
            if align not in {"left", "center", "right"}:
                raise TemplateMappingError(f"Template region {key} has invalid alignment.")
            region.update(
                {
                    "font_name": str(raw.get("font_name") or "auto")[:120],
                    "font_size": round(font_size, 2),
                    "minimum_font_size": round(minimum, 2),
                    "line_spacing": round(line_spacing, 2),
                    "align": align,
                    "overflow_policy": "require_review",
                }
            )
        else:
            fit = str(raw.get("fit") or "contain")
            if fit not in {"contain", "cover"}:
                raise TemplateMappingError(f"Template region {key} has invalid chart fitting.")
            region.update({"fit": fit, "show_grid": bool(raw.get("show_grid", True))})
        normalized_regions.append(region)

    missing = [key for key in REQUIRED_REGION_KEYS if key not in seen]
    if missing:
        raise TemplateMappingError(
            "Template mapping is missing required regions: " + ", ".join(missing) + "."
        )
    normalized_regions.sort(key=lambda item: REQUIRED_REGION_KEYS.index(item["key"]))
    continuation_regions: list[dict[str, Any]] = []
    continuation_ids: set[str] = set()
    for index, raw in enumerate(raw_continuations, start=1):
        if not isinstance(raw, dict):
            raise TemplateMappingError("Every continuation area must be an object.")
        source_key = str(raw.get("source_key") or "teacher_comment").strip()
        if source_key not in CONTINUATION_SOURCE_KEYS:
            raise TemplateMappingError("Choose which text field uses each continuation area.")
        try:
            page = int(raw.get("page") or 0)
        except (TypeError, ValueError) as exc:
            raise TemplateMappingError("Continuation area has an invalid page.") from exc
        maximum_page = max(1, page_count) + MAX_GENERATED_CONTINUATION_PAGES
        if page < 2 or page > maximum_page:
            raise TemplateMappingError(
                f"Continuation areas must use report pages 2 through {maximum_page}."
            )
        try:
            template_page = int(raw.get("template_page") or min(page, max(1, page_count)))
        except (TypeError, ValueError) as exc:
            raise TemplateMappingError("Continuation area has an invalid template source page.") from exc
        if template_page < 1 or template_page > max(1, page_count):
            raise TemplateMappingError(
                "Continuation areas must clone an existing blank-template page."
            )
        continuation_id = str(raw.get("key") or f"{source_key}_continuation_{page}_{index}").strip()
        if not continuation_id or continuation_id in continuation_ids or continuation_id in seen:
            raise TemplateMappingError("Every continuation area needs a unique key.")
        continuation_ids.add(continuation_id)
        raw_box = raw.get("box")
        if not isinstance(raw_box, dict):
            raise TemplateMappingError(f"Continuation area {continuation_id} needs a box.")
        box = {
            field: _finite_number(raw_box.get(field), f"{continuation_id} {field}")
            for field in ("x", "y", "width", "height")
        }
        if box["width"] <= 0 or box["height"] <= 0:
            raise TemplateMappingError(
                f"Continuation area {continuation_id} must have positive dimensions."
            )
        if box["x"] < 0 or box["y"] < 0 or box["x"] + box["width"] > 1:
            raise TemplateMappingError(
                f"Continuation area {continuation_id} extends beyond the page width."
            )
        if box["y"] + box["height"] > 1:
            raise TemplateMappingError(
                f"Continuation area {continuation_id} extends beyond the page height."
            )
        font_size = _finite_number(
            raw.get("font_size", 10), f"{continuation_id} font size"
        )
        minimum = _finite_number(
            raw.get("minimum_font_size", min(font_size, 7)),
            f"{continuation_id} minimum font size",
        )
        if not 7 <= minimum <= font_size <= 36:
            raise TemplateMappingError(
                f"Continuation area {continuation_id} needs font sizes between 7 and 36 points."
            )
        line_spacing = _finite_number(
            raw.get("line_spacing", 1.2), f"{continuation_id} line spacing"
        )
        if not 0.8 <= line_spacing <= 2:
            raise TemplateMappingError(
                f"Continuation area {continuation_id} has invalid line spacing."
            )
        align = str(raw.get("align") or "left")
        if align not in {"left", "center", "right"}:
            raise TemplateMappingError(
                f"Continuation area {continuation_id} has invalid alignment."
            )
        continuation_regions.append(
            {
                "key": continuation_id[:120],
                "source_key": source_key,
                "content_type": "text",
                "page": page,
                "template_page": template_page,
                "box": {field: round(value, 5) for field, value in box.items()},
                "required": False,
                "font_name": str(raw.get("font_name") or "auto")[:120],
                "font_size": round(font_size, 2),
                "minimum_font_size": round(minimum, 2),
                "line_spacing": round(line_spacing, 2),
                "align": align,
                "overflow_policy": "require_review",
            }
        )

    all_text_regions = [
        region for region in normalized_regions if region["content_type"] == "text"
    ] + continuation_regions
    for index, region in enumerate(all_text_regions):
        box = region["box"]
        for other in all_text_regions[index + 1 :]:
            if region["page"] != other["page"]:
                continue
            other_box = other["box"]
            if (
                box["x"] < other_box["x"] + other_box["width"]
                and other_box["x"] < box["x"] + box["width"]
                and box["y"] < other_box["y"] + other_box["height"]
                and other_box["y"] < box["y"] + box["height"]
            ):
                raise TemplateMappingError(
                    f"Text areas {region['key']} and {other['key']} overlap on page {region['page']}."
                )

    continuation_regions.sort(key=lambda item: (item["page"], item["source_key"], item["key"]))
    return {
        "schema_version": 2,
        "layout": layout,
        "typography": {"font_name": "auto", "font_size": body_font_size},
        "name": str(payload.get("name") or "Company report layout").strip()[:120],
        "page_width": round(float(page_width), 2),
        "page_height": round(float(page_height), 2),
        "page_count": max(1, int(page_count)),
        "regions": normalized_regions,
        "continuation_regions": continuation_regions,
    }


def region_lookup(mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(region["key"]): copy.deepcopy(region)
        for region in mapping.get("regions", [])
        if isinstance(region, dict) and region.get("key") in REGION_TYPES
    }
