# Multi-week Sheet import and cumulative weekly reports

Date: 2026-11-11
Status: awaiting maintainer review

## Goal

The maintainer pastes one Google Sheets URL and Brightline imports every week
in it. They can then pick any week and generate a report that covers week 1 up
to that week, showing the student's average test score over those weeks and a
graph of the score across them.

## Decisions already made by the maintainer

1. The dates under each week block in the sample sheet are placeholders. A week
   is identified by its week number, not its dates.
2. The average printed on a report is the student's own average.
3. Sheet weeks meet stored weeks by number: sheet WEEK 2 updates stored Week 2
   and keeps its comments; new numbers create new weeks; other stored weeks are
   untouched.
4. The Weekly Test graph and average are shown whenever a score exists, even
   when the student has no Weekly Test comment for that week.

## Current behaviour (verified in code)

- One Sheet import fills exactly one week. A header row with repeated column
  names is rejected (`duplicate_headers`), and more than one test-score column
  is rejected (`ambiguous_test_score`). A sheet with several week blocks side
  by side therefore cannot be imported.
- A week (`periods` row) is unique on `(start_date, end_date)`. The label is
  only descriptive. Two weeks with the same dates are the same week.
- `weekly_test_history()` already returns every week whose `end_date` is on or
  before the chosen week's, and the report already plots student score, class
  average, highest and lowest per week. X-axis labels are end dates.
- No report prints the student's average across weeks.
- With a published PDF template and sample evidence of a Weekly Test section,
  a student with no Weekly Test comment gets the "did not take the test"
  notice and no graph, even when a score is stored.

## Sheet layout to support

Blocks repeat left to right. Each block has, in order: a class column, a
student column, optional grade and school columns, dated homework columns
under a merged `WEEK N` heading, and one weekly test percentage column. Week
numbers need not be consecutive (the sample has 1, 2 and 4). Every block lists
the same students in the same rows, but the import must not rely on that.

## Design

### 1. Week identity

- Add `periods.week_number INTEGER NULL` with a unique index over non-null
  values. Remove `UNIQUE(start_date, end_date)`.
- Backfill: a label matching `Week N`, `WEEK N` or `N주차` sets `week_number`
  to N. A label that does not match leaves it null.
- Lookup order when an import names a week: by `week_number` when the import
  has one; otherwise by `(start_date, end_date)` among weeks whose
  `week_number` is null. This keeps unnumbered legacy weeks working as today.
- "+ Add week" drafts are already labelled `Week N`; they carry N as their
  week number, so a draft that reuses an existing number lands in that week and
  a draft with a new number never collides on dates.
- Week lists are ordered by `week_number`; unnumbered weeks follow, ordered by
  `end_date`.
- Dates remain stored and displayed as written in the source. They no longer
  decide identity or order for numbered weeks.

Migration: SQLite cannot drop a table constraint in place, and `periods` is
referenced with `ON DELETE CASCADE`. The upgrade therefore:

1. writes a backup to `data/backups/brightline-before-week-number-<timestamp>.db`
   using SQLite's backup API and stops if that fails;
2. rebuilds `periods` with foreign keys disabled, inside one transaction, by
   creating the new table, copying rows, dropping the old table and renaming;
3. runs `PRAGMA foreign_key_check` and compares row counts of `periods`,
   `period_exams`, `homework_periods`, `class_periods`, `imports`, `issues`
   and `report_archive` before committing, and rolls back on any difference.

The upgrade runs once, guarded by a schema marker, and is a no-op on an empty
database.

### 2. Multi-week Sheet import

- Entry point is unchanged: the "Homework + test scores" upload, Google Sheet
  option, paste URL.
- Detection: scan the first header rows for `WEEK N` / `N주차` headings. With
  one or more, the sheet names its own weeks. Columns are split into blocks at
  each student-name header; a block runs from its class column through its
  test-score column. Each block's columns are then passed to the existing
  single-week detection and mapping, so header aliases, date parsing,
  blank-row handling and score validation stay in one place.
- The class column in the sample sheet has a blank header, which today's
  detection rejects. Within a block, a column immediately left of the
  student-name column with a blank header and non-blank data is the class
  column.
- A sheet with no week heading follows today's single-week path and uses the
  week chosen in the upload window.
- Preview returns one entry per block: week number, whether that week already
  exists, student count, number of homework columns, and whether scores are
  present. The upload window shows this list and disables its Week selector,
  because the sheet names its own weeks.
- Save imports all blocks in one database transaction. If any block fails
  validation, nothing is saved and the error names the week.
- Replacement: conflicts are collected across all blocks first. One
  `replacement_confirmation_required` error names the affected weeks and
  students; saving proceeds only with `confirm_replace`. Existing rules hold:
  a blank cell keeps the stored score, zero is a score, identical data is not a
  conflict.
