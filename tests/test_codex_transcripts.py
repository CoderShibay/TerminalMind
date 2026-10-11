import json
import sqlite3
from pathlib import Path

from indexer import codex_transcripts


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")


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
            session_id TEXT PRIMARY KEY, method TEXT
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            content_text, role, project, cwd, ts, uuid, session_id,
            content='claude_messages', content_rowid='id'
        );
        """
    )
    return conn


def _sample_entries() -> list[dict]:
    return [
        {
            "ordinal": 0,
            "type": "session_meta",
            "timestamp": "2026-09-12T01:00:00.000Z",
            "payload": {
                "id": "native-123",
                "timestamp": "2026-09-12T01:00:00.000Z",
                "cwd": "/Users/alisyed/Documents/SpotTrader",
                "cli_version": "0.151.0",
                "thread_source": "user",
            },
        },
        {
            "ordinal": 1,
            "type": "response_item",
            "timestamp": "2026-09-12T01:00:01.000Z",
            "payload": {
                "type": "message", "id": "msg-user", "role": "user",
                "content": [{"type": "input_text", "text": "Fix the retry loop"}],
            },
        },
        {
            "ordinal": 2,
            "type": "response_item",
            "timestamp": "2026-09-12T01:00:02.000Z",
            "payload": {
                "type": "message", "id": "msg-assistant", "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "I will inspect it."}],
            },
        },
        {
            "ordinal": 3,
            "type": "response_item",
            "timestamp": "2026-09-12T01:00:03.000Z",
            "payload": {
                "type": "function_call", "call_id": "call-1", "name": "exec_command",
                "arguments": "{\"cmd\":\"pytest\"}",
            },
        },
        {
            "ordinal": 4,
            "type": "response_item",
            "timestamp": "2026-09-12T01:00:04.000Z",
            "payload": {
                "type": "function_call_output", "call_id": "call-1",
                "output": "2 passed",
            },
        },
        {
            "ordinal": 5,
            "type": "response_item",
            "timestamp": "2026-09-12T01:00:05.000Z",
            "payload": {"type": "reasoning", "id": "secret", "encrypted_content": "..."},
        },
        {
            "ordinal": 6,
            "type": "event_msg",
            "timestamp": "2026-09-12T01:00:06.000Z",
            "payload": {"type": "agent_message", "message": "duplicate assistant text"},
        },
        {
            "ordinal": 7,
            "type": "event_msg",
            "timestamp": "2026-09-12T01:00:07.000Z",
            "payload": {"type": "task_complete"},
        },
    ]


def test_parse_preserves_messages_and_tool_traffic(tmp_path):
    path = tmp_path / "sessions" / "rollout.jsonl"
    _write_jsonl(path, _sample_entries())

    session, messages = codex_transcripts.parse_file(path)

    assert session is not None
    assert session.session_id == "codex:native-123"
    assert session.native_session_id == "native-123"
    assert session.project == "SpotTrader"
    assert session.cwd == "/Users/alisyed/Documents/SpotTrader"
    assert session.status == "idle"
    assert session.kind == "codex:user"
    assert [message.role for message in messages] == [
        "user", "assistant", "assistant", "tool_result"
    ]
    assert messages[0].content_text == "Fix the retry loop"
    assert messages[2].tool_name == "exec_command"
    assert messages[2].content_text == '{"cmd":"pytest"}'
    assert messages[3].tool_name == "exec_command"
    assert messages[3].content_text == "2 passed"
    assert all(message.uuid.startswith("codex:") for message in messages)


def test_subagent_uses_own_id_and_skips_copied_parent_history(tmp_path):
    entries = [
        {
            "ordinal": 0, "type": "session_meta", "timestamp": "2026-09-12T02:00:00Z",
            "payload": {
                "id": "child-456", "session_id": "parent-123",
                "cwd": "/Users/alisyed/terminalmd", "thread_source": "subagent",
                "agent_path": "/root/parser", "subagent_history_start_ordinal": 10,
            },
        },
        {
            "ordinal": 1, "type": "session_meta", "timestamp": "2026-09-12T02:00:00Z",
            "payload": {"id": "parent-123", "cwd": "/Users/alisyed"},
        },
        {
            "ordinal": 5, "type": "response_item", "timestamp": "2026-09-12T02:00:00Z",
            "payload": {
                "type": "message", "id": "copied", "role": "assistant",
                "content": [{"type": "output_text", "text": "Copied parent answer"}],
            },
        },
        {
            "ordinal": 11, "type": "response_item", "timestamp": "2026-09-12T02:00:01Z",
            "payload": {
                "type": "agent_message", "id": "incoming", "author": "/root",
                "recipient": "/root/parser",
                "content": [{"type": "input_text", "text": "Message Type: NEW_TASK"}],
            },
        },
        {
            "ordinal": 12, "type": "response_item", "timestamp": "2026-09-12T02:00:02Z",
            "payload": {
                "type": "agent_message", "id": "outgoing", "author": "/root/parser",
                "recipient": "/root",
                "content": [{"type": "input_text", "text": "Done"}],
            },
        },
    ]
    path = tmp_path / "rollout.jsonl"
    _write_jsonl(path, entries)

    session, messages = codex_transcripts.parse_file(path)

    assert session is not None
    assert session.session_id == "codex:child-456"
    assert session.native_session_id == "child-456"
    assert [message.role for message in messages] == ["user", "assistant"]
    assert all("Copied parent answer" not in (message.content_text or "") for message in messages)
    assert all(message.tool_name == "inter_agent_message" for message in messages)


def test_run_is_incremental_and_fts_searchable(tmp_path):
    root = tmp_path / "sessions"
    path = root / "2026" / "09" / "12" / "rollout.jsonl"
    _write_jsonl(path, _sample_entries())
    conn = _database()

    assert codex_transcripts.run(conn, root) == 4
    assert codex_transcripts.run(conn, root) == 0
    assert conn.execute("SELECT count(*) FROM claude_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 4
    assert conn.execute("SELECT DISTINCT agent FROM claude_messages").fetchone()[0] == "codex"
    assert conn.execute(
        "SELECT count(*) FROM messages_fts WHERE messages_fts MATCH 'retry'"
    ).fetchone()[0] == 1

    entries = _sample_entries()
    entries.append({
        "ordinal": 8, "type": "response_item", "timestamp": "2026-09-12T01:00:08Z",
        "payload": {
            "type": "message", "id": "msg-final", "role": "assistant",
            "content": [{"type": "output_text", "text": "The retry loop is fixed."}],
        },
    })
    _write_jsonl(path, entries)
    assert codex_transcripts.run(conn, root) == 1
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 5

    # A rewritten snapshot replaces old rows, including removals, and clears
    # embeddings that belonged to the old physical message IDs.
    message_id = conn.execute(
        "SELECT id FROM claude_messages WHERE uuid LIKE 'codex:%' LIMIT 1"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO message_embeddings VALUES (?, X'00', 'test')", (message_id,)
    )
    _write_jsonl(path, _sample_entries()[:3])
    assert codex_transcripts.run(conn, root) == 3
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM message_embeddings").fetchone()[0] == 0

    path.unlink()
    assert codex_transcripts.run(conn, root) == 2
    assert conn.execute("SELECT count(*) FROM claude_messages").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM claude_sessions").fetchone()[0] == 0


def test_run_requires_agent_schema_columns(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE claude_messages (id INTEGER PRIMARY KEY);
        CREATE TABLE claude_sessions (session_id TEXT PRIMARY KEY);
        """
    )

    try:
        codex_transcripts.run(conn, tmp_path)
    except RuntimeError as error:
        assert "claude_messages.agent" in str(error)
        assert "claude_sessions.native_session_id" in str(error)
    else:
        raise AssertionError("missing migration columns should fail clearly")


def test_replaced_source_file_removes_orphaned_prior_session(tmp_path):
    root = tmp_path / "sessions"
    path = root / "rollout.jsonl"
    entries = _sample_entries()
    _write_jsonl(path, entries)
    conn = _database()
    assert codex_transcripts.run(conn, root) == 4

    entries[0]["payload"]["id"] = "replacement-456"
    _write_jsonl(path, entries)
    assert codex_transcripts.run(conn, root) > 0
    sessions = [row[0] for row in conn.execute("SELECT session_id FROM claude_sessions")]
    assert sessions == ["codex:replacement-456"]
