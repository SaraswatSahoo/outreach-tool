"""
SQLite database initialization and helper functions.
"""

import sqlite3

from constants import DB_PATH


def get_db():
    """Get a database connection with row factory."""
    # Send threads, generation workers, the inbox monitor and requests all
    # write concurrently; wait for locks instead of failing immediately.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def migrate_reply_status_columns(conn):
    cursor = conn.cursor()
    existing = [row[1] for row in cursor.execute("PRAGMA table_info(recipients)").fetchall()]

    new_columns = [
        ("reply_status", "TEXT DEFAULT 'no_reply'"),
        ("reply_content", "TEXT"),
        ("check_back_date", "TEXT"),
        ("exclude_followup", "INTEGER DEFAULT 0"),
        ("status_updated_at", "TEXT"),
    ]

    for col_name, col_def in new_columns:
        if col_name not in existing:
            cursor.execute(f"ALTER TABLE recipients ADD COLUMN {col_name} {col_def}")

    conn.commit()


def migrate_blocked_domains_table(conn):
    """Safe migration: create blocked_domains table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blocked_domains (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT UNIQUE NOT NULL,
            reason TEXT,
            blocked_at TEXT NOT NULL
        )
    """)
    conn.commit()


def migrate_processed_inbox_table(conn):
    """Inbox messages the monitor has already handled, keyed by Message-ID header."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_inbox_messages (
            message_id TEXT PRIMARY KEY,
            classification TEXT,
            processed_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _add_missing_columns(conn, table, columns):
    """Idempotently add (name, definition) columns to an existing table."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, definition in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    conn.commit()


def migrate_phase3(conn):
    """Columns/tables for reply detection, sequences, scheduling and CSV fields."""
    _add_missing_columns(
        conn,
        "recipients",
        [
            ("replied_at", "TEXT"),  # first detected reply; stops automatic sequences
            ("company", "TEXT"),  # optional per-recipient fields (CSV import)
            ("role", "TEXT"),
            ("notes", "TEXT"),
        ],
    )
    _add_missing_columns(
        conn,
        "followups",
        [
            ("message_id", "TEXT"),  # so later replies/follow-ups can thread onto it
            ("step", "INTEGER DEFAULT 1"),  # 1 = first follow-up, 2 = second, ...
            ("source", "TEXT DEFAULT 'manual'"),  # 'manual' or 'sequence'
        ],
    )
    _add_missing_columns(
        conn,
        "campaigns",
        [
            ("sequence_enabled", "INTEGER DEFAULT 0"),
            ("sequence_json", "TEXT"),  # [{"delay_days": 4}, ...]
            ("auto_send_followups", "INTEGER DEFAULT 0"),
            ("scheduled_at", "TEXT"),  # UTC ISO time to start sending drafts
        ],
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL REFERENCES recipients(id),
            message_id TEXT UNIQUE,
            from_email TEXT,
            subject TEXT,
            body TEXT,
            received_at TEXT,
            classification TEXT,
            summary TEXT,
            detected_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_replies_recipient_id ON replies(recipient_id)")
    conn.commit()


def migrate_jobs_table(conn):
    """Background job progress (generation/sending), so it survives restarts."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            key TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            data TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _migration_1_baseline(conn):
    """
    Everything up to the introduction of schema versioning. Every step is
    idempotent, so this is safe on brand-new databases and on databases created
    by any earlier version of the app.
    """
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            additional_context TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recipients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            email TEXT NOT NULL,
            name TEXT,
            subject TEXT,
            email_body TEXT,
            status TEXT DEFAULT 'draft',
            sent_at DATETIME,
            message_id TEXT,
            error_message TEXT,
            follow_up_sent INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL REFERENCES recipients(id),
            subject TEXT,
            email_body TEXT,
            status TEXT DEFAULT 'draft',
            sent_at DATETIME,
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_recipients_email ON recipients(email);
        CREATE INDEX IF NOT EXISTS idx_recipients_sent_at ON recipients(sent_at);
        CREATE INDEX IF NOT EXISTS idx_recipients_campaign_id ON recipients(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_followups_recipient_id ON followups(recipient_id);
    """)
    conn.commit()

    migrate_reply_status_columns(conn)

    # Create index for reply_status after ensuring the column exists
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recipients_reply_status ON recipients(reply_status);")
    # Rows created before the column existed have NULL, which `NOT IN (...)` filters silently drop
    conn.execute("UPDATE recipients SET reply_status = 'no_reply' WHERE reply_status IS NULL")
    conn.commit()

    migrate_blocked_domains_table(conn)
    migrate_processed_inbox_table(conn)
    migrate_jobs_table(conn)
    migrate_phase3(conn)


