"""
LNU Campus Facility and Damage Reporting and Monitoring System
Login and registration (Type 1 users only).

Folder structure:
    app.py, database.db, schema.sql
    templates/  login.html, register.html,
                Type_1/dashboard.html,
                Type_2/leader/dashboard.html, Type_2/member/dashboard.html,
                Type_3/dashboard.html
    static/     pictures used by the system (login/register use static/campus.jpg)

Run:  python app.py
"""
import os
import difflib
import re
import sqlite3
import uuid
import json
import html
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from functools import wraps

import click
from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "database.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

app = Flask(__name__)   # templates/ and static/ are picked up automatically
def load_secret_key():
    # Vercel production: read the secret from environment variables.
    secret_key = os.environ.get("FLASK_SECRET_KEY")

    if secret_key:
        if len(secret_key) < 32:
            raise RuntimeError(
                "FLASK_SECRET_KEY must contain at least 32 characters."
            )
        return secret_key

    # Local development: use the existing secret-key file.
    if os.environ.get("VERCEL"):
        raise RuntimeError(
            "FLASK_SECRET_KEY must be configured in Vercel Environment Variables."
        )

    path = os.path.join(BASE_DIR, ".flask_secret_key")

    try:
        with open(path, "r", encoding="utf-8") as handle:
            key = handle.read().strip()

        if len(key) >= 32:
            return key

        raise RuntimeError(
            "The local .flask_secret_key file is invalid. "
            "Replace it with a secure secret."
        )

    except FileNotFoundError:
        key = uuid.uuid4().hex + uuid.uuid4().hex

        with open(path, "x", encoding="utf-8") as handle:
            handle.write(key)

        return key


app.secret_key = load_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0").lower() in ("1", "true", "yes")
app.config["SESSION_COOKIE_NAME"] = "corebound_session"
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
app.permanent_session_lifetime = timedelta(days=14)   # used by "Remember me"

UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
MAX_PHOTO_BYTES = 5 * 1024 * 1024
CATEGORIES = [("equipment", "Equipment"), ("facility", "Facility"), ("room", "Room")]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{4,20}$")   # 4 to 20 letters, numbers, _ or .


# ------------------------------------------------------------------
# Brevo transactional email
# ------------------------------------------------------------------
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "corebound0950@gmail.com").strip()
BREVO_SENDER_NAME = os.environ.get("BREVO_SENDER_NAME", "COREBOUND").strip()


def send_email(to_email, subject, text_content, html_content=None, to_name=None):
    """Send transactional email through Brevo. Returns True on accepted delivery request."""
    if not BREVO_API_KEY or not BREVO_SENDER_EMAIL:
        app.logger.warning("Brevo email not sent: BREVO_API_KEY or BREVO_SENDER_EMAIL is not configured.")
        return False
    if not to_email or not EMAIL_RE.fullmatch(str(to_email).strip()):
        app.logger.warning("Brevo email not sent: recipient address is missing or invalid.")
        return False
    payload = {
        "sender": {"name": BREVO_SENDER_NAME or "COREBOUND", "email": BREVO_SENDER_EMAIL},
        "to": [{"email": str(to_email).strip(), **({"name": str(to_name).strip()} if to_name else {})}],
        "subject": str(subject)[:200],
        "textContent": str(text_content)[:20000],
    }
    if html_content:
        payload["htmlContent"] = str(html_content)[:30000]
    req = urllib.request.Request(
        BREVO_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": BREVO_API_KEY,
            "accept": "application/json",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            accepted = 200 <= response.status < 300
            if not accepted:
                app.logger.warning("Brevo rejected email with HTTP status %s.", response.status)
            return accepted
    except urllib.error.HTTPError as exc:
        # Do not log request headers or secrets.
        app.logger.warning("Brevo email failed with HTTP status %s.", exc.code)
    except (urllib.error.URLError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
        app.logger.warning("Brevo email request failed: %s", type(exc).__name__)
    return False


def send_email_html(to_email, subject, heading, paragraphs, to_name=None):
    """Build a small, escaped HTML email and send a matching plain-text version."""
    safe_heading = html.escape(str(heading))
    safe_paragraphs = [str(p) for p in paragraphs]
    html_paragraphs = "".join(
        '<p style="margin:0 0 12px;line-height:1.5">' + html.escape(p) + '</p>'
        for p in safe_paragraphs
    )
    body_html = (
        '<div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;color:#17202b">'
        '<div style="background:#102a43;color:#fff;padding:18px 22px;border-radius:10px 10px 0 0">'
        '<strong>COREBOUND</strong></div><div style="padding:22px;border:1px solid #dce3eb">'
        f'<h2 style="margin:0 0 18px">{safe_heading}</h2>{html_paragraphs}'
        '</div><p style="font-size:12px;color:#627184">Automated message from COREBOUND.</p></div>'
    )
    text_body = heading + "\n\n" + "\n\n".join(safe_paragraphs) + "\n\nAutomated message from COREBOUND."
    return send_email(to_email, subject, text_body, body_html, to_name=to_name)


def super_admin_email(db):
    """Resolve the designated Super Admin's real email, with an optional environment fallback."""
    row = db.execute(
        "SELECT email, full_name FROM users WHERE role='admin' AND username=? COLLATE NOCASE AND status='active'",
        (SUPER_ADMIN_USERNAME,),
    ).fetchone()
    if row and EMAIL_RE.fullmatch((row["email"] or "").strip()):
        return row["email"].strip(), row["full_name"]
    fallback = os.environ.get("COREBOUND_ADMIN_EMAIL", "").strip()
    if EMAIL_RE.fullmatch(fallback):
        return fallback, "COREBOUND Super Admin"
    # Development fallback when the seeded admin has a non-deliverable .local address.
    if EMAIL_RE.fullmatch(BREVO_SENDER_EMAIL):
        return BREVO_SENDER_EMAIL, "COREBOUND Super Admin"
    app.logger.warning("Super Admin email is not configured with a deliverable address.")
    return None, None


# ------------------------------------------------------------------
# Database helpers
# ------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the tables from schema.sql (safe to run more than once)."""
    with sqlite3.connect(DB_PATH) as conn, open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())




# ------------------------------------------------------------------
# Super Admin / account-reporting helpers
# ------------------------------------------------------------------
SUPER_ADMIN_USERNAME = "COREBOUND0950"
ACCOUNT_REPORT_STATUSES = {
    "pending": "Pending", "under_review": "Under review",
    "resolved": "Resolved", "dismissed": "Dismissed",
}


def ensure_admin_tables():
    """Idempotent migration for existing SQLite databases."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS account_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reported_user_id INTEGER NOT NULL,
            reporter_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','under_review','resolved','dismissed')),
            admin_note TEXT,
            reviewed_by INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (reported_user_id) REFERENCES users(id),
            FOREIGN KEY (reporter_id) REFERENCES users(id),
            FOREIGN KEY (reviewed_by) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_account_reports_status
            ON account_reports(status, created_at DESC);
        CREATE TRIGGER IF NOT EXISTS trg_only_designated_admin_insert
        BEFORE INSERT ON users
        WHEN NEW.role = 'admin'
          AND (NEW.username COLLATE NOCASE <> 'COREBOUND0950'
               OR EXISTS (SELECT 1 FROM users WHERE role = 'admin'))
        BEGIN
            SELECT RAISE(ABORT, 'Only the designated Super Admin account is allowed.');
        END;
        CREATE TRIGGER IF NOT EXISTS trg_only_designated_admin_update
        BEFORE UPDATE OF role, username ON users
        WHEN NEW.role = 'admin'
          AND (NEW.username COLLATE NOCASE <> 'COREBOUND0950'
               OR EXISTS (SELECT 1 FROM users WHERE role = 'admin' AND id <> NEW.id))
        BEGIN
            SELECT RAISE(ABORT, 'Only the designated Super Admin account is allowed.');
        END;
    """)
    db.commit()


def ensure_feature_tables():
    """Idempotent migration for the seven report workflow enhancements."""
    db = get_db()
    # Existing installations created before these columns existed are migrated in place.
    columns = {r[1] for r in db.execute("PRAGMA table_info(reports)")}
    for name, definition in (
        ("due_at", "TEXT"), ("overdue_alerted_at", "TEXT"),
        ("before_photo_path", "TEXT"), ("after_photo_path", "TEXT"),
    ):
        if name not in columns:
            db.execute(f"ALTER TABLE reports ADD COLUMN {name} {definition}")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS report_progress_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            evidence_path TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_progress_report ON report_progress_updates(report_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_reports_due ON reports(due_at, status);
        CREATE TABLE IF NOT EXISTS report_assignment_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            actor_id INTEGER,
            previous_leader_id INTEGER,
            new_leader_id INTEGER,
            previous_member_id INTEGER,
            new_member_id INTEGER,
            note TEXT NOT NULL DEFAULT '',
            changed_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
            FOREIGN KEY (actor_id) REFERENCES users(id),
            FOREIGN KEY (previous_leader_id) REFERENCES users(id),
            FOREIGN KEY (new_leader_id) REFERENCES users(id),
            FOREIGN KEY (previous_member_id) REFERENCES users(id),
            FOREIGN KEY (new_member_id) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_assignment_history_report ON report_assignment_history(report_id, changed_at);
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username_key TEXT NOT NULL,
            ip_key TEXT NOT NULL,
            succeeded INTEGER NOT NULL DEFAULT 0,
            attempted_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_login_attempts_time ON login_attempts(username_key, ip_key, attempted_at);
        CREATE TABLE IF NOT EXISTS security_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id INTEGER,
            detail TEXT,
            ip_address TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (actor_id) REFERENCES users(id)
        );
    """)
    # Backfill deadlines on existing reports without a deadline.
    db.execute("""UPDATE reports SET due_at = datetime(created_at, CASE severity
        WHEN 'urgent' THEN '+1 day' WHEN 'high' THEN '+3 days'
        WHEN 'low' THEN '+14 days' ELSE '+7 days' END) WHERE due_at IS NULL""")
    db.commit()


def ensure_email_workflow_tables():
    """Create leader-application storage and the forced-password-change flag safely."""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS leader_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            username TEXT NOT NULL COLLATE NOCASE,
            email TEXT NOT NULL COLLATE NOCASE,
            territory_id INTEGER NOT NULL,
            department TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','denied')),
            submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
            reviewed_at TEXT,
            reviewed_by INTEGER,
            FOREIGN KEY (territory_id) REFERENCES territories(id),
            FOREIGN KEY (reviewed_by) REFERENCES users(id)
        );
        CREATE INDEX IF NOT EXISTS idx_leader_applications_status
            ON leader_applications(status, submitted_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_leader_app_username
            ON leader_applications(username COLLATE NOCASE) WHERE status='pending';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_leader_app_email
            ON leader_applications(email COLLATE NOCASE) WHERE status='pending';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_leader_app_territory
            ON leader_applications(territory_id) WHERE status='pending';
    """)
    user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "must_change_password" not in user_columns:
        db.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
    db.commit()


@app.before_request
def migrate_feature_schema():
    # Skip only if the database hasn't been initialized yet; the regular routes
    # will provide the existing login/DB error behavior in that case.
    try:
        ensure_feature_tables()
        ensure_email_workflow_tables()
    except sqlite3.Error:
        pass


# ------------------------------------------------------------------
# Phase 1 security controls: CSRF, session revalidation, response headers
# ------------------------------------------------------------------
import secrets
from flask import abort

@app.before_request
def corebound_security_guard():
    # Ensure every browser session has an unpredictable CSRF token.
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        supplied = request.headers.get("X-CSRFToken") or request.form.get("csrf_token", "")
        expected = session.get("_csrf_token", "")
        if not supplied or not expected or not secrets.compare_digest(supplied, expected):
            if request.accept_mimetypes.best == "application/json" or request.path.startswith(("/member/", "/leader/reports/")):
                return jsonify(error="Security token expired or missing. Refresh the page and try again."), 400
            abort(400, description="Security token missing or invalid. Refresh the page and try again.")
    # Team members created by a leader must replace their emailed temporary password first.
    if session.get("must_change_password") and request.endpoint not in (
        "change_password", "logout", "static"
    ):
        return redirect(url_for("change_password"))
    # Re-check the current account on every authenticated request so suspensions
    # and role changes take effect without waiting for session expiry.
    if "user_id" in session:
        try:
            row = get_db().execute("SELECT username, role, status FROM users WHERE id=?", (session["user_id"],)).fetchone()
            if row is None or row["status"] == "removed" or row["role"] != session.get("role"):
                session.clear()
                if request.path.startswith(("/member/", "/leader/")):
                    return jsonify(error="Your account session is no longer active. Please log in again."), 401
                return redirect(url_for("login"))
            session["username"] = row["username"]
        except sqlite3.Error:
            # During first-time database initialization there may be no users table.
            pass

@app.after_request
def corebound_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if response.mimetype == "text/html":
        token = session.get("_csrf_token", "")
        # Inject the token into all POST forms without requiring invasive redesign
        # of existing HTML templates; fetch POSTs use the meta token below.
        html = response.get_data(as_text=True)
        if "</head>" in html:
            html = html.replace("</head>", '<meta name="csrf-token" content="' + token + '"></head>', 1)
        form_pattern = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
        def add_token(match):
            tag = match.group(0)
            if re.search(r"\bmethod\s*=\s*([\"']?)post\1", tag, re.IGNORECASE):
                return tag + '<input type="hidden" name="csrf_token" value="' + token + '">'
            return tag
        html = form_pattern.sub(add_token, html)
        response.set_data(html)
    return response

