"""tm status — show active Claude sessions and DB stats."""
from datetime import datetime
from pathlib import Path


def _ts_human(ts: int | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%H:%M on %Y-%m-%d")
    except Exception:
        return str(ts)


def run(conn, args: list[str]) -> int:
    print()

    # Active sessions
    active = conn.execute(
        "SELECT session_id, pid, cwd, project, started_at FROM claude_sessions WHERE status = 'active' ORDER BY started_at DESC"
    ).fetchall()

    if active:
        print("  \033[32m● ACTIVE SESSIONS\033[0m")
        for row in active:
            project = row["project"] or row["cwd"] or "?"
            print(f"    PID {row['pid']}  │  {project}  │  since {_ts_human(row['started_at'])}")
    else:
        print("  \033[2m○ No active Claude sessions\033[0m")

    print()

    # DB stats
    total_prompts = conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0]
    total_msgs = conn.execute("SELECT COUNT(*) FROM claude_messages").fetchone()[0]
    total_sessions = conn.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0]
    user_msgs = conn.execute("SELECT COUNT(*) FROM claude_messages WHERE role='user'").fetchone()[0]
    asst_msgs = conn.execute("SELECT COUNT(*) FROM claude_messages WHERE role='assistant'").fetchone()[0]

    print("  \033[1mDATABASE\033[0m")
    print(f"    {total_prompts:,} prompts  │  {total_sessions} sessions  │  {total_msgs:,} messages ({user_msgs:,} user / {asst_msgs:,} assistant)")

    # Projects breakdown
    projects = conn.execute(
        "SELECT project, COUNT(*) as c FROM claude_messages WHERE project IS NOT NULL GROUP BY project ORDER BY c DESC LIMIT 10"
    ).fetchall()
    if projects:
        print()
        print("  \033[1mBY PROJECT\033[0m")
        for row in projects:
            print(f"    {row['c']:>5}  {row['project']}")

    # DB file size
    db_path = Path(__file__).parent.parent / "db" / "terminalmd.db"
    if db_path.exists():
        size_kb = db_path.stat().st_size / 1024
        print()
        print(f"  DB: {db_path}  ({size_kb:.1f} KB)")

    print()
    return 0
