# Brightline Reports MVP

Brightline is a single-academy, local-first report generator. It imports Assignment, Weekly Test, and Comments data, supports per-class original PDF templates and completed samples or Google Docs templates, and exports student PDFs and class ZIP files.

## Repository structure

```text
frontend/          Website HTML, styles, and JavaScript
backend/           HTTP API, authentication, data storage, integrations, and reports
tests/             Python and frontend regression tests
docs/              Product requirements and design rules
assets/            Bundled PDF template assets
server.py          Application launcher
requirements.txt   Python dependencies
```

The backend serves only the explicitly allowed files in `frontend/`.
Authentication and request-origin checks live in `backend/server.py`.
Local uploads, credentials, the SQLite database, and generated reports remain in
Git-ignored `data/` at the repository root (or `BRIGHTLINE_DATA_DIR` when set).
Run the commands below from the repository root.

## Empty starting state and product requirements

A fresh installation starts with no uploaded classes, students, comments, scores,
templates, samples, or reports. Users provide their own data. Developer uploads are
local test material, not default content; they stay in ignored local storage.
The report-analysis and generation features remain part of the application.

See [PRODUCT_REQUIREMENTS.md](docs/PRODUCT_REQUIREMENTS.md) for the maintained rules
on class-specific sample analysis, original-template authority, preserved wording,
pagination, private data, and release checks. Different report formats require
independent validation; current examples do not prove universal format support.

## What is real in this MVP

- Website import of homework and Weekly Test scores from one Google Sheet through the official Sheets API
- Website upload of searchable PDFs for 과제이행도, Weekly Test, and general comments, plus a shared 과제이행도 comment option
- Per-class original PDF templates with reviewed, published mappings and completed samples for content guidance
- Alternative per-class Google Docs templates through the official Docs and Drive APIs
- Controlled placeholder replacement and PDF export
- Manual review when a Comments PDF cannot safely identify a student
- Local SQLite persistence and ignored local report files
- Optional Basic authentication as an extra layer on localhost

CSV imports remain available in the backend: the existing Royal IB Assignment
format and mapped Assignment, Weekly Test, and Comments imports are supported by
API handlers. The website upload screen does not accept CSV files; use Google
Sheets for homework and scores, and searchable PDFs for individual comments.

MakeEdu is intentionally unavailable until MakeEdu supplies official API documentation and academy API access. The app does not scrape or simulate MakeEdu.

## Google setup

1. Create a Google Cloud project.
2. Enable Google Sheets API, Google Docs API, and Google Drive API.
3. Configure the OAuth consent screen for **External** use, keep the app in Testing, and add your own Google account as a test user.
4. Create an OAuth client ID of type **Web application**.
5. Add this exact authorized redirect URI: `http://127.0.0.1:8000/api/google/oauth/callback`.
6. Download the OAuth client JSON outside this repository.
7. Save a private local copy once, from the Brightline project folder:

```sh
.venv/bin/python -c 'from pathlib import Path; from backend.integrations import GoogleWorkspace; import sys; GoogleWorkspace.save_client_configuration(Path(sys.argv[1]))' "/absolute/path/to/downloaded-google-oauth-client.json"
```

Brightline stores the validated configuration in `data/google-oauth-client.json` with owner-only permissions. It loads this file automatically on every start, so moving the original download or opening a new terminal does not break setup. With a custom `BRIGHTLINE_DATA_DIR`, run setup and the server with the same data directory.

`GOOGLE_OAUTH_CLIENT_FILE` remains an optional override. If you previously set it to an old path, run `unset GOOGLE_OAUTH_CLIENT_FILE` and remove that old export from your launch command or shell configuration. An explicitly configured missing or invalid file is reported rather than silently selecting another OAuth client.

Open **Connections → Connect Google account** and approve access. Brightline stores the refresh token in the ignored local `data/` directory and creates a `Brightline Reports` folder in My Drive. It requests read-only access to existing Drive files plus per-file write access for files it creates.

Each time the Brightline server starts, Google access is inactive until you click **Connect Google account** and finish sign-in. Your saved setup remains available, so no terminal configuration is needed. Refreshing a page during an active server session keeps your connection.

While the OAuth app remains in Google's **Testing** status, Google expires test-user authorization after seven days. Reconnect from Brightline when prompted. This is suitable for the personal MVP; public release requires a separate OAuth verification review.

Never commit or share the OAuth client JSON or token. Keep local credentials in the ignored `data/` directory (or outside the repository). Credential-like JSON filenames and `.env` are also Git-ignored.

Disconnecting Google deletes Brightline's local OAuth token but does not delete generated report Docs in Drive. Clearing imported data also leaves generated Google files in place.

## Install and run

From this folder:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The server is intentionally restricted to `127.0.0.1`. You can add an optional local password:

```sh
export BRIGHTLINE_USERNAME="admin"
export BRIGHTLINE_PASSWORD="use-a-long-unique-password"
```

Do not expose this development server directly to a network. A future hosted version must sit behind authenticated HTTPS.

## Report template placeholders

### Original PDF and completed samples

The original blank PDF is the design authority. Its pages, artwork, section positions,
and confirmed text mapping control generated PDF reports. A completed sample is a
class-specific content reference and never becomes a background or a source of
student comments, scores, or names. Old published `royal_ib_weekly` replacement
layouts must be reviewed and republished using the original PDF mapping.

