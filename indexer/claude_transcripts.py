"""Index ~/.claude/projects/**/*.jsonl → claude_messages table."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import claude_dir

PROJECTS_DIR = claude_dir() / "projects"

_SKIP_PARTS = {"Users", "home", "Documents", Path.home().name, ""}

SKIP_TYPES = {
    "mode", "permission-mode", "file-history-snapshot",
    "attachment", "tool_result", "summary",
}


def _extract_text(content) -> str:
    """Pull plain text out of a content field (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", "").strip())
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str):
                        parts.append(inner.strip())
                    elif isinstance(inner, list):
                        for b in inner:
                            if isinstance(b, dict) and b.get("type") == "text":
                                parts.append(b.get("text", "").strip())
        return "\n".join(p for p in parts if p)
    return ""


def _project_from_cwd(cwd: str | None) -> str | None:
    """Derive project name from the session's working directory.

    Works on Mac, Linux, and Windows paths — normalises to forward slashes
    before splitting so D:\\Work\\MyApp and /Users/alice/MyApp both yield 'MyApp'.
    """
    if not cwd:
        return None
    name = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return name if name and name not in _SKIP_PARTS else None


def _project_from_path(file_path: Path) -> str | None:
    """Fallback: derive project name from the dash-encoded transcript folder name.

    Claude Code encodes the session cwd as a dash-separated folder name
    (e.g. -Users-alice-Documents-SpotTrader). This works on Mac/Linux but
    breaks on Windows paths (drive letters, spaces, underscores all map to dash).
    Used only when cwd is absent from the transcript entries.
    """
    folder = file_path.parent.name
    decoded = folder.replace("-", "/").lstrip("/")
    parts = decoded.split("/")
    for part in reversed(parts):
        if part and part not in _SKIP_PARTS:
            return part
    return None


