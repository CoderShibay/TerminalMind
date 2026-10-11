"""Index Codex rollout JSONL transcripts into TerminalMind's message tables.

Codex writes authoritative conversation items below ``~/.codex/sessions``.
The same content is often mirrored as ``event_msg`` records, and subagent
rollouts begin with a compacted copy of their parent's history.  This importer
therefore reads only ``response_item`` records and honors the subagent history
boundary recorded in the first ``session_meta`` entry.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).parent.parent))


CODEX_DIR = Path.home() / ".codex"
SESSIONS_DIR = CODEX_DIR / "sessions"

_SKIP_PROJECT_PARTS = {"", "Users", "home", "Documents", Path.home().name}
_TEXT_BLOCK_TYPES = {"input_text", "output_text", "text"}


@dataclass(frozen=True)
class CodexSession:
    native_session_id: str
    cwd: str | None
    project: str | None
    started_at: int | None
    updated_at: int | None
    status: str
    version: str | None
    kind: str
    agent_path: str | None
    history_start_ordinal: int

    @property
    def session_id(self) -> str:
        return f"codex:{self.native_session_id}"


@dataclass(frozen=True)
class CodexMessage:
    uuid: str
    session_id: str
    ts: str | None
    role: str
    content_text: str | None
    tool_name: str | None
    project: str | None
    cwd: str | None
    source_file: str


def _project_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    name = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return name if name not in _SKIP_PROJECT_PARTS else None


def _iso_to_epoch_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return int(parsed.timestamp() * 1000)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _extract_content(content: Any) -> str:
    """Extract readable blocks while retaining structured non-text content."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") in _TEXT_BLOCK_TYPES and isinstance(content.get("text"), str):
            return content["text"]
        if content.get("type") == "encrypted_content":
            return ""
        return _json_text(content)
    if not isinstance(content, list):
        return _json_text(content) if content is not None else ""

    parts: list[str] = []
    for block in content:
        text = _extract_content(block)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _stable_uuid(native_session_id: str, item_type: str, native_id: str) -> str:
    identity = f"{native_session_id}\0{item_type}\0{native_id}".encode("utf-8")
    return f"codex:{hashlib.sha256(identity).hexdigest()}"


def _load_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                entries.append(value)
    return entries


def _session_from_entries(entries: list[dict[str, Any]]) -> CodexSession | None:
    meta_entry = next((entry for entry in entries if entry.get("type") == "session_meta"), None)
    if not meta_entry:
        return None

    meta = meta_entry.get("payload") or {}
    native_session_id = meta.get("id") or meta.get("session_id")
    if not native_session_id:
        return None

    cwd = meta.get("cwd")
    timestamps = [
        value
        for entry in entries
        if isinstance((value := entry.get("timestamp")), str)
    ]
    started_at = _iso_to_epoch_ms(meta.get("timestamp") or meta_entry.get("timestamp"))
    updated_at = max((_iso_to_epoch_ms(value) or 0 for value in timestamps), default=0) or started_at

    # A rollout file remains reusable after each completed turn.  "idle" is
    # more accurate than "ended" unless a later task has started and is live.
    last_lifecycle = None
    for entry in entries:
        if entry.get("type") != "event_msg":
            continue
        event_type = (entry.get("payload") or {}).get("type")
        if event_type in {"task_started", "task_complete", "turn_aborted"}:
            last_lifecycle = event_type
    status = "active" if last_lifecycle == "task_started" else "idle"

    thread_source = meta.get("thread_source")
    if not thread_source:
        source = meta.get("source")
        thread_source = "subagent" if isinstance(source, dict) and "subagent" in source else str(source or "user")

    start_ordinal = meta.get("subagent_history_start_ordinal")
    if not isinstance(start_ordinal, int):
        start_ordinal = 0

    return CodexSession(
        native_session_id=str(native_session_id),
        cwd=cwd,
        project=_project_from_cwd(cwd),
        started_at=started_at,
        updated_at=updated_at,
        status=status,
        version=meta.get("cli_version"),
        kind=f"codex:{thread_source}",
        agent_path=meta.get("agent_path"),
        history_start_ordinal=start_ordinal,
    )


def _native_item_id(payload: dict[str, Any], ordinal: int) -> str:
    return str(payload.get("id") or payload.get("call_id") or f"ordinal:{ordinal}")


def _tool_name_for_call(payload: dict[str, Any]) -> str:
    item_type = payload.get("type")
    if item_type == "web_search_call":
        return "web_search"
    if item_type == "tool_search_call":
        return "tool_search"
    return str(payload.get("name") or item_type or "tool")


def _tool_call_text(payload: dict[str, Any]) -> str:
    item_type = payload.get("type")
    if item_type == "function_call":
        return _extract_content(payload.get("arguments"))
    if item_type == "custom_tool_call":
        return _extract_content(payload.get("input"))
    if item_type == "tool_search_call":
        return _extract_content(payload.get("arguments"))
    if item_type == "web_search_call":
        return _json_text(payload.get("action") or {})
    return _json_text(payload)


