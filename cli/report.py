"""tm report — project activity report from session data, with shell working time."""
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


def _working_time(conn, tag: str, cutoff_ms: int) -> str:
    """Total working time for a project from shell command spans.

    For each day, measures from the first shell command in that project
    to the last. Sums across all days in the period.
    Returns a formatted string like '3h 22m', or '' if no shell data.
    """
    try:
        rows = conn.execute(
            """SELECT strftime('%Y-%m-%d', ts/1000, 'unixepoch', 'localtime') as day,
                      MIN(ts) as first_ts,
                      MAX(ts) as last_ts
               FROM shell_commands
               WHERE cwd LIKE ? AND ts >= ?
               GROUP BY day""",
            (f"%{tag}%", cutoff_ms)
        ).fetchall()
    except Exception:
        return ""

    if not rows:
        return ""

    total_ms = sum(r["last_ts"] - r["first_ts"] for r in rows)
    total_mins = total_ms // 60000

    if total_mins < 1:
        return "<1m"
    if total_mins < 60:
        return f"{total_mins}m"
    return f"{total_mins // 60}h {total_mins % 60:02d}m"


def _has_shell_data(conn, cutoff_ms: int) -> bool:
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM shell_commands WHERE ts >= ?", (cutoff_ms,)
        ).fetchone()[0]
        return count > 0
    except Exception:
        return False


def run(conn, args: list[str]) -> int:
    # Parse --days flag
    days = 30
    i = 0
    while i < len(args):
        if args[i] in ("--days", "--last") and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        else:
            i += 1

    cutoff     = (datetime.now() - timedelta(days=days)).isoformat()
    cutoff_ms  = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    cutoff_label = f"Last {days} days"

    has_shell = _has_shell_data(conn, cutoff_ms)

    # ── Per-project stats ─────────────────────────────────────────────────────
    session_rows = conn.execute(
        """SELECT s.session_id, s.started_at,
                  COUNT(m.id) as msg_count,
                  t.project_tags
           FROM claude_sessions s
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           LEFT JOIN session_titles t  ON t.session_id = s.session_id
           WHERE datetime(s.started_at/1000,'unixepoch') >= ?
           GROUP BY s.session_id""",
        (cutoff,)
    ).fetchall()

    project_data: dict[str, dict] = {}
    sessions_by_project: dict[str, set] = {}

    for row in session_rows:
        tags = [t.strip() for t in (row["project_tags"] or "").split(",") if t.strip()]
        for tag in tags:
            if tag not in project_data:
                project_data[tag] = {"sessions": 0, "messages": 0, "last_active": None}
                sessions_by_project[tag] = set()
            if row["session_id"] not in sessions_by_project[tag]:
                sessions_by_project[tag].add(row["session_id"])
                project_data[tag]["sessions"] += 1
                project_data[tag]["messages"] += row["msg_count"] or 0
            la = row["started_at"]
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
        "SELECT COUNT(*) FROM claude_prompts WHERE ts >= ?", (cutoff_ms,)
    ).fetchone()[0]

    total_shell = 0
    if has_shell:
        try:
            total_shell = conn.execute(
                "SELECT COUNT(*) FROM shell_commands WHERE ts >= ?", (cutoff_ms,)
            ).fetchone()[0]
        except Exception:
            pass

    # ── Activity by day of week ───────────────────────────────────────────────
    dow_rows = conn.execute(
        """SELECT strftime('%w', started_at/1000, 'unixepoch', 'localtime') as dow,
                  COUNT(*) as c
           FROM claude_sessions
           WHERE datetime(started_at/1000,'unixepoch') >= ?
           GROUP BY dow""",
        (cutoff,)
    ).fetchall()
    dow_map   = {r["dow"]: r["c"] for r in dow_rows}
    dow_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    max_dow   = max(dow_map.values(), default=1)

    # ── Print ─────────────────────────────────────────────────────────────────
    W = 68
    print()
    print("  " + "═" * W)
    print(f"  \033[1m  Project Report  —  {cutoff_label}\033[0m")
    print("  " + "═" * W)

    summary = f"\n  {total_sessions} sessions  ·  {total_msgs} prompts  ·  {total_prompts} total prompts sent"
    if has_shell:
        summary += f"  ·  {total_shell} shell commands"
    print(summary + "\n")

    if sorted_projects:
        if has_shell:
            print(f"  {'PROJECT':<20} {'ACTIVITY':<20} {'SESS':>5}  {'MSGS':>6}  {'TIME':>8}  LAST")
        else:
            print(f"  {'PROJECT':<20} {'ACTIVITY':<20} {'SESS':>5}  {'MSGS':>6}  LAST")
        print("  " + "─" * W)

        for tag, data in sorted_projects:
            bar  = _bar(data["sessions"], max_sessions)
            last = _last_active(data["last_active"])
            sess = data["sessions"]
            msgs = data["messages"]

            if has_shell:
                wtime = _working_time(conn, tag, cutoff_ms)
                wtime_str = f"{wtime:>8}" if wtime else f"{'—':>8}"
                print(f"  {tag:<20} \033[35m{bar}\033[0m  {sess:>4}  {msgs:>6}  "
                      f"\033[36m{wtime_str}\033[0m  {last}")
            else:
                print(f"  {tag:<20} \033[35m{bar}\033[0m  {sess:>4}  {msgs:>6}  {last}")

        if untagged and untagged["c"]:
            if has_shell:
                print(f"  {'(untagged)':<20} {'░'*18}  {untagged['c']:>4}  {untagged['msgs'] or 0:>6}  {'':>8}")
            else:
                print(f"  {'(untagged)':<20} {'░'*18}  {untagged['c']:>4}  {untagged['msgs'] or 0:>6}")
    else:
        print("  No sessions with project tags found in this period.")

    # ── Day of week breakdown ─────────────────────────────────────────────────
    print(f"\n  ACTIVITY BY DAY")
    print("  " + "─" * 40)
    for i, name in enumerate(dow_names):
        count = dow_map.get(str(i), 0)
        bar   = _bar(count, max_dow, width=20)
        print(f"  {name}  \033[34m{bar}\033[0m  {count}")

    print()
    print("  " + "─" * W)
    if not has_shell:
        import platform as _platform
        if _platform.system() == "Windows":
            _hook_hint = ". \"$env:USERPROFILE\\.terminalmd\\daemon\\shell_hook.ps1\"  in $PROFILE"
        else:
            _hook_hint = "source ~/.terminalmd/daemon/shell_hook.sh  in ~/.zshrc"
        print(f"  \033[2mTIME column appears once shell hook is active ({_hook_hint})\033[0m")
    print(f"  Use \033[1mtm report --days 7\033[0m for last week, \033[1m--days 90\033[0m for 3 months\n")

    return 0
