# Brightline Reports MVP

Brightline is a single-academy, local-first report generator. It imports Assignment, Weekly Test, and Comments data, connects one Google Docs template per class, and exports student PDFs and class ZIP files.

## What is real in this MVP

- Assignment CSV import using the existing Royal IB format
- Mapped CSV imports for Assignment, Weekly Test, and Comments
- Google Sheets reads through the official Sheets API
- Per-class Google Docs templates through the official Docs and Drive APIs
- Controlled placeholder replacement and PDF export
- Manual review when a Comments PDF cannot safely identify a student
- Local SQLite persistence and ignored local report files
- Optional Basic authentication as an extra layer on localhost

MakeEdu is intentionally unavailable until MakeEdu supplies official API documentation and academy API access. The app does not scrape or simulate MakeEdu.

## Google setup

1. Create a Google Cloud project.
2. Enable Google Sheets API, Google Docs API, and Google Drive API.
3. Create a service account and download its JSON credential outside this repository.
4. Share each source spreadsheet with the service-account email as Viewer.
5. Share each Google Docs template with the service-account email as Viewer, with copying allowed.
6. Create an output folder inside a dedicated, restricted-membership report Shared Drive (or an admin-created limited-access folder). Every folder/drive member must be authorized to see student data. Give the service account **Content Manager** access so Brightline can remove temporary preview copies. A regular My Drive folder is not supported by this service-account MVP.
7. Set the credential path before starting the app:

```sh
export GOOGLE_SERVICE_ACCOUNT_FILE="/absolute/path/to/service-account.json"
```

The output folder can be tested and saved from **Connections → Google Docs + Drive**, or supplied at startup:

```sh
export GOOGLE_REPORT_FOLDER_ID="your-drive-folder-id"
```

Never place the credential JSON inside this repository. Credential-like JSON filenames and `.env` are Git-ignored as a second safety layer.

Disconnecting Google or clearing local data does not delete generated report Docs in Drive. Apply the academy's retention policy inside the restricted report folder.

## Install and run

From this folder:

```sh
/Users/mintee/coding/Document-Automation/.venv/bin/python -m pip install -r requirements.txt
/Users/mintee/coding/Document-Automation/.venv/bin/python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The server is intentionally restricted to `127.0.0.1`. You can add an optional local password:

```sh
export BRIGHTLINE_USERNAME="admin"
export BRIGHTLINE_PASSWORD="use-a-long-unique-password"
```

Do not expose this development server directly to a network. A future hosted version must sit behind authenticated HTTPS.

## Report template placeholders

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

1. Import Assignment data. This creates the reporting period, classes, and students.
2. Import Weekly Test data for the same class and period.
3. Import Comments data. A manual PDF must represent one class.
4. Connect and test a Google Docs template for each class.
5. Preview one student before downloading a class ZIP.

For mapped CSV and Sheet imports, a unique Student ID is strongly recommended. Without it, Brightline accepts only an exact, unambiguous name within the class and uses school or grade to narrow the match when those fields are supplied.

## Verification

```sh
/Users/mintee/coding/Document-Automation/.venv/bin/python -m unittest -v test_mvp.py
node --check app.js
```

Live Google verification requires a real service account, shared Sheet, shared template Doc, and writable output folder.