def _tool_result_text(payload: dict[str, Any]) -> str:
    if payload.get("type") == "tool_search_output":
        return _json_text(payload.get("tools") or [])
    return _extract_content(payload.get("output"))


def _messages_from_entries(
    entries: list[dict[str, Any]], session: CodexSession, source_file: str
) -> list[CodexMessage]:
    call_names: dict[str, str] = {}
    for entry in entries:
        if entry.get("type") != "response_item":
            continue
        payload = entry.get("payload") or {}
        if payload.get("type") in {
            "function_call", "custom_tool_call", "web_search_call", "tool_search_call"
        } and payload.get("call_id"):
            call_names[str(payload["call_id"])] = _tool_name_for_call(payload)

    messages: list[CodexMessage] = []
    seen: set[str] = set()
    for fallback_ordinal, entry in enumerate(entries):
        ordinal = entry.get("ordinal", fallback_ordinal)
        if not isinstance(ordinal, int) or ordinal < session.history_start_ordinal:
            continue
        if entry.get("type") != "response_item":
            continue

        payload = entry.get("payload") or {}
        item_type = payload.get("type")
        role: str | None = None
        content_text: str | None = None
        tool_name: str | None = None

        if item_type == "message" and payload.get("role") in {"user", "assistant"}:
            role = str(payload["role"])
            content_text = _extract_content(payload.get("content"))
        elif item_type in {"function_call", "custom_tool_call", "web_search_call", "tool_search_call"}:
            role = "assistant"
            tool_name = _tool_name_for_call(payload)
            content_text = _tool_call_text(payload)
        elif item_type in {"function_call_output", "custom_tool_call_output", "tool_search_output"}:
            role = "tool_result"
            call_id = str(payload.get("call_id") or "")
            tool_name = call_names.get(call_id)
            content_text = _tool_result_text(payload)
        elif item_type == "agent_message":
            author = payload.get("author")
            recipient = payload.get("recipient")
            role = "assistant" if session.agent_path and author == session.agent_path else "user"
            tool_name = "inter_agent_message"
            header = _extract_content(payload.get("content"))
            route = f"{author or '?'} -> {recipient or '?'}"
            content_text = f"{route}\n{header}" if header else route
        else:
            # Reasoning is encrypted, while event messages duplicate response
            # items.  Neither belongs in the searchable transcript.
            continue

        if not content_text and not tool_name:
            continue
        native_id = _native_item_id(payload, ordinal)
        uuid = _stable_uuid(session.native_session_id, str(item_type), native_id)
        if uuid in seen:
            continue
        seen.add(uuid)
        messages.append(CodexMessage(
            uuid=uuid,
            session_id=session.session_id,
            ts=entry.get("timestamp"),
            role=role,
            content_text=content_text or None,
            tool_name=tool_name,
            project=session.project,
            cwd=session.cwd,
            source_file=source_file,
        ))
    return messages


def parse_file(path: Path) -> tuple[CodexSession | None, list[CodexMessage]]:
    """Parse one rollout file without touching a database."""
    entries = _load_entries(path)
    session = _session_from_entries(entries)
    if not session:
        return None, []
    return session, _messages_from_entries(entries, session, str(path))


def _require_agent_columns(conn) -> None:
    message_columns = {row[1] for row in conn.execute("PRAGMA table_info(claude_messages)")}
    session_columns = {row[1] for row in conn.execute("PRAGMA table_info(claude_sessions)")}
    missing = []
    if "agent" not in message_columns:
        missing.append("claude_messages.agent")
    for column in ("agent", "native_session_id"):
        if column not in session_columns:
            missing.append(f"claude_sessions.{column}")
    if missing:
        raise RuntimeError("Codex ingestion requires schema migration columns: " + ", ".join(missing))


def _unchanged(conn, path: Path, stat) -> bool:
    row = conn.execute(
        "SELECT last_size, last_mtime FROM sync_state WHERE file_path = ?", (str(path),)
    ).fetchone()
    return bool(
        row
        and row["last_size"] == stat.st_size
        and abs(row["last_mtime"] - stat.st_mtime) < 1
    )


