"""Index file touch events from Claude, Codex, and AGY tool calls."""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import claude_dir

PROJECTS_DIR = claude_dir() / "projects"

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}

AGENT_FILE_TOOLS = {
    "view_file": ("Read", ("AbsolutePath", "file_path", "path")),
    "write_to_file": ("Write", ("TargetFile", "file_path", "path")),
    "replace_file_content": ("Edit", ("TargetFile", "file_path", "path")),
    "apply_patch": ("Edit", ()),
}
COMMAND_TOOLS = {"exec", "exec_command", "run_command"}

# Matches absolute paths starting with /Users, /home, /root, /tmp, /opt, or ~/
_PATH_RE = re.compile(
    r'(?:^|[\s\'"=`(])((?:/(?:Users|home|root|tmp|opt|var|etc)|~)/[^\s\'"\\;|&<>*?\[\]{}()]+)'
)
_PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$", re.MULTILINE)
_SKIP_PARTS = {"Users", "home", Path.home().name, ""}


def _project_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    name = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return name if name and name not in _SKIP_PARTS else None


def _extract_bash_paths(command: str) -> list[str]:
    paths = []
    for m in _PATH_RE.finditer(command):
        p = m.group(1).strip().rstrip("/.,)`")
        # Skip very short paths and URLs
        if len(p) > 8 and "://" not in p:
            paths.append(p)
    return paths


def _json_object(text: str | None) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _absolute_path(value: str, cwd: str | None) -> str | None:
    value = value.strip().strip("'\"")
    if not value or "://" in value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        if not cwd:
            return None
        path = Path(cwd) / path
    if path.exists() and path.is_dir():
        return None
    return str(path)


def _agent_message_paths(tool_name: str, content: str | None, cwd: str | None) -> list[tuple[str, str]]:
    """Return ``(path, display_tool)`` pairs for one normalized tool call."""
    args = _json_object(content)
    rows: list[tuple[str, str]] = []

    if tool_name in AGENT_FILE_TOOLS:
        display_tool, keys = AGENT_FILE_TOOLS[tool_name]
        for key in keys:
            value = args.get(key)
            if isinstance(value, str) and (path := _absolute_path(value, cwd)):
                rows.append((path, display_tool))
                break
        if tool_name == "apply_patch":
            for value in _PATCH_PATH_RE.findall(content or ""):
                if path := _absolute_path(value, cwd):
                    rows.append((path, display_tool))

    if tool_name in COMMAND_TOOLS:
        command = args.get("cmd") or args.get("CommandLine") or content or ""
        if isinstance(command, str):
            for value in _extract_bash_paths(command):
                if path := _absolute_path(value, cwd):
                    rows.append((path, "Bash"))

    return list(dict.fromkeys(rows))


def _index_file(conn, jsonl_path: Path) -> int:
    stat = jsonl_path.stat()
    state_key = "ft:" + str(jsonl_path)
    cur = conn.execute(
        "SELECT last_size, last_mtime FROM sync_state WHERE file_path = ?",
        (state_key,),
    ).fetchone()
    if cur and cur["last_size"] == stat.st_size and abs(cur["last_mtime"] - stat.st_mtime) < 1:
        return 0

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

            if entry.get("type") != "assistant":
                continue

            session_id = entry.get("sessionId", "")
            ts = entry.get("timestamp", "")
            cwd = entry.get("cwd")
            project = _project_from_cwd(cwd)
            content = entry.get("message", {}).get("content", [])

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                inp = block.get("input") or {}

                if name in FILE_TOOLS:
                    fp = inp.get("file_path") or inp.get("path", "")
                    if fp and isinstance(fp, str):
                        rows.append((session_id, fp, name, ts, project))

                elif name == "Bash":
                    cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                    for path in _extract_bash_paths(cmd):
                        rows.append((session_id, path, "Bash", ts, project))

    if rows:
        conn.executemany(
            """INSERT OR IGNORE INTO file_touches (session_id, file_path, tool_name, ts, project)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )

    conn.execute(
        """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
           VALUES (?, ?, ?, ?)""",
        (state_key, stat.st_size, stat.st_mtime, int(time.time() * 1000)),
    )
    conn.commit()
    return len(rows)


def _index_agent_messages(conn) -> int:
    """Rebuild derived touches for Codex and AGY from their indexed calls."""
    conn.execute(
        "DELETE FROM file_touches WHERE session_id LIKE 'codex:%' OR session_id LIKE 'agy:%'"
    )
    rows = []
    messages = conn.execute(
        """SELECT session_id, ts, tool_name, content_text, cwd, project
           FROM claude_messages
           WHERE agent IN ('codex', 'agy') AND role = 'assistant'
             AND tool_name IS NOT NULL"""
    ).fetchall()
    for message in messages:
        for path, display_tool in _agent_message_paths(
            message["tool_name"], message["content_text"], message["cwd"]
        ):
            rows.append((
                message["session_id"], path, display_tool, message["ts"], message["project"]
            ))
    if rows:
        conn.executemany(
            """INSERT OR IGNORE INTO file_touches
               (session_id, file_path, tool_name, ts, project)
               VALUES (?, ?, ?, ?, ?)""",
            rows,
        )
    conn.commit()
    return len(rows)


def _remove_current_directories(conn) -> int:
    """Drop command arguments that resolve to existing directories, not files."""
    ids = []
    for row in conn.execute("SELECT id, file_path FROM file_touches"):
        path = Path(row["file_path"]).expanduser()
        if path.exists() and path.is_dir():
            ids.append((row["id"],))
    if ids:
        conn.executemany("DELETE FROM file_touches WHERE id = ?", ids)
        conn.commit()
    return len(ids)


def run(conn) -> int:
    total = 0
    if PROJECTS_DIR.exists():
        for jsonl_path in PROJECTS_DIR.rglob("*.jsonl"):
            total += _index_file(conn, jsonl_path)
    total += _index_agent_messages(conn)
    _remove_current_directories(conn)
    return total
