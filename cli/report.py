"""tm report — project activity report from session data."""
from datetime import datetime, timedelta


def _bar(count: int, max_count: int, width: int = 18) -> str:
    filled = round(count / max_count * width) if max_count else 0
    return "█" * filled + "░" * (width - filled)


def _last_active(ts) -> str:
    if not ts:
        return "?"
    try:
        if isinstance(ts, int):
            d = datetime.fromtimestamp(ts / 1000)
        else:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        diff = datetime.now() - d.replace(tzinfo=None)
        if diff.days == 0:
            return "today"
        if diff.days == 1:
            return "yesterday"
        if diff.days < 7:
            return f"{diff.days}d ago"
        if diff.days < 30:
            return f"{diff.days // 7}w ago"
        return d.strftime("%b %d")
    except Exception:
        return "?"


def run(conn, args: list[str]) -> int:
    # Parse --days flag
    days = 30
    i = 0
    while i < len(args):
        if args[i] in ("--days", "--last") and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        else:
            i += 1

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    cutoff_label = f"Last {days} days" if days != 9999 else "All time"

    # ── Per-project stats ─────────────────────────────────────────────────────
    # Sessions and message counts per detected project tag
    tag_rows = conn.execute(
        """SELECT t.project_tags,
                  COUNT(DISTINCT s.session_id) as session_count,
                  COUNT(m.id) as msg_count,
                  MAX(s.started_at) as last_active
           FROM session_titles t
           JOIN claude_sessions s ON s.session_id = t.session_id
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           WHERE datetime(s.started_at/1000,'unixepoch') >= ?
             AND t.project_tags IS NOT NULL AND t.project_tags != ''
           GROUP BY s.session_id, t.project_tags""",
        (cutoff,)
    ).fetchall()

    # Expand tags (sessions can have multiple)
    project_data: dict[str, dict] = {}
    for row in tag_rows:
        for tag in (row["project_tags"] or "").split(","):
            tag = tag.strip()
            if not tag:
                continue
            if tag not in project_data:
                project_data[tag] = {"sessions": 0, "messages": 0, "last_active": None}
            project_data[tag]["sessions"] += 1
            project_data[tag]["messages"] += row["msg_count"] or 0
            la = row["last_active"]
            if la and (not project_data[tag]["last_active"] or la > project_data[tag]["last_active"]):
                project_data[tag]["last_active"] = la

    # Sessions with no tags
    untagged = conn.execute(
        """SELECT COUNT(DISTINCT s.session_id) as c, COUNT(m.id) as msgs
           FROM claude_sessions s
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           WHERE datetime(s.started_at/1000,'unixepoch') >= ?
             AND (t.project_tags IS NULL OR t.project_tags = '')""",
        (cutoff,)
    ).fetchone()

    # Sort by session count desc
    sorted_projects = sorted(project_data.items(), key=lambda x: x[1]["sessions"], reverse=True)
    max_sessions = max((v["sessions"] for _, v in sorted_projects), default=1)

    # ── Totals ────────────────────────────────────────────────────────────────
    total_sessions = conn.execute(
        "SELECT COUNT(*) FROM claude_sessions WHERE datetime(started_at/1000,'unixepoch') >= ?",
        (cutoff,)
    ).fetchone()[0]
    total_msgs = conn.execute(
        """SELECT COUNT(*) FROM claude_messages m
           JOIN claude_sessions s ON s.session_id = m.session_id
           WHERE datetime(s.started_at/1000,'unixepoch') >= ?
             AND m.role = 'user'""",
        (cutoff,)
    ).fetchone()[0]
    total_prompts = conn.execute(
        "SELECT COUNT(*) FROM claude_prompts WHERE ts >= ?",
        (int((datetime.now() - timedelta(days=days)).timestamp() * 1000),)
    ).fetchone()[0]

    # ── Activity by day of week ───────────────────────────────────────────────
    dow_rows = conn.execute(
        """SELECT strftime('%w', started_at/1000, 'unixepoch', 'localtime') as dow,
                  COUNT(*) as c
           FROM claude_sessions
           WHERE datetime(started_at/1000,'unixepoch') >= ?
           GROUP BY dow""",
        (cutoff,)
    ).fetchall()
    dow_map = {r["dow"]: r["c"] for r in dow_rows}
    dow_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    max_dow = max(dow_map.values(), default=1)

    # ── Print ─────────────────────────────────────────────────────────────────
    W = 62
    print()
    print("  " + "═" * W)
    print(f"  \033[1m  Project Report  —  {cutoff_label}\033[0m")
    print("  " + "═" * W)
    print(f"\n  {total_sessions} sessions  ·  {total_msgs} prompts  ·  {total_prompts} total prompts sent\n")

    if sorted_projects:
        print(f"  {'PROJECT':<20} {'ACTIVITY':<20} {'SESS':>5}  {'MSGS':>6}  LAST")
        print("  " + "─" * W)
        for tag, data in sorted_projects:
            bar    = _bar(data["sessions"], max_sessions)
            last   = _last_active(data["last_active"])
            sess   = data["sessions"]
            msgs   = data["messages"]
            print(f"  {tag:<20} \033[35m{bar}\033[0m  {sess:>4}  {msgs:>6}  {last}")

        if untagged and untagged["c"]:
            print(f"  {'(untagged)':<20} {'░'*18}  {untagged['c']:>4}  {untagged['msgs'] or 0:>6}")
    else:
        print("  No sessions with project tags found in this period.")

    # ── Day of week breakdown ─────────────────────────────────────────────────
    print(f"\n  {'ACTIVITY BY DAY':}")
    print("  " + "─" * 40)
    for i, name in enumerate(dow_names):
        count = dow_map.get(str(i), 0)
        bar   = _bar(count, max_dow, width=20)
        print(f"  {name}  \033[34m{bar}\033[0m  {count}")

    print()
    print("  " + "─" * W)
    print(f"  Use \033[1mtm report --days 7\033[0m for last week, \033[1m--days 90\033[0m for 3 months\n")

    return 0
