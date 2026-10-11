"""Index AGY/Antigravity conversations into TerminalMind.

The current AGY client writes one normalized transcript under ``brain`` for
each conversation and stores display metadata in ``conversation_summaries.db``.
Older Gemini CLI JSONL chats remain supported as a compatibility source.
Hidden model thinking and duplicated conversation-history snapshots are never
indexed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


AGY_HOME = Path.home() / ".gemini" / "antigravity-cli"
BRAIN_DIR = AGY_HOME / "brain"
SUMMARIES_DB = AGY_HOME / "conversation_summaries.db"
LEGACY_DIR = Path.home() / ".gemini" / "tmp"

_SKIP_TYPES = {"SYSTEM_MESSAGE", "CONVERSATION_HISTORY", "CHECKPOINT"}
_TOOL_RESULT_TYPES = {
    "GENERIC", "SEARCH_WEB", "VIEW_FILE", "LIST_DIRECTORY", "CODE_ACTION",
    "RUN_COMMAND", "GREP_SEARCH", "ERROR_MESSAGE",
}


@dataclass(frozen=True)
class AgySession:
    native_session_id: str
    cwd: str | None
    project: str | None
    started_at: int | None
    updated_at: int | None
    status: str
    kind: str
    title: str | None = None
    preview: str | None = None

    @property
    def session_id(self) -> str:
        return f"agy:{self.native_session_id}"


@dataclass(frozen=True)
class AgyMessage:
    uuid: str
    session_id: str
    ts: str | None
    role: str
    content_text: str
    tool_name: str | None
    project: str | None
    cwd: str | None
    source_file: str


def _epoch_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _stable_uuid(session_id: str, identity: str) -> str:
    digest = hashlib.sha256(f"{session_id}\0{identity}".encode()).hexdigest()
    return f"agy:{digest}"


def _content_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cwd_from_uris(raw: Any) -> str | None:
    if not raw:
        return None
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        values = [raw]
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list) or not values:
        return None
    value = str(values[0])
    if value.startswith("file:"):
        return unquote(urlparse(value).path)
    return value or None


def _project(cwd: str | None, fallback: str | None = None) -> str | None:
    if cwd:
        name = Path(cwd).name
        if name and name not in {Path.home().name, "Documents", "Users"}:
            return name
    return fallback or None


def _summary_rows(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        rows = {
            str(row["conversation_id"]): dict(row)
            for row in conn.execute("SELECT * FROM conversation_summaries")
        }
        conn.close()
        return rows
    except sqlite3.Error:
        return {}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _parse_brain(path: Path, summary: dict[str, Any] | None = None) -> tuple[AgySession, list[AgyMessage]]:
    rows = _load_jsonl(path)
    native_id = path.parents[2].name
    summary = summary or {}
    cwd = _cwd_from_uris(summary.get("workspace_uris"))
    fallback_project = summary.get("project_id")
    timestamps = [row.get("created_at") for row in rows if row.get("created_at")]
    started = min((_epoch_ms(ts) or 0 for ts in timestamps), default=0) or None
    updated = _epoch_ms(summary.get("last_modified_time"))
    if not updated:
        updated = max((_epoch_ms(ts) or 0 for ts in timestamps), default=0) or started
    raw_status = str(summary.get("status") or "")
    status = "idle" if "IDLE" in raw_status or not raw_status else "active"
    session = AgySession(
        native_session_id=native_id,
        cwd=cwd,
        project=_project(cwd, fallback_project),
        started_at=started,
        updated_at=updated,
        status=status,
        kind="agy:conversation",
        title=(summary.get("title") or None),
        preview=(summary.get("preview") or None),
    )

    messages: list[AgyMessage] = []
    for ordinal, row in enumerate(rows):
        row_type = str(row.get("type") or "")
        if row_type in _SKIP_TYPES:
            continue
        step = row.get("step_index", ordinal)
        ts = row.get("created_at")
        if row_type == "USER_INPUT":
            content = str(row.get("content") or "").strip()
            if content:
                messages.append(AgyMessage(
                    _stable_uuid(native_id, f"{step}:user:{_content_key(content)}"), session.session_id, ts,
                    "user", content, None, session.project, cwd, str(path),
                ))
        elif row_type == "PLANNER_RESPONSE":
            content = str(row.get("content") or "").strip()
            if content:
                messages.append(AgyMessage(
                    _stable_uuid(native_id, f"{step}:assistant:{_content_key(content)}"), session.session_id, ts,
                    "assistant", content, None, session.project, cwd, str(path),
                ))
            calls = row.get("tool_calls") or []
            if isinstance(calls, list):
                for subindex, call in enumerate(calls):
                    if not isinstance(call, dict):
                        continue
                    name = str(call.get("name") or "tool")
                    args = call.get("args", {})
                    call_text = _json_text(args)
                    messages.append(AgyMessage(
                        _stable_uuid(native_id, f"{step}:call:{subindex}:{name}:{_content_key(call_text)}"),
                        session.session_id, ts, "assistant", call_text, name,
                        session.project, cwd, str(path),
                    ))
        elif row_type in _TOOL_RESULT_TYPES:
            content = str(row.get("content") or row.get("error") or "").strip()
            if content:
                messages.append(AgyMessage(
                    _stable_uuid(native_id, f"{step}:result:{row_type}:{_content_key(content)}"), session.session_id, ts,
                    "tool_result", content, row_type.lower(), session.project, cwd, str(path),
                ))
    unique: dict[str, AgyMessage] = {}
    for message in messages:
        unique.setdefault(message.uuid, message)
    return session, list(unique.values())


def _extract_legacy_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return _json_text(value) if value is not None else ""


def _parse_legacy(path: Path) -> tuple[AgySession | None, list[AgyMessage]]:
    rows = _load_jsonl(path)
    if not rows or not rows[0].get("sessionId"):
        return None, []
    header = rows[0]
    native_id = str(header["sessionId"])
    directories = header.get("directories") or []
    cwd = str(directories[0]) if directories else None
    session = AgySession(
        native_id, cwd, _project(cwd), _epoch_ms(header.get("startTime")),
        _epoch_ms(header.get("lastUpdated")), "idle", f"agy:legacy:{header.get('kind') or 'main'}",
    )
    by_id: dict[str, AgyMessage] = {}
    order: list[str] = []
    for ordinal, row in enumerate(rows[1:]):
        row_type = row.get("type")
        if row_type not in {"user", "gemini", "tool"}:
            continue
        native_message_id = str(row.get("id") or f"ordinal:{ordinal}")
        role = "assistant" if row_type == "gemini" else str(row_type)
        content = _extract_legacy_content(row.get("content")).strip()
        if not content:
            continue
        message = AgyMessage(
            _stable_uuid(native_id, native_message_id), session.session_id,
            row.get("timestamp"), role, content,
            str(row.get("toolName") or "legacy_tool") if row_type == "tool" else None,
            session.project, cwd, str(path),
        )
        if native_message_id not in by_id:
            order.append(native_message_id)
        if native_message_id not in by_id or len(content) >= len(by_id[native_message_id].content_text):
            by_id[native_message_id] = message
    return session, [by_id[key] for key in order]


def _unchanged(conn, path: Path, stat: Any) -> bool:
    row = conn.execute(
        "SELECT last_size, last_mtime FROM sync_state WHERE file_path = ?", (str(path),)
    ).fetchone()
    return bool(row and row[0] == stat.st_size and abs(row[1] - stat.st_mtime) < 1)


def _replace_file(conn, path: Path, session: AgySession, messages: list[AgyMessage], stat: Any) -> int:
    ids = [row[0] for row in conn.execute(
        "SELECT id FROM claude_messages WHERE source_file = ? AND agent = 'agy'", (str(path),)
    )]
    old_session_ids = {str(row[0]) for row in conn.execute(
        "SELECT DISTINCT session_id FROM claude_messages WHERE source_file = ? AND agent = 'agy'",
        (str(path),),
    )}
    conn.executemany("DELETE FROM message_embeddings WHERE message_id = ?", [(i,) for i in ids])
    conn.execute("DELETE FROM claude_messages WHERE source_file = ? AND agent = 'agy'", (str(path),))
    conn.execute(
        """INSERT INTO claude_sessions
           (session_id,pid,cwd,project,started_at,updated_at,status,version,kind,agent,native_session_id)
           VALUES (?,NULL,?,?,?,?,?,NULL,?,'agy',?)
           ON CONFLICT(session_id) DO UPDATE SET cwd=excluded.cwd, project=excluded.project,
             started_at=excluded.started_at, updated_at=excluded.updated_at,
             status=excluded.status, kind=excluded.kind, agent='agy',
             native_session_id=excluded.native_session_id""",
        (session.session_id, session.cwd, session.project, session.started_at,
         session.updated_at, session.status, session.kind, session.native_session_id),
    )
    conn.executemany(
        """INSERT INTO claude_messages
           (uuid,session_id,ts,role,content_text,tool_name,project,cwd,source_file,agent)
           VALUES (?,?,?,?,?,?,?,?,?,'agy')""",
        [(m.uuid,m.session_id,m.ts,m.role,m.content_text,m.tool_name,m.project,m.cwd,m.source_file)
         for m in messages],
    )
    for old_session_id in old_session_ids - {session.session_id}:
        if not conn.execute(
            "SELECT 1 FROM claude_messages WHERE session_id = ? LIMIT 1", (old_session_id,)
        ).fetchone():
            conn.execute("DELETE FROM claude_sessions WHERE session_id = ?", (old_session_id,))
            conn.execute("DELETE FROM session_titles WHERE session_id = ?", (old_session_id,))
    if session.title or session.preview:
        conn.execute(
            """INSERT OR IGNORE INTO session_titles
               (session_id,title,summary,project_tags,generated_at,method)
               VALUES (?,?,?,?,?,'agy')""",
            (session.session_id, session.title or session.preview, session.preview,
             session.project, int(time.time() * 1000)),
        )
    conn.execute(
        "INSERT OR REPLACE INTO sync_state VALUES (?,?,?,?)",
        (str(path), stat.st_size, stat.st_mtime, int(time.time() * 1000)),
    )
    conn.commit()
    return len(messages)


def _remove_missing(conn, roots: list[Path], present: set[str]) -> int:
    prefixes = []
    for root in roots:
        if root.exists():
            prefixes.extend((str(root) + "/", str(root.resolve()) + "/"))
    removed = 0
    sources = [str(row[0]) for row in conn.execute(
        "SELECT DISTINCT source_file FROM claude_messages WHERE agent='agy' AND source_file IS NOT NULL"
    )]
    for source in sources:
        if not any(source.startswith(prefix) for prefix in prefixes):
            continue
        if source in present or Path(source).exists():
            continue
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM claude_messages WHERE source_file=? AND agent='agy'", (source,)
        )]
        sessions = [row[0] for row in conn.execute(
            "SELECT DISTINCT session_id FROM claude_messages WHERE source_file=? AND agent='agy'", (source,)
        )]
        conn.executemany("DELETE FROM message_embeddings WHERE message_id=?", [(i,) for i in ids])
        conn.execute("DELETE FROM claude_messages WHERE source_file=? AND agent='agy'", (source,))
        conn.execute("DELETE FROM sync_state WHERE file_path=?", (source,))
        for session_id in sessions:
            if not conn.execute("SELECT 1 FROM claude_messages WHERE session_id=? LIMIT 1", (session_id,)).fetchone():
                conn.execute("DELETE FROM claude_sessions WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM session_titles WHERE session_id=?", (session_id,))
        removed += len(ids)
    return removed


def run(conn, brain_dir: Path | None = None, summaries_db: Path | None = None,
        legacy_dir: Path | None = None) -> int:
    """Index changed current and legacy AGY transcript snapshots."""
    brain = brain_dir or BRAIN_DIR
    summaries = _summary_rows(summaries_db or SUMMARIES_DB)
    legacy = legacy_dir or LEGACY_DIR
    brain_paths = sorted(brain.glob("*/.system_generated/logs/transcript_full.jsonl")) if brain.exists() else []
    legacy_paths = sorted(legacy.glob("**/chats/*.jsonl")) if legacy.exists() else []
    paths = brain_paths + legacy_paths
    present = {str(path) for path in paths}
    total = _remove_missing(conn, [brain, legacy], present)
    for path in brain_paths:
        stat = path.stat()
        if _unchanged(conn, path, stat):
            continue
        session, messages = _parse_brain(path, summaries.get(path.parents[2].name))
        total += _replace_file(conn, path, session, messages, stat)
    for path in legacy_paths:
        stat = path.stat()
        if _unchanged(conn, path, stat):
            continue
        session, messages = _parse_legacy(path)
        if session:
            total += _replace_file(conn, path, session, messages, stat)
    if total:
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
    return total