def deadline_for(severity):
    days = {"urgent": 1, "high": 3, "medium": 7, "low": 14}.get(severity, 7)
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def auto_assign_report(db, report_id, territory_id, ticket_no, title, reporter_id):
    """Route by territory to its leader and, when available, the least-loaded member."""
    leader = db.execute("SELECT id, full_name FROM users WHERE role='team_leader' AND territory_id=? AND status='active' ORDER BY id LIMIT 1", (territory_id,)).fetchone()
    if not leader:
        return None, None, "submitted"
    member = db.execute("""SELECT u.id, u.full_name FROM users u
        WHERE u.role='team_member' AND u.leader_id=? AND u.status='active'
        ORDER BY (SELECT COUNT(*) FROM reports r WHERE r.assigned_member_id=u.id
                  AND r.status IN ('assigned','verifying','confirmed','in_progress')), u.id LIMIT 1""", (leader['id'],)).fetchone()
    if member:
        old = db.execute("SELECT assigned_leader_id, assigned_member_id FROM reports WHERE id=?", (report_id,)).fetchone()
        db.execute("UPDATE reports SET assigned_leader_id=?, assigned_member_id=?, status='assigned' WHERE id=?", (leader['id'], member['id'], report_id))
        db.execute("INSERT INTO report_assignment_history(report_id,actor_id,previous_leader_id,new_leader_id,previous_member_id,new_member_id,note) VALUES(?,?,?,?,?,?,?)",
                   (report_id, leader['id'], old['assigned_leader_id'], leader['id'], old['assigned_member_id'], member['id'], 'Automatic territory assignment'))
        add_history_note(db, report_id, 'assigned', f"Automatically assigned to {member['full_name']} in territory {territory_id}", leader['id'])
        notify(db, member['id'], report_id, f"New task {ticket_no}: {title} has been assigned to you.")
        notify(db, leader['id'], report_id, f"New report {ticket_no}: {title} was automatically assigned to {member['full_name']}.")
        notify(db, reporter_id, report_id, f"Your report {ticket_no} has been assigned to the campus team.")
        return leader['id'], member['id'], 'assigned'
    # Assign ownership to the leader; keep status submitted so they can assign a member.
    old = db.execute("SELECT assigned_leader_id, assigned_member_id FROM reports WHERE id=?", (report_id,)).fetchone()
    db.execute("UPDATE reports SET assigned_leader_id=? WHERE id=?", (leader['id'], report_id))
    db.execute("INSERT INTO report_assignment_history(report_id,actor_id,previous_leader_id,new_leader_id,previous_member_id,new_member_id,note) VALUES(?,?,?,?,?,?,?)",
               (report_id, leader['id'], old['assigned_leader_id'], leader['id'], old['assigned_member_id'], None, 'Automatic territory assignment; member pending'))
    notify(db, leader['id'], report_id, f"New report {ticket_no}: {title} needs a team member assignment.")
    return leader['id'], None, 'submitted'


def alert_overdue_reports(db):
    """Create one-time overdue notifications for each report and its responsible people."""
    rows = db.execute("""SELECT id, ticket_no, title, reporter_id, assigned_leader_id, assigned_member_id
        FROM reports WHERE due_at IS NOT NULL AND due_at < datetime('now')
        AND status NOT IN ('resolved','invalid') AND overdue_alerted_at IS NULL""").fetchall()
    for r in rows:
        msg = f"OVERDUE: report {r['ticket_no']} — {r['title']} is past its deadline."
        for uid in {r['reporter_id'], r['assigned_leader_id'], r['assigned_member_id']}:
            if uid:
                notify(db, uid, r['id'], msg)
        db.execute("UPDATE reports SET overdue_alerted_at=datetime('now') WHERE id=?", (r['id'],))
    if rows:
        db.commit()


def admin_required(view):
    """Restrict the view to the one designated Super Admin account."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "admin" or session.get("username", "").casefold() != SUPER_ADMIN_USERNAME.casefold():
            flash("You do not have permission to access that page.", "error")
            return redirect(url_for("home"))
        ensure_admin_tables()
        exists = get_db().execute(
            "SELECT 1 FROM users WHERE id=? AND role='admin' AND username=? AND status='active'",
            (session["user_id"], SUPER_ADMIN_USERNAME)
        ).fetchone()
        if not exists:
            session.clear()
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def security_audit(action, target_type=None, target_id=None, detail=None):
    try:
        db = get_db()
        db.execute("INSERT INTO security_audit_log(actor_id,action,target_type,target_id,detail,ip_address) VALUES(?,?,?,?,?,?)",
                   (session.get("user_id"), action, target_type, target_id, (detail or "")[:1000], (request.remote_addr or "")[:64]))
    except sqlite3.Error:
        pass


def account_type_label(role):
    return {
        "reporter": "Type 1 — Reporter",
        "team_leader": "Type 2 — Team Leader",
        "team_member": "Type 2 — Team Member",
        "admin": "Type 3 — Super Admin",
    }.get(role, role.replace("_", " ").title())


def admin_dashboard_context():
    db = get_db()
    q = request.args.get("q", "").strip()[:100]
    role_filter = request.args.get("role", "").strip()
    territory_filter = request.args.get("territory", "").strip()
    department_filter = request.args.get("department", "").strip()[:120]
    status_filter = request.args.get("status", "").strip()
    joined_from = request.args.get("joined_from", "").strip()
    joined_to = request.args.get("joined_to", "").strip()
    where, params = ["1=1"], []
    if role_filter in ("reporter", "team_leader", "team_member", "admin"):
        where.append("u.role = ?")
        params.append(role_filter)
    if territory_filter.isdigit():
        where.append("u.territory_id = ?")
        params.append(int(territory_filter))
    if department_filter:
        where.append("u.department LIKE ?")
        params.append(f"%{department_filter}%")
    if status_filter in ("active", "warned", "removed"):
        where.append("u.status = ?")
        params.append(status_filter)
    if joined_from:
        where.append("date(u.created_at) >= date(?)")
        params.append(joined_from)
    if joined_to:
        where.append("date(u.created_at) <= date(?)")
        params.append(joined_to)
    if q:
        where.append("(u.full_name LIKE ? OR u.username LIKE ? OR u.email LIKE ? OR u.department LIKE ?)")
        pattern = f"%{q}%"
        params.extend([pattern] * 4)
    users = db.execute(
        "SELECT u.id, u.full_name, u.username, u.email, u.department, u.role, u.status, "
        "u.warning_count, u.created_at, t.name AS territory, l.full_name AS leader_name "
        "FROM users u LEFT JOIN territories t ON t.id=u.territory_id "
        "LEFT JOIN users l ON l.id=u.leader_id WHERE " + " AND ".join(where) +
        " ORDER BY CASE u.role WHEN 'admin' THEN 0 WHEN 'reporter' THEN 1 "
        "WHEN 'team_leader' THEN 2 ELSE 3 END, u.created_at DESC, u.id DESC LIMIT 500",
        params
    ).fetchall()
    counts = {r["role"]: r["n"] for r in db.execute(
        "SELECT role, COUNT(*) n FROM users GROUP BY role")}
    total_reports = db.execute("SELECT COUNT(*) FROM account_reports").fetchone()[0]
    pending_reports = db.execute("SELECT COUNT(*) FROM account_reports WHERE status IN ('pending','under_review')").fetchone()[0]
    territories = db.execute("SELECT id, name FROM territories ORDER BY name").fetchall()
    departments = db.execute("SELECT DISTINCT department FROM users WHERE trim(department)<>'' ORDER BY department COLLATE NOCASE").fetchall()
    report_stats = {r['status']: r['n'] for r in db.execute("SELECT status, COUNT(*) n FROM reports GROUP BY status")}
    overdue_count = db.execute("SELECT COUNT(*) FROM reports WHERE due_at < datetime('now') AND status NOT IN ('resolved','invalid')").fetchone()[0]
    recent_activity = db.execute("SELECT h.status, COUNT(*) n FROM report_status_history h WHERE h.changed_at >= datetime('now','-30 days') GROUP BY h.status ORDER BY n DESC").fetchall()
    territory_stats = db.execute("SELECT t.name, COUNT(r.id) AS total, SUM(CASE WHEN r.status='resolved' THEN 1 ELSE 0 END) AS resolved, SUM(CASE WHEN r.due_at < datetime('now') AND r.status NOT IN ('resolved','invalid') THEN 1 ELSE 0 END) AS overdue FROM territories t LEFT JOIN reports r ON r.territory_id=t.id GROUP BY t.id,t.name ORDER BY total DESC,t.name").fetchall()
    monthly_stats = db.execute("SELECT strftime('%Y-%m',created_at) AS month, COUNT(*) AS total, SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END) AS resolved FROM reports WHERE created_at >= datetime('now','-6 months') GROUP BY strftime('%Y-%m',created_at) ORDER BY month").fetchall()
    completion_rate = round(100 * report_stats.get('resolved',0) / max(1, sum(report_stats.values())), 1)
    leaders = db.execute("SELECT id, full_name, username, territory_id FROM users WHERE role='team_leader' AND status='active' ORDER BY full_name").fetchall()
    pending_leader_applications = db.execute(
        "SELECT a.id, a.full_name, a.username, a.email, a.department, a.submitted_at, t.name AS territory "
        "FROM leader_applications a JOIN territories t ON t.id=a.territory_id "
        "WHERE a.status='pending' ORDER BY a.submitted_at, a.id"
    ).fetchall()
    notifications = db.execute(
        "SELECT id, message, created_at FROM notifications WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT 8",
        (session["user_id"],)
    ).fetchall()
    unread = db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                        (session["user_id"],)).fetchone()[0]
    return {
        "users": users, "q": q, "role_filter": role_filter,
        "counts": counts, "total_users": sum(counts.values()),
        "total_reports": total_reports, "pending_reports": pending_reports,
        "territories": territories, "departments": departments, "leaders": leaders,
        "territory_filter": territory_filter, "department_filter": department_filter,
        "status_filter": status_filter, "joined_from": joined_from, "joined_to": joined_to,
        "report_stats": report_stats, "overdue_count": overdue_count, "recent_activity": recent_activity,
        "territory_stats": territory_stats, "monthly_stats": monthly_stats, "completion_rate": completion_rate,
        "notifications": notifications, "unread_notifications": unread,
        "pending_leader_applications": pending_leader_applications,
        "type_labels": {r: account_type_label(r) for r in ("reporter","team_leader","team_member","admin")},
    }


@app.route("/admin")
@admin_required
def admin_dashboard():
    alert_overdue_reports(get_db())
    return render_template("Type_3/dashboard.html", **admin_dashboard_context())


@app.route("/admin/leader-applications/<int:application_id>/decision", methods=["POST"])
@admin_required
def admin_decide_leader_application(application_id):
    """Approve or deny a pending leader application; only approval creates a user account."""
    decision = request.form.get("decision", "").strip().lower()
    if decision not in ("approve", "deny"):
        flash("Choose approve or deny.", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    application = db.execute(
        "SELECT a.*, t.name AS territory_name FROM leader_applications a "
        "JOIN territories t ON t.id=a.territory_id WHERE a.id=? AND a.status='pending'",
        (application_id,),
    ).fetchone()
    if application is None:
        flash("That leader application is no longer pending.", "error")
        return redirect(url_for("admin_dashboard"))

    if decision == "approve":
        # Guard against conflicts created after the application was submitted.
        conflict = db.execute(
            "SELECT 1 FROM users WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE "
            "OR (role='team_leader' AND territory_id=?)",
            (application["username"], application["email"], application["territory_id"]),
        ).fetchone()
        if conflict:
            flash("This application conflicts with an existing account or leader assignment. The application remains pending.", "error")
            return redirect(url_for("admin_dashboard"))
        try:
            db.execute(
                "INSERT INTO users (full_name, username, email, department, password_hash, role, territory_id, status) "
                "VALUES (?, ?, ?, ?, ?, 'team_leader', ?, 'active')",
                (application["full_name"], application["username"], application["email"],
                 application["territory_name"], application["password_hash"], application["territory_id"]),
            )
            db.execute(
                "UPDATE leader_applications SET status='approved', reviewed_at=datetime('now'), reviewed_by=? WHERE id=?",
                (session["user_id"], application_id),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            flash("Could not approve this application because of a database conflict.", "error")
            return redirect(url_for("admin_dashboard"))

        send_email_html(
            application["email"], "Your COREBOUND Team Leader application was approved",
            "Application approved",
            [f"Hello {application['full_name']}, your Team Leader application for {application['territory_name']} has been approved.",
             "Your account is now active. You can log in using the username and password you chose during registration."],
            to_name=application["full_name"],
        )
        security_audit("leader_application_approved", "leader_application", application_id,
                       f"Approved team leader for territory {application['territory_id']}")
        db.commit()
        flash(f"Approved {application['full_name']}. An email decision was sent if Brevo is configured.", "success")
    else:
        db.execute(
            "UPDATE leader_applications SET status='denied', reviewed_at=datetime('now'), reviewed_by=? WHERE id=?",
            (session["user_id"], application_id),
        )
        db.commit()
        send_email_html(
            application["email"], "Your COREBOUND Team Leader application decision",
            "Application not approved",
            [f"Hello {application['full_name']}, your Team Leader application for {application['territory_name']} was not approved.",
             "No active Team Leader account was created. If you believe this decision was made in error, please contact the COREBOUND administrator."],
            to_name=application["full_name"],
        )
        security_audit("leader_application_denied", "leader_application", application_id,
                       f"Denied team leader for territory {application['territory_id']}")
        db.commit()
        flash(f"Denied {application['full_name']}. An email decision was sent if Brevo is configured.", "success")

    return redirect(url_for("admin_dashboard"))


@app.route("/admin/accounts", methods=["POST"])
@admin_required
def admin_create_account():
    form = request.form
    full_name = form.get("full_name", "").strip()
    username = form.get("username", "").strip()
    email = form.get("email", "").strip().lower()
    department = form.get("department", "").strip()
    password = form.get("password", "")
    role = form.get("role", "reporter")
    territory_id = form.get("territory_id", "").strip()
    leader_id = form.get("leader_id", "").strip()

    if role not in ("reporter", "team_leader", "team_member"):
        flash("Choose a valid non-admin account type.", "error")
        return redirect(url_for("admin_dashboard"))
    if not (full_name and username and email and department and password):
        flash("Fill in all required account fields.", "error")
        return redirect(url_for("admin_dashboard"))
    if username.casefold() == SUPER_ADMIN_USERNAME.casefold():
        flash("That username is reserved for the designated Super Admin account.", "error")
        return redirect(url_for("admin_dashboard"))
    if not USERNAME_RE.fullmatch(username):
        flash("Username must be 4–20 characters: letters, numbers, _ or .", "error")
        return redirect(url_for("admin_dashboard"))
    if not EMAIL_RE.fullmatch(email):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("admin_dashboard"))
    problems = password_problems(password)
    if problems:
        flash("Password needs " + ", ".join(problems) + ".", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE OR email=? COLLATE NOCASE",
                  (username, email)).fetchone():
        flash("That username or email is already registered.", "error")
        return redirect(url_for("admin_dashboard"))

    territory = None
    leader = None
    if role == "team_leader":
        territory = db.execute("SELECT id, name FROM territories WHERE id=?", (territory_id,)).fetchone()
        if not territory:
            flash("Choose a valid campus territory for the team leader.", "error")
            return redirect(url_for("admin_dashboard"))
        if db.execute("SELECT 1 FROM users WHERE role='team_leader' AND territory_id=?", (territory_id,)).fetchone():
            flash("That territory already has a team leader.", "error")
            return redirect(url_for("admin_dashboard"))
        department = territory["name"]
    elif role == "team_member":
        leader = db.execute("SELECT id, territory_id, department FROM users WHERE id=? AND role='team_leader' AND status='active'",
                            (leader_id,)).fetchone()
        if not leader:
            flash("Choose an active team leader for the member.", "error")
            return redirect(url_for("admin_dashboard"))
        territory = db.execute("SELECT id, name FROM territories WHERE id=?", (leader["territory_id"],)).fetchone()
        if not territory:
            flash("The selected team leader does not have a valid territory.", "error")
            return redirect(url_for("admin_dashboard"))
        department = territory["name"]
    try:
        db.execute(
            "INSERT INTO users (full_name, username, email, department, password_hash, role, territory_id, leader_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (full_name, username, email, department, generate_password_hash(password), role,
             territory["id"] if territory else None, leader["id"] if leader else None)
        )
        db.commit()
        security_audit("account_created", "user", db.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()[0], f"Created role {role}")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash("The account could not be created. Check the username, email, and team assignment.", "error")
    else:
        flash(f"{account_type_label(role)} account created successfully.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users/<int:user_id>/status", methods=["POST"])
@admin_required
def admin_update_user_status(user_id):
    status = request.form.get("status", "")
    if status not in ("active", "warned", "removed"):
        flash("Invalid account status.", "error")
        return redirect(url_for("admin_dashboard"))
    db = get_db()
    target = db.execute("SELECT id, role, username, full_name, email FROM users WHERE id=?", (user_id,)).fetchone()
    if target is None:
        flash("Account not found.", "error")
    elif target["role"] == "admin" or target["username"].casefold() == SUPER_ADMIN_USERNAME.casefold():
        flash("The Super Admin account is protected from account-status changes here.", "error")
    else:
        db.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))
        if status == "warned":
            try:
                db.execute("UPDATE users SET warning_count=COALESCE(warning_count,0)+1 WHERE id=?", (user_id,))
            except sqlite3.OperationalError:
                pass  # Older schemas may not include warning_count.
        db.commit()
        security_audit("account_status_changed", "user", user_id, f"Status changed to {status}")
        db.commit()
        if status == "warned":
            reason = request.form.get("warning_reason", "").strip()[:1000] or "Please review your recent activity and follow COREBOUND's rules and procedures."
            role_label = {"reporter": "Type 1 Reporter", "team_member": "Type 2 Team Member", "team_leader": "Team Leader"}.get(target["role"], target["role"].replace("_", " ").title())
            send_email_html(
                target["email"], "COREBOUND: Official account warning", "You have received an account warning",
                [f"Hello {target['full_name']},", f"The COREBOUND Super Admin has issued a warning for your {role_label} account.",
                 f"Reason: {reason}", "Please review the system rules and contact the Super Admin if you need clarification."],
                to_name=target["full_name"],
            )
            db.execute("INSERT INTO notifications (user_id, report_id, message) VALUES (?, NULL, ?)",
                       (user_id, "COREBOUND warning issued: " + reason[:180]))
            db.commit()
        flash("Account status updated. A warning email was attempted if the account was warned and Brevo is configured.", "success")
    return redirect(url_for("admin_dashboard", q=request.args.get("q", ""), role=request.args.get("role", "")))


@app.route("/admin/notifications/<int:notification_id>/read", methods=["POST"])
@admin_required
def admin_mark_notification_read(notification_id):
    db = get_db()
    db.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
               (notification_id, session["user_id"]))
    db.commit()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/reports")
@admin_required
def admin_reports():
    db = get_db()
    status_filter = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()[:100]
    where, params = ["1=1"], []
    if status_filter in ACCOUNT_REPORT_STATUSES:
        where.append("ar.status=?")
        params.append(status_filter)
    if q:
        pattern = f"%{q}%"
        where.append("(reported.username LIKE ? OR reported.full_name LIKE ? OR reporter.username LIKE ? "
                     "OR reporter.full_name LIKE ? OR ar.reason LIKE ? OR ar.description LIKE ?)")
        params.extend([pattern] * 6)
    reports = db.execute(
        "SELECT ar.*, reported.full_name AS reported_name, reported.username AS reported_username, "
        "reported.role AS reported_role, reported.status AS reported_account_status, "
        "reporter.full_name AS reporter_name, reporter.username AS reporter_username, "
        "reviewer.full_name AS reviewer_name "
        "FROM account_reports ar JOIN users reported ON reported.id=ar.reported_user_id "
        "JOIN users reporter ON reporter.id=ar.reporter_id "
        "LEFT JOIN users reviewer ON reviewer.id=ar.reviewed_by WHERE " + " AND ".join(where) +
        " ORDER BY CASE ar.status WHEN 'pending' THEN 0 WHEN 'under_review' THEN 1 ELSE 2 END, "
        "ar.created_at DESC, ar.id DESC LIMIT 500", params
    ).fetchall()
    counts = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) n FROM account_reports GROUP BY status")}
    unread = db.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
                        (session["user_id"],)).fetchone()[0]
    return render_template("Type_3/reports.html", reports=reports, counts=counts,
                           status_filter=status_filter, q=q, unread_notifications=unread,
                           status_labels=ACCOUNT_REPORT_STATUSES)


@app.route("/admin/reports/<int:report_id>", methods=["POST"])
@admin_required
def admin_update_account_report(report_id):
    status = request.form.get("status", "")
    note = request.form.get("admin_note", "").strip()
    if status not in ACCOUNT_REPORT_STATUSES or len(note) > 1000:
        flash("Choose a valid status and keep the note under 1,000 characters.", "error")
        return redirect(url_for("admin_reports"))
    db = get_db()
    row = db.execute("SELECT id FROM account_reports WHERE id=?", (report_id,)).fetchone()
    if row is None:
        flash("Account report not found.", "error")
    else:
        db.execute("UPDATE account_reports SET status=?, admin_note=?, reviewed_by=?, updated_at=datetime('now') WHERE id=?",
                   (status, note, session["user_id"], report_id))
        db.commit()
        flash("Account report updated.", "success")
    return redirect(url_for("admin_reports"))


@app.route("/report-account", methods=["GET", "POST"])
def report_account():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if session.get("role") == "admin":
        flash("Super Admin accounts do not submit user reports from this form.", "error")
        return redirect(url_for("home"))
    db = get_db()
    targets = db.execute(
        "SELECT id, full_name, username, role FROM users WHERE status <> 'removed' AND id <> ? AND role <> 'admin' ORDER BY full_name",
        (session["user_id"],)
    ).fetchall()
    if request.method == "POST":
        reported_id = request.form.get("reported_user_id", type=int)
        reason = request.form.get("reason", "").strip()
        description = request.form.get("description", "").strip()
        target = next((u for u in targets if u["id"] == reported_id), None)
        if not target:
            flash("Choose a valid account to report.", "error")
        elif reason not in ("harassment", "impersonation", "spam", "misconduct", "other"):
            flash("Choose a valid reason.", "error")
        elif len(description) < 10 or len(description) > 2000:
            flash("Please provide a description between 10 and 2,000 characters.", "error")
        else:
            db.execute("INSERT INTO account_reports (reported_user_id, reporter_id, reason, description) VALUES (?, ?, ?, ?)",
                       (reported_id, session["user_id"], reason, description))
            admin = db.execute("SELECT id FROM users WHERE role='admin' AND username=?",
                               (SUPER_ADMIN_USERNAME,)).fetchone()
            if admin:
                db.execute("INSERT INTO notifications (user_id, message) VALUES (?, ?)",
                           (admin["id"], f"New account report submitted about {target['username']}."))
            db.commit()
            flash("Your account report was submitted to the Super Admin for review.", "success")
            return redirect(url_for("home"))
    return render_template("report_account.html", targets=targets)


@app.cli.command("bootstrap-super-admin")
@click.password_option(prompt="Super Admin password")
def bootstrap_super_admin(password):
    """Create the one designated Super Admin account if it does not already exist."""
    if password_problems(password):
        raise click.ClickException("Password needs: " + ", ".join(password_problems(password)))
    ensure_admin_db_file()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS account_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reported_user_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','under_review','resolved','dismissed')),
                admin_note TEXT,
                reviewed_by INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (reported_user_id) REFERENCES users(id),
                FOREIGN KEY (reporter_id) REFERENCES users(id),
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            );
            CREATE TRIGGER IF NOT EXISTS trg_only_designated_admin_insert
            BEFORE INSERT ON users
            WHEN NEW.role='admin' AND (NEW.username COLLATE NOCASE <> 'COREBOUND0950'
                 OR EXISTS (SELECT 1 FROM users WHERE role='admin'))
            BEGIN SELECT RAISE(ABORT, 'Only the designated Super Admin account is allowed.'); END;
        """)
        existing = conn.execute("SELECT id FROM users WHERE role='admin' OR username=? COLLATE NOCASE",
                                 (SUPER_ADMIN_USERNAME,)).fetchone()
        if existing:
            raise click.ClickException("A Super Admin/designated username already exists. No account was created.")
        try:
            conn.execute(
                "INSERT INTO users (full_name, username, email, department, password_hash, role) "
                "VALUES (?, ?, ?, ?, ?, 'admin')",
                ("COREBOUND Super Admin", SUPER_ADMIN_USERNAME, "corebound0950@gmail.com",
                 "Administration", generate_password_hash(password))
            )
        except sqlite3.IntegrityError as exc:
            raise click.ClickException(f"Could not create the designated Super Admin: {exc}") from exc
    click.echo("The designated Super Admin account was created.")

