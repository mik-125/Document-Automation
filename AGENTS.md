# AGENTS.md

This file applies to the entire Brightline Reports repository.

## Working relationship

- Act as a concise senior full-stack engineer. The primary maintainer is a beginner programmer, so explain consequential choices in plain language and proactively catch security, data-loss, maintainability, and architecture risks.
- Prefer a complete, production-ready implementation over pseudocode or scaffolding. Include imports and do not leave placeholders unless explicitly requested.
- Make reasonable assumptions when they do not materially change the product. State them briefly before coding.
- If missing information would materially change the implementation, ask a short clarifying question before editing.
- Do not modify files outside the user's stated scope. If a correct change requires more files, list them as `Requires edits in: X, Y, Z. Proceed?`
- Do not overwrite, revert, reformat, or stage unrelated working-tree changes. This repository may contain valuable uncommitted work.
- For debugging, do not guess. Collect the exact error, stack trace, relevant files, and reproduction steps, then trace the failing path before proposing a fix.

## Product invariants

Brightline is a single-academy, local-first student-report generator. A fresh installation must start empty.

- Never seed or commit real classes, students, scores, comments, uploaded files, report mappings, completed samples, generated reports, credentials, or local database state.
- Treat `data/` and any `BRIGHTLINE_DATA_DIR` as private runtime state. Preserve existing local data during development and Git operations.
- The original blank PDF is authoritative for layout, page artwork, section placement, and confirmed mappings.
- Completed report samples are class-specific content evidence only. Never use them as page backgrounds or copy their student identity, scores, or feedback into another report.
- Preserve teachers' source wording. Routing may organize passages, but must not paraphrase, invent, silently discard, or duplicate content.
- Evaluate each class and report format independently. Do not generalize one subject's headings, fonts, charts, or layout to every class.
- Ambiguous imports, identities, mappings, or unsupported layouts must require review instead of silently guessing.
- The system performs searchable-PDF parsing and evidence-based routing, not OCR or general semantic AI. Do not claim broader support without separate validation.
- Keep the existing academy-requested missing-test policy: when the required PDF sample evidence exists and Weekly Test feedback is absent, use the approved notice, suppress the conflicting test graph, and fit the report on one page. Do not mutate stored scores or represent this assumption as verified attendance.

Read `README.md` and `docs/PRODUCT_REQUIREMENTS.md` before changing report readiness, imports, PDF layout, templates, privacy behavior, or Google connections.

## Repository map

- `server.py`: repository-root launcher; delegates to `backend.server.run()`.
- `backend/server.py`: HTTP server, route handling, request validation, local authentication, application-state assembly, imports, report generation, and downloads.
- `backend/database.py`: SQLite schema, migrations, transactions, persistence, readiness, provenance, and reset behavior.
- `backend/integrations.py`: tabular mapping and Google Sheets, Docs, Drive, and OAuth integration.
- `backend/report_engine.py`: CSV/PDF parsing, template inspection, report payloads, mapped PDF generation, and validation.
- `backend/adaptive_reports.py`: sample-aware PDF layout, fitting, pagination, and continuation behavior.
- `backend/template_mapping.py`: mapping schema and validation. Published mappings, not drafts, drive report generation.
- `backend/sample_content.py`, `backend/comment_sections.py`, `backend/pdf_content.py`: evidence extraction and comment-section routing.
- `backend/report_charts.py`, `backend/weekly_report.py`: charts and fallback weekly-report rendering.
- `frontend/index.html`, `frontend/styles.css`, `frontend/app.js`: dependency-free browser UI. There is no frontend build step.
- `tests/`: Python `unittest` regression and frontend-contract tests.
- `assets/royal-ib-report-template.pdf`: bundled two-page fillable Royal IB template; treat it as a binary design asset.

## Architecture and implementation rules

- Keep the application simple: Python standard-library HTTP server, SQLite, vanilla HTML/CSS/JavaScript, and the existing PDF and Google libraries. Add dependencies only when the platform or existing packages cannot solve the problem cleanly.
- Run the application from the repository root so paths resolve correctly.
- Keep backend persistence, API payloads, frontend state, and tests synchronized when a data contract changes.
- Use database methods and explicit transactions for multi-step writes. Preserve foreign keys, WAL mode, import provenance, class/period scoping, and rollback-on-failure behavior.
- Student matching must prefer stable external IDs. Name-only matching is allowed only when exact and unambiguous within the class, optionally narrowed by school or grade.
- Validate request content type, payload shape, base64 data, numeric ranges, file size, file type, path/filename safety, class/period ownership, and replacement confirmation before writing.
- Preserve zero scores and distinguish them from missing values. Blank reimports must not erase valid existing scores unless replacement is explicitly confirmed.
- Uploaded comment PDFs must remain scoped to one selected class, reporting period, and comment section. Unknown or ambiguous students go to review; they must not be auto-assigned.
- Do not expose arbitrary filesystem paths or serve files outside the explicit frontend allowlist and controlled report-download paths.
- Frontend HTML derived from data must be escaped. Prefer `textContent` for plain text; if rendering with `innerHTML`, pass every untrusted value through `escapeHtml`.
- Keep modal scroll locking centralized, bounded previews progressive, and long tables horizontally and vertically contained.
- Maintain accessible labels, button states, error messages, and loading/generation guards when changing UI workflows.

