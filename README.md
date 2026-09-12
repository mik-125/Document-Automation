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
3. Configure the OAuth consent screen for **External** use, keep the app in Testing, and add your own Google account as a test user.
4. Create an OAuth client ID of type **Web application**.
5. Add this exact authorized redirect URI: `http://127.0.0.1:8000/api/google/oauth/callback`.
6. Download the OAuth client JSON outside this repository.
7. Set the client path before starting the app:

```sh
export GOOGLE_OAUTH_CLIENT_FILE="/absolute/path/to/google-oauth-client.json"
```

Open **Connections → Connect Google account** and approve access. Brightline stores the refresh token in the ignored local `data/` directory and creates a `Brightline Reports` folder in My Drive. It requests read-only access to existing Drive files plus per-file write access for files it creates.

While the OAuth app remains in Google's **Testing** status, Google expires test-user authorization after seven days. Reconnect from Brightline when prompted. This is suitable for the personal MVP; public release requires a separate OAuth verification review.

Never place the OAuth client JSON or token inside this repository. Credential-like JSON filenames, `data/`, and `.env` are Git-ignored as a second safety layer.

Disconnecting Google deletes Brightline's local OAuth token but does not delete generated report Docs in Drive. Clearing imported data also leaves generated Google files in place.

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

### Private ngrok sharing

Set `BRIGHTLINE_PUBLIC_URL` to your exact ngrok HTTPS origin (no path), and set
`BRIGHTLINE_PASSWORD` to a unique password of at least 16 characters before starting
the server. Keep the listener on `127.0.0.1`. Start ngrok with
`ngrok http http://127.0.0.1:8000 --url YOUR_HTTPS_ORIGIN --inspect=false`.
The public link requires the Brightline username and password; all authenticated
visitors share the same data and administrative access. Share only with trusted
operators. Do not use this as a student or parent portal.

For Google connections, register `YOUR_HTTPS_ORIGIN/api/google/oauth/callback`
in your Google OAuth client's authorized redirect URIs. Existing local redirect
URIs can remain. Ngrok forwards traffic through its service; your Mac must remain
awake and both processes must stay running. Stop ngrok to take the link offline.

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

Live Google verification requires a personal Google OAuth client, an authorized test user, a source Sheet, and a template Doc.
