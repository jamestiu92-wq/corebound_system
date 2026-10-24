# COREBOUND — Seven workflow enhancements

This project keeps the existing Flask + SQLite structure and adds: automatic territory/team assignment, auditable report status history, severity-based deadlines and overdue alerts, progress notes with optional image evidence, before/after repair photos, Super Admin report analytics, and expanded account filters.

## Setup
1. Use Python 3.10+ and install Flask: `pip install flask click werkzeug`.
2. Put the provided static assets (including `LNU.png`, `logo.png`/`LOGO.png`, and background images) in `static/`.
3. Initialize a fresh database with `sqlite3 database.db < schema.sql`, or place your existing `database.db` beside `app.py`; the app migrates the new columns/tables on requests.
4. Run `python app.py`. If your existing project uses a separate entrypoint command, use that instead.

## Feature behavior
- Routing uses the department's territory, assigns the territory leader and least-loaded active team member when available. If no member exists, it alerts the leader and leaves the report ready for manual assignment.
- Default due times: urgent 1 day, high 3 days, medium 7 days, low 14 days.
- Overdue alerts are sent once per report to the reporter and assigned team users when dashboards are opened.
- Evidence uploads accept PNG/JPEG/WEBP up to 5 MB.
- Admin dashboard analytics are embedded on the existing dashboard; no extra Super Admin page is added.

Back up your database before deploying. Run the test suite below before production use.


## Phase 1–10 upgrade
See `PHASES_1_TO_10.md` for the complete scope and safe-upgrade instructions. Run `python -m unittest discover -s tests -v` for automated static/schema checks. A full browser QA pass still needs to be run in the target environment.


## Phase 1–10 upgrade
See `PHASES_1_TO_10.md` for the complete scope and safe-upgrade instructions. Run `python -m unittest discover -s tests -v` for automated static/schema checks. A full browser QA pass still needs to be run in the target environment.


## Original visual assets
The package does not contain your original campus image/logo files. Copy `static/campus-background.jpg` (and any `static/LOGO.png` or `static/LNU.png` used by your original installation) from your existing project into the matching `static/` paths to preserve the original branding.
