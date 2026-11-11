# Multi-week Sheet Import and Cumulative Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One pasted Google Sheet imports every `WEEK N` block, and a report for week N shows the student's test scores for weeks 1..N with their average and a graph.

**Architecture:** Weeks gain a `week_number` that replaces the date range as identity. A new pure module splits a sheet into per-week column blocks and hands each block to the existing single-week import, all inside one transaction. Report history selects weeks by number and adds a cumulative average that the chart subtitle prints.

**Tech Stack:** Python 3.13, SQLite (stdlib `sqlite3`), ReportLab, PyMuPDF, plain JS frontend, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-11-multi-week-sheet-cumulative-reports-design.md`

## Global Constraints

- Read `AGENTS.md` first; its rules apply to every task.
- Tests use synthetic data and a temporary `BRIGHTLINE_DATA_DIR`. Never run tests, migrations or imports against the repository's `data/` directory.
- No real student names, class names, IDs or scores in code, tests, docs or commit messages.
- Do not touch the uncommitted `BRIGHTLINE_PUBLIC_ORIGIN` change in `backend/server.py` and `tests/test_mvp.py`; do not stage, revert or reformat it.
- Do not commit unless the maintainer has said to. Where a task says "Checkpoint", run the listed tests and stop; commit only with that permission, staging only the files the task names.
- No new dependencies.
- Teacher wording is never generated: a missing narrative stays blank.
- Exact copy: week label `Week N`; subtitle `Week 1 ~ Week 4 · 평균 84%` (one week: `Week 2 · 평균 84%`); average rounded to one decimal with a trailing `.0` dropped.
- Backup file name: `data/backups/brightline-before-week-number-<YYYYMMDD-HHMMSS>.db` (relative to the active data directory).
- Run each test module with `.venv/bin/python -m unittest tests.<module> -v`.

## Spec amendments made while planning

Two points the spec got wrong against the real sheet layout; the spec file is updated to match.

1. The class column in each block has a blank header. A column immediately left of a student-name column, with blank header cells and non-blank data, is the block's class column.
2. A sheet with one or more week headings takes the block path and uses the heading's number (the spec said two or more). A sheet with no week heading takes today's single-week path.

## Review Focus

1. A block lists students in a different order, or has a student the other blocks lack: each block imports on its own rows; nothing is aligned across blocks. (Task 4)
2. Heading spellings `WEEK 1`, `Week1`, `week 01`, `1주차`, and a merged heading present only in its first cell: all resolve to the same number. (Task 3)
3. A stored database with two weeks whose labels carry the same number: the upgrade must not fail; the earliest keeps the number, the others stay unnumbered. (Task 1)
4. A student with a score in only one of the included weeks, or none: one-point graph and a one-week average; no score anywhere means no graph and no average line. (Tasks 5, 6)
5. Blank spacer columns between blocks, trailing empty columns, or a block with no test-score column: spacers are ignored; a block without scores still imports homework and reports `has_scores: false`. (Task 3)

---

### Task 1: Week number in the database

**Files:**
- Modify: `backend/database.py` (schema script near line 118, `_initialize`, `ensure_period` at 392, `state` at 1323)
- Test: `tests/test_week_numbers.py` (create)

**Interfaces:**
- Produces:
  - `week_number_from_label(label: str) -> int | None` (module level). Matches `week\s*0*(\d+)` case-insensitively or `0*(\d+)\s*주차`; returns `None` for no match or a number below 1.
  - `Database.find_period(self, *, start_date: str = "", end_date: str = "", label: str = "", week_number: int | None = None, connection=None) -> dict | None`
  - `Database.ensure_period(self, start_date, end_date, label="", connection=None, *, week_number: int | None = None) -> dict` (existing positional callers keep working)
  - Every period dict now has `week_number: int | None`.

- [ ] **Step 1: Write the failing tests** in `tests/test_week_numbers.py`, each using `Database(Path(tempdir) / "brightline.db")`:

```python
def test_label_parsing(self):
    for label, expected in [("Week 2", 2), ("WEEK 04", 4), ("week1", 1), ("3주차", 3),
                            ("2026-07-13 to 2026-07-17", None), ("Week 0", None), ("", None)]:
        self.assertEqual(week_number_from_label(label), expected)

