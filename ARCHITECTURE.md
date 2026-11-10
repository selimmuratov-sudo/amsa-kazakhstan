# AMSA Kazakhstan Digital Ecosystem — audit and implementation plan

Audit date: 10 November 2026

## 1. Existing project audit

### Current structure

| File | Current responsibility |
| --- | --- |
| `index.html` | Public landing page and homepage sections |
| `about.html`, `opportunities.html`, `values.html`, `contacts.html`, `local-committees.html`, `events.html`, `search.html` | Separate public pages with shared navigation |
| `styles.css` | Shared responsive visual system |
| `server.py` | Python standard-library HTTP server, form handling, session/auth logic, admin roster |
| `app/` | FastAPI API, SQLAlchemy models, session security, RBAC, member, committee, event, news, exchange, report and search endpoints |
| `migrations/` | Alembic schema migrations for identity, member registry, events, news, usernames, exchanges, committee reports, certificates, Google sync logs, forced password replacement and login throttling |
| `templates/` | Authenticated member portal and role-checked member, committee, event, news, exchange and report interfaces |
| `data/amsa.sqlite3` | Local SQLite database created by `server.py` |
| `data/platform.sqlite3` | New local development database managed by Alembic |
| `README.md` | Local setup and run instructions |
| `outputs/amsa-kazakhstan-site.zip` | Packaged site artifact |

The project has since added a FastAPI backend, dependency manifests, automated API tests and database migrations. The public HTML and design establish the dark navy, electric blue, lime, and light-surface direction; these remain the visual foundation.

### Current state

- The original public design and static pages are retained; `server.py` remains an older prototype.
- The active application entrypoint is FastAPI at `uvicorn app.main:app`; SQLAlchemy and Alembic manage a separate platform database.
- Authentication uses username/password, Argon2 password hashes, CSRF-protected cookie sessions, scoped roles, forced replacement of admin-issued temporary passwords and persistent IP/account-pair login throttling. Rate-limit keys are HMAC pseudonyms; email is optional.
- The platform includes private member profiles, committee directory and scoped officers, event management/registration, news CMS, exchange applications and committee reporting.
- Local development uses SQLite. PostgreSQL URLs are supported. Encrypted database/media backups and a non-destructive staging restore command are included; the deployment still needs to schedule off-host copies and exercise recovery.
- In-app notifications, event attendance/certificates and the optional Google Sheets report sync workflow are implemented; the Sheets integration stays inactive until service credentials and a spreadsheet are configured. Email delivery/outbox processing, private exchange files and analytics remain future modules. Google Forms embed and structured report workflow are implemented.
- Committee-scoped administrator password reset uses a one-time temporary secret, revokes active sessions and forces replacement at login. Self-service email recovery, email verification, MFA, deployment/TLS configuration and a privacy/retention workflow remain unimplemented. Do not expose the local prototype to the public internet as-is.
- The legacy `data/amsa.sqlite3` is separate from the migrated platform database at `data/platform.sqlite3`.

## 2. Target architecture

Keep the existing public website and its brand system. Add a versioned backend API and authenticated application area without turning the public site into a new design.

```text
Browser
├── Public Website (existing HTML/CSS; news/events rendered from API)
└── Member/Admin Portal (same brand; role-aware views)
        │ HTTPS, session cookie, CSRF
        ▼
Backend API (FastAPI, Pydantic validation)
├── Auth + RBAC policy
├── Members / Local Committees
├── News / Events / Exchanges / Reports
├── Notifications / Certificates / Analytics
└── Google Workspace adapters
        │
        ├── PostgreSQL + SQLAlchemy + Alembic migrations
        ├── Private object storage for profile photos and documents
        ├── Email provider + background job/outbox
        └── Google Forms embed; optional Sheets API sync via server-side OAuth/service credentials
```

### Proposed data model

Primary entities and relationships (not all tables should be created before their module is implemented):