def _index_file(conn, jsonl_path: Path) -> int:
    stat = jsonl_path.stat()
    cur = conn.execute(
        "SELECT last_size, last_mtime FROM sync_state WHERE file_path = ?",
        (str(jsonl_path),),
    ).fetchone()

    if cur and cur["last_size"] == stat.st_size and abs(cur["last_mtime"] - stat.st_mtime) < 1:
        return 0

    # Fallback project label from the folder name encoding (Mac/Linux only — breaks on Windows)
    path_project = _project_from_path(jsonl_path)
    rows = []

    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            if entry_type in SKIP_TYPES or entry_type not in ("user", "assistant"):
                continue

            uuid = entry.get("uuid")
            ts = entry.get("timestamp")
            cwd = entry.get("cwd")
            session_id = entry.get("sessionId", "")
            message = entry.get("message", {})
            content = message.get("content", "")

            # Prefer cwd-derived project (works on all platforms) over path-decoded fallback
            project = _project_from_cwd(cwd) or path_project

            if entry_type == "user":
                # Detect tool result messages — they arrive as "user" role in the API
                # but are actually the tool responding, not the human typing.
                if isinstance(content, list):
                    block_types = {b.get("type") for b in content if isinstance(b, dict)}
                    if block_types == {"tool_result"}:
                        text = _extract_text(content)
                        if text:
                            rows.append((uuid, session_id, ts, "tool_result", text, None, project, cwd, str(jsonl_path)))
                        continue
                text = _extract_text(content)
                if text:
                    rows.append((uuid, session_id, ts, "user", text, None, project, cwd, str(jsonl_path)))

            elif entry_type == "assistant":
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        btype = block.get("type")
                        if btype == "text":
                            text = block.get("text", "").strip()
                            if text:
                                rows.append((uuid, session_id, ts, "assistant", text, None, project, cwd, str(jsonl_path)))
                        elif btype == "tool_use":
                            rows.append((uuid, session_id, ts, "assistant", None, block.get("name"), project, cwd, str(jsonl_path)))

    if not rows:
        conn.execute(
            """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
               VALUES (?, ?, ?, ?)""",
            (str(jsonl_path), stat.st_size, stat.st_mtime, int(time.time() * 1000)),
        )
        conn.commit()
        return 0

    conn.executemany(
        """INSERT OR IGNORE INTO claude_messages
           (uuid, session_id, ts, role, content_text, tool_name, project, cwd, source_file)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.execute(
        """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
           VALUES (?, ?, ?, ?)""",
        (str(jsonl_path), stat.st_size, stat.st_mtime, int(time.time() * 1000)),
    )
    conn.commit()
    return len(rows)


def run(conn) -> int:
    if not PROJECTS_DIR.exists():
        return 0

    total = 0
    for jsonl_path in PROJECTS_DIR.rglob("*.jsonl"):
        total += _index_file(conn, jsonl_path)

    if total > 0:
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()

    # Enrich messages with project from prompts (prompts have full project path)
    _enrich_projects(conn)

    return total


def _project_label(full_path: str | None) -> str | None:
    if not full_path:
        return None
    parts = full_path.rstrip("/").split("/")
    for part in reversed(parts):
        if part and part not in _SKIP_PARTS:
            return part
    return None


def _enrich_projects(conn) -> None:
    """Match transcript sessions to history.jsonl projects by timestamp overlap.

    history.jsonl uses different session UUIDs than transcript files, so we
    can't join on session_id. Instead, for each transcript session we find the
    first and last message timestamps, then pick the history.jsonl project whose
    prompts fall closest to that window.
    """
    # All history prompts that have a real project path (not just home dir)
    history_prompts = conn.execute(
        """SELECT session_id, project, ts
           FROM claude_prompts
           WHERE project IS NOT NULL AND project != ?
           ORDER BY ts ASC""",
        (str(Path.home()),)
    ).fetchall()

    if not history_prompts:
        return

    # For each transcript session, get its time window
    sessions = conn.execute(
        """SELECT session_id,
                  MIN(ts) as first_ts,
                  MAX(ts) as last_ts
           FROM claude_messages
           GROUP BY session_id"""
    ).fetchall()

    import datetime as dt

    def iso_to_ms(ts_str: str) -> int:
        try:
            ts_str = ts_str.replace("Z", "+00:00")
            d = dt.datetime.fromisoformat(ts_str)
            return int(d.timestamp() * 1000)
        except Exception:
            return 0

    # Build list of (ts_ms, project) from history prompts
    history_pts = [(row["ts"], _project_label(row["project"]), row["project"])
                   for row in history_prompts if row["ts"]]

    updates = []
    for sess in sessions:
        sid = sess["session_id"]
        first_ms = iso_to_ms(sess["first_ts"]) if sess["first_ts"] else 0
        last_ms  = iso_to_ms(sess["last_ts"])  if sess["last_ts"]  else 0
        if not first_ms:
            continue

        # Find history prompts that fall within or close to this session's window
        # Allow 6-hour lookahead for short sessions
        window_end = max(last_ms, first_ms + 6 * 3600 * 1000)

        best_label = None
        best_path  = None
        best_diff  = float("inf")

        for h_ts, h_label, h_path in history_pts:
            if not h_label:
                continue
            diff = abs(h_ts - first_ms)
            # Prefer prompts inside the window
            if first_ms <= h_ts <= window_end:
                diff = 0  # exact match
            if diff < best_diff:
                best_diff = diff
                best_label = h_label
                best_path  = h_path

        # Only apply if the match is within 24 hours
        if best_label and best_diff < 24 * 3600 * 1000:
            updates.append((best_label, best_path, sid))

    if updates:
        conn.executemany(
            "UPDATE claude_messages SET project = ?, cwd = ? WHERE session_id = ?",
            updates,
        )
        conn.executemany(
            "UPDATE claude_sessions SET project = ?, cwd = ? WHERE session_id = ?",
            updates,
        )
        conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
        conn.commit()