def test_same_dates_different_numbers_are_separate_weeks(self):
    a = db.ensure_period("2026-07-13", "2026-07-17", "Week 1")
    b = db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
    self.assertNotEqual(a["id"], b["id"]); self.assertEqual((a["week_number"], b["week_number"]), (1, 2))

def test_number_wins_over_dates(self):
    a = db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
    b = db.ensure_period("2026-08-03", "2026-08-07", "", week_number=2)
    self.assertEqual(a["id"], b["id"]); self.assertEqual(b["start_date"], "2026-07-13")

def test_unnumbered_weeks_still_match_by_dates(self):
    a = db.ensure_period("2026-07-13", "2026-07-17", "Summer")
    self.assertEqual(db.ensure_period("2026-07-13", "2026-07-17")["id"], a["id"])

def test_dates_only_lookup_is_refused_when_ambiguous(self):
    db.ensure_period("2026-07-13", "2026-07-17", "Week 1"); db.ensure_period("2026-07-13", "2026-07-17", "Week 2")
    with self.assertRaisesRegex(ValueError, "Several weeks share these dates"):
        db.ensure_period("2026-07-13", "2026-07-17")

def test_state_orders_numbered_weeks_newest_first_then_unnumbered(self):
    # Week 1, Week 4, Week 2, and unnumbered "Summer" -> labels ["Week 4", "Week 2", "Week 1", "Summer"]

def test_upgrade_from_old_schema_preserves_rows(self):
    # Build the OLD periods table (UNIQUE(start_date, end_date), no week_number) with sqlite3 directly,
    # plus one row each in period_exams, homework_periods, class_periods referencing a period
    # labelled "Week 2" and one labelled "2026-07-20 to 2026-07-24". Open Database(path).
    # Assert: counts unchanged in all four tables; PRAGMA foreign_key_check returns no rows;
    # week_number is 2 and None; exactly one file matches backups/brightline-before-week-number-*.db;
    # opening Database(path) again creates no second backup.

def test_upgrade_with_duplicate_label_numbers(self):
    # Old schema, two periods both labelled "Week 2" with different dates.
    # After upgrade the earlier start_date has week_number 2, the other None; no exception.
```

- [ ] **Step 2: Run** `tests.test_week_numbers` — expect import errors / failures.

- [ ] **Step 3: Implement.**
  - Schema script: `periods` gets `week_number INTEGER` and loses `UNIQUE(start_date, end_date)`; add `CREATE UNIQUE INDEX IF NOT EXISTS idx_periods_week_number ON periods(week_number) WHERE week_number IS NOT NULL` after the upgrade has run (not in the script, because the old table has no such column yet).
  - `_migrate_period_week_numbers()`: runs when `PRAGMA table_info(periods)` lacks `week_number`. Order: (1) `sqlite3.Connection.backup` into the backups directory beside the database, abort on failure; (2) on a dedicated connection run `PRAGMA foreign_keys = OFF`, then in one transaction create `periods_new`, copy rows with the backfilled number (assign each number to the earliest `start_date`, later duplicates `NULL`), drop `periods`, rename; (3) before commit compare row counts of `periods`, `period_exams`, `homework_periods`, `class_periods`, `imports`, `issues`, `report_archive` with the counts taken before step 2 and require an empty `PRAGMA foreign_key_check`; roll back and raise `RuntimeError("Week-number upgrade failed; the database was not changed.")` on any difference.
  - `_migrate_legacy_period_data` currently relies on `INSERT OR IGNORE` against the removed unique constraint; change it to look up by dates first.
  - `find_period`: number given (explicitly or parsed from `label`) → row with that `week_number`. Otherwise rows matching both dates: none → `None`; exactly one → it; several → the single unnumbered one if there is exactly one, else `ValueError("Several weeks share these dates. Choose the week by its number.")`.
  - `ensure_period`: `find_period`, else insert with `week_number` and label (`Week N` when the label is blank and a number is given).
  - `state()`: `ORDER BY week_number IS NULL, week_number DESC, start_date DESC, end_date DESC`.

- [ ] **Step 4: Run** `tests.test_week_numbers` then `tests.test_empty_start` and `tests.test_mvp` — expect PASS.

- [ ] **Step 5: Checkpoint** (`backend/database.py`, `tests/test_week_numbers.py`).

---

### Task 2: Server resolves weeks by number

**Files:**
- Modify: `backend/server.py` (`period_from_payload` at 759, `_reuse_existing_period` at 1134, the `DB.ensure_period` calls near 1567, 1749 and 2695)
- Test: `tests/test_week_upload_workflow.py`, `tests/test_week_data_rules.py` (extend)

**Interfaces:**
- Consumes: `DB.find_period`, `DB.ensure_period(..., week_number=)`, `week_number_from_label`.
- Produces: period dicts flowing through imports carry `week_number`; `period_from_payload` accepts an optional integer `week_number` in the payload and otherwise derives it from `period_label`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_week_data_rules.py` (reuse its `pdf()` helper):

