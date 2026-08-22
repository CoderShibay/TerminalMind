"""tm sessions — list Claude Code sessions."""
from datetime import datetime


def _ts_human(ts: int | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def run(conn, args: list[str]) -> int:
    project_filter = None
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]
            i += 2
        else:
            i += 1

    sql = """
        SELECT s.session_id, s.pid, s.cwd, s.project, s.started_at, s.status,
               COUNT(m.id) as msg_count
        FROM claude_sessions s
        LEFT JOIN claude_messages m ON m.session_id = s.session_id
    """
    params = []
    if project_filter:
        sql += " WHERE s.project LIKE ? OR s.cwd LIKE ?"
        params += [f"%{project_filter}%", f"%{project_filter}%"]

    sql += " GROUP BY s.session_id ORDER BY s.started_at DESC LIMIT 40"

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("No sessions found.")
        return 0

    print(f"\n  {'SESSION':<10} {'STARTED':<17} {'STATUS':<8} {'MSGS':>5}  PROJECT / CWD")
    print("  " + "─" * 70)

    for row in rows:
        sid = row["session_id"][:8] + "…"
        started = _ts_human(row["started_at"])
        status = row["status"] or "?"
        msgs = row["msg_count"] or 0
        project = row["project"] or ""
        cwd = row["cwd"] or ""
        location = project if project else cwd.split("/")[-1] if cwd else "?"

        status_color = "\033[32m" if status == "active" else "\033[2m"
        print(f"  {sid:<10} {started:<17} {status_color}{status:<8}\033[0m {msgs:>5}  {location}")

    print()
    return 0