def _mark_indexed(conn, path: Path, stat) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
           VALUES (?, ?, ?, ?)""",
        (str(path), stat.st_size, stat.st_mtime, int(time.time() * 1000)),
    )


def _index_file(conn, path: Path) -> int:
    # Snapshot metadata before reading. If an active rollout grows during the
    # read, the next sync sees a size/mtime mismatch and safely replays it;
    # stable message UUIDs make that replay idempotent.
    stat = path.stat()
    if _unchanged(conn, path, stat):
        return 0
    session, messages = parse_file(path)
    if not session:
        _mark_indexed(conn, path, stat)
        conn.commit()
        return 0

    existing_rows = list(conn.execute(
            "SELECT id, uuid, role, content_text, tool_name, project, cwd, ts "
            "FROM claude_messages WHERE source_file = ? AND agent = 'codex'",
            (str(path),),
        ))
    existing_ids = [row[0] for row in existing_rows]
    old_session_ids = {str(row[0]) for row in conn.execute(
        "SELECT DISTINCT session_id FROM claude_messages "
        "WHERE source_file = ? AND agent = 'codex'",
        (str(path),),
    )}
    old_values = {
        str(row[1]): tuple(row[2:])
        for row in existing_rows
    }
    new_values = {
        message.uuid: (
            message.role, message.content_text, message.tool_name,
            message.project, message.cwd, message.ts,
        )
        for message in messages
    }
    changed = sum(old_values.get(key) != value for key, value in new_values.items())
    changed += sum(key not in new_values for key in old_values)
    if existing_ids:
        conn.executemany(
            "DELETE FROM message_embeddings WHERE message_id = ?",
            [(message_id,) for message_id in existing_ids],
        )
        conn.execute(
            "DELETE FROM claude_messages WHERE source_file = ? AND agent = 'codex'",
            (str(path),),
        )

    conn.execute(
        """INSERT INTO claude_sessions
           (session_id, pid, cwd, project, started_at, updated_at, status, version, kind,
            agent, native_session_id)
           VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, 'codex', ?)
           ON CONFLICT(session_id) DO UPDATE SET
             cwd=excluded.cwd, project=excluded.project, started_at=excluded.started_at,
             updated_at=excluded.updated_at, status=excluded.status, version=excluded.version,
             kind=excluded.kind, agent=excluded.agent,
             native_session_id=excluded.native_session_id""",
        (
            session.session_id, session.cwd, session.project, session.started_at,
            session.updated_at, session.status, session.version, session.kind,
            session.native_session_id,
        ),
    )

    conn.executemany(
        """INSERT INTO claude_messages
           (uuid, session_id, ts, role, content_text, tool_name, project, cwd, source_file, agent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'codex')""",
        [
            (
                message.uuid, message.session_id, message.ts, message.role,
                message.content_text, message.tool_name, message.project,
                message.cwd, message.source_file,
            )
            for message in messages
        ],
    )
    for old_session_id in old_session_ids - {session.session_id}:
        if not conn.execute(
            "SELECT 1 FROM claude_messages WHERE session_id = ? LIMIT 1",
            (old_session_id,),
        ).fetchone():
            conn.execute("DELETE FROM claude_sessions WHERE session_id = ?", (old_session_id,))
            conn.execute("DELETE FROM session_titles WHERE session_id = ?", (old_session_id,))
    _mark_indexed(conn, path, stat)
    conn.commit()
    return changed


def _remove_missing_files(conn, root: Path, present: set[str]) -> int:
    root_prefix = str(root.resolve()) + "/"
    sources = [
        str(row[0]) for row in conn.execute(
            "SELECT DISTINCT source_file FROM claude_messages "
            "WHERE agent = 'codex' AND source_file IS NOT NULL"
        )
        if str(row[0]).startswith(root_prefix)
    ]
    removed = 0
    for source in sources:
        if source in present or Path(source).exists():
            continue
        ids = [
            row[0] for row in conn.execute(
                "SELECT id FROM claude_messages WHERE source_file = ? AND agent = 'codex'",
                (source,),
            )
        ]
        conn.executemany(
            "DELETE FROM message_embeddings WHERE message_id = ?",
            [(message_id,) for message_id in ids],
        )
        session_ids = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT session_id FROM claude_messages "
                "WHERE source_file = ? AND agent = 'codex'",
                (source,),
            )
        ]
        conn.execute(
            "DELETE FROM claude_messages WHERE source_file = ? AND agent = 'codex'",
            (source,),
        )
        conn.execute("DELETE FROM sync_state WHERE file_path = ?", (source,))
        for session_id in session_ids:
            remaining = conn.execute(
                "SELECT 1 FROM claude_messages WHERE session_id = ? LIMIT 1", (session_id,)
            ).fetchone()
            if not remaining:
                conn.execute("DELETE FROM claude_sessions WHERE session_id = ?", (session_id,))
                conn.execute("DELETE FROM session_titles WHERE session_id = ?", (session_id,))
        removed += len(ids)
    return removed


def run(conn, sessions_dir: Path | None = None) -> int:
    """Index changed Codex rollout files and rebuild the shared message FTS."""
    _require_agent_columns(conn)
    root = sessions_dir or SESSIONS_DIR
    if not root.exists():
        return 0

    paths = sorted(root.rglob("*.jsonl"))
    present = {str(path) for path in paths}
    total = _remove_missing_files(conn, root, present)
    for path in paths:
        total += _index_file(conn, path)
    if total:
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
    return total
