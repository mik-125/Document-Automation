# Brightline Reports

Brightline turns an academy's weekly scores and teacher comments into finished student report PDFs.

You bring three things: a Google Sheet of homework and Weekly Test scores, searchable PDFs of teacher comments, and a report template for each class. Brightline matches everything to the right student, fills the template, and exports one PDF per student or a ZIP for the whole class.

**Status:** MVP for a single academy. It runs only on your own machine (`127.0.0.1`), stores data in local SQLite, and is not ready to be hosted for other users.

## Quickstart

Run from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

A fresh install is empty: no classes, students, scores, comments, templates, or reports. To import scores you need a Google connection, so do the Google setup below next.

## Google setup (one time)

Brightline reads scores from Google Sheets and can optionally use Google Docs templates. Both go through Google's official APIs using your own OAuth client.

1. Create a Google Cloud project.
2. Enable the **Google Sheets API**, **Google Docs API**, and **Google Drive API**.
3. Set up the OAuth consent screen as **External**, leave it in **Testing**, and add your Google account as a test user.
4. Create an OAuth client ID of type **Web application**.
5. Add this exact redirect URI: `http://127.0.0.1:8000/api/google/oauth/callback`
6. Download the client JSON and keep it **outside** this repository.
7. Register it with Brightline once:

   ```sh
   .venv/bin/python -c 'from pathlib import Path; from backend.integrations import GoogleWorkspace; import sys; GoogleWorkspace.save_client_configuration(Path(sys.argv[1]))' "/absolute/path/to/client.json"
   ```

Brightline saves a validated copy to `data/google-oauth-client.json` with owner-only permissions and loads it on every start. After this, you can move or delete the original download.

Then open **Connections → Connect Google account** in the app. Brightline creates a `Brightline Reports` folder in your Drive. It asks for read-only access to your existing Drive files and write access only to files it creates.

Things to know:

- **A valid connection survives server restarts.** Brightline reloads the ignored, owner-only refresh token until you disconnect or Google revokes it. If that happens, the app shows `Google connection expired. Reconnect.`
- **Testing mode expires every 7 days.** Google revokes test-user access after a week, so reconnect when the app prompts you. Releasing to the public requires Google's OAuth verification.
- **Disconnecting** deletes the local token. Reports already created in Drive are not deleted.
- `GOOGLE_OAUTH_CLIENT_FILE` overrides the saved client. If you set it in the past, run `unset GOOGLE_OAUTH_CLIENT_FILE`. A missing or invalid override causes an error; Brightline does not fall back to a different client.
- With a custom `BRIGHTLINE_DATA_DIR`, run the setup command and the server with the same value.

## How to make reports

