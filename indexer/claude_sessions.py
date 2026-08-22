"""Index ~/.claude/sessions/*.json → claude_sessions table."""
import json
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".claude" / "sessions"


def _project_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    parts = cwd.rstrip("/").split("/")
    for part in reversed(parts):
        if part and part not in ("Users", "alisyed"):
            return part
    return None


def run(conn) -> int:
    if not SESSIONS_DIR.exists():
        return 0

    rows = []
    for json_path in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        session_id = data.get("sessionId", "")
        if not session_id:
            continue

        cwd = data.get("cwd")
        rows.append((
            session_id,
            data.get("pid"),
            cwd,
            _project_from_cwd(cwd),
            data.get("startedAt"),
            data.get("updatedAt"),
            data.get("status", "ended"),
            data.get("version"),
            data.get("kind"),
        ))

    if not rows:
        return 0

    conn.executemany(
        """INSERT OR REPLACE INTO claude_sessions
           (session_id, pid, cwd, project, started_at, updated_at, status, version, kind)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )

    # Enrich session project from prompts (more accurate than cwd)
    prompt_projects = conn.execute(
        "SELECT DISTINCT session_id, project FROM claude_prompts WHERE project IS NOT NULL"
    ).fetchall()
    for row in prompt_projects:
        label = _project_from_cwd(row["project"])
        if label and label != "alisyed":
            conn.execute(
                "UPDATE claude_sessions SET project = ?, cwd = ? WHERE session_id = ?",
                (label, row["project"], row["session_id"]),
            )

    conn.commit()
    return len(rows)
