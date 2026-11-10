# AMSA Kazakhstan Digital Ecosystem

The existing public website and design are preserved. The project now also has a FastAPI backend foundation and a private member portal.

## Local development

Python 3.10+ is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
cp .env.example .env
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli create-super-admin --username amsa-admin --name "AMSA Admin"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open `http://127.0.0.1:8000`. The server presents the public pages, registration, login, member cabinet, member/committee/event/news/exchange administration, committee directory, events calendar, active exchange calls and public search. API documentation is at `/api/docs` in development.

The super-admin command creates a random password and prints it once. Save it in a password manager.

## Implemented in this increment

- Student registration and login with Argon2 password hashes, server-side sessions, HttpOnly cookies and CSRF validation.
- Persistent authentication throttling (10 login failures per username/IP pair, 50 per IP, and 5 registration attempts per IP in a 15-minute window); rate-limit identifiers are HMAC pseudonyms and failed passwords or raw IPs are not stored. Set `AMSA_AUTH_RATE_LIMIT_PEPPER` to a deployment-managed random secret for stable keys.
- Account login uses a unique username; email is optional and is not requested during registration.
- Private member profiles; each account can read and edit only its own profile.
- Member password changes from the cabinet; changing a password revokes other active sessions.
- Committee-scoped admin password reset without email: temporary credential is shown once with `no-store`, all member sessions are revoked, and the next login is restricted to changing the temporary password.
- Member status review, scoped access for local committee staff, search/filter and CSV export for authorized roles.
- Audit events for account registration, login/logout, profile edits and membership status changes.
- Local committee directory, public publish controls, scoped director/officer assignments and member counts for authorized admins.
- Events calendar and editor, active-membership registration, capacity waitlist, cancellation and automatic promotion from the waitlist; scoped attendance management, member certificates, public verification, revocation and member-only print views.
- Search across public pages, published committees and published events; member information is never indexed.
- News CMS with image uploads, drafts, timed publication, pinning, archive, public article pages and live homepage ticker/carousel.
- Exchange call management, public application intake, active-member eligibility, private candidate applications, reviewer workflow, candidate-facing status notes, history and withdrawal.
- Exchange-reviewer role with a national review interface, filters, candidate motivation, experience and destination preferences.
- Quarterly local-committee reporting with committee-scoped drafts, submission, national review, change requests, approval, comments and status history.
- Private in-app notification center with event, membership, exchange and report updates, unread counts, cursor pagination and per-user read receipts.
- Optional Google Forms embed configured with `AMSA_GOOGLE_REPORTS_FORM_URL`; only HTTPS Google Forms view URLs are accepted.
- Optional server-side Google Sheets sync for approved committee reports, with a stable report-ID upsert, per-attempt audit/status, and national-admin retry controls. Configure `AMSA_GOOGLE_REPORTS_SPREADSHEET_ID`, `AMSA_GOOGLE_SERVICE_ACCOUNT_FILE`, and `AMSA_GOOGLE_REPORTS_SHEET_NAME`; share the spreadsheet with the service account as Editor.
- SQLAlchemy models and Alembic migrations for identity, usernames, roles, member profiles, cities, universities, local committees, events, certificates, news, exchanges, reports and Google Sheets sync runs.
- PostgreSQL support through `AMSA_DATABASE_URL`; local development defaults to a separate SQLite file at `data/platform.sqlite3`.

The older `server.py` is retained as the first local prototype. Use the FastAPI/uvicorn commands above for the new portal.

## Checks

Run the API and access-control tests with:

```bash
.venv/bin/pytest -q
```

## Optional Google Sheets sync

1. Create or select a Google Cloud project and enable the Google Sheets API.
2. Create a service account, download its JSON key to a private path outside this repository, and grant the service account Editor access to the target spreadsheet.
3. Set `AMSA_GOOGLE_REPORTS_SPREADSHEET_ID`, `AMSA_GOOGLE_SERVICE_ACCOUNT_FILE` (absolute path), and `AMSA_GOOGLE_REPORTS_SHEET_NAME` in the server environment; install dependencies from `requirements.txt` and restart the app.
4. Approve a committee report. The server creates the configured worksheet if needed and writes the report fields using the Sheets-only OAuth scope. National reviewers see sync status and can retry from the report card.

Keep the service-account JSON outside source control. Select a dedicated empty worksheet: the first column is the stable AMSA report ID, and an existing worksheet is accepted only when its first header cell is `AMSA report ID`. Sync attempts contain sanitized error codes; report content is sent only after national approval.

## Encrypted backups

Backups contain a consistent database snapshot plus uploaded news photos. Configure `AMSA_BACKUP_ENCRYPTION_KEY` with a Fernet key generated by `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`. Keep that key in a deployment secret manager, separate from both this repository and the backup destination. Losing the key makes backups unrecoverable.

```bash
.venv/bin/python -m app.backup backup
.venv/bin/python -m app.backup verify data/backups/amsa-<UTC timestamp>.tar.gz.fernet
.venv/bin/python -m app.backup restore-staging data/backups/amsa-<UTC timestamp>.tar.gz.fernet /private/tmp/amsa-restore-check
```

The restore command only extracts into a new or empty directory and validates the SQLite database. It never runs a restore against the configured live database. PostgreSQL backups use `pg_dump`; verification needs `pg_restore`, and staging restore only extracts the dump for an operator to restore into a separately created staging database. Schedule `backup` with the hosting platform's scheduler and copy encrypted outputs to storage outside the application host; test `verify` regularly.

## Production requirements

The current default is local development. Setting `AMSA_ENVIRONMENT=production` makes startup fail unless PostgreSQL is configured, `AMSA_COOKIE_SECURE=true`, the allowed-host list contains deployment hostnames, and deployment-specific rate-limit and Fernet backup secrets are present. Responses include standard browser security headers, with HSTS enabled only in production. Before opening registration to students on the internet, also configure HTTPS, account recovery policy, private document storage and monitoring, and approve a privacy/retention notice. Encrypted database/news-media backup and staging validation commands are included, but an off-host destination and scheduled backup job must be configured by the deployment operator. In-app notifications work; email delivery/outbox processing is not connected. Google Forms can be embedded; the Sheets sync code is included but remains inactive until an authorized Workspace project, service-account file and spreadsheet are configured. Private exchange-document upload and production hosting/operations remain future work.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the audit, schema, component design and phased roadmap.
