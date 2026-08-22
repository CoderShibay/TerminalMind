"""tm today — what did you work on today."""
from datetime import datetime, date, timedelta


def run(conn, args: list[str]) -> int:
    # Support --yesterday flag
    target_date = date.today()
    if "--yesterday" in args:
        target_date = date.today() - timedelta(days=1)

    day_str = target_date.isoformat()
    label   = "Today" if target_date == date.today() else "Yesterday"

    print()
    print(f"  \033[1m{label}  —  {target_date.strftime('%A, %B %d')}\033[0m")
    print()

    # Sessions that had activity today
    sessions = conn.execute(
        """SELECT s.session_id, s.started_at, t.title, t.project_tags,
                  COUNT(m.id) as msg_count,
                  MIN(m.ts) as first_msg, MAX(m.ts) as last_msg,
                  n.note
           FROM claude_messages m
           JOIN claude_sessions s ON s.session_id = m.session_id
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           LEFT JOIN session_notes n ON n.session_id = s.session_id
           WHERE substr(m.ts, 1, 10) = ?
             AND m.role = 'user'
           GROUP BY s.session_id
           ORDER BY MIN(m.ts) ASC""",
        (day_str,)
    ).fetchall()

    if not sessions:
        print(f"  No Claude sessions found for {day_str}.")
        print()
        return 0

    total_msgs = sum(r["msg_count"] for r in sessions)
    print(f"  {len(sessions)} session(s)  ·  {total_msgs} messages\n")

    def fmt_time(ts):
        if not ts: return "?"
        try:
            return datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone().strftime("%H:%M")
        except Exception:
            return "?"

    for r in sessions:
        title = r["title"] or r["session_id"][:8]
        tags  = r["project_tags"] or ""
        tag_str = f"  \033[2m[{tags}]\033[0m" if tags else ""
        start = fmt_time(r["first_msg"])
        end   = fmt_time(r["last_msg"])
        note  = f"\n     \033[33m↳ {r['note']}\033[0m" if r["note"] else ""

        print(f"  \033[1m{title}\033[0m{tag_str}")
        print(f"  \033[2m{start} – {end}  ·  {r['msg_count']} messages  ·  {r['session_id'][:8]}\033[0m{note}")
        print()

    # Prompt count today
    prompts_today = conn.execute(
        """SELECT COUNT(*) FROM claude_prompts
           WHERE datetime(ts/1000, 'unixepoch', 'localtime') LIKE ?""",
        (f"{day_str}%",)
    ).fetchone()[0]

    if prompts_today:
        print(f"  {prompts_today} prompts sent\n")

    return 0
