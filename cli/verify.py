"""tm verify — show exactly what's indexed and whether anything is missing."""
from datetime import datetime
from pathlib import Path


def _iso(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def run(conn, args: list[str]) -> int:
    claude_dir = Path.home() / ".claude"
    print()

    # ── What Claude Code has on disk ─────────────────────────────────────────
    history_file  = claude_dir / "history.jsonl"
    sessions_dir  = claude_dir / "sessions"
    projects_dir  = claude_dir / "projects"

    disk_prompts   = sum(1 for _ in history_file.open()) if history_file.exists() else 0
    disk_transcripts = list(projects_dir.rglob("*.jsonl")) if projects_dir.exists() else []
    disk_sessions  = list(sessions_dir.glob("*.json")) if sessions_dir.exists() else []

    print("  \033[1mON DISK  (~/.claude/)\033[0m")
    print(f"    {disk_prompts:,} lines in history.jsonl")
    print(f"    {len(disk_transcripts)} transcript files across {len(list(projects_dir.iterdir())) if projects_dir.exists() else 0} project folders")
    print(f"    {len(disk_sessions)} session files")
    print()

    # ── What's in the DB ──────────────────────────────────────────────────────
    db_prompts  = conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0]
    db_messages = conn.execute("SELECT COUNT(*) FROM claude_messages").fetchone()[0]
    db_sessions = conn.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0]
    db_titled   = conn.execute("SELECT COUNT(*) FROM session_titles").fetchone()[0]
    db_ollama   = conn.execute("SELECT COUNT(*) FROM session_titles WHERE method='ollama'").fetchone()[0]

    oldest = conn.execute(
        "SELECT MIN(ts) FROM claude_messages"
    ).fetchone()[0]
    newest = conn.execute(
        "SELECT MAX(ts) FROM claude_messages"
    ).fetchone()[0]

    db_path = Path(__file__).parent.parent / "db" / "terminalmd.db"
    db_size_kb = round(db_path.stat().st_size / 1024) if db_path.exists() else 0

    print("  \033[1mIN DATABASE\033[0m")
    print(f"    {db_prompts:,} prompts indexed")
    print(f"    {db_messages:,} messages indexed")
    print(f"    {db_sessions} sessions indexed  ({db_titled} with titles, {db_ollama} via Ollama)")
    print(f"    Oldest message: {_iso(oldest)}")
    print(f"    Newest message: {_iso(newest)}")
    print(f"    DB size: {db_size_kb} KB")
    print()

    # ── Sync state ────────────────────────────────────────────────────────────
    synced_files = conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]
    last_sync_row = conn.execute(
        "SELECT MAX(indexed_at) FROM sync_state"
    ).fetchone()[0]
    if last_sync_row:
        last_sync = datetime.fromtimestamp(last_sync_row / 1000).strftime("%Y-%m-%d %H:%M:%S")
    else:
        last_sync = "never"

    print("  \033[1mSYNC STATE\033[0m")
    print(f"    {synced_files} files tracked")
    print(f"    Last sync: {last_sync}")
    print()

    # ── Health checks ─────────────────────────────────────────────────────────
    issues = []

    if db_sessions < len(disk_sessions):
        issues.append(f"  {len(disk_sessions) - db_sessions} session file(s) on disk but not in DB — run `tm sync`")

    if db_titled < db_sessions:
        issues.append(f"  {db_sessions - db_titled} session(s) have no title — run `tm sync`")

    unsynced = []
    for f in disk_transcripts:
        row = conn.execute(
            "SELECT last_size FROM sync_state WHERE file_path = ?", (str(f),)
        ).fetchone()
        if row is None:
            unsynced.append(f.name[:40])

    if unsynced:
        issues.append(f"  {len(unsynced)} transcript file(s) never indexed — run `tm sync`")

    if issues:
        print("  \033[1;33m⚠  ISSUES FOUND\033[0m")
        for issue in issues:
            print(f"  \033[33m→\033[0m {issue.strip()}")
    else:
        print("  \033[32m✓  Everything looks good — all files indexed, all sessions titled\033[0m")

    print()

    # ── Ollama status ─────────────────────────────────────────────────────────
    import subprocess
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and "llama3.2" in r.stdout:
            print("  \033[32m✓  Ollama available — llama3.2 ready (titles will use AI)\033[0m")
        elif r.returncode == 0:
            print("  \033[33m⚠  Ollama installed but llama3.2 not found\033[0m")
            print("     Run: \033[1mollama pull llama3.2\033[0m")
        else:
            print("  \033[2m○  Ollama not running — using heuristic titles (perfectly fine)\033[0m")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print("  \033[2m○  Ollama not installed — using heuristic titles (perfectly fine)\033[0m")
        print("     Optional: install from \033[1mhttps://ollama.com\033[0m then `ollama pull llama3.2`")

    print()
    return 0
