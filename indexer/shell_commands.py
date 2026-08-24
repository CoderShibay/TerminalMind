"""Index ~/terminalmd/shell_log.jsonl → shell_commands table."""
import json
import time
from pathlib import Path

LOG_PATH = Path.home() / "terminalmd" / "shell_log.jsonl"


def run(conn) -> int:
    """Index new shell commands from shell_log.jsonl.

    Uses the byte offset stored in sync_state.last_size to seek past
    already-indexed lines — never re-reads the same data twice.
    Returns the count of new commands added.
    """
    if not LOG_PATH.exists():
        return 0

    current_size = LOG_PATH.stat().st_size

    # last_size is repurposed as byte offset for this file
    cur = conn.execute(
        "SELECT last_size FROM sync_state WHERE file_path = ?",
        (str(LOG_PATH),),
    ).fetchone()
    last_offset = cur["last_size"] if cur else 0

    if current_size <= last_offset:
        return 0  # nothing new

    rows = []
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        f.seek(last_offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            cmd = entry.get("cmd", "").strip()
            if not cmd:
                continue

            rows.append((
                entry.get("ts"),
                entry.get("dur"),
                entry.get("exit"),
                cmd,
                entry.get("cwd"),
                entry.get("pid"),
            ))

    if not rows:
        _update_offset(conn, current_size)
        return 0

    conn.executemany(
        """INSERT OR IGNORE INTO shell_commands
           (ts, duration_ms, exit_code, command, cwd, shell_pid)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.execute("INSERT INTO shell_commands_fts(shell_commands_fts) VALUES('rebuild')")
    _update_offset(conn, current_size)
    conn.commit()
    return len(rows)


def _update_offset(conn, offset: int) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO sync_state (file_path, last_size, last_mtime, indexed_at)
           VALUES (?, ?, 0, ?)""",
        (str(LOG_PATH), offset, int(time.time() * 1000)),
    )
