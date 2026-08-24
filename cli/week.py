"""tm week — what you worked on this week, grouped by day."""
from datetime import datetime, date, timedelta


def _fmt_time(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except Exception:
        return "?"


def _dur(first: str | None, last: str | None) -> str:
    if not first or not last:
        return ""
    try:
        s = datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        e = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        mins = int((e - s).total_seconds() / 60)
        if mins < 1:
            return ""
        if mins < 60:
            return f"{mins}m"
        return f"{mins // 60}h {mins % 60}m"
    except Exception:
        return ""


def run(conn, args: list[str]) -> int:
    today = date.today()
    week_start = today - timedelta(days=6)

    print()
    print(f"  \033[1mThis Week  —  {week_start.strftime('%b %d')} – {today.strftime('%b %d, %Y')}\033[0m")
    print()

    week_sessions = 0
    week_msgs     = 0
    week_shell    = 0
    any_day       = False

    for offset in range(6, -1, -1):   # oldest → newest
        target  = today - timedelta(days=offset)
        day_str = target.isoformat()

        if offset == 0:
            label = "Today"
        elif offset == 1:
            label = "Yesterday"
        else:
            label = target.strftime("%A, %b %d")

        # Claude sessions with activity this day
        sessions = conn.execute(
            """SELECT s.session_id, t.title, t.project_tags,
                      COUNT(m.id) as msg_count,
                      MIN(m.ts) as first_msg, MAX(m.ts) as last_msg,
                      n.note
               FROM claude_messages m
               JOIN claude_sessions s ON s.session_id = m.session_id
               LEFT JOIN session_titles t ON t.session_id = s.session_id
               LEFT JOIN session_notes n   ON n.session_id = s.session_id
               WHERE substr(m.ts, 1, 10) = ?
                 AND m.role = 'user'
               GROUP BY s.session_id
               ORDER BY MIN(m.ts) ASC""",
            (day_str,)
        ).fetchall()

        # Shell commands this day
        shell_count = 0
        try:
            shell_count = conn.execute(
                """SELECT COUNT(*) FROM shell_commands
                   WHERE strftime('%Y-%m-%d', ts/1000, 'unixepoch', 'localtime') = ?""",
                (day_str,)
            ).fetchone()[0]
        except Exception:
            pass

        if not sessions and not shell_count:
            continue

        any_day = True
        day_msgs = sum(r["msg_count"] for r in sessions)
        week_sessions += len(sessions)
        week_msgs     += day_msgs
        week_shell    += shell_count

        # Day header
        shell_note = f"  ·  {shell_count} commands" if shell_count else ""
        print(f"  \033[1m{label}\033[0m  "
              f"\033[2m{len(sessions)} session{'s' if len(sessions) != 1 else ''}  "
              f"·  {day_msgs} messages{shell_note}\033[0m")

        for r in sessions:
            title   = (r["title"] or r["session_id"][:8])[:50]
            tags    = r["project_tags"] or ""
            tag_str = f"  \033[2m[{tags}]\033[0m" if tags else ""
            start   = _fmt_time(r["first_msg"])
            end     = _fmt_time(r["last_msg"])
            dur     = _dur(r["first_msg"], r["last_msg"])
            dur_str = f"  \033[2m{dur}\033[0m" if dur else ""
            note    = f"\n              \033[33m↳ {r['note'][:60]}\033[0m" if r["note"] else ""

            print(f"    {start}–{end}  {title}{tag_str}{dur_str}{note}")

        print()

    if not any_day:
        print("  No sessions or commands this week.\n")
        return 0

    # ── Week totals ───────────────────────────────────────────────────────────
    prompts_week = conn.execute(
        "SELECT COUNT(*) FROM claude_prompts WHERE ts >= ?",
        (int(datetime.combine(week_start, datetime.min.time()).timestamp() * 1000),)
    ).fetchone()[0]

    shell_str = f"  ·  {week_shell} shell commands" if week_shell else ""
    print(f"  \033[2m── Week total: {week_sessions} sessions  ·  {prompts_week} prompts{shell_str}\033[0m\n")

    return 0