```python
def test_added_week_with_reused_dates_is_its_own_week(self):
    # Week 2 exists on 2026-07-20..24. Import a Weekly Test PDF with
    # period_label="Week 5", period_start="2026-07-20", period_end="2026-07-24".
    # Assert a second period exists with week_number 5 and Week 2's comments are unchanged.

def test_added_week_with_existing_number_lands_in_that_week(self):
    # period_label="Week 2" with different dates -> import goes to the stored Week 2;
    # differing comment text raises replacement_confirmation_required.
```

- [ ] **Step 2: Run** both modules — expect the new tests to FAIL.

- [ ] **Step 3: Implement.** `_reuse_existing_period` delegates to `DB.find_period` and converts its `ValueError` to `ValidationError`. Each `ensure_period` call passes `week_number=period.get("week_number")`. Reject a payload `week_number` that is not an integer ≥ 1 with `ValidationError("Week number must be a whole number of 1 or more.")`.

- [ ] **Step 4: Run** `tests.test_week_data_rules`, `tests.test_week_upload_workflow`, `tests.test_comment_pdf_import`, `tests.test_mvp` — expect PASS.

- [ ] **Step 5: Checkpoint** (`backend/server.py` hunks from this task only, the two test files).

---

### Task 3: Split a sheet into week blocks

**Files:**
- Create: `backend/sheet_weeks.py`
- Test: `tests/test_sheet_week_blocks.py` (create)

**Interfaces:**
- Consumes: `normalize_header`, `HEADER_ALIASES`, `IntegrationError` from `backend.integrations`; `week_number_from_label` from `backend.database`.
- Produces: `split_week_blocks(values: list[list[Any]]) -> list[dict[str, Any]]`, each item `{"week_number": int, "values": list[list[Any]]}` ordered by week number. Returns `[]` when the sheet has no week heading. `values` is the block's own columns, starting at its first header row, with the blank class header cell replaced by `"Class"`, ready for `_automatic_sheet_values(values, "assignment")`.

- [ ] **Step 1: Write the failing tests.** Build the fixture with a helper:

```python
def block(week, marks_and_scores, heading=None):
    # -> rows: [["", "학생", "학년", "학교", heading or f"WEEK {week}", "", "", "", "", "weekly test 점수 (%)"],
    #           ["", "", "", "", "7/13", "7/14", "7/15", "7/16", "7/17", ""],
    #           ["Class A", name, grade, school, *five_marks, score], ...]
def side_by_side(*blocks, spacer=0):  # joins blocks row-wise, optional blank spacer columns
```

Tests:

```python
def test_three_blocks_non_consecutive(self):
    result = split_week_blocks(side_by_side(block(1, A), block(2, B), block(4, C)))
    self.assertEqual([b["week_number"] for b in result], [1, 2, 4])
    self.assertEqual(result[2]["values"][0][:2], ["Class", "학생"])
    self.assertEqual(len(result[0]["values"][0]), 10)

def test_blocks_feed_existing_detection(self):
    from backend import server
    for b in split_week_blocks(side_by_side(block(1, A), block(2, B))):
        _, _, preview = server._automatic_sheet_values(b["values"], "assignment")
        self.assertTrue({"student_name", "class_name", "weekly_test_score"} <= set(preview["suggested_mapping"]))

def test_single_block_with_heading_is_returned(self):      # len == 1
def test_no_week_heading_returns_empty(self):              # heading="과제" -> []
def test_heading_spellings(self):                          # "Week1", "week 01", "1주차" -> 1
def test_spacer_and_trailing_blank_columns_ignored(self):  # spacer=2, plus 3 trailing "" columns
def test_block_without_score_column(self):                 # block still returned, 9 columns wide
def test_named_class_header_kept(self):                    # header "수업" stays "수업", not "Class"
def test_block_without_heading_raises(self):
    with self.assertRaises(IntegrationError) as ctx: split_week_blocks(side_by_side(block(1, A), block(2, B, heading="")))
    self.assertEqual(ctx.exception.code, "week_heading_missing")
def test_duplicate_week_number_raises(self):               # code "duplicate_week_number", message names "Week 2"
def test_title_rows_above_header_are_skipped(self):        # two free-text rows above the header row
```