# Final table definitions with ON DELETE CASCADE, used to rebuild older tables.
# Deleting a campaign removes its recipients; deleting a recipient removes its
# follow-ups and replies.
CASCADE_TABLES = {
    "recipients": """
        CREATE TABLE recipients__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            name TEXT,
            subject TEXT,
            email_body TEXT,
            status TEXT DEFAULT 'draft',
            sent_at DATETIME,
            message_id TEXT,
            error_message TEXT,
            follow_up_sent INTEGER DEFAULT 0,
            reply_status TEXT DEFAULT 'no_reply',
            reply_content TEXT,
            check_back_date TEXT,
            exclude_followup INTEGER DEFAULT 0,
            status_updated_at TEXT,
            replied_at TEXT,
            company TEXT,
            role TEXT,
            notes TEXT
        )""",
    "followups": """
        CREATE TABLE followups__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
            subject TEXT,
            email_body TEXT,
            status TEXT DEFAULT 'draft',
            sent_at DATETIME,
            error_message TEXT,
            message_id TEXT,
            step INTEGER DEFAULT 1,
            source TEXT DEFAULT 'manual'
        )""",
    "replies": """
        CREATE TABLE replies__new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_id INTEGER NOT NULL REFERENCES recipients(id) ON DELETE CASCADE,
            message_id TEXT UNIQUE,
            from_email TEXT,
            subject TEXT,
            body TEXT,
            received_at TEXT,
            classification TEXT,
            summary TEXT,
            detected_at TEXT NOT NULL
        )""",
}

INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_recipients_email ON recipients(email);
    CREATE INDEX IF NOT EXISTS idx_recipients_sent_at ON recipients(sent_at);
    CREATE INDEX IF NOT EXISTS idx_recipients_campaign_id ON recipients(campaign_id);
    CREATE INDEX IF NOT EXISTS idx_recipients_reply_status ON recipients(reply_status);
    CREATE INDEX IF NOT EXISTS idx_followups_recipient_id ON followups(recipient_id);
    CREATE INDEX IF NOT EXISTS idx_replies_recipient_id ON replies(recipient_id);
"""


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _migration_2_cascading_deletes(conn):
    """
    SQLite can't add ON DELETE CASCADE to an existing foreign key, so rebuild
    the child tables (create new -> copy rows -> drop old -> rename), following
    the procedure in the SQLite ALTER TABLE docs. Rows already orphaned by
    older versions are dropped, since they can never be shown anyway.
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")  # must be set outside a transaction
    try:
        conn.execute("BEGIN")
        for table, create_sql in CASCADE_TABLES.items():
            conn.execute(create_sql)
            old_cols = set(_columns(conn, table))
            shared = ", ".join(c for c in _columns(conn, f"{table}__new") if c in old_cols)
            conn.execute(f"INSERT INTO {table}__new ({shared}) SELECT {shared} FROM {table}")
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}__new RENAME TO {table}")

        conn.execute("DELETE FROM recipients WHERE campaign_id NOT IN (SELECT id FROM campaigns)")
        conn.execute("DELETE FROM followups WHERE recipient_id NOT IN (SELECT id FROM recipients)")
        conn.execute("DELETE FROM replies WHERE recipient_id NOT IN (SELECT id FROM recipients)")
        for statement in INDEXES.strip().split(";"):
            if statement.strip():
                conn.execute(statement)

        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Foreign key check failed after rebuild: {problems[:5]}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


# (version, migration). Append new migrations; never edit or reorder old ones.
MIGRATIONS = [
    (1, _migration_1_baseline),
    (2, _migration_2_cascading_deletes),
]


def schema_version(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL, applied_at TEXT)")
    return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0


def init_db():
    """Create the database if needed and apply any pending migrations, in order."""
    conn = get_db()
    try:
        conn.execute("PRAGMA journal_mode = WAL")  # persistent; lets readers run during writes
        current = schema_version(conn)
        conn.commit()
        for version, migrate in MIGRATIONS:
            if version > current:
                migrate(conn)
                conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, datetime('now'))", (version,))
                conn.commit()
    finally:
        conn.close()


def query_db(query, args=(), one=False):
    """Execute a query and return results as list of dicts."""
    conn = get_db()
    cursor = conn.execute(query, args)
    rows = cursor.fetchall()
    conn.close()

    result = [dict(row) for row in rows]
    if one:
        return result[0] if result else None
    return result


def execute_db(query, args=()):
    """Execute a write query and return lastrowid."""
    conn = get_db()
    cursor = conn.execute(query, args)
    conn.commit()
    lastrowid = cursor.lastrowid
    conn.close()
    return lastrowid
