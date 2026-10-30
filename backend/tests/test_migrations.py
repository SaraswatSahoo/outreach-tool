import os
import sqlite3
import subprocess
import sys
import tempfile

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

ALL_VERSIONS = [version for version, _ in db.MIGRATIONS]

# The schema as created by the very first version of the app (no reply tracking,
# no cascades, no schema_version table)
ORIGINAL_SCHEMA = """
CREATE TABLE campaigns (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, goal TEXT NOT NULL,
    additional_context TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE recipients (id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    email TEXT NOT NULL, name TEXT, subject TEXT, email_body TEXT, status TEXT DEFAULT 'draft', sent_at DATETIME,
    message_id TEXT, error_message TEXT, follow_up_sent INTEGER DEFAULT 0);
CREATE TABLE followups (id INTEGER PRIMARY KEY AUTOINCREMENT, recipient_id INTEGER NOT NULL REFERENCES recipients(id),
    subject TEXT, email_body TEXT, status TEXT DEFAULT 'draft', sent_at DATETIME, error_message TEXT);
INSERT INTO campaigns (id, name, goal) VALUES (1, 'Old', 'goal');
INSERT INTO recipients (id, campaign_id, email, status, message_id, follow_up_sent) VALUES (1, 1, 'a@x.com', 'sent', '<m@x>', 1);
INSERT INTO followups (recipient_id, subject, email_body, status) VALUES (1, 'Re', 'body', 'sent');
INSERT INTO followups (recipient_id, subject, email_body, status) VALUES (99, 'orphan', 'x', 'draft');
"""


def _migrate(db_path):
    """Run init_db in a fresh interpreter pointed at db_path (module paths are read at import)."""
    env = dict(os.environ, OUTREACH_DB_PATH=db_path)
    code = "import db; db.init_db(); db.init_db()"  # twice: must be idempotent
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, check=True)


def test_original_database_upgrades_with_data_and_cascades():
    path = os.path.join(tempfile.mkdtemp(), "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(ORIGINAL_SCHEMA)
    conn.close()

    _migrate(path)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    assert [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")] == ALL_VERSIONS

    r = conn.execute("SELECT * FROM recipients").fetchone()
    assert (r["email"], r["message_id"], r["follow_up_sent"], r["reply_status"]) == ("a@x.com", "<m@x>", 1, "no_reply")
    fu = conn.execute("SELECT * FROM followups").fetchall()
    assert [(f["subject"], f["step"], f["source"]) for f in fu] == [("Re", 1, "manual")]  # orphan dropped

    for table, parent in (("recipients", "campaigns"), ("followups", "recipients"), ("replies", "recipients")):
        fks = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        assert [(fk["table"], fk["on_delete"]) for fk in fks] == [(parent, "CASCADE")]

    conn.execute("INSERT INTO replies (recipient_id, body, detected_at) VALUES (1, 'hi', 'now')")
    conn.execute("DELETE FROM campaigns WHERE id = 1")
    assert [
        conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("recipients", "followups", "replies")
    ] == [0, 0, 0]
    conn.close()


def test_fresh_database_gets_latest_schema():
    path = os.path.join(tempfile.mkdtemp(), "new.db")
    _migrate(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == ALL_VERSIONS[-1]
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"email_checks", "replies", "jobs"} <= tables
    cols = {row[1] for row in conn.execute("PRAGMA table_info(recipients)")}
    assert {"replied_at", "company", "role", "notes", "reply_status"} <= cols
    conn.close()


def test_deleting_campaign_via_api_cascades(client, fake_ai, fake_smtp):
    import db
    from tests.helpers import create_campaign, generate_and_send

    cid = create_campaign(client, "a@acme.com")
    generate_and_send(client, cid)
    client.post(f"/api/campaign/{cid}/generate-followups", json={})
    from tests.helpers import wait_for

    wait_for(client, f"/api/campaign/{cid}/generate-followup-progress", "generating")
    assert client.delete(f"/api/campaign/{cid}").status_code == 200
    conn = db.get_db()
    assert conn.execute("SELECT COUNT(*) FROM followups").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM recipients").fetchone()[0] == 0
    conn.close()
