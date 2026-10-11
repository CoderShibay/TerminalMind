import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "terminalmd.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 1


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> sqlite3.Connection:
    conn = get_conn()
    existing = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='claude_messages'"
    ).fetchone()
    # Existing databases need their columns before schema.sql creates indexes
    # that refer to them. Fresh databases can be created directly at v1.
    if existing:
        _migrate(conn)
    conn.executescript(SCHEMA_PATH.read_text())
    if not existing:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply small, idempotent migrations to databases created by older tm versions."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        return

    if "agent" not in _columns(conn, "claude_messages"):
        conn.execute(
            "ALTER TABLE claude_messages ADD COLUMN agent TEXT NOT NULL DEFAULT 'claude'"
        )
    session_columns = _columns(conn, "claude_sessions")
    if "agent" not in session_columns:
        conn.execute(
            "ALTER TABLE claude_sessions ADD COLUMN agent TEXT NOT NULL DEFAULT 'claude'"
        )
    if "native_session_id" not in session_columns:
        conn.execute("ALTER TABLE claude_sessions ADD COLUMN native_session_id TEXT")

    conn.execute(
        "UPDATE claude_sessions SET native_session_id = session_id "
        "WHERE native_session_id IS NULL"
    )
    conn.executescript(
        f"""
        DROP TABLE IF EXISTS messages_fts;
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content_text, role, project, cwd, ts, uuid, session_id, agent,
            content='claude_messages', content_rowid='id'
        );
        INSERT INTO messages_fts(messages_fts) VALUES ('rebuild');
        CREATE INDEX IF NOT EXISTS claude_messages_agent_idx ON claude_messages(agent);
        CREATE INDEX IF NOT EXISTS claude_sessions_agent_idx ON claude_sessions(agent);
        CREATE INDEX IF NOT EXISTS claude_sessions_native_id_idx
            ON claude_sessions(native_session_id);
        PRAGMA user_version = {SCHEMA_VERSION};
        """
    )
