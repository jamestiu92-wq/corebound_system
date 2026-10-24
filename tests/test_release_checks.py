import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ReleaseChecks(unittest.TestCase):
    def test_app_syntax(self):
        ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))

    def test_schema_initializes_and_integrity_is_ok(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            con = sqlite3.connect(tmp.name)
            con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"users", "reports", "report_status_history", "report_progress_updates", "report_assignment_history", "login_attempts", "security_audit_log"}.issubset(tables))
            cols = {r[1] for r in con.execute("PRAGMA table_info(reports)")}
            self.assertTrue({"due_at", "overdue_alerted_at", "before_photo_path", "after_photo_path"}.issubset(cols))
            con.close()

    def test_security_features_present(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for marker in ("corebound_security_guard", "corebound_security_headers", "X-CSRFToken", "login_attempts", "SESSION_COOKIE_SECURE", "security_audit_log"):
            self.assertIn(marker, source)

    def test_required_project_files(self):
        for relative in ("schema.sql", "requirements.txt", "README.md", "PHASES_1_TO_10.md", "TEST_NOTES.md", "templates/login.html", "templates/Type_3/dashboard.html"):
            self.assertTrue((ROOT / relative).exists(), relative)

if __name__ == "__main__":
    unittest.main()