- [ ] **Step 2: Run** `tests.test_sheet_week_blocks` — expect FAIL (module missing).

- [ ] **Step 3: Implement `split_week_blocks`.** Algorithm, since the signature does not fix it:
  1. Header row = first of the top 50 rows holding a cell whose `normalize_header` is a `student_name` alias. No such row → `[]`.
  2. Header band = that row plus following rows (at most 3 in total) whose student column is blank.
  3. Week headings = cells in the band, or in the row directly above it, for which `week_number_from_label` returns a number. None → `[]`.
  4. A block starts at each student-name column. Its first column is the column to its left when that column's band cells are all blank and it has data below, or when it is a recognised `class_name` header; otherwise the student column itself.
  5. A block ends one column before the next block starts; the last block ends at the last column that has any non-blank cell in the band.
  6. A block's week is the single week heading inside its span. None → `IntegrationError("A student block has no WEEK heading. Label every block, for example WEEK 3.", "week_heading_missing")`. A number used twice → `IntegrationError("Week N appears more than once in this sheet.", "duplicate_week_number")`.
  7. Emit the band rows and every following row sliced to the span; blank the week-heading cell; write `"Class"` into the blank class header cell of the first band row.

- [ ] **Step 4: Run** `tests.test_sheet_week_blocks` — expect PASS.

- [ ] **Step 5: Checkpoint** (`backend/sheet_weeks.py`, `tests/test_sheet_week_blocks.py`).

---

### Task 4: Multi-week preview and import

**Files:**
- Modify: `backend/server.py` (`import_mapped_table` at 1397, `_google_sheet_preview` at 2774, `_google_sheet_sync` at 2811)
- Test: `tests/test_multi_week_sheet_import.py` (create)

**Interfaces:**
- Consumes: `split_week_blocks`; Task 2's period resolution.
- Produces:
  - `import_mapped_table(..., connection: sqlite3.Connection | None = None) -> str` — with a connection it writes inside it and does not commit.
  - `week_block_previews(blocks: list[dict]) -> list[dict]`, items `{"week_number": int, "label": "Week N", "exists": bool, "student_count": int, "homework_columns": int, "has_scores": bool}`.
  - `import_week_blocks(*, blocks: list[dict], year: int, source_name: str, source_provider: str, confirm_replace: bool = False) -> str`
  - Preview response gains `"weeks": [...]` for a block sheet; sync ignores `period_id` / `period_*` for one.

- [ ] **Step 1: Write the failing tests** (call `app.import_week_blocks` directly; fixture helpers imported from `tests.test_sheet_week_blocks`):

```python
def test_three_weeks_saved_in_one_call(self):
    summary = app.import_week_blocks(blocks=blocks_1_2_4, year=2026, source_name="Sheet · Tab", source_provider="google_sheets")
    periods = {p["week_number"]: p for p in app.DB.state()["periods"]}
    self.assertEqual(sorted(periods), [1, 2, 4])
    self.assertEqual(periods[4]["label"], "Week 4")
    # each student has a homework row and a score row in each of the three weeks
    self.assertIn("Week 1:", summary); self.assertIn("Week 4:", summary)
    self.assertEqual(app.DB.selected_period_id(), periods[4]["id"])

def test_failure_in_last_block_saves_nothing(self):
    # Week 4 block has a score of 140 -> ValidationError; afterwards no periods, classes or students exist.

def test_identical_reimport_needs_no_confirmation(self):
def test_changed_score_requires_confirmation_and_names_week(self):
    with self.assertRaises(IntegrationError) as ctx: app.import_week_blocks(blocks=changed_week_2, ...)
    self.assertEqual(ctx.exception.code, "replacement_confirmation_required")
    self.assertIn("Week 2", str(ctx.exception)); self.assertNotIn("Week 1", str(ctx.exception))
    # and the stored Week 2 score is unchanged
def test_blank_keeps_and_zero_replaces(self):           # blank cell keeps stored score; "0" with confirm stores 0
def test_week_2_updates_stored_week_and_keeps_comments(self):
    # stored Week 2 with a weekly_test comment; import block 2 -> comment text unchanged, score from sheet
def test_blocks_with_different_rosters(self):           # extra student only in Week 4; reordered rows in Week 2
def test_students_absent_from_sheet_untouched(self):
def test_one_import_entry_per_week(self):               # three rows in imports, each with its period_id
def test_preview_lists_weeks(self):
    self.assertEqual(app.week_block_previews(blocks_1_2_4)[0],
        {"week_number": 1, "label": "Week 1", "exists": False, "student_count": 3, "homework_columns": 5, "has_scores": True})
def test_single_week_sheet_without_heading_uses_selected_week(self):  # legacy path unchanged
```

