"""Index ~/.claude/history.jsonl → claude_prompts table."""
import json
import os
import time
from pathlib import Path

HISTORY_PATH = Path.home() / ".claude" / "history.jsonl"


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

    # Rebuild FTS for prompts (simple full rebuild — fast enough at this scale)
    conn.execute("INSERT INTO prompts_fts(prompts_fts) VALUES('rebuild')")

    conn.execute(
        """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
           VALUES (?, ?, ?, ?)""",
        (str(HISTORY_PATH), stat.st_size, stat.st_mtime, int(time.time() * 1000)),
    )
    conn.commit()
    return len(rows)
