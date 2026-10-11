import json
import sqlite3
from pathlib import Path

from indexer import agy_transcripts


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE claude_messages (
            id INTEGER PRIMARY KEY, uuid TEXT UNIQUE, session_id TEXT NOT NULL, ts TEXT,
            role TEXT, content_text TEXT, tool_name TEXT, project TEXT, cwd TEXT,
            source_file TEXT, agent TEXT NOT NULL DEFAULT 'claude'
        );
        CREATE TABLE claude_sessions (
            session_id TEXT PRIMARY KEY, pid INTEGER, cwd TEXT, project TEXT,
            started_at INTEGER, updated_at INTEGER, status TEXT, version TEXT, kind TEXT,
            agent TEXT NOT NULL DEFAULT 'claude', native_session_id TEXT
        );
        CREATE TABLE sync_state (
            file_path TEXT PRIMARY KEY, last_size INTEGER, last_mtime REAL, indexed_at INTEGER
        );
        CREATE TABLE message_embeddings (
            message_id INTEGER PRIMARY KEY, embedding BLOB NOT NULL, model TEXT
        );
        CREATE TABLE session_titles (
            session_id TEXT PRIMARY KEY, title TEXT, summary TEXT, project_tags TEXT,
            generated_at INTEGER, method TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content_text, role, project, cwd, ts, uuid, session_id, agent,
            content='claude_messages', content_rowid='id'
        );
        """
    )
    return conn


def _summary_db(path: Path, conversation_id: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE conversation_summaries (
           conversation_id TEXT, title TEXT, preview TEXT, last_modified_time TEXT,
           workspace_uris TEXT, status TEXT, project_id TEXT)"""
    )
    conn.execute(
        "INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?)",
        (
            conversation_id, "Index agent transcripts", "AGY implementation session",
            "2026-09-12 06:26:00+00:00",
            '["file:///Users/alisyed/terminalmd"]',
            "CASCADE_RUN_STATUS_IDLE", "default-cli-project",
        ),
    )
    conn.commit()
    conn.close()


def test_current_store_maps_messages_tools_and_metadata_without_thinking(tmp_path):
    native_id = "agy-native-1"
    path = tmp_path / "brain" / native_id / ".system_generated" / "logs" / "transcript_full.jsonl"
    _write_jsonl(path, [
        {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-09-12T01:00:00Z", "content": "Index Codex and AGY"},
        {"step_index": 1, "type": "PLANNER_RESPONSE", "created_at": "2026-09-12T01:00:01Z", "content": "I will inspect the store.", "thinking": "PRIVATE_CHAIN", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": "/tmp/a"}}]},
        {"step_index": 2, "type": "GENERIC", "created_at": "2026-09-12T01:00:02Z", "content": "file contents"},
        {"step_index": 3, "type": "CONVERSATION_HISTORY", "created_at": "2026-09-12T01:00:03Z", "content": "duplicate"},
    ])
    summaries = tmp_path / "summaries.db"
    _summary_db(summaries, native_id)

    session, messages = agy_transcripts._parse_brain(
        path, agy_transcripts._summary_rows(summaries)[native_id]
    )
    assert session.session_id == f"agy:{native_id}"
    assert session.cwd == "/Users/alisyed/terminalmd"
    assert session.project == "terminalmd"
    assert session.status == "idle"
    assert [message.role for message in messages] == ["user", "assistant", "assistant", "tool_result"]
    assert messages[2].tool_name == "view_file"
    assert messages[3].tool_name == "generic"
    assert all("PRIVATE_CHAIN" not in message.content_text for message in messages)
    assert all("duplicate" not in message.content_text for message in messages)


def test_run_is_incremental_replaces_snapshots_and_removes_deleted_files(tmp_path):
    native_id = "agy-native-2"
    brain = tmp_path / "brain"
    path = brain / native_id / ".system_generated" / "logs" / "transcript_full.jsonl"
    rows = [
        {"step_index": 0, "type": "USER_INPUT", "created_at": "2026-09-12T01:00:00Z", "content": "alpha request"},
        {"step_index": 1, "type": "PLANNER_RESPONSE", "created_at": "2026-09-12T01:00:01Z", "content": "beta response"},
    ]
    _write_jsonl(path, rows)
    summaries = tmp_path / "summaries.db"
    _summary_db(summaries, native_id)
    legacy = tmp_path / "legacy"
    conn = _database()

    assert agy_transcripts.run(conn, brain, summaries, legacy) == 2
    assert agy_transcripts.run(conn, brain, summaries, legacy) == 0
    assert conn.execute("SELECT agent FROM claude_sessions").fetchone()[0] == "agy"
    assert conn.execute("SELECT title FROM session_titles").fetchone()[0] == "Index agent transcripts"
    assert conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'alpha'").fetchone()[0] == 1

    message_id = conn.execute("SELECT id FROM claude_messages LIMIT 1").fetchone()[0]
    conn.execute("INSERT INTO message_embeddings VALUES (?,X'00','test')", (message_id,))
    rows[0]["content"] = "gamma request"
    _write_jsonl(path, rows[:1])
    assert agy_transcripts.run(conn, brain, summaries, legacy) >= 1
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM message_embeddings").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'gamma'").fetchone()[0] == 1

    path.unlink()
    assert agy_transcripts.run(conn, brain, summaries, legacy) == 1
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM claude_sessions").fetchone()[0] == 0


def test_legacy_store_keeps_visible_content_and_skips_thoughts(tmp_path):
    legacy = tmp_path / "legacy"
    path = legacy / "project" / "chats" / "session.jsonl"
    _write_jsonl(path, [
        {"sessionId": "legacy-1", "startTime": "2026-05-01T00:00:00Z", "lastUpdated": "2026-05-01T00:01:00Z", "kind": "main", "directories": ["/Users/alisyed/ModelShrink"]},
        {"id": "u1", "timestamp": "2026-05-01T00:00:01Z", "type": "user", "content": [{"text": "compress model"}]},
        {"id": "a1", "timestamp": "2026-05-01T00:00:02Z", "type": "gemini", "content": "visible answer", "thoughts": [{"description": "PRIVATE"}]},
    ])
    conn = _database()
    assert agy_transcripts.run(conn, tmp_path / "brain", tmp_path / "missing.db", legacy) == 2
    values = [row[0] for row in conn.execute("SELECT content_text FROM claude_messages ORDER BY id")]
    assert values == ["compress model", "visible answer"]
    assert all("PRIVATE" not in value for value in values)