- [ ] **Step 2: Run** `tests.test_multi_week_sheet_import` — expect FAIL.

- [ ] **Step 3: Implement.**
  - `import_mapped_table`: accept `connection`; use it in place of `DB.connect()` for the write phase.
  - `import_week_blocks`: for each block in week order run `_automatic_sheet_values(block["values"], "assignment")`, build the period `{"id": "", "label": f"Week {n}", "week_number": n, **dates}` where dates come from `_assignment_period_from_headers(headers, f"{year}-01-01")`, and call `import_mapped_table(kind="assignment", connection=transaction, confirm_replace=confirm_replace)`. Open one transaction around the loop. Catch an `IntegrationError` with code `replacement_confirmation_required` per block, record `Week N (…its examples…)`, and continue; after the loop, if any were recorded, roll back and raise one `IntegrationError("This import would replace different existing data in " + "; ".join(recorded) + ". Confirm replacement to continue.", "replacement_confirmation_required")`. Any other exception rolls back and is re-raised with `Week N: ` prefixed to its message. Return the per-week summaries joined with ` · `, each prefixed `Week N: `.
  - `_google_sheet_preview`: for `kind == "assignment"`, call `split_week_blocks(result["values"])`; when non-empty, build the response from the first block's preview plus `"weeks": week_block_previews(blocks)` and omit `assignment_period`.
  - `_google_sheet_sync`: for `kind == "assignment"` with blocks, skip `period_from_payload`, take `year` from `payload["period_start"]` when it parses, else the current year, and call `import_week_blocks`.

- [ ] **Step 4: Run** `tests.test_multi_week_sheet_import`, `tests.test_automatic_google_sheet_detection`, `tests.test_mvp` — expect PASS.

- [ ] **Step 5: Checkpoint** (`backend/server.py` hunks from this task, `tests/test_multi_week_sheet_import.py`).

---

### Task 5: Cumulative history and average

**Files:**
- Modify: `backend/server.py` (`weekly_test_history` at 1827, `get_ready_student` at 1877)
- Test: `tests/test_week_data_rules.py` (extend)

**Interfaces:**
- Produces:
  - `weekly_test_history(class_id: str, student_id: str, period_id: str | None) -> tuple[list[dict], list[dict]]`; series items gain `"week_number": int | None`.
  - `cumulative_test_average(series: list[dict]) -> float | None` — mean of non-`None` `student` values, `None` when there are none.
  - Student payload gains `"test_average": float | None` and `"test_weeks": list[str]` (labels of the weeks that contributed).

- [ ] **Step 1: Write the failing tests:**

```python
def test_history_includes_only_weeks_up_to_chosen_number(self):
    # scores: Week 1 = 80, Week 2 = 90, Week 4 = 70, all three weeks on identical dates
    _, series = app.weekly_test_history(self.class_id, sid, week2_id)
    self.assertEqual([i["week_number"] for i in series], [1, 2])
    _, series = app.weekly_test_history(self.class_id, sid, week4_id)
    self.assertEqual([i["student"] for i in series], [80.0, 90.0, 70.0])

def test_average_matches_hand_computed_value(self):
    self.assertEqual(app.get_ready_student(sid, week4_id)["test_average"], 80.0)
    self.assertEqual(app.get_ready_student(sid, week4_id)["test_weeks"], ["Week 1", "Week 2", "Week 4"])
    self.assertEqual(app.get_ready_student(sid, week2_id)["test_average"], 85.0)

def test_week_without_student_score_is_left_out(self):
    # classmate scored in Week 2, this student did not: series item for Week 2 has student None,
    # test_average averages Weeks 1 and 4 only -> 75.0, test_weeks == ["Week 1", "Week 4"]

def test_no_scores_gives_no_average(self):                # test_average is None, test_series == []
def test_unnumbered_week_keeps_date_rule(self):           # legacy period: weeks with end_date <= its end_date
```