- Errors: a block without a week heading, two blocks with the same week
  number, or a week number below 1 stop the import with a specific message.
- Students present in Brightline but absent from the sheet are not changed or
  removed.
- Import provenance records one import entry per week so each can be reviewed
  or removed separately, as today.

### 3. Cumulative report content

- For a numbered week N, `weekly_test_history()` selects the class's numbered
  weeks with `week_number <= N`, ordered by number. For an unnumbered week it
  keeps today's date rule.
- A week contributes to a student's series only if that student has an
  effective score in it (Sheet score first, else the PDF `총점` score). A week
  with no score is omitted; it is never drawn as zero.
- Graph x-axis labels are `Week N` for numbered weeks instead of end dates.
- Cumulative average: the arithmetic mean of the student's weekly percentages
  over the included weeks, shown to one decimal place with a trailing `.0`
  dropped. It is printed on the Weekly Test graph's subtitle line, replacing
  the date range: `Week 1 ~ Week 4 · 평균 84%`, or `Week 2 · 평균 84%` when one
  week is included.
- The student payload gains `test_average` and `test_weeks` so preview and
  diagnostics can show the same numbers the PDF prints.
- The 과제이행도 graph is unchanged: it shows the chosen week's daily marks with
  the dates as written.

### 4. Missed-test rule change

New rule: the "did not take the test" notice is printed, and the graph and
average are omitted, only when the student has neither a Weekly Test comment
nor an effective Weekly Test score for the chosen week.

- Score but no comment: graph and average are shown; the narrative area is
  left blank. No text is generated.
- Comment but no score for the chosen week: narrative is shown; the graph
  shows earlier weeks' scores if any, otherwise no graph.
- The rule still applies only to published PDF templates with sample evidence
  of a Weekly Test section.

This replaces the documented academy assumption that a stored score is ignored
when feedback is missing, so `README.md`, `docs/PRODUCT_REQUIREMENTS.md` and
`AGENTS.md` must change with the code.

### 5. Out of scope

- Comment PDF parsing, template mapping, sample analysis and pagination.
- Google Docs template output; it gains no average or cumulative graph.
- The uncommitted `BRIGHTLINE_PUBLIC_ORIGIN` change in the working tree.
- Layout glitches noted during the earlier run (school name overflow, score
  label overlap).

## Files expected to change

- `backend/database.py`: schema, migration, period lookup and ordering.
- `backend/server.py`: block detection, multi-week preview and sync, period
  resolution by number, `weekly_test_history()`, missed-test condition,
  payload fields.
- `backend/report_charts.py`, `backend/adaptive_reports.py`: axis labels and
  subtitle with the average.
- `frontend/app.js`: multi-week preview list, disabled Week selector, week
  ordering.
- `tests/`: new and updated modules listed below.
- `README.md`, `docs/PRODUCT_REQUIREMENTS.md`, `AGENTS.md`.

## Testing

All tests use synthetic data and a temporary `BRIGHTLINE_DATA_DIR`.

- Block detection: three blocks with non-consecutive week numbers; a single
  week sheet still takes the legacy path; missing heading, duplicate number
  and invalid number each raise their specific error.
- Import: three weeks saved in one call; a failure in the last block leaves
  the database unchanged; identical re-import raises no conflict; a changed
  score requires confirmation and names the week; blank keeps, zero replaces.
- Identity: two numbered weeks with identical dates stay separate; an import
  for week 2 lands in the stored Week 2; unnumbered legacy weeks still match
  by dates.
- Migration: a database built with the old schema and populated rows upgrades
  with identical row counts, a clean `foreign_key_check`, a backup file on
  disk, and backfilled week numbers; a second start does not migrate again.
- History and average: week N includes weeks 1..N only; a week without a score
  is omitted; the average matches a hand-computed value; one-week formatting.
- Missed-test rule: score without comment shows graph and average and no
  notice; neither score nor comment shows the notice.
- Baseline: `unittest discover`, `tests.test_empty_start`,
  `node --check frontend/app.js`, `git diff --check`.

Manual verification after implementation, on a copy of the real data
directory: upgrade, import the sample sheet after reconnecting Google, then
render and inspect Week 1, Week 2 and Week 4 reports for one student with a
full history and one with a gap.

## Risks

- The migration rebuilds a table that other tables cascade from. The backup,
  disabled foreign keys, row-count comparison and rollback exist to contain
  that. It is never run against the real data directory by tests.
- Reports for stored weeks change: students who currently get the missed-test
  notice despite a stored score will get a graph instead. Saved report history
  is not rewritten.
- Graph labels change from dates to week names for every numbered week,
  including weeks imported before this change.
