import sqlite3

from db import _migrate


def _legacy_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE claude_messages (
            id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, session_id TEXT, ts TEXT,
            role TEXT, content_text TEXT, tool_name TEXT, project TEXT, cwd TEXT,
            source_file TEXT
        );
        CREATE TABLE claude_sessions (
            session_id TEXT PRIMARY KEY, pid INTEGER, cwd TEXT, project TEXT,
            started_at INTEGER, updated_at INTEGER, status TEXT, version TEXT, kind TEXT
        );
        CREATE TABLE message_embeddings (
            message_id INTEGER PRIMARY KEY, embedding BLOB, model TEXT
        );
        CREATE TABLE session_titles (session_id TEXT PRIMARY KEY, method TEXT);
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content_text, role, project, cwd, ts, uuid, session_id,
            content='claude_messages', content_rowid='id'
        );
        INSERT INTO claude_sessions(session_id) VALUES ('legacy-session');
        INSERT INTO claude_messages(uuid,session_id,role,content_text)
            VALUES ('legacy-message','legacy-session','user','migration marker');
        """
    )
    return conn


def test_v1_migration_backfills_agents_native_ids_and_fts():
    conn = _legacy_database()
    _migrate(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    session = conn.execute(
        "SELECT agent,native_session_id FROM claude_sessions"
    ).fetchone()
    assert tuple(session) == ("claude", "legacy-session")
    assert "agent" in [row[1] for row in conn.execute("PRAGMA table_info(messages_fts)")]
    assert conn.execute(
        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'migration'"
    ).fetchone()[0] == 1

    _migrate(conn)
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 1
