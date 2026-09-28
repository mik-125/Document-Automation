# AGENTS.md

These instructions apply to the entire Brightline Reports repository. They condense the current `README.md` and `docs/PRODUCT_REQUIREMENTS.md` into one operating contract for coding agents.

## Authority and conflict handling

- Follow the user's latest explicit request first, then this file.
- Treat the product rules below as authoritative. The README, product requirements, implementation, and tests are expected to agree with them.
- If code, tests, or documentation contradict these rules, do not silently choose one or broaden the product. Report the exact conflict and ask which contract should change.
- A behavior change is incomplete until its code, tests, `README.md`, `docs/PRODUCT_REQUIREMENTS.md`, and this file agree. Ask permission before editing files outside the user's stated scope.

## How to work with the maintainer

- Act as a concise senior full-stack engineer. The maintainer is a beginner programmer, so explain consequential choices plainly and proactively flag security, privacy, data-loss, architecture, and maintainability risks.
- Before coding, state material assumptions. Ask a short question only when the missing answer would change the implementation.
- Deliver complete, production-ready code with imports and tests; do not leave pseudocode or placeholders unless requested.
- Do not modify files outside the requested scope. If more files are necessary, say: `Requires edits in: X, Y, Z. Proceed?`
- Preserve all unrelated working-tree changes. Never overwrite, revert, reformat, stage, or commit them.
- For debugging, do not guess. Collect the exact error, stack trace, relevant files, and reproduction steps, then trace the failing path.
- Keep changes simple and focused. Prefer existing platform features and dependencies over new packages or abstractions.
- In the maintainer's local Codex environment, prefix shell commands with `rtk` when it is available.

## Product definition

Brightline is a single-academy, local-first student-report generator. It:

- imports homework and Weekly Test scores from one Google Sheet through the official Sheets API;
- imports searchable PDFs containing 과제이행도, Weekly Test, and general comments;
- supports a shared 과제이행도 comment for a selected class and reporting period;
- uses either a reviewed, published mapping for an original PDF template or a tested Google Docs template;
- uses completed report samples only to understand class-specific content structure;
- exports individual student PDFs and class ZIP archives;
- stores application data locally in SQLite and ignored files.

The browser upload workflow does not accept CSV. Mapped CSV and the legacy Royal IB Assignment format remain backend/API capabilities. Supported external integrations are Google Sheets and Google Docs/Drive. Do not add scraping or unofficial integrations.

## Empty-state and private-data contract

- A fresh installation contains no classes, students, reporting periods, scores, comments, uploads, templates, samples, mappings, or reports. A Week 1 UI option is not seeded data.
- Never hardcode or commit real student identities, class names, database IDs, scores, comments, uploads, completed samples, generated reports, mappings, OAuth credentials, tokens, or machine-specific paths.
- Treat the repository-root `data/` directory and any `BRIGHTLINE_DATA_DIR` as private runtime state. Preserve existing contents during Git operations.
- Keep `data/`, `output/`, `tmp/`, `.env`, `.venv/`, `__pycache__/`, `*.pyc`, credential-like JSON, tokens, backups, and `.DS_Store` untracked.
- Use a temporary `BRIGHTLINE_DATA_DIR` for tests and experiments. Never aim reset, migration, or destructive test flows at the maintainer's real data directory.
- Clearing local Brightline data must not delete report Docs already created in Google Drive.

## Required user workflow and readiness

1. Connect Google and import homework plus Weekly Test scores from one Google Sheet. This creates reporting periods, classes, and students. Blank reimported scores preserve existing scores; zero is a valid score.
2. Add the required 과제이행도 comment for the same class and period through a searchable individual PDF or a shared class comment.
3. Import searchable Weekly Test comments, general comments, or both. Each PDF represents exactly one class. Unknown or ambiguous students must go to manual review.
4. Configure each class with either an original blank PDF whose mapping is reviewed and published, or a Google Docs template containing all required placeholders.
5. Resolve unmatched comments, preview at least one report, then download individual reports or a class ZIP. Ready students may generate while classmates remain incomplete.

Normal readiness requires assignment data, the required 과제이행도 comment, a Weekly Test score, and at least one Weekly Test or general comment. The only exception is the documented missing-test-feedback policy below.

Student matching should use a stable Student ID. Without one, accept only an exact, unambiguous name within the class, optionally narrowed by school or grade. Never guess identity.

## Templates, samples, and report content

- The original blank PDF is the design authority for pages, artwork, structure, section positions, and confirmed mappings.
- A completed sample is class-specific content evidence only. It must never become a background or a source of another student's identity, scores, comments, or wording.
- Preserve each teacher's exact wording and source-section boundaries. Routing may organize intact passages but must not paraphrase, invent, lose, or duplicate content.
- Evaluate every class and report format independently. Do not generalize headings, fonts, charts, routing, or layouts from one subject.
- This is searchable-PDF parsing and local evidence-based routing, not OCR or general semantic AI. Ambiguous or unsupported content requires review instead of guessing.
- Only a published PDF mapping drives report generation. Saving a draft does not activate it. Publishing is a local configuration action, not public transmission.
- Google Docs templates are an alternative to PDF templates. Required placeholders are `student_name`, `class_name`, `reporting_week`, `assignment_summary`, `weekly_test_summary`, and `teacher_comment`. Optional supported placeholders are `student_id`, `grade_or_year`, `school`, `assignment_percent`, and `weekly_test_score`. Placeholders inside drawings or images are unsupported.

## PDF fitting and pagination rules

