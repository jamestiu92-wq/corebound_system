# COREBOUND Upgrade Plan — Phases 1–10

This package consolidates the existing seven report-workflow enhancements and adds security, operational reliability, and verification scaffolding. It preserves the Type 1, Type 2, and Type 3 interface structure.

1. **Security foundation** — persistent local secret key; environment override; secure cookie toggle; CSRF tokens for HTML forms and same-origin fetch POSTs; common security headers; per-request account/role revalidation; case-insensitive login; login throttling; audit events for account changes.
2. **Database compatibility** — idempotent migrations add missing workflow/security tables and report columns in place; no automatic destructive reset. Back up `database.db` before upgrading.
3. **Territory-based assignment** — new reports route to the active territory leader and least-loaded active member when available; leader can assign/reassign members.
4. **Traceability** — status history plus assignment history records previous/new leader/member, actor, timestamp, and note. Existing history remains intact.
5. **Deadlines and overdue alerts** — severity-based deadlines and one-time overdue notifications; dashboard counters.
6. **Progress updates** — comments and optional image evidence; updates notify relevant participants.
7. **Repair evidence** — before/after photo uploads validated by file signature and size.
8. **Super Admin analytics** — current workload, resolution rate, territory comparisons, and six-month report trend are shown on the existing admin dashboard (keeps the Super Admin to two main pages).
9. **Account filters** — role, territory, department, status, and registration-date filters plus keyword search.
10. **Release readiness** — requirements, test scripts, migration/backup instructions, environment configuration, and a clear manual QA checklist.

## External APIs
No paid/external API keys are required for these phases. Email, SMS, maps/geocoding, weather/disaster feeds, and external identity providers are not secretly enabled; each needs provider selection and credentials. This release is API-ready but does not claim those external integrations are live.

## Upgrade safely
1. Stop the Flask server.
2. Copy your existing `database.db` to a separate backup location.
3. Extract the ZIP into a new folder; copy your current `database.db` into that folder only if you want to run against your existing records.
4. Install dependencies: `python -m pip install -r requirements.txt`.
5. Set a stable `SECRET_KEY` environment variable for deployment (at least 32 characters). For local development, the app creates `.flask_secret_key` on first start; keep that file private and persistent.
6. For HTTPS production, set `COOKIE_SECURE=1`; do not enable it for plain HTTP localhost.
7. Start with `python app.py` and review `TEST_NOTES.md`.

**Important:** the ZIP intentionally does not contain a live `database.db`, private secret, user passwords, or uploaded repair photos. Bring those over separately from your own installation. The schema and migration code are included.


## Original visual assets
The package does not contain your original campus image/logo files. Copy `static/campus-background.jpg` (and any `static/LOGO.png` or `static/LNU.png` used by your original installation) from your existing project into the matching `static/` paths to preserve the original branding.