| Area | Proposed tables | Important relationships / access |
| --- | --- | --- |
| Identity | `users`, `roles`, `permissions`, `user_role_assignments`, `sessions`, `password_reset_tokens` | Account identity is separate from membership. Role assignments carry a scope such as national or one local committee. |
| Member database | `member_profiles`, `universities`, `cities`, `local_committees`, `committee_positions` | Profile belongs to one user. Private fields are visible to the member and explicitly authorized staff only. |
| Activity | `events`, `event_registrations`, `attendance`, `member_activity_records`, `certificates`, `documents` | Event history and certificates are private to the member unless shared/verified by a public certificate ID. |
| Exchange | `exchange_programs`, `exchange_calls`, `exchange_applications`, `application_documents`, `application_reviews`, `application_status_history` | Candidate sees own application; reviewers see only assigned applications. Status transitions are audited. |
| Reports | `committee_reports`, `report_comments`, `report_status_history`, `google_sync_runs` | Committee officers see their committee; national reviewers see assigned/all national reports. |
| Publishing | `news_articles`, `news_categories`, `news_media`, `events` | Draft/scheduled/published states and publish timestamps drive public pages and ticker. |
| Operations | `notifications`, `email_outbox`, `audit_log`, `site_settings` | No API secret belongs in a browser or normal application table. |

`universities` and `local_committees` should be managed reference data, not free-form values, once the committee directory is confirmed. Membership state and login/account state must be separate. Uploaded files must be stored privately and served only after backend authorization.

## 3. Implementation sequence

1. **Foundation and audit trail** — replace monolithic request handling with an API/service/model boundary, add migrations and configuration, strengthen auth/session lifecycle, define RBAC and audit logging. Preserve current static public pages.
2. **Member Database and cabinets** — add private member profiles, controlled directory filters, member self-service, account creation by admins, scoped staff access, and CSV export for authorized administrators.
3. **Local Committees** — create committee directory and scoped director/officer permissions; public pages show only approved public fields.
4. **Events and registration** — event CMS, capacity, registrations, waitlists, cancellation and member history are implemented; authorized admins can record attendance, issue/revoke certificates, and members can print and publicly verify issued certificates.
5. **International exchanges** — implemented calls, member applications, review/status workflow, reviewer comments and candidate dashboard; add private supporting documents and notifications.
6. **News CMS and public content** — featured story, ticker, carousel/grid/archive, scheduled publishing, local news and announcements.
7. **Reports and Google Workspace** — quarterly committee reports and national review workflow are implemented; server-side Sheets API sync, attempt logging and retry controls are implemented and await authorized Workspace configuration.
8. **Notifications, analytics, multilingual content** — in-app notification center is implemented; add email outbox/delivery, scheduled announcements, real metrics from database and English/Russian/Kazakh content.
9. **Production readiness** — permission tests, form/API tests, backup/restore procedure, privacy review, HTTPS and hosting configuration, logging/monitoring.

## 4. Components and external inputs

- Python backend framework and API schema; PostgreSQL service; SQLAlchemy and Alembic.
- Secure session/cookie configuration, CSRF protection, password reset/email verification strategy, and role/scope policy.
- Private object storage choice for photos/documents and retention/access rules.
- Email delivery provider and sender address for verification, notifications, and application status updates.
- Google Workspace project, OAuth/service-account configuration, authorized Forms/Sheets and required scopes if automatic sync is needed. Google Form embedding does not require Sheets API credentials.
- Hosting/domain and TLS termination before students other than the operator can access the portal.
- Approved public directory fields, membership policy, committee list, exchange calls, events/news, and privacy notice. No placeholder counts or fabricated production content should be published.

## 5. Implemented increments

The first implementation increment now provides a FastAPI application, SQLAlchemy model layer, Alembic migrations, PostgreSQL URL support, private profile endpoints, Argon2 password hashes, database-backed sessions, CSRF checks, audit events, a super-admin bootstrap CLI, member search/filter/export, committee-scoped roster/status access, and encrypted database/news-media backups with staging-only restore. The existing public pages and brand styles remain in place.

