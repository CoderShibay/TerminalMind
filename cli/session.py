"""tm session — show the current Claude Code session ID and title."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import claude_dir


def _ts(ts_ms: int | None) -> str:
    if not ts_ms:
        return "?"
    try:
        d = datetime.fromtimestamp(ts_ms / 1000)
        now = datetime.now()
        if d.date() == now.date():
            return "Today " + d.strftime("%H:%M")
        if d.date() == (now - timedelta(days=1)).date():
            return "Yesterday " + d.strftime("%H:%M")
        return d.strftime("%b %d %H:%M")
    except Exception:
        return "?"


def _load_sessions() -> list[dict]:
    """Read all session JSON files, sorted by updatedAt descending."""
    sessions_dir = claude_dir() / "sessions"
    if not sessions_dir.exists():
        return []

    result = []
    for p in sessions_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("sessionId"):
                result.append(data)
        except Exception:
            pass

    result.sort(key=lambda d: d.get("updatedAt", 0), reverse=True)
    return result


def run(conn, args: list[str]) -> int:
    sessions = _load_sessions()

    if not sessions:
        print("\n  No active Claude Code sessions found.\n")
        return 0

    # Prefer active/busy sessions; fall back to most recently updated
    current = None
    for s in sessions:
        if s.get("status") in ("active", "busy"):
            current = s
            break
    if not current:
        current = sessions[0]  # most recently updated

    sid = current.get("sessionId", "")
    status = current.get("status", "?")
    started = current.get("startedAt", 0)
    cwd = current.get("cwd", "")

    # Look up title from DB
    row = conn.execute(
        "SELECT title FROM session_titles WHERE session_id = ?", (sid,)
    ).fetchone()
    title = (row["title"] if row else None) or "Untitled session"

    short = sid[:8]

    print()
    print(f"  \033[1m{title}\033[0m")
    print(f"  \033[32m{short}\033[0m  \033[2m·  {_ts(started)}  ·  {status}\033[0m")
    if cwd:
        print(f"  \033[2m{cwd}\033[0m")
    print()
    print(f"  \033[2mFull ID: {sid}\033[0m")
    print()

    # Copy short ID to clipboard
    try:
        from cli.clipboard import copy
        if copy(short):
            print(f"  \033[2m✓ {short} copied to clipboard\033[0m\n")
    except Exception:
        pass

    return 0