- [ ] **Step 2: Run** `tests.test_week_data_rules` — expect the new tests to FAIL.

- [ ] **Step 3: Implement.** For a period with a `week_number`, select the class's rows `WHERE p.week_number IS NOT NULL AND p.week_number <= ? ORDER BY p.week_number`; otherwise keep the existing `p.end_date <= ?` query. Update the one caller.

- [ ] **Step 4: Run** `tests.test_week_data_rules`, `tests.test_report_charts`, `tests.test_adaptive_reports` — expect PASS.

- [ ] **Step 5: Checkpoint** (`backend/server.py` hunks from this task, `tests/test_week_data_rules.py`).

---

### Task 6: Graph labels and subtitle

**Files:**
- Modify: `backend/report_charts.py` (`draw_weekly_test_chart` at 258), `backend/adaptive_reports.py` (`_draw_chart` at 383)
- Test: `tests/test_report_charts.py` (extend)

**Interfaces:**
- Consumes: series items with `week_number`, `period_label`, `start_date`, `end_date`, `student`; payload `test_average`.
- Produces: `weekly_test_subtitle(series: list[dict], average: float | None) -> str` in `backend/report_charts.py`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_subtitle_formats(self):
    s = lambda n, v=80.0: {"week_number": n, "period_label": f"Week {n}", "start_date": "2026-07-13", "end_date": "2026-07-17", "student": v}
    self.assertEqual(weekly_test_subtitle([s(1), s(2), s(4)], 84.0), "Week 1 ~ Week 4 · 평균 84%")
    self.assertEqual(weekly_test_subtitle([s(2)], 84.25), "Week 2 · 평균 84.2%")
    self.assertEqual(weekly_test_subtitle([s(1), s(2)], None), "Week 1 ~ Week 2")
    legacy = {"week_number": None, "period_label": "Summer", "start_date": "2026-07-13", "end_date": "2026-07-17", "student": 80.0}
    self.assertEqual(weekly_test_subtitle([legacy], 80.0), "기간: 2026-07-13 ~ 2026-07-17 · 평균 80%")

def test_chart_axis_uses_week_labels(self):
    # draw to a ReportLab canvas, extract text with PyMuPDF: contains "Week 1" and "Week 4",
    # and does not contain "2026-07-17"

def test_single_point_chart_renders(self):                # one series item draws without error
```

- [ ] **Step 2: Run** `tests.test_report_charts` — expect FAIL.

- [ ] **Step 3: Implement.** In `draw_weekly_test_chart` the label is `Week N` when `week_number` is set, else the current `end_date`/`period_label` fallback. `_draw_chart` passes `weekly_test_subtitle(series, student.get("test_average"))`. Use `f"{round(value, 1):g}"` for the number.

- [ ] **Step 4: Run** `tests.test_report_charts`, `tests.test_adaptive_reports` — expect PASS.

- [ ] **Step 5: Checkpoint** (the two backend files, `tests/test_report_charts.py`).

---

### Task 7: Missed-test rule

**Files:**
- Modify: `backend/server.py` (state assembly near 701–708, `get_ready_student` near 1905), `scripts/check_reports.py`, `frontend/app.js` (any use of `missing_test_score_warning`)
- Test: `tests/test_week_data_rules.py`, `tests/test_adaptive_reports.py` (extend)

**Interfaces:**
- Produces: `missing_test_assumption(*, pdf_template_ready: bool, guide: dict, weekly_test_text: str, exams: list[dict]) -> bool` in `backend/server.py`, used at both sites. True only when the template is ready, the guide has a reliable `weekly_test` section, the text is blank, and no exam in `effective_score_exams(exams)` has a score. The field `missing_test_score_warning` is removed everywhere.

- [ ] **Step 1: Write the failing tests:**

```python
def test_score_without_comment_shows_graph_not_notice(self):
    payload = app.get_ready_student(sid, week_id)          # Sheet score 73, no weekly_test comment
    self.assertFalse(payload["missing_test_feedback_assumption"])
    self.assertEqual(payload["test_average"], 73.0)

def test_neither_score_nor_comment_shows_notice(self):     # assumption True