1. **Import scores.** Import homework and Weekly Test scores from one Google Sheet. Include student details, dated homework marks, and a Weekly Test percentage column (0–100). This creates the reporting period, classes, and students. Blank cells keep existing scores; `0` counts as a score.
2. **Add 과제이행도 comments (required).** Upload a searchable PDF of individual homework comments, or enter one shared comment for the whole class and week.
3. **Add Weekly Test and/or general comments.** Upload searchable PDFs, one class per PDF. If a comment can't be matched to a student with confidence, it goes to manual review.
4. **Set up a template for each class.** Pick one of the two options under [Templates](#templates).
5. **Review and export.** Resolve unmatched comments, preview at least one student, then download individual PDFs or a class ZIP.

A student is ready when they have assignment data, a 과제이행도 comment, a Weekly Test score, and at least one Weekly Test or general comment. Students who are ready can generate reports even if others in the class aren't.

**Student matching:** Include a unique Student ID in the Sheet. Without one, Brightline accepts only an exact, unambiguous name match within the class, narrowed by school or grade when you provide them. It never guesses.

## Templates

Each class uses one of these.

### Option A: Original PDF template (recommended)

Upload the class's **blank** report PDF, review how Brightline mapped its sections, and **publish** the mapping. Only published mappings are used to generate reports; saving a draft doesn't activate it.

You can also upload **completed sample reports** for the class. Brightline uses them only to learn the content structure (which headings exist, and what goes in which section). A sample is never used as a background and never supplies names, scores, or wording. Check what Brightline picked up under **View analysis**.

### Option B: Google Docs template

Put these placeholders in normal text or table cells (not in drawings or images):

| Required | Optional |
| --- | --- |
| `{{student_name}}` | `{{student_id}}` |
| `{{class_name}}` | `{{grade_or_year}}` |
| `{{reporting_week}}` | `{{school}}` |
| `{{assignment_summary}}` | `{{assignment_percent}}` |
| `{{weekly_test_summary}}` | `{{weekly_test_score}}` |
| `{{teacher_comment}}` | |

Formatting around each placeholder carries through to the finished report.

## What Brightline guarantees about report content

- **Teacher wording is never changed.** Brightline can move whole passages into the correct section, but it never paraphrases, summarizes, invents, drops, or duplicates text.
- **No clipped text.** If content can't fit under the layout rules, the report stops for review and is not cut off.
- **No guessing.** Ambiguous students, unreadable layouts, and unclear sections go to review.
- **Each class is handled on its own.** A layout that works for one class does not prove it works for another format.
- **Routing is inspectable.** Preview shows where each passage was assigned, its confidence, and the class-sample signals used. Low-confidence routing blocks generation for review.

The detailed fitting, pagination, and section-routing rules (font size limits, continuation pages, and how comments get split into sections) are in [`docs/PRODUCT_REQUIREMENTS.md`](docs/PRODUCT_REQUIREMENTS.md). That file is the single source of truth for product behavior.

### Missing Weekly Test feedback

With a published PDF template whose samples clearly show a Weekly Test section, a student who has **no Weekly Test feedback** gets a standard Korean notice saying they did not take the test. The test graph is left out and the report fits on one page. **This happens even if a score is stored.** Stored scores and comments are not changed. This behavior was requested by the academy and does not apply to Google Docs templates. See the product requirements for the exact rule.

## Your data

- Everything stays local in `data/` at the repository root, or in `BRIGHTLINE_DATA_DIR` if you set one. That includes the database, uploads, OAuth credentials, and generated reports. Git ignores this folder.
- Never commit student data, completed samples, generated reports, OAuth client files, or tokens. `.env` and credential-like JSON files are also ignored by Git.
- Clearing data in Brightline does not delete files it has already created in Google Drive.

## Security

- The server listens only on `127.0.0.1`. Do not expose it to your network or the internet. A hosted version would need authenticated HTTPS and real user accounts.
- For an extra layer of protection on localhost, you can turn on a password:

  ```sh
  export BRIGHTLINE_USERNAME="admin"
  export BRIGHTLINE_PASSWORD="use-a-long-unique-password"
  ```

- The backend serves only an explicit allowlist of files from `frontend/`. Host and origin checks live in `backend/server.py`.

## Not supported

- **CSV upload in the website.** The backend API still accepts mapped CSV imports and the legacy assignment CSV format, but the website uses Google Sheets for scores and searchable PDFs for comments.
- **Scanned or image-only PDFs.** Brightline reads PDF text and does not do OCR.
- **MakeEdu.** Brightline won't support it until MakeEdu provides official API documentation and access. Brightline does not scrape.
- **Placeholders in Google Docs drawings or images.**

## Project structure

```text
server.py         Launcher (calls backend.server.run)
backend/          HTTP API, auth, SQLite storage, Google integration, report engine
frontend/         Browser UI (plain HTML/CSS/JS, no build step)
tests/            Python unittest suite and frontend contract tests
docs/             Product requirements
assets/           Bundled PDF template assets
requirements.txt  Python dependencies
```

Coding agents: read [`AGENTS.md`](AGENTS.md) before you make changes.

## Development and testing

```sh
.venv/bin/python -m unittest discover -v
node --check frontend/app.js
```

Tests use synthetic data and a temporary `BRIGHTLINE_DATA_DIR`. They never touch the network or real credentials. Never run tests against your real data directory.

A live Google check needs your own OAuth client, an authorized test account, a source Sheet, and a template Doc. After any change to PDF output, open the generated reports and check them yourself. Passing unit tests doesn't prove the pages look right.

For private diagnostics, run `.venv/bin/python scripts/check_reports.py`. It writes ignored PDFs, per-page PNGs, passage routing, font/fill measurements, and review flags under `data/report-checks/` by default.