- Never clip, truncate, silently discard, or duplicate source text. If the rules cannot be satisfied, return an actionable review error.
- Preserve Korean and English words, identifiers, decimals, numbered question blocks, paragraph order, and section ownership across line and page boundaries.
- Fit recognized original-PDF panels at the largest suitable readable size. Test and teacher text may shrink below 8 points, but 5 points is the hard safety floor.
- Weekly Test feedback stays inside its first original text panel. Shrink it as needed; do not create a Weekly Test continuation page. Changing this rule requires explicit product approval and synchronized updates to all contract documents and tests.
- Additional teacher-comment pages are allowed only when the final comment panel is at least half occupied. Compact sparse remainders into earlier panels.
- Teacher overflow pages must derive from an appropriate original full-width feedback page, relabeled for the overflowing section. If unavailable, repeat the original section panel. Never derive layout or artwork from a completed sample.
- Omit unused trailing original pages. Preserve the original backgrounds and stored mapping.
- After any PDF change, verify extracted text and rendered pages for content conservation, duplicates, clipping, readable fonts, charts, identity fields, page order, pagination, and continuation headings.
- Use synthetic PDFs in committed tests. Authorized local examples may be used for private verification but must never be committed.

## Missing Weekly Test feedback policy

For a ready, published PDF template whose completed-sample analysis reliably identifies a Weekly Test section, missing Weekly Test feedback is treated as an academy-requested output assumption that the student missed the test, even if a stored score exists.

- Replace the Weekly Test narrative with: `학생은 이번 위클리 테스트를 응시하지 않았기에 정확한 이해도 판단이 어렵습니다. 위클리 테스트를 나중에 한 번 풀어보시는 것을 추천드립니다.`
- Suppress the Weekly Test graph and fit the report on one page.
- Do not alter the stored score, source comments, or claim verified attendance.
- Assignment data and 과제이행도 comments remain required.
- This exception does not apply to Google Docs or PDF templates without the required sample evidence.

## Architecture map

- `server.py`: repository-root launcher calling `backend.server.run()`.
- `backend/server.py`: HTTP routes, validation, local authentication, state assembly, imports, report generation, and downloads.
- `backend/database.py`: SQLite schema, migrations, transactions, provenance, readiness, and reset behavior.
- `backend/integrations.py`: tabular mapping and Google Sheets, Docs, Drive, and OAuth.
- `backend/report_engine.py`: CSV/PDF parsing, template inspection, mapped PDF generation, and report validation.
- `backend/adaptive_reports.py`: sample-aware fitting and pagination.
- `backend/template_mapping.py`: PDF mapping schema and validation.
- `backend/sample_content.py`, `comment_sections.py`, and `pdf_content.py`: evidence extraction and comment routing.
- `backend/report_charts.py` and `weekly_report.py`: charts and fallback report rendering.
- `frontend/index.html`, `styles.css`, and `app.js`: dependency-free browser UI with no build step.
- `tests/`: Python `unittest` backend and frontend-contract tests.
- `assets/royal-ib-report-template.pdf`: bundled two-page fillable binary template.

## Implementation and security rules

- Keep backend persistence, API payloads, frontend state, and regression tests synchronized when a contract changes.
- Use database methods and explicit transactions for multi-step writes. Preserve foreign keys, WAL mode, import provenance, class/period scoping, and rollback on failure.
- Validate content type, payload shape, numeric ranges, base64 data, file size/type, filenames, ownership, and replacement confirmation before writing.
- Scope every comment import and shared comment to its selected class, period, and section.
- Serve only the explicit frontend allowlist and controlled report downloads. Never expose arbitrary filesystem paths.
- Escape every untrusted value rendered through `innerHTML`; prefer `textContent` for plain text.
- Preserve accessible labels, centralized modal scroll locking, bounded progressive previews, loading guards, and contained table scrolling.
- The development server is loopback-only. Preserve allowed-host, same-origin, cross-site, and bind restrictions. Do not expose it to a LAN or public internet without an explicitly approved authenticated-HTTPS design.
- Optional Basic authentication is an extra localhost layer, not a replacement for HTTPS or authorization.
- Never log or return OAuth codes, client secrets, refresh tokens, authorization headers, credential responses, student comments, or report contents.
- Store OAuth configuration and tokens only in ignored local files with restrictive permissions. Reject service-account JSON as an OAuth web client.
- Preserve the existing least-privilege Google scopes. Saved client setup may persist, but Google access must require an explicit connection after every server start.
- Disconnecting Google deletes the local token but must not delete generated Drive files.
- Never weaken authentication, authorization, input validation, or permission boundaries to satisfy a test.

## Run and verify

Run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open `http://127.0.0.1:8000`. The OAuth redirect URI is `http://127.0.0.1:8000/api/google/oauth/callback`.

For every code change, run the smallest relevant test module while iterating, then the baseline checks:

```sh
.venv/bin/python -m unittest discover -v
.venv/bin/python -m unittest tests.test_empty_start -v
node --check frontend/app.js
git diff --check
```

For UI or end-to-end work, start the real server and verify both `/` and `/api/state`, then exercise the affected browser workflow. For report changes, generate and inspect the longest realistic synthetic or authorized local case. Unit tests do not replace rendered-PDF inspection, browser verification, or live Google verification.

Live Google checks require the maintainer's private OAuth client, authorized test account, source Sheet, and template Doc. Automated tests must not require network access or real credentials.

## Git and release discipline

- Before committing, inspect the complete diff and staged file list. Stage only the requested, reviewed files.
- Never commit private runtime data, uploads, reports, credentials, tokens, machine paths, backups, or unrelated changes.
- Do not commit, push, publish a mapping, migrate/reset real data, or start public sharing unless the user explicitly requests it.
- Before pushing, fetch and confirm the intended branch and remote. After pushing, verify the remote branch SHA.
- Report verification precisely: distinguish tests, syntax checks, server checks, browser checks, rendered-PDF checks, and live Google checks.
