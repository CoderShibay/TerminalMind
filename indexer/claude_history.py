"""Index ~/.claude/history.jsonl → claude_prompts table."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import claude_dir

HISTORY_PATH = claude_dir() / "history.jsonl"


def _project_name(path: str | None) -> str | None:
    if not path:
        return None
    p = path.rstrip("/")
    return p.split("/")[-1] if p else None


def run(conn) -> int:
    if not HISTORY_PATH.exists():
        return 0

    stat = HISTORY_PATH.stat()
    cur = conn.execute(
        "SELECT last_size, last_mtime FROM sync_state WHERE file_path = ?",
        (str(HISTORY_PATH),),
    ).fetchone()

    if cur and cur["last_size"] == stat.st_size and abs(cur["last_mtime"] - stat.st_mtime) < 1:
        return 0  # nothing changed

    rows = []
    with open(HISTORY_PATH, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            text = entry.get("display", "").strip()
            if not text:
                continue

            rows.append((
                entry.get("sessionId", ""),
                entry.get("timestamp", 0),
                entry.get("project"),
                None,  # cwd not in history.jsonl
                text,
            ))

    if not rows:
        return 0

    conn.executemany(
        """INSERT OR IGNORE INTO claude_prompts (session_id, ts, project, cwd, prompt_text)
           VALUES (?, ?, ?, ?, ?)""",
        rows,
    )

    conn.execute("INSERT INTO prompts_fts(prompts_fts) VALUES('rebuild')")

    conn.execute(
        """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
           VALUES (?, ?, ?, ?)""",
        (str(HISTORY_PATH), stat.st_size, stat.st_mtime, int(time.time() * 1000)),
    )
    conn.commit()

    # Create synthetic session records for sessions that exist in history.jsonl
    # but have no corresponding transcript file (older sessions)
    _backfill_sessions(conn)

    return len(rows)


_SKIP_PARTS = {"Users", "home", "Documents", Path.home().name, ""}


def _project_label(path: str | None) -> str | None:
    if not path:
        return None
    for part in reversed(path.rstrip("/").split("/")):
        if part and part not in _SKIP_PARTS:
            return part
    return None


def _backfill_sessions(conn) -> None:
    """Create session records from history.jsonl for sessions with no transcript."""
    orphaned = conn.execute(
        """SELECT p.session_id,
                  MIN(p.ts) as started_at,
                  MAX(p.ts) as updated_at,
                  p.project
           FROM claude_prompts p
           LEFT JOIN claude_sessions s ON s.session_id = p.session_id
           WHERE s.session_id IS NULL
           GROUP BY p.session_id"""
    ).fetchall()

    for row in orphaned:
        label = _project_label(row["project"])
        conn.execute(
            """INSERT OR IGNORE INTO claude_sessions
               (session_id, pid, cwd, project, started_at, updated_at, status, version, kind)
               VALUES (?, NULL, ?, ?, ?, ?, 'ended', NULL, 'history-only')""",
            (row["session_id"], row["project"], label,
             row["started_at"], row["updated_at"]),
        )
    conn.commit()