## Security and privacy boundaries

- The server is intentionally loopback-only. Preserve the `127.0.0.1`/`localhost`/`::1` host checks, same-origin checks, cross-site rejection, and bind restrictions.
- Do not expose the development server directly to a LAN or the public internet. Public access requires an explicitly approved authenticated HTTPS design.
- Basic authentication is an optional additional localhost layer, not a substitute for transport security or authorization.
- Never log or return OAuth codes, client secrets, refresh tokens, authorization headers, full credential responses, student comments, or sensitive report contents.
- Google OAuth client configuration and tokens must stay in ignored local files with restrictive permissions. Do not accept service-account JSON as an OAuth client.
- Preserve least-privilege Google scopes and explicit user connection. Saved setup may survive restarts, but Google access must not silently reactivate at server startup.
- Validate Google file IDs/URLs and permissions; do not accept arbitrary URLs or assume Drive files are writable.
- Keep generated Google files when disconnecting or clearing local imports unless the user explicitly requests their deletion.
- Never weaken authentication, authorization, input validation, origin enforcement, or permission boundaries to make a test pass.

## PDF and report rules

- Never clip, truncate, or silently drop source text. If content cannot fit at the configured safety floor, require review with an actionable error.
- Preserve words across line and page boundaries, including Korean and English text, identifiers, decimals, and numbered question blocks.
- Weekly Test feedback normally stays in its designated area unless the published mapping explicitly provides an allowed continuation region.
- Teacher-comment continuation pages are allowed only under the maintained pagination rules; compact sparse trailing content instead of producing nearly empty pages.
- Keep unused trailing template pages out of generated reports while retaining every page required by mapped content.
- Continuation areas must be explicit, non-overlapping, in bounds, tied to a supported source field, and capped by the mapping schema's page limit.
- Preserve the original template background. Generated continuation pages must use a safe, readable heading and panel rather than guessing at unsupported artwork.
- After any PDF change, verify both extracted content and rendered pages. Check text conservation, duplicates, clipping, font readability, charts, identity fields, page order, and continuation headings.
- Use synthetic fixtures in tests. Authorized local examples may be used for private verification but must never be committed.

## Local data and generated files

The following are private or generated and must remain untracked:

- `data/`, including `brightline.db`, uploads, mappings, templates, examples, reports, exports, tokens, and OAuth client files
- `output/`, `tmp/`, `.env`, `.venv/`, `__pycache__/`, and `*.pyc`
- credential-like JSON files, tokens, client secrets, backups, and `.DS_Store`

Use `BRIGHTLINE_DATA_DIR` with a temporary directory for isolated tests or experiments. Never point destructive tests or reset flows at the user's real `data/` directory.

## Development commands

Run commands from the repository root.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open `http://127.0.0.1:8000`.

Required baseline verification for code changes:

```sh
.venv/bin/python -m unittest discover -v
node --check frontend/app.js
git diff --check
```

Also run the smallest relevant test module while iterating. Examples:

- Import/comment parsing: `tests.test_comment_pdf_import`, `tests.test_comment_sections`, `tests.test_week_upload_workflow`
- Google setup/imports: `tests.test_google_configuration`, `tests.test_automatic_google_sheet_detection`, `tests.test_google_sheet_preview_scrolling`
- Templates/PDFs: `tests.test_pdf_template_mapping`, `tests.test_adaptive_reports`, `tests.test_sample_content`, `tests.test_weekly_report`
- Readiness/report workflow: `tests.test_report_comment_workflow`, `tests.test_report_zip_manifest`, `tests.test_mvp`
- UI contracts: `tests.test_modal_scroll_isolation`, `tests.test_template_table_ui`
- Empty default state: `tests.test_empty_start`

For UI or end-to-end changes, tests alone are insufficient. Start the real server, verify `/` and `/api/state`, and exercise the affected workflow in the browser. For report changes, generate and inspect the longest realistic authorized or synthetic case.

Live Google checks require the maintainer's private OAuth client, test account, source Sheet, and template Doc. Unit tests must not require network access or real credentials.

## Change discipline

- Keep changes focused and readable. Avoid unnecessary abstractions, frameworks, build systems, and broad rewrites.
- Update or add regression tests for every bug fix or behavior change.
- If behavior changes, update `README.md` or `docs/PRODUCT_REQUIREMENTS.md` only with the user's permission when those files were not already in scope.
- Before committing, inspect the complete diff and staged file list. Confirm that no local data, reports, uploads, credentials, machine paths, or unrelated changes are included.
- Do not commit, push, publish mappings, migrate real local data, reset data, or start public sharing unless the user explicitly asks.
- Report verification honestly. Distinguish unit tests, syntax checks, live server checks, browser checks, rendered-PDF inspection, and real Google integration checks.