@app.cli.command("configure-super-admin")
@click.option("--password", prompt="Super Admin password", hide_input=True,
              confirmation_prompt=True)
def configure_super_admin(password):
    """Create or reset the one designated Super Admin account."""
    if password_problems(password):
        raise click.ClickException("Password needs: " + ", ".join(password_problems(password)))
    ensure_admin_db_file()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS account_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reported_user_id INTEGER NOT NULL,
                reporter_id INTEGER NOT NULL,
                reason TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','under_review','resolved','dismissed')),
                admin_note TEXT,
                reviewed_by INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (reported_user_id) REFERENCES users(id),
                FOREIGN KEY (reporter_id) REFERENCES users(id),
                FOREIGN KEY (reviewed_by) REFERENCES users(id)
            );
            CREATE TRIGGER IF NOT EXISTS trg_only_designated_admin_insert
            BEFORE INSERT ON users
            WHEN NEW.role='admin' AND (NEW.username COLLATE NOCASE <> 'COREBOUND0950'
                 OR EXISTS (SELECT 1 FROM users WHERE role='admin'))
            BEGIN SELECT RAISE(ABORT, 'Only the designated Super Admin account is allowed.'); END;
        """)
        rows = conn.execute(
            "SELECT id, role FROM users WHERE username=? COLLATE NOCASE OR role='admin'",
            (SUPER_ADMIN_USERNAME,)
        ).fetchall()
        if any(row["role"] == "admin" for row in rows):
            admin = next(row for row in rows if row["role"] == "admin")
            other = conn.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE AND id<>?",
                (SUPER_ADMIN_USERNAME, admin["id"])
            ).fetchone()
            if other:
                raise click.ClickException("Username COREBOUND0950 is already used by another account.")
            conn.execute(
                "UPDATE users SET username=?, password_hash=?, status='active' WHERE id=? AND role='admin'",
                (SUPER_ADMIN_USERNAME, generate_password_hash(password), admin["id"])
            )
            conn.commit()
            click.echo("Existing designated Super Admin account updated and activated.")
        else:
            if rows:
                raise click.ClickException(
                    "Username COREBOUND0950 already belongs to a non-admin account. "
                    "Rename that account before configuring the Super Admin."
                )
            conn.execute(
                "INSERT INTO users (full_name, username, email, department, password_hash, role) "
                "VALUES (?, ?, ?, ?, ?, 'admin')",
                ("COREBOUND Super Admin", SUPER_ADMIN_USERNAME, "corebound0950@admin.local",
                 "Administration", generate_password_hash(password))
            )
            conn.commit()
            click.echo("Designated Super Admin account created.")



def ensure_admin_db_file():
    if not os.path.exists(DB_PATH):
        raise click.ClickException("database.db does not exist. Initialize it from schema.sql first.")



# ------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------
def password_problems(pw):
    """Return the list of password rules that are NOT met."""
    problems = []

    if len(pw) < 6:
        problems.append("at least 12 characters")
    if not re.search(r"[A-Z]", pw):
        problems.append("1 uppercase letter")
    if not re.search(r"[a-z]", pw):
        problems.append("1 lowercase letter")
    if not re.search(r"\d", pw):
        problems.append("1 number")
    if not re.search(r"[^A-Za-z0-9]", pw):
        problems.append("1 special character")
    return problems


RECAPTCHA_SITE_KEY = os.environ.get("RECAPTCHA_SITE_KEY", "6Lfnp_stAAAAAPFXWM1NT8kvVmjFg_QVyLU7t-NY").strip()
RECAPTCHA_SECRET_KEY = os.environ.get("RECAPTCHA_SECRET_KEY", "").strip()
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


@app.context_processor
def inject_recaptcha_site_key():
    """Make the public reCAPTCHA site key available to the login/register templates."""
    return {"recaptcha_site_key": RECAPTCHA_SITE_KEY}


def verify_captcha(form):
    """Verify a reCAPTCHA v2 response with Google; fail closed on missing keys/errors."""
    token = (form.get("g-recaptcha-response") or "").strip()
    if not token or not RECAPTCHA_SECRET_KEY:
        app.logger.warning("reCAPTCHA rejected: missing token or server secret key")
        return False

    payload = urllib.parse.urlencode({
        "secret": RECAPTCHA_SECRET_KEY,
        "response": token,
        **({"remoteip": request.remote_addr} if request.remote_addr else {}),
    }).encode("ascii")
    req = urllib.request.Request(
        RECAPTCHA_VERIFY_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read(16384).decode("utf-8"))
        return result.get("success") is True
    except (urllib.error.URLError, TimeoutError, ValueError, UnicodeDecodeError) as exc:
        app.logger.warning("reCAPTCHA verification failed: %s", type(exc).__name__)
        return False


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("home") if "user_id" in session else url_for("login"))


def account_error(form, full_name, username, email, password, confirm, extra_fields=()):
    """
    Checks shared by every registration form (Type 1 and Team Leader).
    Returns an error message, or None when everything is fine.
    """
    if not (full_name and username and email and password and confirm and all(extra_fields)):
        return "Please fill in every field."
    if username.casefold() == SUPER_ADMIN_USERNAME.casefold():
        return "That username is reserved for the designated Super Admin account."
    if not USERNAME_RE.match(username):
        return "Username must be 4 to 20 characters: letters, numbers, _ or ."
    if not EMAIL_RE.match(email):
        return "Enter a valid e-mail address."
    if password_problems(password):
        return "Your password does not meet all the requirements."
    if password != confirm:
        return "The passwords do not match."
    if not verify_captcha(form):
        return "Please complete the CAPTCHA."

    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone():
        return "That username is already taken."
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return "An account with this e-mail already exists."
    return None


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html", form={})

    form = request.form
    full_name = form.get("full_name", "").strip()
    username = form.get("username", "").strip()
    email = form.get("email", "").strip().lower()
    department = form.get("department", "").strip()
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")

    error = account_error(form, full_name, username, email, password, confirm, [department])
    if error is None:
        # This page always creates a Type 1 (reporter) account.
        db = get_db()
        db.execute(
            "INSERT INTO users (full_name, username, email, department, password_hash, role) "
            "VALUES (?, ?, ?, ?, ?, 'reporter')",
            (full_name, username, email, department, generate_password_hash(password)),
        )
        db.commit()
        admin_email, admin_name = super_admin_email(db)
        if admin_email:
            send_email_html(
                admin_email, "COREBOUND: New Type 1 account registered",
                "New Type 1 account",
                [f"Name: {full_name}", f"Username: {username}", f"Email: {email}",
                 f"Department: {department}", f"Registered: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"],
                to_name=admin_name,
            )
        flash("Account created. You can log in now.", "success")
        return redirect(url_for("login"))

    flash(error, "error")
    return render_template("register.html", form=form), 400


# Territories (buildings or campus locations) that do not have a team leader yet
FREE_TERRITORIES_SQL = (
    "SELECT t.id, t.name FROM territories t "
    "WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.role = 'team_leader' AND u.territory_id = t.id) "
    "AND NOT EXISTS (SELECT 1 FROM leader_applications a WHERE a.territory_id=t.id AND a.status='pending') "
    "ORDER BY t.name"
)


@app.route("/register/leader", methods=["GET", "POST"])
def register_leader():
    """Team Leader (Type 2) registration. The leader picks the building or location to handle."""
    db = get_db()
    territories = db.execute(FREE_TERRITORIES_SQL).fetchall()

    def page(form, status=200):
        return render_template("Type_2/leader/register.html", form=form, territories=territories), status

    if request.method == "GET":
        return page({})

    form = request.form
    full_name = form.get("full_name", "").strip()
    username = form.get("username", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")
    territory_id = form.get("territory_id", "").strip()

    error = account_error(form, full_name, username, email, password, confirm, [territory_id])
    territory = next((t for t in territories if str(t["id"]) == territory_id), None)
    if error is None and territory is None:
        error = "That building or location is not available. Please choose another one."

    if error is None:
        try:
            db.execute(
                "INSERT INTO leader_applications "
                "(full_name, username, email, department, password_hash, territory_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (full_name, username, email, territory["name"],
                 generate_password_hash(password), territory["id"]),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            error = "Someone just submitted an application for that username, email, or location. Please check your details and try again."
        else:
            admin_email, admin_name = super_admin_email(db)
            if admin_email:
                send_email_html(
                    admin_email, "COREBOUND: Team Leader application needs review",
                    "New Team Leader application",
                    [f"Applicant: {full_name}", f"Username: {username}", f"Email: {email}",
                     f"Requested building/location: {territory['name']}",
                     "Open the COREBOUND Super Admin dashboard to approve or deny this application."],
                    to_name=admin_name,
                )
            flash("Your application was submitted and is pending Super Admin approval. You will receive an email with the decision.", "success")
            return redirect(url_for("login"))

    flash(error, "error")
    return page(form, 400)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", form={})

    form = request.form
    username = form.get("username", "").strip()
    password = form.get("password", "")
    db = get_db()
    username_key = username.casefold()[:254]
    ip_key = (request.remote_addr or "unknown")[:64]
    recent_failures = db.execute("SELECT COUNT(*) FROM login_attempts WHERE (username_key=? OR ip_key=?) AND succeeded=0 AND attempted_at >= datetime('now','-15 minutes')", (username_key, ip_key)).fetchone()[0]
    if recent_failures >= 10:
        flash("Too many login attempts. Please wait 15 minutes before trying again.", "error")
        return render_template("login.html", form={"username": username}), 429

    if not verify_captcha(form):
        flash("Please complete the CAPTCHA.", "error")
        return render_template("login.html", form=form), 400

    user = get_db().execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()

    if user is None or not check_password_hash(user["password_hash"], password):
        db.execute("INSERT INTO login_attempts(username_key,ip_key,succeeded) VALUES(?,?,0)", (username_key, ip_key))
        db.commit()
        flash("Incorrect username or password.", "error")
        return render_template("login.html", form={"username": username}), 401

    if user["status"] == "removed":
        db.execute("INSERT INTO login_attempts(username_key,ip_key,succeeded) VALUES(?,?,0)", (username_key, ip_key))
        db.commit()
        flash("This account has been removed. Contact the administrator.", "error")
        return render_template("login.html", form=form), 403

    db.execute("INSERT INTO login_attempts(username_key,ip_key,succeeded) VALUES(?,?,1)", (username_key, ip_key))
    db.execute("DELETE FROM login_attempts WHERE attempted_at < datetime('now','-30 days')")
    db.commit()
    session.clear()
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session["full_name"] = user["full_name"]
    session["username"] = user["username"]
    session["must_change_password"] = bool(user["must_change_password"]) if "must_change_password" in user.keys() else False
    session["show_skeleton"] = True        # dashboard shows the loading skeleton once
    session.permanent = bool(form.get("remember"))
    return redirect(url_for("home"))


@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if not session.get("must_change_password"):
        return redirect(url_for("home"))
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        user = get_db().execute("SELECT password_hash FROM users WHERE id=?", (session["user_id"],)).fetchone()
        problems = password_problems(new_password)
        if not user or not check_password_hash(user["password_hash"], current):
            flash("Current temporary password is incorrect.", "error")
        elif problems:
            flash("New password needs " + ", ".join(problems) + ".", "error")
        elif new_password != confirm:
            flash("New passwords do not match.", "error")
        elif check_password_hash(user["password_hash"], new_password):
            flash("Choose a password different from the temporary password.", "error")
        else:
            db = get_db()
            db.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                       (generate_password_hash(new_password), session["user_id"]))
            db.commit()
            session["must_change_password"] = False
            flash("Password changed successfully.", "success")
            return redirect(url_for("home"))
    return render_template("change_password.html")


# Which dashboard each role sees (paths are inside templates/)
DASHBOARDS = {
    "reporter":    "Type_1/dashboard.html",
    "team_leader": "Type_2/leader/dashboard.html",
    "team_member": "Type_2/member/dashboard.html",
    "admin":       "Type_3/dashboard.html",
}


# How report statuses are grouped on the Type 1 dashboard
STATUS_GROUPS = {
    "submitted": "pending", "assigned": "pending", "verifying": "pending",
    "confirmed": "progress", "in_progress": "progress",
    "resolved": "resolved",
    "invalid": "closed",
}
STATUS_LABELS = {
    "submitted": "Submitted", "assigned": "Assigned", "verifying": "Being checked",
    "confirmed": "Confirmed", "in_progress": "In progress",
    "resolved": "Resolved", "invalid": "Rejected",
}
GROUP_ORDER = ("pending", "progress", "resolved", "closed")


PH_TZ = timezone(timedelta(hours=8))   # Philippine time. SQLite stores UTC.


def to_local(value):
    """Read a UTC timestamp from SQLite and return it in Philippine time."""
    try:
        return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(PH_TZ)
    except (TypeError, ValueError):
        return None


def pretty_date(value):
    stamp = to_local(value)
    return stamp.strftime("%b %d, %Y").replace(" 0", " ") if stamp else (value or "")


def pretty_datetime(value):
    stamp = to_local(value)
    return stamp.strftime("%b %d, %Y, %I:%M %p").replace(" 0", " ") if stamp else (value or "")


def header_context(user_id):
    """What every Type 1 page needs for its header: profile and notifications."""
    db = get_db()
    user = db.execute(
        "SELECT full_name, department, status FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if user is None or user["status"] == "removed":
        return None

    notes = db.execute(
        "SELECT message, is_read, created_at FROM notifications "
        "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 5", (user_id,)
    ).fetchall()
    unread = db.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0", (user_id,)
    ).fetchone()[0]

    return {
        "user": {"full_name": user["full_name"], "department": user["department"]},
        "notifications": [{"message": n["message"], "unread": not n["is_read"],
                           "date": pretty_date(n["created_at"])} for n in notes],
        "unread": unread,
    }


def reporter_context(user_id):
    """Everything the Type 1 dashboard needs: header data, counts, recent reports."""
    context = header_context(user_id)
    if context is None:
        return None
    db = get_db()

    counts = dict.fromkeys(GROUP_ORDER, 0)
    for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM reports WHERE reporter_id = ? GROUP BY status", (user_id,)
    ):
        counts[STATUS_GROUPS[row["status"]]] += row["n"]
    total = sum(counts.values())
    percent = {k: (round(v * 100 / total) if total else 0) for k, v in counts.items()}

    # Donut chart: a CSS conic-gradient built from the counts
    if total:
        stops, start = [], 0.0
        for key in GROUP_ORDER:
            end = start + counts[key] * 100 / total
            stops.append(f"var(--c-{key}) {start:.2f}% {end:.2f}%")
            start = end
        donut = "conic-gradient(" + ", ".join(stops) + ")"
    else:
        donut = "var(--c-pending)"

    recent = []
    for r in db.execute(
        "SELECT ticket_no, title, location_text, status, updated_at FROM reports "
        "WHERE reporter_id = ? ORDER BY created_at DESC, id DESC LIMIT 5", (user_id,)
    ):
        recent.append({
            "ticket_no": r["ticket_no"], "issue": r["title"], "location": r["location_text"],
            "status": STATUS_LABELS[r["status"]], "group": STATUS_GROUPS[r["status"]],
            "updated": pretty_date(r["updated_at"]),
        })

    context.update({"total": total, "counts": counts, "percent": percent,
                    "donut": donut, "recent": recent})
    return context


def reporter_required(view):
    """Only logged-in Type 1 users may open the page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "reporter":
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def detect_image_type(head):
    """Look at the first bytes of the file, not the file name."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


@app.errorhandler(413)
def too_large(_error):
    if request.path.startswith("/member/"):          # the member pop-up expects JSON
        return jsonify(error="The photo is too large. Please choose an image under 5 MB."), 413
    flash("The photo is too large. Please choose an image under 5 MB.", "error")
    return redirect(url_for("report"))


def _duplicate_normalize(value):
    """Normalize text for tolerant duplicate-report comparison."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


