import sqlite3

import server
from indexer import file_hotspots


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE claude_messages (
            id INTEGER PRIMARY KEY, session_id TEXT, ts TEXT, role TEXT,
            content_text TEXT, tool_name TEXT, project TEXT, cwd TEXT,
            agent TEXT NOT NULL
        );
        CREATE TABLE claude_sessions (
            session_id TEXT PRIMARY KEY, agent TEXT NOT NULL
        );
        CREATE TABLE file_touches (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, file_path TEXT NOT NULL,
            tool_name TEXT, ts TEXT, project TEXT,
            UNIQUE(session_id, file_path, tool_name, ts)
        );
        """
    )
    return conn


def test_agent_file_path_extraction_supports_codex_and_agy():
    assert file_hotspots._agent_message_paths(
        "apply_patch",
        "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch",
        "/Users/alisyed/terminalmd",
    ) == [("/Users/alisyed/terminalmd/src/app.py", "Edit")]
    assert file_hotspots._agent_message_paths(
        "view_file", '{"AbsolutePath":"/Users/alisyed/terminalmd/server.py"}', None
    ) == [("/Users/alisyed/terminalmd/server.py", "Read")]
    assert file_hotspots._agent_message_paths(
        "run_command",
        '{"CommandLine":"python3 /Users/alisyed/terminalmd/main.py"}',
        "/Users/alisyed",
    ) == [("/Users/alisyed/terminalmd/main.py", "Bash")]
    assert file_hotspots._agent_message_paths(
        "exec_command", '{"cmd":"ls /Users/alisyed/terminalmd"}', "/Users/alisyed"
    ) == []


def test_tools_count_calls_once_and_normalise_agy_mcp(monkeypatch):
    conn = _database()
    conn.executemany(
        "INSERT INTO claude_messages VALUES (NULL,?,?,?,?,?,?,?,?)",
        [
            ("claude:1", "2026-09-12T01:00:00Z", "assistant", None, "Read", "tm", "/tmp", "claude"),
            ("codex:1", "2026-09-12T01:00:01Z", "assistant", '{"cmd":"pwd"}', "exec_command", "tm", "/tmp", "codex"),
            ("codex:1", "2026-09-12T01:00:02Z", "tool_result", "ok", "exec_command", "tm", "/tmp", "codex"),
            ("agy:1", "2026-09-12T01:00:03Z", "assistant",
             '{"ServerName":"playwright","ToolName":"browser_navigate"}',
             "call_mcp_tool", "tm", "/tmp", "agy"),
        ],
    )
    monkeypatch.setattr(server, "get_conn", lambda: conn)

    result = server.api_tools()

    assert result["total_calls"] == 3
    assert result["agent_counts"] == {"claude": 1, "codex": 1, "agy": 1}
    assert {row["tool_name"] for row in result["tools"]} == {
        "Read", "exec_command", "mcp__playwright__browser_navigate"
    }
    assert server.api_tools(agent="codex")["total_calls"] == 1


def test_errors_keep_full_namespaced_session_ids_and_agents(monkeypatch):
    conn = _database()
    error = "FileNotFoundError: No such file or directory '/tmp/missing.txt'"
    conn.executemany(
        "INSERT INTO claude_messages VALUES (NULL,?,?,?,?,?,?,?,?)",
        [
            ("codex:019aaaa", "2026-09-12T01:00:00Z", "tool_result", error,
             "exec_command", "tm", "/tmp", "codex"),
            ("codex:019bbbb", "2026-09-12T01:01:00Z", "tool_result", error,
             "exec_command", "tm", "/tmp", "codex"),
            ("agy:abc", "2026-09-12T01:02:00Z", "tool_result", error,
             "error_message", "tm", "/tmp", "agy"),
        ],
    )
    monkeypatch.setattr(server, "get_conn", lambda: conn)

    result = server.api_errors()

    assert result["total"] == 1
    assert result["errors"][0]["sessions"] == 3
    assert result["errors"][0]["agents"] == ["agy", "codex"]
    assert server.api_errors(agent="codex")["errors"][0]["sessions"] == 2


def test_files_report_and_filter_agent_provenance(monkeypatch):
    conn = _database()
    conn.executemany(
        "INSERT INTO claude_sessions VALUES (?,?)",
        [("claude:1", "claude"), ("codex:1", "codex"), ("agy:1", "agy")],
    )
    conn.executemany(
        "INSERT INTO file_touches VALUES (NULL,?,?,?,?,?)",
        [
            ("claude:1", "/Users/alisyed/shared.py", "Read", "2026-09-12T01:00:00Z", "tm"),
            ("codex:1", "/Users/alisyed/shared.py", "Edit", "2026-09-12T01:01:00Z", "tm"),
            ("agy:1", "/Users/alisyed/agy.py", "Write", "2026-09-12T01:02:00Z", "tm"),
        ],
    )
    monkeypatch.setattr(server, "get_conn", lambda: conn)

    result = server.api_files()

    assert result["total_files"] == 2
    shared = next(row for row in result["files"] if row["file_path"].endswith("shared.py"))
    assert shared["agents"] == ["claude", "codex"]
    assert server.api_files(agent="agy")["total_files"] == 1