The public site now has separate pages for committee directory, events, news and search, plus shared mobile navigation. Committee administrators can maintain directory cards and assign scoped officers. The event area has a public calendar, admin editor with draft/published/cancelled states, membership-checked registration, capacity waitlists, cancellation and automatic waitlist promotion. The member cabinet lists registrations. Search returns public content without searching member PII.

News management supports raster cover uploads, drafts, scheduled publication, pinning, archive, a public archive/article route, Unicode-aware public search, homepage ticker and a rotating featured-story banner. Role-checked news administration includes image upload preview and a publication workflow.

International exchange calls can be drafted, opened, closed and archived by national reviewers. Active members can submit one private application per open call and withdraw while it remains in early review. National reviewers see candidate details and move applications through a validated decision flow; changes and candidate-facing notes are recorded in application history. Candidates see only their own submissions, status, messages and history in the member cabinet. Supporting-document upload and notifications remain future work.

Quarterly committee reports are scoped to assigned local officers. Officers can draft and revise a report, submit it for national review, and respond to a request for changes. National board members can filter all committee reports, add comments, request changes, approve submissions and inspect the status history. Comments and changes are audit logged; committee officers cannot read another committee's reports. An optional Google Forms embed accepts only a configured HTTPS `docs.google.com/forms/d/e/.../viewform` URL. Approved reports can be upserted into a dedicated worksheet using the report ID as a stable key; sync attempts store sanitized outcomes and admins can retry. The integration reads a service-account credential file on the server and uses the Sheets-only OAuth scope. It remains inactive until the authorized spreadsheet is shared with the service-account email and `AMSA_GOOGLE_REPORTS_SPREADSHEET_ID`, `AMSA_GOOGLE_SERVICE_ACCOUNT_FILE` and `AMSA_GOOGLE_REPORTS_SHEET_NAME` are configured.

The notification center stores private in-app messages per user, with unread counts, cursor pagination, per-user read receipts, and bulk mark-read. It is connected transactionally to event registration/waitlist promotion, membership status changes, exchange application decisions, report submissions, reviewer comments and report decisions. No outbound email is sent; an email provider and outbox worker remain deployment inputs.

Homepage and admin summary metrics use database counts. Login and registration use unique usernames; email is optional and omitted from the registration form. Members can rotate passwords in the cabinet, which revokes other sessions. The admin bootstrap command generates a random password instead of requiring an email or shared password.

Automated API tests cover registration without email, username login, profile privacy, CSRF enforcement, scoped access, public search, news upload/publishing/archive, exchange application privacy/review, quarterly report scope/review/history and Google Sheets sync-state access, Google Forms URL validation, route availability, event registration capacity and waitlist promotion. The existing `server.py` remains as a legacy local prototype; the new entrypoint is `uvicorn app.main:app`.

## 6. Remaining implementation sequence

1. **Foundation hardening** — email verification and reset flow, account lifecycle, comprehensive permission assignments, migration and backup procedures.
2. **Member Database and cabinets** — approved reference data, admin creation/invitation, profile photo/document service, member activity and certificate history, full CSV pagination.
3. **Local Committees** — directory management and scoped director/officer roles are implemented; expand public content review and reference data management.
4. **Events and registration** — event publishing, capacity, registration, waitlists, cancellation, attendance, certificates and member history are implemented; expand event analytics and notification delivery.
5. **International exchanges** — first secure call, application and review workflow is implemented; add private supporting-document storage and candidate notifications.
6. **News CMS and public content** — first version of image uploads, drafts, scheduled publishing, ticker, carousel and archive is implemented; add richer editorial workflow, translations and partner/news relations.
7. **Reports and Google Workspace** — report drafting/review and optional Sheets API sync are implemented; configure an approved Workspace service account and spreadsheet, then verify the live organization-owned sheet before enabling automatic sync.
8. **Notifications, analytics, multilingual content** — in-app notification center is implemented; add email outbox/delivery, scheduled announcements, real metrics from database and English/Russian/Kazakh content.
9. **Production readiness** — private exchange document uploads, account recovery, permission tests, backup/restore procedure, privacy review, HTTPS and hosting configuration, logging/monitoring.