def test_rendered_report_with_score_and_no_comment(self):
    # in tests/test_adaptive_reports.py with its synthetic template: generate the PDF, extract text:
    # contains "Week 1 · 평균 73%", does not contain "응시하지 않았기에"; page count == 1
```

- [ ] **Step 2: Run** both modules — expect the new tests to FAIL. Existing tests that assert the old rule (score present, notice shown) fail after Step 3 and must be rewritten to the new rule, not deleted.

- [ ] **Step 3: Implement** the helper, call it at both sites, and remove `missing_test_score_warning` (`grep -rn missing_test_score_warning` must return nothing outside `docs/superpowers/`).

- [ ] **Step 4: Run** `tests.test_week_data_rules`, `tests.test_adaptive_reports`, `tests.test_mvp`, `tests.test_report_comment_workflow`; `node --check frontend/app.js` — expect PASS.

- [ ] **Step 5: Checkpoint.**

---

### Task 8: Upload window shows the sheet's weeks

**Files:**
- Modify: `frontend/app.js` (`nextWeekNumber` at 683, upload modal render near 882, preview handling near 963, sync payload near 1041), `frontend/styles.css` if a list style is needed
- Test: `tests/test_week_upload_workflow.py` (extend; this module asserts on `frontend/app.js` source text)

**Interfaces:**
- Consumes: preview response `weeks` from Task 4; period objects with `week_number`.

- [ ] **Step 1: Write the failing contract tests**, following the module's existing source-assertion style:

```python
def test_multi_week_preview_is_rendered_and_week_select_disabled(self):
    # app.js contains a renderer reading `preview.weeks`, builds each row through escapeHtml,
    # and the #upload-week-select is disabled while the staged source has weeks
def test_multi_week_sync_omits_period_fields(self):
def test_next_week_number_prefers_week_number(self):
```

- [ ] **Step 2: Run** `tests.test_week_upload_workflow` — expect FAIL.

- [ ] **Step 3: Implement.** When the staged Sheet preview has `weeks`, show one line per week — `Week 1 · new · 29 students · 5 homework days · scores` (`existing` instead of `new` when `exists`; `no scores` when `has_scores` is false) — above the existing sample table, add the note `This sheet names its own weeks.`, disable the Week selector and "+ Add week", and send the sync request without `period_id`, `period_label`, `period_start` or `period_end`. `nextWeekNumber` uses `period.week_number` when present before falling back to the label regex. All values go through `escapeHtml`.

- [ ] **Step 4: Run** `tests.test_week_upload_workflow`, `tests.test_google_sheet_preview_scrolling`, `tests.test_modal_scroll_isolation`; `node --check frontend/app.js` — expect PASS.

- [ ] **Step 5: Checkpoint.**

---

### Task 9: Documentation, baseline and rendered check

**Files:**
- Modify: `README.md`, `docs/PRODUCT_REQUIREMENTS.md`, `AGENTS.md`

- [ ] **Step 1: Update the three documents** so they agree with the code on: week identity by number (dates descriptive, same dates allowed); one Sheet may hold several `WEEK N` blocks, imported all-or-nothing; the class column may have a blank header; graph labels `Week N`; the subtitle with the student's cumulative average; a week without a score is omitted; the missed-test notice only when there is neither score nor comment; the automatic backup before the one-time upgrade. Remove the sentences stating the notice appears "even if a score is stored".

- [ ] **Step 2: Run the baseline:**

```bash
.venv/bin/python -m unittest discover -v
.venv/bin/python -m unittest tests.test_empty_start -v
node --check frontend/app.js
git diff --check
```

Expected: all pass, no whitespace errors.

- [ ] **Step 3: Rendered check on a copy of the real data.** Copy `data/` to a temporary directory, start with `BRIGHTLINE_DATA_DIR` pointing at the copy, and confirm: the upgrade wrote a backup inside the copy; stored weeks have numbers 2 and 3; `scripts/check_reports.py --output-dir <temp>` renders; open the PNGs for one student in each stored week and check the `Week N` axis labels, the average subtitle, no clipped text. Then import the three-block fixture layout through `import_week_blocks` on the copy and render Week 1, Week 2 and Week 4 for one student with full history and one with a gap. Report what was and was not verified.

- [ ] **Step 4: Hand over.** State that the live Google import of the real sheet is not verified: it needs the maintainer to reconnect Google in the app, after which the real `data/` directory is upgraded on first start (backup written automatically).

- [ ] **Step 5: Checkpoint.**
