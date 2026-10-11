"""Index ~/.claude/sessions/*.json → claude_sessions table."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import claude_dir

SESSIONS_DIR = claude_dir() / "sessions"

_SKIP_PARTS = {"Users", "home", Path.home().name, ""}


def _project_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    parts = cwd.rstrip("/").split("/")
    for part in reversed(parts):
        if part and part not in _SKIP_PARTS:
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
            session_id,
        ))

    if not rows:
        return 0

    conn.executemany(
        """INSERT INTO claude_sessions
           (session_id, pid, cwd, project, started_at, updated_at, status, version, kind,
            agent, native_session_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'claude', ?)
           ON CONFLICT(session_id) DO UPDATE SET
             pid=excluded.pid, cwd=excluded.cwd, project=excluded.project,
             started_at=excluded.started_at, updated_at=excluded.updated_at,
             status=excluded.status, version=excluded.version, kind=excluded.kind,
             agent='claude', native_session_id=excluded.native_session_id""",
        rows,
    )

    # Enrich session project from prompts (more accurate than cwd)
    prompt_projects = conn.execute(
        "SELECT DISTINCT session_id, project FROM claude_prompts WHERE project IS NOT NULL"
    ).fetchall()
    for row in prompt_projects:
        label = _project_from_cwd(row["project"])
        if label and label not in _SKIP_PARTS:
            conn.execute(
                "UPDATE claude_sessions SET project = ?, cwd = ? "
                "WHERE session_id = ? AND agent = 'claude'",
                (label, row["project"], row["session_id"]),
            )

    conn.commit()
    return len(rows)