Sample analysis records recognized English/Korean headings, their positions, body
font statistics, content topics, and image locations. Graph type is recorded only
when supported by an explicit chart label; an image alone is not treated as proof
of a graph type. These observations are available in **View analysis**.

When generating a report, explicit comment headings corresponding to the class's
sample sections route intact text blocks to the matching original-template regions.
With reliable completed-sample bodies that distinguish test overviews from detailed
teacher feedback, mixed unheaded narratives are split into contiguous source blocks.
A student's test introduction starts the overview (a numeric score is optional);
a numbered question-feedback block ends it. Passing references to questions,
general advice, and vocabulary overlap do not fragment the overview. The course
introduction and detailed feedback remain together in the teacher section. Samples
that put detailed questions in the test section do not use this splitting rule.
Without sufficient evidence, the selected import category is retained.
Homework imports are not moved merely because they mention a test. Source wording is preserved.
The system does not paraphrase feedback or invent details from a sample. Imported
PDF section boundaries are preserved for future routing; older imports that already
lost those boundaries may need to be imported again.

This is local, evidence-based classification, not general-purpose semantic AI or
OCR; ambiguous content still requires preview/review. Effective PDF font sizes
include text-matrix scaling. Body-font names and sizes are inspected per section;
unavailable/subset fonts use the app's Unicode fallback rather than missing glyphs.
For recognized labeled panels in the ORIGINAL PDF, Brightline fits text at the
largest suitable size (normally up to 9.5–12 pt), reflows PDF soft line breaks,
and keeps Weekly Test feedback inside its first original panel. With academy
approval, test and teacher text may shrink below 8 pt (5 pt safety floor); text
that cannot meet the rules at that floor requires review rather than clipping.
An additional teacher-comment page is kept only when its text fills at least
half the final comment panel. Sparse final pages are compacted into earlier
panels, and unused trailing original pages are omitted. Eligible overflow gets a
continuation copied from an original full-width feedback page, explicitly relabeled
for the overflowing section; never from the completed sample. If no such panel is
available, its original section panel is repeated. Drawn panels and headings, not subject names or current uploaded
students, drive this behavior. The original backgrounds and stored mappings remain
unchanged. Unsupported/unreadable layouts retain the confirmed-mapping path and
raise an error rather than guessing arbitrary content areas or clipping text.

For sample-aware PDF reports, missing Weekly Test feedback uses the academy's
explicit assumption that the student missed the test, even when a stored score
exists. The requested Korean notice replaces the test narrative, its graph is
suppressed, and the report must fit on one page. This is an output policy, not
verified attendance: imported scores and teacher wording are never changed.
Assignment data and homework comments are still required.

### Google Docs templates (alternative)

Google Docs templates are an alternative to original PDF templates, not a
requirement for every class. The website's Google Sheets import still requires a
Google connection when using a PDF report template.

Use these exact placeholders in ordinary Google Docs text or table cells:

- `{{student_name}}` — required
- `{{class_name}}` — required
- `{{reporting_week}}` — required
- `{{assignment_summary}}` — required
- `{{weekly_test_summary}}` — required
- `{{teacher_comment}}` — required
- `{{student_id}}`
- `{{grade_or_year}}`
- `{{school}}`
- `{{assignment_percent}}`
- `{{weekly_test_score}}`

Text formatting around each placeholder is preserved by Google Docs. Placeholders inside drawings or images are not supported.

## Import order

1. Connect Google and import **Homework + test scores** from one Google Sheet. Include student details, dated homework marks, and a Weekly Test percentage column (0–100). This creates the reporting period, classes, and students. Blank scores preserve existing results.
2. Add **과제이행도 comments (required)** for the same class and period. Upload a searchable PDF with individual homework comments, or enter a shared comment for everyone in the selected class and week.
3. Import **Weekly Test comments**, **General comments**, or both as searchable PDFs. Each PDF must represent one class. Normally, each student needs a Weekly Test score and at least one of these two comment types in addition to Assignment data and the required 과제이행도 comment.
4. Set up a template for each class: upload an **original blank PDF**, review its mapping and publish it, using completed samples as class-specific content references; or connect and test a **Google Docs template** with the placeholders above.
5. Review any unmatched comments and preview one student before downloading a class ZIP. Students with complete inputs can generate reports even when classmates still need data.

**Missing Weekly Test feedback:** for a ready, published PDF template whose
completed-sample analysis reliably identifies a Weekly Test section, missing
feedback triggers the missed-test notice described above, even if a score exists.
This exception allows generation without a Weekly Test score or either optional
comment type; Assignment data and 과제이행도 comments remain required. The output
omits the test graph and fits on one page without changing stored scores or source
comments. Do not assume this exception applies to Google Docs or PDF templates
without the required sample evidence.

For Google Sheet imports (and backend mapped CSV imports), a unique Student ID is strongly recommended. Without it, Brightline accepts only an exact, unambiguous name within the class and uses school or grade to narrow the match when those fields are supplied.

## Verification

```sh
.venv/bin/python -m unittest discover -v
node --check frontend/app.js
```

Live Google verification requires a personal Google OAuth client, an authorized test user, a source Sheet, and a template Doc.