def find_possible_duplicate_reports(db, *, department_id, category, title, location, description, incident_date):
    """Find likely matches among unresolved reports in the same department/category.

    This is a warning-only heuristic: it never blocks submission on its own.
    """
    rows = db.execute(
        "SELECT r.id, r.ticket_no, r.title, r.category, r.location_text, r.description, "
        "r.incident_date, r.status, d.name AS department "
        "FROM reports r JOIN departments d ON d.id = r.department_id "
        "WHERE r.department_id = ? AND r.category = ? "
        "AND r.status NOT IN ('resolved', 'invalid') "
        "ORDER BY r.created_at DESC LIMIT 300",
        (department_id, category),
    ).fetchall()
    new_location = _duplicate_normalize(location)
    new_title = _duplicate_normalize(title)
    new_description = _duplicate_normalize(description)
    matches = []
    for row in rows:
        old_location = _duplicate_normalize(row["location_text"])
        # Location is a key signal: avoid warning for unrelated rooms/buildings.
        loc_score = difflib.SequenceMatcher(None, new_location, old_location).ratio()
        if not old_location or not new_location or loc_score < 0.72:
            continue
        title_score = difflib.SequenceMatcher(None, new_title, _duplicate_normalize(row["title"])).ratio()
        desc_score = difflib.SequenceMatcher(None, new_description, _duplicate_normalize(row["description"])).ratio()
        same_day = (row["incident_date"] == incident_date)
        score = 0.42 * loc_score + 0.35 * title_score + 0.23 * desc_score
        if (title_score >= 0.45 or desc_score >= 0.55) and (score >= 0.58 or (same_day and score >= 0.50)):
            matches.append({
                "id": row["id"], "ticket_no": row["ticket_no"], "title": row["title"],
                "category": dict(CATEGORIES).get(row["category"], row["category"]),
                "department": row["department"], "location": row["location_text"],
                "incident_date": row["incident_date"],
                "status": STATUS_LABELS.get(row["status"], row["status"]),
                "score": round(score * 100),
            })
    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:5]


