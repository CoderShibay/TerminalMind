"""tm sessions — list all Claude Code sessions, newest to oldest."""
from datetime import datetime, timedelta


def _ts(ts: int | None) -> str:
    if not ts:
        return "?"
    try:
        d = datetime.fromtimestamp(ts / 1000)
        now = datetime.now()
        if d.date() == now.date():
            return "Today     " + d.strftime("%H:%M")
        if d.date() == (now - timedelta(days=1)).date():
            return "Yesterday " + d.strftime("%H:%M")
        return d.strftime("%b %d      %H:%M")
    except Exception:
        return str(ts)


def run(conn, args: list[str]) -> int:
    project_filter = None
    limit = 200
    simple = False

    i = 0
    while i < len(args):
        if args[i] in ("--project", "-p") and i + 1 < len(args):
            project_filter = args[i + 1]; i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] in ("--simple", "--ids"):
            simple = True; i += 1
        else:
            i += 1

    sql = """
        SELECT s.session_id, s.started_at, s.status,
               t.title, t.project_tags
        FROM claude_sessions s
        LEFT JOIN session_titles t ON t.session_id = s.session_id
    """
    params: list = []

    if project_filter:
        sql += " WHERE (t.project_tags LIKE ? OR t.title LIKE ? OR s.project LIKE ?)"
        params += [f"%{project_filter}%", f"%{project_filter}%", f"%{project_filter}%"]

    sql += " ORDER BY s.started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("\n  No sessions found.\n")
        return 0

    if simple:
        # Clean two-column output: ID  Title — easy to copy or pipe
        print()
        for row in rows:
            sid   = row["session_id"][:8]
            title = (row["title"] or "Untitled session")[:70]
            print(f"  {sid}  {title}")
        print()
        return 0

    # Full table: ID, date, status, title, tags
    print()
    print(f"  \033[2m{'ID':<10} {'STARTED':<19} {'ST':<6} TITLE\033[0m")
    print("  " + "─" * 78)

    for row in rows:
        sid    = row["session_id"][:8]
        ts     = _ts(row["started_at"])
        status = row["status"] or "?"
        title  = (row["title"] or "Untitled session")[:48]
        tags   = row["project_tags"] or ""

        if status in ("active", "busy"):
            st_color = "\033[32m"
        elif status == "idle":
            st_color = "\033[33m"
        else:
            st_color = "\033[2m"

        tag_str = f"  \033[2m[{tags}]\033[0m" if tags else ""
        print(f"  \033[36m{sid}\033[0m  \033[2m{ts:<19}\033[0m  {st_color}{status[:4]:<6}\033[0m {title}{tag_str}")

    print()
    print(f"  \033[2m{len(rows)} sessions  ·  --simple for ID+title only  ·  --project NAME to filter\033[0m\n")
    return 0
