-- LNU Campus Facility and Damage Reporting and Monitoring System
-- SQLite schema.  Create the database with:  sqlite3 database.db < schema.sql

PRAGMA foreign_keys = ON;

-- Territories: areas of campus, each handled by one team
CREATE TABLE IF NOT EXISTS territories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    description TEXT
);

-- Users (all 3 types, separated by role)
--   reporter    = Type 1 (students, faculty, staff, teachers)
--   team_leader = Type 2 leader
--   team_member = Type 2 member
--   admin       = Type 3
-- Login uses the username.
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name      TEXT NOT NULL,
    username       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    department     TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'reporter'
                   CHECK (role IN ('reporter','team_leader','team_member','admin')),
    territory_id   INTEGER,                     -- team_leader / team_member only
    leader_id      INTEGER,                     -- team_member -> their team_leader
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','warned','removed')),
    warning_count  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (territory_id) REFERENCES territories(id),
    FOREIGN KEY (leader_id)    REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_leader_per_territory
    ON users(territory_id) WHERE role = 'team_leader';

-- Departments: shown in the report form. Each one belongs to a territory,
-- which decides which team receives the report.
CREATE TABLE IF NOT EXISTS departments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    territory_id  INTEGER NOT NULL,
    FOREIGN KEY (territory_id) REFERENCES territories(id)
);

-- Reports submitted by Type 1 users
CREATE TABLE IF NOT EXISTS reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no           TEXT NOT NULL UNIQUE,          -- e.g. LNU-2026-00042
    reporter_id         INTEGER NOT NULL,
    department_id       INTEGER NOT NULL,
    territory_id        INTEGER NOT NULL,              -- copied from the department, used for routing
    category            TEXT NOT NULL
                        CHECK (category IN ('equipment','facility','room')),
    title               TEXT NOT NULL,
    location_text       TEXT NOT NULL,                 -- free text, e.g. 'Room 204, Science Building'
    description         TEXT NOT NULL,
    incident_date       TEXT NOT NULL,                 -- YYYY-MM-DD
    incident_time       TEXT NOT NULL,                 -- HH:MM
    severity            TEXT NOT NULL DEFAULT 'medium'
                        CHECK (severity IN ('low','medium','high','urgent')),
    photo_path          TEXT,                          -- relative to static/, e.g. uploads/ab12.jpg
    status              TEXT NOT NULL DEFAULT 'submitted'
                        CHECK (status IN ('submitted','assigned','verifying',
                                          'confirmed','in_progress','resolved','invalid')),
    assigned_leader_id  INTEGER,
    assigned_member_id  INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at         TEXT,
    due_at              TEXT,
    overdue_alerted_at  TEXT,
    before_photo_path   TEXT,
    after_photo_path    TEXT,
    FOREIGN KEY (reporter_id)        REFERENCES users(id),
    FOREIGN KEY (department_id)      REFERENCES departments(id),
    FOREIGN KEY (territory_id)       REFERENCES territories(id),
    FOREIGN KEY (assigned_leader_id) REFERENCES users(id),
    FOREIGN KEY (assigned_member_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_reports_reporter  ON reports(reporter_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_territory ON reports(territory_id, status);
CREATE INDEX IF NOT EXISTS idx_reports_member    ON reports(assigned_member_id, status);

-- Team member's on-site finding
CREATE TABLE IF NOT EXISTS report_verifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id      INTEGER NOT NULL UNIQUE,
    member_id      INTEGER NOT NULL,
    damage_exists  INTEGER NOT NULL CHECK (damage_exists IN (0,1)),
    remarks        TEXT,
    proof_path     TEXT,
    verified_at    TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY (member_id) REFERENCES users(id)
);

-- Status timeline shown to reporters
CREATE TABLE IF NOT EXISTS report_status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id   INTEGER NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT,
    changed_by  INTEGER,
    changed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (report_id)  REFERENCES reports(id) ON DELETE CASCADE,
    FOREIGN KEY (changed_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_history_report ON report_status_history(report_id, changed_at);

-- Warnings issued by admins for fake reports
CREATE TABLE IF NOT EXISTS user_warnings (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    admin_id   INTEGER NOT NULL,
    report_id  INTEGER,
    reason     TEXT NOT NULL,
    issued_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)   REFERENCES users(id),
    FOREIGN KEY (admin_id)  REFERENCES users(id),
    FOREIGN KEY (report_id) REFERENCES reports(id)
);

-- Notifications for report updates
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    report_id   INTEGER,
    message     TEXT NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0,1)),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id)   REFERENCES users(id),
    FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);

-- Feedback about the system, sent by Type 1 users
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    category    TEXT NOT NULL CHECK (category IN ('suggestion','compliment','problem','other')),
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id, created_at DESC);



-- Reports about user accounts submitted by authenticated non-admin users.
CREATE TABLE IF NOT EXISTS account_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    reported_user_id  INTEGER NOT NULL,
    reporter_id       INTEGER NOT NULL,
    reason            TEXT NOT NULL,
    description       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','under_review','resolved','dismissed')),
    admin_note        TEXT,
    reviewed_by       INTEGER,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (reported_user_id) REFERENCES users(id),
    FOREIGN KEY (reporter_id) REFERENCES users(id),
    FOREIGN KEY (reviewed_by) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_account_reports_status
    ON account_reports(status, created_at DESC);

-- Only the designated account may hold the admin role, and there can only be one.
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

-- Triggers
CREATE TRIGGER IF NOT EXISTS trg_reports_updated
AFTER UPDATE ON reports
FOR EACH ROW
BEGIN
    UPDATE reports SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_report_created
AFTER INSERT ON reports
FOR EACH ROW
BEGIN
    INSERT INTO report_status_history (report_id, status, note, changed_by)
    VALUES (NEW.id, NEW.status, 'Report submitted', NEW.reporter_id);
END;

CREATE TRIGGER IF NOT EXISTS trg_report_status_changed
AFTER UPDATE OF status ON reports
FOR EACH ROW
WHEN OLD.status <> NEW.status
BEGIN
    INSERT INTO report_status_history (report_id, status)
    VALUES (NEW.id, NEW.status);
END;

CREATE TRIGGER IF NOT EXISTS trg_warning_issued
AFTER INSERT ON user_warnings
FOR EACH ROW
BEGIN
    UPDATE users
       SET warning_count = warning_count + 1,
           status = CASE WHEN status = 'removed' THEN 'removed' ELSE 'warned' END
     WHERE id = NEW.user_id;
END;

-- Seven-feature enhancement: deadlines, progress notes, repair evidence, and assignment history.

CREATE INDEX IF NOT EXISTS idx_reports_due ON reports(due_at, status);

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


-- Phase 4 / Phase 1 audit records and login throttling (safe for fresh installs).
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