@app.route("/report", methods=["GET", "POST"])
@reporter_required
def report():
    db = get_db()
    context = header_context(session["user_id"])
    if context is None:
        session.clear()
        return redirect(url_for("login"))
    departments = db.execute("SELECT id, name, territory_id FROM departments ORDER BY name").fetchall()

    def page(form, status=200, duplicate_matches=None, duplicate_override=False):
        return render_template(
            "Type_1/report_template.html", form=form, departments=departments,
            categories=CATEGORIES, today=datetime.now().strftime("%Y-%m-%d"),
            duplicate_matches=duplicate_matches or [], duplicate_override=duplicate_override, **context
        ), status

    if request.method == "GET":
        return page({})

    form = request.form
    title = form.get("title", "").strip()
    category = form.get("category", "")
    location = form.get("location", "").strip()
    description = form.get("description", "").strip()
    severity = form.get("severity", "medium").strip().lower()
    if severity not in ("low", "medium", "high", "urgent"):
        severity = "medium"
    incident_date = form.get("incident_date", "").strip()
    incident_time = form.get("incident_time", "").strip()
    department = next((d for d in departments if str(d["id"]) == form.get("department_id", "")), None)

    error = None
    if not (title and category and location and description and incident_date and incident_time):
        error = "Please fill in every field."
    elif len(title) > 100 or len(location) > 150:
        error = "The title or location is too long."
    elif category not in dict(CATEGORIES):
        error = "Choose a category."
    elif department is None:
        error = "Choose a department."
    elif len(description) < 10 or len(description) > 1000:
        error = "The description must be 10 to 1000 characters."
    else:
        try:
            incident = datetime.strptime(f"{incident_date} {incident_time}", "%Y-%m-%d %H:%M")
            if incident > datetime.now():
                error = "The incident date and time cannot be in the future."
        except ValueError:
            error = "Enter a valid incident date and time."

    # Optional photo
    photo = request.files.get("photo")
    photo_type = None
    if error is None and photo and photo.filename:
        head = photo.stream.read(12)
        photo.stream.seek(0, os.SEEK_END)
        size = photo.stream.tell()
        photo.stream.seek(0)
        photo_type = detect_image_type(head)
        if photo_type is None:
            error = "The photo must be a PNG, JPG or WEBP image."
        elif size > MAX_PHOTO_BYTES:
            error = "The photo is too large. Please choose an image under 5 MB."

    if error:
        flash(error, "error")
        return page(form, 400)

    # Warn about likely duplicates before saving the uploaded photo or creating a ticket.
    # The reporter can proceed if this is a separate issue by confirming the override.
    duplicate_override = form.get("confirm_separate_issue") == "1"
    if not duplicate_override:
        duplicate_matches = find_possible_duplicate_reports(
            db, department_id=department["id"], category=category, title=title,
            location=location, description=description, incident_date=incident_date,
        )
        if duplicate_matches:
            return page(form, 200, duplicate_matches=duplicate_matches)

    photo_path = None
    if photo_type:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.{photo_type}"
        photo.save(os.path.join(UPLOAD_DIR, filename))
        photo_path = f"uploads/{filename}"

    try:
        cur = db.execute(
            "INSERT INTO reports (ticket_no, reporter_id, department_id, territory_id, category, "
            "title, location_text, description, incident_date, incident_time, photo_path, severity, due_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("TMP-" + uuid.uuid4().hex, session["user_id"], department["id"],
             department["territory_id"], category, title, location, description,
             incident_date, incident_time, photo_path, severity, deadline_for(severity)),
        )
        ticket_no = f"LNU-{datetime.now().year}-{cur.lastrowid:05d}"
        db.execute("UPDATE reports SET ticket_no = ? WHERE id = ?", (ticket_no, cur.lastrowid))
        auto_assign_report(db, cur.lastrowid, department["territory_id"], ticket_no, title, session["user_id"])
        db.commit()
    except sqlite3.Error:
        db.rollback()
        if photo_path:
            os.remove(os.path.join(BASE_DIR, "static", photo_path))
        flash("Something went wrong while saving your report. Please try again.", "error")
        return page(form, 500)

    # Email the active leader responsible for the selected building/territory.
    leader = db.execute(
        "SELECT u.full_name, u.email FROM users u "
        "WHERE u.role='team_leader' AND u.territory_id=? AND u.status='active' ORDER BY u.id LIMIT 1",
        (department["territory_id"],),
    ).fetchone()
    reporter = db.execute("SELECT full_name, username, email FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if leader and EMAIL_RE.fullmatch((leader["email"] or "").strip()):
        send_email_html(
            leader["email"], f"COREBOUND: New facility report {ticket_no}",
            f"New facility report: {ticket_no}",
            [f"Ticket number: {ticket_no}", f"Issue: {title}",
             f"Category: {dict(CATEGORIES).get(category, category)}", f"Building/department: {department['name']}",
             f"Location: {location}", f"Severity: {severity.title()}",
             f"Incident date/time: {incident_date} {incident_time}",
             f"Reported by: {reporter['full_name']} (@{reporter['username']})",
             f"Reporter email: {reporter['email']}", f"Description: {description}",
             "Please sign in to COREBOUND to review and manage this report."],
            to_name=leader["full_name"],
        )
    else:
        app.logger.warning("No active leader with a valid email was found for report %s.", ticket_no)

    flash(f"Report submitted. Your ticket number is {ticket_no}.", "success")
    return redirect(url_for("report"))


PAGE_SIZE = 10
GROUP_LABELS = {
    "pending": "Pending", "progress": "In Progress",
    "resolved": "Resolved", "closed": "Rejected / Closed",
}


def like_pattern(text):
    """Turn user text into a safe LIKE pattern (so % and _ are searched literally)."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def pretty_incident(day, time_):
    try:
        stamp = datetime.strptime(f"{day} {time_}", "%Y-%m-%d %H:%M")
        return stamp.strftime("%b %d, %Y, %I:%M %p").replace(" 0", " ")
    except ValueError:
        return f"{day} {time_}"


@app.route("/my-reports")
@reporter_required
def my_reports():
    context = header_context(session["user_id"])
    if context is None:
        session.clear()
        return redirect(url_for("login"))
    db = get_db()
    user_id = session["user_id"]

    q = request.args.get("q", "").strip()[:100]
    group = request.args.get("status", "all")
    if group not in GROUP_ORDER:
        group = "all"

    # Counts for the filter tabs (always across all of the user's reports)
    counts = dict.fromkeys(GROUP_ORDER, 0)
    for row in db.execute(
        "SELECT status, COUNT(*) AS n FROM reports WHERE reporter_id = ? GROUP BY status", (user_id,)
    ):
        counts[STATUS_GROUPS[row["status"]]] += row["n"]
    all_count = sum(counts.values())

    where, params = ["r.reporter_id = ?"], [user_id]
    if group != "all":
        statuses = [s for s, g in STATUS_GROUPS.items() if g == group]
        where.append("r.status IN (" + ",".join("?" * len(statuses)) + ")")
        params += statuses
    if q:
        where.append(
            "(r.title LIKE ? ESCAPE '\\' OR r.ticket_no LIKE ? ESCAPE '\\' "
            "OR r.location_text LIKE ? ESCAPE '\\' OR d.name LIKE ? ESCAPE '\\')"
        )
        params += [like_pattern(q)] * 4
    where_sql = " AND ".join(where)

    matched = db.execute(
        "SELECT COUNT(*) FROM reports r JOIN departments d ON d.id = r.department_id WHERE " + where_sql,
        params,
    ).fetchone()[0]
    pages = max(1, -(-matched // PAGE_SIZE))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)

    rows = []
    for r in db.execute(
        "SELECT r.id, r.ticket_no, r.title, r.category, r.location_text, r.incident_date, "
        "r.incident_time, r.status, r.updated_at, d.name AS department "
        "FROM reports r JOIN departments d ON d.id = r.department_id WHERE " + where_sql +
        " ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ):
        rows.append({
            "id": r["id"], "ticket_no": r["ticket_no"], "title": r["title"],
            "category": dict(CATEGORIES).get(r["category"], r["category"]),
            "department": r["department"], "location": r["location_text"],
            "incident": pretty_incident(r["incident_date"], r["incident_time"]),
            "status": STATUS_LABELS[r["status"]], "group": STATUS_GROUPS[r["status"]],
            "updated": pretty_date(r["updated_at"]),
        })

    tabs = [("all", "All", all_count)] + [(g, GROUP_LABELS[g], counts[g]) for g in GROUP_ORDER]
    return render_template(
        "Type_1/my_report.html", reports=rows, tabs=tabs, group=group, q=q,
        page=page, pages=pages, matched=matched, all_count=all_count, **context
    )


@app.route("/my-reports/<int:report_id>")
@reporter_required
def my_report_detail(report_id):
    """JSON for the details pop-up. Only the person who sent the report can open it."""
    db = get_db()
    r = db.execute(
        "SELECT r.*, d.name AS department, "
        "leader.full_name AS leader_name, member.full_name AS member_name "
        "FROM reports r JOIN departments d ON d.id = r.department_id "
        "LEFT JOIN users leader ON leader.id = r.assigned_leader_id "
        "LEFT JOIN users member ON member.id = r.assigned_member_id "
        "WHERE r.id = ? AND r.reporter_id = ?", (report_id, session["user_id"])
    ).fetchone()
    if r is None:
        return jsonify(error="Report not found."), 404

    timeline = [
        {"status": STATUS_LABELS.get(h["status"], h["status"]), "note": h["note"] or "",
         "when": pretty_datetime(h["changed_at"]), "changed_by": h["changed_by_name"] or "System"}
        for h in db.execute(
            "SELECT h.status, h.note, h.changed_at, u.full_name AS changed_by_name "
            "FROM report_status_history h LEFT JOIN users u ON u.id = h.changed_by "
            "WHERE h.report_id = ? ORDER BY h.changed_at, h.id", (report_id,)
        )
    ]
    return jsonify(
        ticket_no=r["ticket_no"], title=r["title"],
        category=dict(CATEGORIES).get(r["category"], r["category"]),
        department=r["department"], location=r["location_text"], description=r["description"],
        incident=pretty_incident(r["incident_date"], r["incident_time"]),
        submitted=pretty_datetime(r["created_at"]), updated=pretty_datetime(r["updated_at"]),
        status=STATUS_LABELS[r["status"]], group=STATUS_GROUPS[r["status"]],
        due_at=pretty_datetime(r["due_at"]) if r["due_at"] else None,
        overdue=bool(r["due_at"] and r["due_at"] < datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") and r["status"] not in ("resolved", "invalid")),
        before_photo_url=url_for("static", filename=r["before_photo_path"]) if r["before_photo_path"] else None,
        after_photo_url=url_for("static", filename=r["after_photo_path"]) if r["after_photo_path"] else None,
        progress=[dict(message=p["message"], user=p["full_name"], when=pretty_datetime(p["created_at"]), evidence_url=url_for("static", filename=p["evidence_path"]) if p["evidence_path"] else None) for p in db.execute("SELECT p.message,p.created_at,p.evidence_path,u.full_name FROM report_progress_updates p JOIN users u ON u.id=p.user_id WHERE p.report_id=? ORDER BY p.created_at,p.id", (report_id,))],
        assigned_leader=r["leader_name"] or "Not assigned",
        assigned_member=r["member_name"] or "Not assigned",
        photo_url=url_for("static", filename=r["photo_path"]) if r["photo_path"] else None,
        timeline=timeline,
        assignment_history=[dict(actor=a["actor_name"] or "System", previous_leader=a["previous_leader"] or "Unassigned", new_leader=a["new_leader"] or "Unassigned", previous_member=a["previous_member"] or "Unassigned", new_member=a["new_member"] or "Unassigned", note=a["note"], when=pretty_datetime(a["changed_at"])) for a in db.execute("SELECT ah.*, actor.full_name AS actor_name, pl.full_name AS previous_leader, nl.full_name AS new_leader, pm.full_name AS previous_member, nm.full_name AS new_member FROM report_assignment_history ah LEFT JOIN users actor ON actor.id=ah.actor_id LEFT JOIN users pl ON pl.id=ah.previous_leader_id LEFT JOIN users nl ON nl.id=ah.new_leader_id LEFT JOIN users pm ON pm.id=ah.previous_member_id LEFT JOIN users nm ON nm.id=ah.new_member_id WHERE ah.report_id=? ORDER BY ah.changed_at,ah.id", (report_id,))],
    )


FEEDBACK_CATEGORIES = [
    ("suggestion", "Suggestion"), ("compliment", "Compliment"),
    ("problem", "Problem with the system"), ("other", "Other"),
]
FEEDBACK_DAILY_LIMIT = 5


@app.route("/feedback", methods=["GET", "POST"])
@reporter_required
def feedback():
    context = header_context(session["user_id"])
    if context is None:
        session.clear()
        return redirect(url_for("login"))
    db = get_db()
    user_id = session["user_id"]

    def page(form, status=200):
        history = [
            {"rating": r["rating"], "category": dict(FEEDBACK_CATEGORIES).get(r["category"], r["category"]),
             "message": r["message"], "date": pretty_date(r["created_at"])}
            for r in db.execute(
                "SELECT rating, category, message, created_at FROM feedback "
                "WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 5", (user_id,)
            )
        ]
        return render_template("Type_1/feedback.html", form=form, categories=FEEDBACK_CATEGORIES,
                               history=history, **context), status

    if request.method == "GET":
        return page({})

    form = request.form
    rating = form.get("rating", type=int)
    category = form.get("category", "")
    message = form.get("message", "").strip()

    error = None
    if rating is None or not 1 <= rating <= 5:
        error = "Please give a rating from 1 to 5 stars."
    elif category not in dict(FEEDBACK_CATEGORIES):
        error = "Choose a feedback type."
    elif not 10 <= len(message) <= 1000:
        error = "Your message must be 10 to 1000 characters."
    elif db.execute(
        "SELECT COUNT(*) FROM feedback WHERE user_id = ? AND created_at >= datetime('now', '-1 day')",
        (user_id,),
    ).fetchone()[0] >= FEEDBACK_DAILY_LIMIT:
        error = f"You can send up to {FEEDBACK_DAILY_LIMIT} feedback messages a day. Please try again tomorrow."

    if error:
        flash(error, "error")
        return page(form, 400)

    db.execute("INSERT INTO feedback (user_id, rating, category, message) VALUES (?, ?, ?, ?)",
               (user_id, rating, category, message))
    db.commit()
    flash("Thank you! Your feedback was sent.", "success")
    return redirect(url_for("feedback"))


# ------------------------------------------------------------------
# Team Leader (Type 2) dashboard
# ------------------------------------------------------------------
OPEN_STATUSES = ("assigned", "verifying", "confirmed", "in_progress")
LEADER_VIEWS = {            # key: (tab label, statuses it shows, None = all)
    "new":      ("Unassigned", ("submitted",)),
    "active":   ("Ongoing", OPEN_STATUSES),
    "resolved": ("Resolved", ("resolved",)),
    "closed":   ("Rejected / Closed", ("invalid",)),
    "all":      ("All", None),
}


def leader_required(view):
    """Only logged-in Team Leaders may open the page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "team_leader":
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def leader_territory(db, user_id):
    """The leader's territory as (id, name), or None."""
    row = db.execute(
        "SELECT u.territory_id AS id, t.name FROM users u "
        "LEFT JOIN territories t ON t.id = u.territory_id WHERE u.id = ?", (user_id,)
    ).fetchone()
    return row if row and row["id"] else None


def add_history_note(db, report_id, status, note, user_id):
    """
    Attach a note to the history row the database trigger just wrote for this status change,
    or add a new row when the status stayed the same (for example a re-assignment).
    """
    last = db.execute(
        "SELECT id, status, note FROM report_status_history WHERE report_id = ? ORDER BY id DESC LIMIT 1",
        (report_id,),
    ).fetchone()
    if last and last["status"] == status and last["note"] is None:
        db.execute("UPDATE report_status_history SET note = ?, changed_by = ? WHERE id = ?",
                   (note, user_id, last["id"]))
    else:
        db.execute("INSERT INTO report_status_history (report_id, status, note, changed_by) VALUES (?, ?, ?, ?)",
                   (report_id, status, note, user_id))


def leader_context(user_id, args):
    context = header_context(user_id)
    territory = leader_territory(get_db(), user_id)
    if context is None or territory is None:
        return None
    db = get_db()
    tid = territory["id"]

    view = args.get("view", "new")
    if view not in LEADER_VIEWS:
        view = "new"
    q = args.get("q", "").strip()[:100]

    # Counts for the tabs and the stat cards
    by_status = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) AS n FROM reports WHERE territory_id = ? GROUP BY status", (tid,))}
    counts = {key: (sum(by_status.values()) if statuses is None else sum(by_status.get(s, 0) for s in statuses))
              for key, (_label, statuses) in LEADER_VIEWS.items()}

    where, params = ["r.territory_id = ?"], [tid]
    statuses = LEADER_VIEWS[view][1]
    if statuses:
        where.append("r.status IN (" + ",".join("?" * len(statuses)) + ")")
        params += list(statuses)
    if q:
        where.append(
            "(r.title LIKE ? ESCAPE '\\' OR r.ticket_no LIKE ? ESCAPE '\\' OR r.location_text LIKE ? ESCAPE '\\' "
            "OR d.name LIKE ? ESCAPE '\\' OR rep.full_name LIKE ? ESCAPE '\\')"
        )
        params += [like_pattern(q)] * 5
    joins = ("FROM reports r JOIN departments d ON d.id = r.department_id "
             "JOIN users rep ON rep.id = r.reporter_id "
             "LEFT JOIN users m ON m.id = r.assigned_member_id WHERE " + " AND ".join(where))

    matched = db.execute("SELECT COUNT(*) " + joins, params).fetchone()[0]
    pages = max(1, -(-matched // PAGE_SIZE))
    page = min(max(args.get("page", 1, type=int), 1), pages)

    rows = []
    for r in db.execute(
        "SELECT r.id, r.ticket_no, r.title, r.category, r.location_text, r.status, r.created_at, "
        "d.name AS department, rep.full_name AS reporter, m.full_name AS member " + joins +
        " ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ):
        rows.append({
            "id": r["id"], "ticket_no": r["ticket_no"], "title": r["title"],
            "category": dict(CATEGORIES).get(r["category"], r["category"]),
            "department": r["department"], "location": r["location_text"], "reporter": r["reporter"],
            "submitted": pretty_date(r["created_at"]), "member": r["member"],
            "status": STATUS_LABELS[r["status"]], "group": STATUS_GROUPS[r["status"]],
        })

    members = [dict(m) for m in db.execute(
        "SELECT u.id, u.full_name, u.username, u.email, "
        "(SELECT COUNT(*) FROM reports r WHERE r.assigned_member_id = u.id "
        " AND r.status IN ('assigned','verifying','confirmed','in_progress')) AS open_tasks "
        "FROM users u WHERE u.role = 'team_member' AND u.leader_id = ? AND u.status <> 'removed' "
        "ORDER BY u.full_name", (user_id,))]

    tabs = [(key, label, counts[key]) for key, (label, _s) in LEADER_VIEWS.items()]
    context.update({
        "territory": territory["name"], "reports": rows, "tabs": tabs, "counts": counts,
        "view": view, "q": q, "page": page, "pages": pages, "matched": matched, "members": members,
    })
    return context


def render_leader_dashboard(form=None, open_team=False, status=200):
    alert_overdue_reports(get_db())
    context = leader_context(session["user_id"], request.args)
    if context is None:
        session.clear()
        return redirect(url_for("login"))
    return render_template("Type_2/leader/dashboard.html", form=form or {}, open_team=open_team, **context), status


@app.route("/leader/reports/<int:report_id>")
@leader_required
def leader_report_detail(report_id):
    """JSON for the leader's report pop-up. Only reports of the leader's own territory."""
    db = get_db()
    territory = leader_territory(db, session["user_id"])
    r = db.execute(
        "SELECT r.*, d.name AS department, rep.full_name AS reporter, rep.department AS reporter_dept, "
        "m.full_name AS member FROM reports r JOIN departments d ON d.id = r.department_id "
        "JOIN users rep ON rep.id = r.reporter_id LEFT JOIN users m ON m.id = r.assigned_member_id "
        "WHERE r.id = ? AND r.territory_id = ?", (report_id, territory["id"] if territory else -1)
    ).fetchone()
    if r is None:
        return jsonify(error="Report not found."), 404

    timeline = [
        {"status": STATUS_LABELS.get(h["status"], h["status"]), "note": h["note"] or "",
         "when": pretty_datetime(h["changed_at"]), "changed_by": h["changed_by_name"] or "System"}
        for h in db.execute(
            "SELECT h.status, h.note, h.changed_at, u.full_name AS changed_by_name "
            "FROM report_status_history h LEFT JOIN users u ON u.id=h.changed_by "
            "WHERE h.report_id = ? ORDER BY h.changed_at, h.id", (report_id,))
    ]
    members = [{"id": m["id"], "name": m["full_name"], "open_tasks": m["open_tasks"]} for m in db.execute(
        "SELECT u.id, u.full_name, (SELECT COUNT(*) FROM reports x WHERE x.assigned_member_id = u.id "
        "AND x.status IN ('assigned','verifying','confirmed','in_progress')) AS open_tasks "
        "FROM users u WHERE u.role = 'team_member' AND u.leader_id = ? AND u.status <> 'removed' "
        "ORDER BY u.full_name", (session["user_id"],))]

    return jsonify(
        id=r["id"], ticket_no=r["ticket_no"], title=r["title"],
        category=dict(CATEGORIES).get(r["category"], r["category"]),
        department=r["department"], location=r["location_text"], description=r["description"],
        reporter=f"{r['reporter']} ({r['reporter_dept']})",
        incident=pretty_incident(r["incident_date"], r["incident_time"]),
        submitted=pretty_datetime(r["created_at"]),
        status=STATUS_LABELS[r["status"]], group=STATUS_GROUPS[r["status"]],
        member=r["member"], member_id=r["assigned_member_id"],
        can_assign=r["status"] in ("submitted", "assigned"),
        photo_url=url_for("static", filename=r["photo_path"]) if r["photo_path"] else None,
        due_at=pretty_datetime(r["due_at"]) if r["due_at"] else None,
        overdue=bool(r["due_at"] and r["due_at"] < datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") and r["status"] not in ("resolved", "invalid")),
        before_photo_url=url_for("static", filename=r["before_photo_path"]) if r["before_photo_path"] else None,
        after_photo_url=url_for("static", filename=r["after_photo_path"]) if r["after_photo_path"] else None,
        progress=[dict(message=p["message"], user=p["full_name"], when=pretty_datetime(p["created_at"]), evidence_url=url_for("static", filename=p["evidence_path"]) if p["evidence_path"] else None) for p in db.execute("SELECT p.message,p.created_at,p.evidence_path,u.full_name FROM report_progress_updates p JOIN users u ON u.id=p.user_id WHERE p.report_id=? ORDER BY p.created_at,p.id", (report_id,))],
        timeline=timeline, members=members,
        assignment_history=[dict(actor=a["actor_name"] or "System", previous_leader=a["previous_leader"] or "Unassigned", new_leader=a["new_leader"] or "Unassigned", previous_member=a["previous_member"] or "Unassigned", new_member=a["new_member"] or "Unassigned", note=a["note"], when=pretty_datetime(a["changed_at"])) for a in db.execute("SELECT ah.*, actor.full_name AS actor_name, pl.full_name AS previous_leader, nl.full_name AS new_leader, pm.full_name AS previous_member, nm.full_name AS new_member FROM report_assignment_history ah LEFT JOIN users actor ON actor.id=ah.actor_id LEFT JOIN users pl ON pl.id=ah.previous_leader_id LEFT JOIN users nl ON nl.id=ah.new_leader_id LEFT JOIN users pm ON pm.id=ah.previous_member_id LEFT JOIN users nm ON nm.id=ah.new_member_id WHERE ah.report_id=? ORDER BY ah.changed_at,ah.id", (report_id,))],
    )


@app.route("/leader/reports/<int:report_id>/assign", methods=["POST"])
@leader_required
def leader_assign(report_id):
    """Give a report to one of the leader's team members."""
    db = get_db()
    leader_id = session["user_id"]
    territory = leader_territory(db, leader_id)
    data = request.get_json(silent=True) or {}
    member_id = data.get("member_id")

    report = db.execute(
        "SELECT id, ticket_no, title, reporter_id, status FROM reports WHERE id = ? AND territory_id = ?",
        (report_id, territory["id"] if territory else -1)).fetchone()
    if report is None:
        return jsonify(error="Report not found."), 404
    if report["status"] not in ("submitted", "assigned"):
        return jsonify(error="This report is already being handled and can't be re-assigned."), 409

    member = db.execute(
        "SELECT id, full_name, email FROM users WHERE id = ? AND role = 'team_member' AND leader_id = ? "
        "AND status = 'active'", (member_id, leader_id)).fetchone() if isinstance(member_id, int) else None
    if member is None:
        return jsonify(error="Choose a member of your team."), 400

    previous = db.execute("SELECT assigned_leader_id, assigned_member_id FROM reports WHERE id=?", (report_id,)).fetchone()
    db.execute(
        "UPDATE reports SET assigned_leader_id = ?, assigned_member_id = ?, status = 'assigned' WHERE id = ?",
        (leader_id, member["id"], report_id))
    db.execute("INSERT INTO report_assignment_history(report_id,actor_id,previous_leader_id,new_leader_id,previous_member_id,new_member_id,note) VALUES(?,?,?,?,?,?,?)",
               (report_id, leader_id, previous["assigned_leader_id"], leader_id, previous["assigned_member_id"], member["id"], "Team leader assignment/reassignment"))
    add_history_note(db, report_id, "assigned", f"Assigned to {member['full_name']}", leader_id)
    db.execute("INSERT INTO notifications (user_id, report_id, message) VALUES (?, ?, ?)",
               (report["reporter_id"], report_id,
                f"Your report {report['ticket_no']} was assigned to {member['full_name']}."))
    db.execute("INSERT INTO notifications (user_id, report_id, message) VALUES (?, ?, ?)",
               (member["id"], report_id, f"New task: {report['ticket_no']} - {report['title']}"))
    db.commit()

    # Email the assigned member after the assignment is safely committed.
    # A temporary email-service failure must not undo the assignment.
    leader = db.execute(
        "SELECT full_name FROM users WHERE id = ? AND role = 'team_leader'",
        (leader_id,)
    ).fetchone()
    leader_name = leader["full_name"] if leader else "Your team leader"
    email_sent = send_email_html(
        member["email"],
        f"COREBOUND: New work assignment {report['ticket_no']}",
        "You have received a new work assignment",
        [
            f"Hello {member['full_name']},",
            f"{leader_name} has assigned you a work report in COREBOUND.",
            f"Report ticket: {report['ticket_no']}",
            f"Report title: {report['title']}",
            "Please log in to COREBOUND to review the report details and take the required action.",
        ],
        to_name=member["full_name"],
    )
    if not email_sent:
        app.logger.warning(
            "Assignment email could not be sent for report %s to team member ID %s.",
            report["ticket_no"], member["id"]
        )

    return jsonify(ok=True, member=member["full_name"], email_sent=email_sent)


@app.route("/leader/team", methods=["POST"])
@leader_required
def leader_add_member():
    """A leader creates an account for a member of their own team."""
    db = get_db()
    leader_id = session["user_id"]
    territory = leader_territory(db, leader_id)
    if territory is None:
        return redirect(url_for("home"))

    form = request.form
    full_name = form.get("full_name", "").strip()
    username = form.get("username", "").strip()
    email = form.get("email", "").strip().lower()
    password = form.get("password", "")
    confirm = form.get("confirm_password", "")

    error = account_error(form, full_name, username, email, password, confirm)
    if error is None:
        db.execute(
            "INSERT INTO users (full_name, username, email, department, password_hash, role, territory_id, leader_id, must_change_password) "
            "VALUES (?, ?, ?, ?, ?, 'team_member', ?, ?, 1)",
            (full_name, username, email, territory["name"], generate_password_hash(password),
             territory["id"], leader_id))
        db.commit()
        send_email_html(
            email, "Your COREBOUND team member account",
            "Your team member account is ready",
            [f"Hello {full_name}, your COREBOUND team member account has been created.",
             f"Username: {username}", f"Temporary password: {password}",
             "For security, you must change this temporary password immediately after your first login. Do not share these credentials with anyone."],
            to_name=full_name,
        )
        flash(f"{full_name} was added to your team. COREBOUND attempted to email the username and temporary password.", "success")
        return redirect(url_for("leader_team" if form.get("from") == "team" else "home"))

    flash(error, "error")
    if form.get("from") == "team":
        return render_team_page(form=form, open_team=True, status=400)
    return render_leader_dashboard(form=form, open_team=True, status=400)


def team_members(db, leader_id):
    """Every active member of this leader's team, with their workload counts."""
    open_list = ",".join("'%s'" % s for s in OPEN_STATUSES)
    return [dict(m) for m in db.execute(
        "SELECT u.id, u.full_name, u.username, u.email, u.created_at, "
        f"COALESCE(SUM(CASE WHEN r.status IN ({open_list}) THEN 1 ELSE 0 END), 0) AS open_tasks, "
        "COALESCE(SUM(CASE WHEN r.status = 'resolved' THEN 1 ELSE 0 END), 0) AS resolved, "
        "COALESCE(SUM(CASE WHEN r.status = 'invalid' THEN 1 ELSE 0 END), 0) AS rejected "
        "FROM users u LEFT JOIN reports r ON r.assigned_member_id = u.id "
        "WHERE u.role = 'team_member' AND u.leader_id = ? AND u.status <> 'removed' "
        "GROUP BY u.id ORDER BY u.full_name COLLATE NOCASE", (leader_id,))]


def team_context(user_id, q):
    context = header_context(user_id)
    db = get_db()
    territory = leader_territory(db, user_id)
    if context is None or territory is None:
        return None
    everyone = team_members(db, user_id)
    needle = q.lower()
    shown = [m for m in everyone
             if not needle or needle in m["full_name"].lower()
             or needle in m["username"].lower() or needle in m["email"].lower()]
    for m in shown:
        m["joined"] = pretty_date(m["created_at"])
    waiting = db.execute("SELECT COUNT(*) FROM reports WHERE territory_id = ? AND status = 'submitted'",
                         (territory["id"],)).fetchone()[0]
    context.update({
        "territory": territory["name"], "members": shown, "q": q, "total_members": len(everyone),
        "open_tasks": sum(m["open_tasks"] for m in everyone),
        "resolved": sum(m["resolved"] for m in everyone), "waiting": waiting,
    })
    return context


def render_team_page(form=None, open_team=False, status=200):
    context = team_context(session["user_id"], request.args.get("q", "").strip()[:100])
    if context is None:
        session.clear()
        return redirect(url_for("login"))
    return render_template("Type_2/leader/my_team.html", form=form or {}, open_team=open_team, **context), status


@app.route("/leader/team", methods=["GET"])
@leader_required
def leader_team():
    return render_team_page()


@app.route("/leader/team/<int:member_id>")
@leader_required
def leader_member_detail(member_id):
    """JSON for the member pop-up: contact details and the reports assigned to them."""
    db = get_db()
    leader_id = session["user_id"]
    m = db.execute(
        "SELECT id, full_name, username, email, created_at FROM users "
        "WHERE id = ? AND role = 'team_member' AND leader_id = ? AND status <> 'removed'",
        (member_id, leader_id)).fetchone()
    if m is None:
        return jsonify(error="Team member not found."), 404

    tasks = [
        {"id": t["id"], "ticket_no": t["ticket_no"], "title": t["title"],
         "status": STATUS_LABELS[t["status"]], "group": STATUS_GROUPS[t["status"]],
         "updated": pretty_date(t["updated_at"])}
        for t in db.execute(
            "SELECT id, ticket_no, title, status, updated_at FROM reports "
            "WHERE assigned_member_id = ? ORDER BY updated_at DESC, id DESC LIMIT 20", (member_id,))
    ]
    return jsonify(name=m["full_name"], username=m["username"], email=m["email"],
                   joined=pretty_date(m["created_at"]), tasks=tasks)


# ------------------------------------------------------------------
# Team Member (Type 2) dashboard
# ------------------------------------------------------------------
MEMBER_VIEWS = {            # key: (tab label, statuses it shows, None = all)
    "todo":   ("To check", ("assigned", "verifying")),
    "repair": ("Repair", ("confirmed", "in_progress")),
    "done":   ("Completed", ("resolved", "invalid")),
    "all":    ("All", None),
}


def member_required(view):
    """Only logged-in Team Members may open the page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "team_member":
            return redirect(url_for("home"))
        return view(*args, **kwargs)
    return wrapped


def notify(db, user_id, report_id, message):
    if user_id:
        db.execute("INSERT INTO notifications (user_id, report_id, message) VALUES (?, ?, ?)",
                   (user_id, report_id, message))


def member_context(user_id, args):
    context = header_context(user_id)
    db = get_db()
    territory = leader_territory(db, user_id)
    if context is None:
        return None

    view = args.get("view", "todo")
    if view not in MEMBER_VIEWS:
        view = "todo"
    q = args.get("q", "").strip()[:100]

    by_status = {r["status"]: r["n"] for r in db.execute(
        "SELECT status, COUNT(*) AS n FROM reports WHERE assigned_member_id = ? GROUP BY status", (user_id,))}
    counts = {key: (sum(by_status.values()) if statuses is None else sum(by_status.get(s, 0) for s in statuses))
              for key, (_label, statuses) in MEMBER_VIEWS.items()}

    where, params = ["r.assigned_member_id = ?"], [user_id]
    statuses = MEMBER_VIEWS[view][1]
    if statuses:
        where.append("r.status IN (" + ",".join("?" * len(statuses)) + ")")
        params += list(statuses)
    if q:
        where.append(
            "(r.title LIKE ? ESCAPE '\\' OR r.ticket_no LIKE ? ESCAPE '\\' OR r.location_text LIKE ? ESCAPE '\\' "
            "OR d.name LIKE ? ESCAPE '\\' OR rep.full_name LIKE ? ESCAPE '\\')"
        )
        params += [like_pattern(q)] * 5
    joins = ("FROM reports r JOIN departments d ON d.id = r.department_id "
             "JOIN users rep ON rep.id = r.reporter_id WHERE " + " AND ".join(where))

    matched = db.execute("SELECT COUNT(*) " + joins, params).fetchone()[0]
    pages = max(1, -(-matched // PAGE_SIZE))
    page = min(max(args.get("page", 1, type=int), 1), pages)

    rows = []
    for r in db.execute(
        "SELECT r.id, r.ticket_no, r.title, r.category, r.location_text, r.status, r.updated_at, "
        "d.name AS department, rep.full_name AS reporter " + joins +
        " ORDER BY r.updated_at DESC, r.id DESC LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ):
        rows.append({
            "id": r["id"], "ticket_no": r["ticket_no"], "title": r["title"],
            "category": dict(CATEGORIES).get(r["category"], r["category"]),
            "department": r["department"], "location": r["location_text"], "reporter": r["reporter"],
            "updated": pretty_date(r["updated_at"]),
            "status": STATUS_LABELS[r["status"]], "group": STATUS_GROUPS[r["status"]],
        })

    tabs = [(key, label, counts[key]) for key, (label, _s) in MEMBER_VIEWS.items()]
    context.update({
        "territory": territory["name"] if territory else "", "reports": rows, "tabs": tabs, "counts": counts,
        "stats": {"todo": counts["todo"], "repair": counts["repair"],
                  "resolved": by_status.get("resolved", 0), "invalid": by_status.get("invalid", 0)},
        "view": view, "q": q, "page": page, "pages": pages, "matched": matched,
    })
    return context


def render_member_dashboard():
    alert_overdue_reports(get_db())
    context = member_context(session["user_id"], request.args)
    if context is None:
        session.clear()
        return redirect(url_for("login"))
    return render_template("Type_2/member/dashboard.html", **context)


@app.route("/member/tasks/<int:report_id>")
@member_required
def member_task_detail(report_id):
    """JSON for the task pop-up. Only reports assigned to this member."""
    db = get_db()
    r = db.execute(
        "SELECT r.*, d.name AS department, rep.full_name AS reporter, rep.department AS reporter_dept "
        "FROM reports r JOIN departments d ON d.id = r.department_id "
        "JOIN users rep ON rep.id = r.reporter_id "
        "WHERE r.id = ? AND r.assigned_member_id = ?", (report_id, session["user_id"])
    ).fetchone()
    if r is None:
        return jsonify(error="Task not found."), 404

    finding = None
    v = db.execute("SELECT damage_exists, remarks, proof_path, verified_at FROM report_verifications "
                   "WHERE report_id = ?", (report_id,)).fetchone()
    if v:
        finding = {"damage_exists": bool(v["damage_exists"]), "remarks": v["remarks"] or "",
                   "proof_url": url_for("static", filename=v["proof_path"]) if v["proof_path"] else None,
                   "when": pretty_datetime(v["verified_at"])}

    timeline = [
        {"status": STATUS_LABELS.get(h["status"], h["status"]), "note": h["note"] or "",
         "when": pretty_datetime(h["changed_at"]), "changed_by": h["changed_by_name"] or "System"}
        for h in db.execute(
            "SELECT h.status, h.note, h.changed_at, u.full_name AS changed_by_name "
            "FROM report_status_history h LEFT JOIN users u ON u.id=h.changed_by "
            "WHERE h.report_id = ? ORDER BY h.changed_at, h.id", (report_id,))
    ]
    return jsonify(
        id=r["id"], ticket_no=r["ticket_no"], title=r["title"],
        category=dict(CATEGORIES).get(r["category"], r["category"]),
        department=r["department"], location=r["location_text"], description=r["description"],
        reporter=f"{r['reporter']} ({r['reporter_dept']})",
        incident=pretty_incident(r["incident_date"], r["incident_time"]),
        submitted=pretty_datetime(r["created_at"]),
        status=STATUS_LABELS[r["status"]], group=STATUS_GROUPS[r["status"]], status_key=r["status"],
        photo_url=url_for("static", filename=r["photo_path"]) if r["photo_path"] else None,
        finding=finding, timeline=timeline,
        assignment_history=[dict(actor=a["actor_name"] or "System", previous_leader=a["previous_leader"] or "Unassigned", new_leader=a["new_leader"] or "Unassigned", previous_member=a["previous_member"] or "Unassigned", new_member=a["new_member"] or "Unassigned", note=a["note"], when=pretty_datetime(a["changed_at"])) for a in db.execute("SELECT ah.*, actor.full_name AS actor_name, pl.full_name AS previous_leader, nl.full_name AS new_leader, pm.full_name AS previous_member, nm.full_name AS new_member FROM report_assignment_history ah LEFT JOIN users actor ON actor.id=ah.actor_id LEFT JOIN users pl ON pl.id=ah.previous_leader_id LEFT JOIN users nl ON nl.id=ah.new_leader_id LEFT JOIN users pm ON pm.id=ah.previous_member_id LEFT JOIN users nm ON nm.id=ah.new_member_id WHERE ah.report_id=? ORDER BY ah.changed_at,ah.id", (report_id,))],
        due_at=pretty_datetime(r["due_at"]) if r["due_at"] else None,
        overdue=bool(r["due_at"] and r["due_at"] < datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S") and r["status"] not in ("resolved", "invalid")),
        before_photo_url=url_for("static", filename=r["before_photo_path"]) if r["before_photo_path"] else None,
        after_photo_url=url_for("static", filename=r["after_photo_path"]) if r["after_photo_path"] else None,
        progress=[dict(message=p["message"], user=p["full_name"], when=pretty_datetime(p["created_at"]), evidence_url=url_for("static", filename=p["evidence_path"]) if p["evidence_path"] else None) for p in db.execute("SELECT p.message,p.created_at,p.evidence_path,u.full_name FROM report_progress_updates p JOIN users u ON u.id=p.user_id WHERE p.report_id=? ORDER BY p.created_at,p.id", (report_id,))],
    )


@app.route("/member/tasks/<int:report_id>/<action>", methods=["POST"])
@member_required
def member_task_update(report_id, action):
    """
    One step of the member's workflow:
      start    assigned           -> verifying     (on my way to check)
      finding  assigned/verifying -> confirmed or invalid (is the damage real?)
      repair   confirmed          -> in_progress   (repair started)
      resolve  in_progress        -> resolved      (fixed)
    """
    if action not in ("start", "finding", "repair", "resolve"):
        return jsonify(error="Unknown action."), 404

    db = get_db()
    me = session["user_id"]
    name = session["full_name"]
    task = db.execute(
        "SELECT id, ticket_no, title, reporter_id, assigned_leader_id, status FROM reports "
        "WHERE id = ? AND assigned_member_id = ?", (report_id, me)).fetchone()
    if task is None:
        return jsonify(error="Task not found."), 404

    allowed = {"start": ("assigned",), "finding": ("assigned", "verifying"),
               "repair": ("confirmed",), "resolve": ("in_progress",)}
    if task["status"] not in allowed[action]:
        return jsonify(error="This task has already moved on. Please close this window and try again."), 409

    remarks = request.form.get("remarks", "").strip()
    if len(remarks) > 500:
        return jsonify(error="Please keep your note under 500 characters."), 400

    ticket, title = task["ticket_no"], task["title"]
    reporter, leader = task["reporter_id"], task["assigned_leader_id"]
    proof_path = None

    if action == "start":
        new_status, note = "verifying", "Started checking on site"
        notify(db, reporter, report_id, f"{name} is checking your report {ticket} on site.")

    elif action == "finding":
        damage = request.form.get("damage_exists")
        if damage not in ("1", "0"):
            return jsonify(error="Choose whether the damage exists."), 400
        if len(remarks) < 10:
            return jsonify(error="Describe what you found (at least 10 characters)."), 400

        photo = request.files.get("photo")
        photo_type = None
        if photo and photo.filename:
            head = photo.stream.read(12)
            photo.stream.seek(0, os.SEEK_END)
            size = photo.stream.tell()
            photo.stream.seek(0)
            photo_type = detect_image_type(head)
            if photo_type is None:
                return jsonify(error="The photo must be a PNG, JPG or WEBP image."), 400
            if size > MAX_PHOTO_BYTES:
                return jsonify(error="The photo is too large. Please choose an image under 5 MB."), 400
        if photo_type:
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            filename = f"proof_{uuid.uuid4().hex}.{photo_type}"
            photo.save(os.path.join(UPLOAD_DIR, filename))
            proof_path = f"uploads/{filename}"

        exists = damage == "1"
        new_status, note = ("confirmed" if exists else "invalid"), remarks
        try:
            db.execute("INSERT INTO report_verifications (report_id, member_id, damage_exists, remarks, proof_path) "
                       "VALUES (?, ?, ?, ?, ?)", (report_id, me, int(exists), remarks, proof_path))
        except sqlite3.IntegrityError:
            db.rollback()
            if proof_path:
                os.remove(os.path.join(BASE_DIR, "static", proof_path))
            return jsonify(error="A finding was already recorded for this report."), 409
        if exists:
            notify(db, reporter, report_id, f"The damage in your report {ticket} was confirmed. Repair will follow.")
            notify(db, leader, report_id, f"{name} confirmed the damage for {ticket} - {title}.")
        else:
            notify(db, reporter, report_id, f"The team could not find the reported damage for {ticket}.")
            notify(db, leader, report_id, f"{name} found no damage for {ticket} - {title}.")

    elif action == "repair":
        new_status, note = "in_progress", remarks or "Repair started"
        notify(db, reporter, report_id, f"Repair has started for your report {ticket}.")

    else:  # resolve
        if len(remarks) < 10:
            return jsonify(error="Describe what was done (at least 10 characters)."), 400
        new_status, note = "resolved", remarks
        notify(db, reporter, report_id, f"Your report {ticket} has been resolved. Thank you for reporting it!")
        notify(db, leader, report_id, f"{name} resolved {ticket} - {title}.")

    db.execute("UPDATE reports SET status = ?, resolved_at = CASE WHEN ? = 'resolved' THEN datetime('now') "
               "ELSE resolved_at END WHERE id = ?", (new_status, new_status, report_id))
    add_history_note(db, report_id, new_status, note, me)
    db.commit()
    # Email the responsible Team Leader whenever a member changes the issue status.
    if leader:
        leader_row = db.execute("SELECT full_name, email FROM users WHERE id=? AND role='team_leader'", (leader,)).fetchone()
        if leader_row:
            send_email_html(
                leader_row["email"], f"COREBOUND: Report update {ticket}", f"Report status updated: {ticket}",
                [f"Hello {leader_row['full_name']},", f"{name} updated the report status.", f"Report: {ticket} — {title}",
                 f"New status: {STATUS_LABELS.get(new_status, new_status)}", f"Update details: {note}",
                 "Sign in to COREBOUND to review the report."], to_name=leader_row["full_name"])
    # Email the original reporter when the issue is completed or denied as invalid.
    if new_status in ("resolved", "invalid"):
        reporter_row = db.execute("SELECT full_name, email FROM users WHERE id=?", (reporter,)).fetchone()
        if reporter_row:
            if new_status == "resolved":
                heading = f"Your report {ticket} has been completed"
                paragraphs = [f"Hello {reporter_row['full_name']},", f"Your report {ticket} — {title} — has been marked completed.", f"Completion notes: {note}", "Thank you for helping improve campus facilities."]
                subject = f"COREBOUND: Report {ticket} completed"
            else:
                heading = f"Your report {ticket} was denied"
                paragraphs = [f"Hello {reporter_row['full_name']},", f"Your report {ticket} — {title} — was marked as unconfirmed/invalid after inspection.", f"Reason/details: {note}", "Please contact the campus team if you believe this decision needs review."]
                subject = f"COREBOUND: Report {ticket} denied"
            send_email_html(reporter_row["email"], subject, heading, paragraphs, to_name=reporter_row["full_name"])
    return jsonify(ok=True, status=STATUS_LABELS[new_status])


@app.route("/member/tasks/<int:report_id>/progress", methods=["POST"])
@member_required
def member_task_progress(report_id):
    return save_report_progress(report_id, "member")


@app.route("/leader/reports/<int:report_id>/progress", methods=["POST"])
@leader_required
def leader_report_progress(report_id):
    return save_report_progress(report_id, "leader")


def save_report_progress(report_id, actor_role):
    db = get_db()
    uid = session["user_id"]
    if actor_role == "member":
        report = db.execute("SELECT * FROM reports WHERE id=? AND assigned_member_id=?", (report_id, uid)).fetchone()
    else:
        territory = leader_territory(db, uid)
        report = db.execute("SELECT * FROM reports WHERE id=? AND territory_id=?", (report_id, territory["id"] if territory else -1)).fetchone()
    if not report:
        return jsonify(error="Report not found."), 404
    message = request.form.get("message", "").strip()
    if len(message) < 3 or len(message) > 1000:
        return jsonify(error="Progress notes must be 3–1,000 characters."), 400
    evidence = request.files.get("evidence")
    evidence_path = None
    if evidence and evidence.filename:
        head=evidence.stream.read(12); evidence.stream.seek(0, os.SEEK_END); size=evidence.stream.tell(); evidence.stream.seek(0)
        kind=detect_image_type(head)
        if kind is None or size > MAX_PHOTO_BYTES:
            return jsonify(error="Evidence must be a PNG, JPG or WEBP image under 5 MB."), 400
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        filename=f"progress_{uuid.uuid4().hex}.{kind}"; evidence.save(os.path.join(UPLOAD_DIR, filename)); evidence_path=f"uploads/{filename}"
    db.execute("INSERT INTO report_progress_updates(report_id,user_id,message,evidence_path) VALUES(?,?,?,?)", (report_id,uid,message,evidence_path))
    add_history_note(db, report_id, report["status"], "Progress update: " + message[:400], uid)
    recipients={report["reporter_id"], report["assigned_leader_id"], report["assigned_member_id"]}
    for recipient in recipients:
        if recipient and recipient != uid:
            notify(db, recipient, report_id, f"Progress update for {report['ticket_no']}: {message[:180]}")
    db.commit()
    # Every member progress update is emailed to the responsible Team Leader.
    if actor_role == "member" and report["assigned_leader_id"]:
        leader_row = db.execute("SELECT full_name, email FROM users WHERE id=? AND role='team_leader'", (report["assigned_leader_id"],)).fetchone()
        updater = db.execute("SELECT full_name FROM users WHERE id=?", (uid,)).fetchone()
        if leader_row:
            send_email_html(
                leader_row["email"], f"COREBOUND: Progress update for {report['ticket_no']}",
                f"Team member update: {report['ticket_no']}",
                [f"Hello {leader_row['full_name']},", f"{updater['full_name'] if updater else 'A team member'} updated the issue.",
                 f"Report: {report['ticket_no']} — {report['title']}", f"Current status: {STATUS_LABELS.get(report['status'], report['status'])}",
                 f"Update: {message}", "Sign in to COREBOUND to review the report."], to_name=leader_row["full_name"])
    return jsonify(ok=True)


@app.route("/leader/reports/<int:report_id>/evidence", methods=["POST"])
@leader_required
def leader_report_evidence(report_id):
    db=get_db(); territory=leader_territory(db,session["user_id"])
    r=db.execute("SELECT id,ticket_no,reporter_id,assigned_member_id FROM reports WHERE id=? AND territory_id=?",(report_id,territory["id"] if territory else -1)).fetchone()
    if not r: return jsonify(error="Report not found."),404
    kind=request.form.get("kind")
    if kind not in ("before","after"): return jsonify(error="Choose before or after evidence."),400
    photo=request.files.get("photo")
    if not photo or not photo.filename: return jsonify(error="Choose a photo."),400
    head=photo.stream.read(12); photo.stream.seek(0,os.SEEK_END); size=photo.stream.tell(); photo.stream.seek(0)
    ext=detect_image_type(head)
    if ext is None or size>MAX_PHOTO_BYTES: return jsonify(error="Photo must be PNG, JPG or WEBP under 5 MB."),400
    os.makedirs(UPLOAD_DIR,exist_ok=True); filename=f"{kind}_{uuid.uuid4().hex}.{ext}"; photo.save(os.path.join(UPLOAD_DIR,filename)); path=f"uploads/{filename}"
    col="before_photo_path" if kind=="before" else "after_photo_path"
    db.execute(f"UPDATE reports SET {col}=? WHERE id=?",(path,report_id))
    add_history_note(db,report_id,r and db.execute("SELECT status FROM reports WHERE id=?",(report_id,)).fetchone()[0],f"{kind.title()} repair photo uploaded",session["user_id"])
    for recipient in {r["reporter_id"],r["assigned_member_id"]}:
        if recipient and recipient!=session["user_id"]: notify(db,recipient,report_id,f"{kind.title()} repair photo added to {r['ticket_no']}.")
    db.commit(); return jsonify(ok=True,url=url_for("static",filename=path))


@app.route("/home")
def home():
    """Send the logged-in user to the dashboard that matches their role."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    template = DASHBOARDS.get(session.get("role"))
    if template is None:
        session.clear()
        return redirect(url_for("login"))

    if session["role"] == "team_leader":
        return render_leader_dashboard()
    if session["role"] == "team_member":
        return render_member_dashboard()
    if session["role"] == "admin":
        # The designated Type 3 account has exactly two main pages.
        ensure_admin_tables()
        if session.get("username", "").casefold() != SUPER_ADMIN_USERNAME.casefold():
            session.clear()
            return redirect(url_for("login"))
        return redirect(url_for("admin_dashboard"))

    context = {
        "full_name": session["full_name"],
        "role": session["role"],
        "show_skeleton": session.pop("show_skeleton", False),   # only right after login
    }
    if session["role"] == "reporter":
        data = reporter_context(session["user_id"])
        if data is None:                       # account no longer exists
            session.clear()
            return redirect(url_for("login"))
        context.update(data)
    return render_template(template, **context)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------
# Locations fill the Team Leader registration form:  flask --app app add-territory
# Departments fill the dropdown in the report form:  flask --app app add-department
# ------------------------------------------------------------------
@app.cli.command("add-territory")
@click.option("--name", prompt="Building or location name")
def add_territory(name):
    """Add a building or campus location that a Team Leader can be assigned to."""
    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("INSERT INTO territories (name) VALUES (?)", (name.strip(),))
        except sqlite3.IntegrityError:
            raise click.ClickException("That location already exists.")
    click.echo("Location added.")


@app.cli.command("add-department")
@click.option("--name", prompt="Department name")
@click.option("--territory", prompt="Territory (campus area) that handles it")
def add_department(name, territory):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO territories (name) VALUES (?)", (territory.strip(),))
        territory_id = conn.execute(
            "SELECT id FROM territories WHERE name = ?", (territory.strip(),)
        ).fetchone()[0]
        try:
            conn.execute("INSERT INTO departments (name, territory_id) VALUES (?, ?)",
                         (name.strip(), territory_id))
        except sqlite3.IntegrityError:
            raise click.ClickException("That department already exists.")
    click.echo("Department added.")


# Buildings and campus locations. Each one can have one Team Leader.
LOCATIONS = [
    "PMF Building", "HRDC", "HUM Building", "ITSO",
    "Computer Laboratories Main Campus", "Computer Laboratories YoungField Campus",
    "Student Center", "Montejo Building", "ORC Building", "IGP Canteen",
    "Extension Building", "GYM", "Alba Hall", "Conversion Building",
    "ACAD Building", "LNU Cafe", "LNU Hostel", "LNU Library",
    "LNU Wellness-Center", "LNU ILS Building", "LNU Dormitory", "LNU Museum",
]


def seed_locations(only_if_empty=False):
    """Add the LOCATIONS above. Names that already exist are skipped. Returns how many were added."""
    with sqlite3.connect(DB_PATH) as conn:
        if only_if_empty and conn.execute("SELECT COUNT(*) FROM territories").fetchone()[0]:
            return 0
        before = conn.total_changes
        conn.executemany("INSERT OR IGNORE INTO territories (name) VALUES (?)", [(n,) for n in LOCATIONS])
        return conn.total_changes - before


@app.cli.command("seed-locations")
def seed_locations_command():
    """Add the campus buildings and locations (safe to run again)."""
    click.echo(f"{seed_locations()} location(s) added.")


# Apply schema.sql on every start. Every statement uses IF NOT EXISTS, so existing data
# is kept and any new tables are simply added.
init_db()
with app.app_context():
    ensure_admin_tables()
# A brand-new database gets the campus locations automatically.
seed_locations(only_if_empty=True)


def seed_departments_from_territories():
    """Ensure every leader-assignment territory is selectable in the report form.

    Department options use the same names and territory IDs as the locations
    assigned to team leaders. Safe to run on every startup.
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO departments (name, territory_id) "
            "SELECT name, id FROM territories ORDER BY name"
        )
        conn.commit()


# Keep the reporter's Department dropdown aligned with the Team Leader locations.
# Vercel's deployed source filesystem is read-only. Do not perform this startup
# write there; run the seeding step against a writable database during deployment
# or use a persistent external database for production. Local development keeps
# the original automatic seeding behavior.
if not os.environ.get("VERCEL"):
    seed_departments_from_territories()

if __name__ == "__main__":
    app.run(debug=True)