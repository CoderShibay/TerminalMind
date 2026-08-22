"""tm digest — morning briefing: pinned sessions, yesterday, this week, today."""
import subprocess
from datetime import datetime, date, timedelta


def _ts(ts) -> str:
    if not ts:
        return "?"
    try:
        if isinstance(ts, int):
            return datetime.fromtimestamp(ts / 1000).strftime("%H:%M")
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except Exception:
        return "?"


def _date_sessions(conn, target: date) -> list:
    day = target.isoformat()
    return conn.execute(
        """SELECT s.session_id, s.started_at, s.kind,
                  COUNT(m.id) as msg_count,
                  t.title, t.project_tags, n.note
           FROM claude_sessions s
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           LEFT JOIN session_notes n ON n.session_id = s.session_id
           WHERE date(s.started_at / 1000, 'unixepoch', 'localtime') = ?
           GROUP BY s.session_id
           ORDER BY s.started_at ASC""",
        (day,)
    ).fetchall()


def _fmt_tags(tags: str | None) -> str:
    if not tags:
        return ""
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    return "  \033[2m[" + ", ".join(parts[:3]) + "]\033[0m" if parts else ""


def _week_stats(conn) -> dict:
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    sess = conn.execute(
        "SELECT COUNT(*) FROM claude_sessions WHERE datetime(started_at/1000,'unixepoch') >= ?",
        (cutoff,)
    ).fetchone()[0]
    msgs = conn.execute(
        "SELECT COUNT(*) FROM claude_messages WHERE ts >= ? AND role='user'",
        (cutoff,)
    ).fetchone()[0]
    top = conn.execute(
        """SELECT t.project_tags FROM session_titles t
           JOIN claude_sessions s ON s.session_id = t.session_id
           WHERE datetime(s.started_at/1000,'unixepoch') >= ?
             AND t.project_tags IS NOT NULL AND t.project_tags != ''""",
        (cutoff,)
    ).fetchall()
    tag_counts = {}
    for row in top:
        for tag in (row["project_tags"] or "").split(","):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    top_tag = max(tag_counts, key=tag_counts.get) if tag_counts else "—"
    return {"sessions": sess, "messages": msgs, "top_project": top_tag}


def run(conn, args: list[str]) -> int:
    today     = date.today()
    yesterday = today - timedelta(days=1)
    now_str   = datetime.now().strftime("%A, %B %d %Y")

    lines = []
    W = 60

    def hr(char="═"):
        lines.append(char * W)

    def section(title):
        lines.append(f"\n  \033[1m{title}\033[0m")

    hr()
    lines.append(f"  \033[1mTerminalMind Digest  —  {now_str}\033[0m")
    hr()

    # ── Pinned sessions ───────────────────────────────────────────────────────
    pinned = conn.execute(
        """SELECT s.session_id, s.started_at, t.title, t.project_tags,
                  n.note, COUNT(m.id) as msg_count
           FROM session_pins p
           JOIN claude_sessions s ON s.session_id = p.session_id
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           LEFT JOIN session_notes n ON n.session_id = s.session_id
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           GROUP BY s.session_id
           ORDER BY p.pinned_at DESC"""
    ).fetchall()

    section(f"📌  PINNED  ({len(pinned)})")
    if pinned:
        for r in pinned:
            title = r["title"] or r["session_id"][:8]
            tags  = _fmt_tags(r["project_tags"])
            note  = f"\n       \033[33m↳ {r['note']}\033[0m" if r["note"] else ""
            lines.append(f"    • {title}{tags}{note}")
    else:
        lines.append("    \033[2mNo pinned sessions — pin important ones with 'p' in Browse.\033[0m")

    # ── Today ─────────────────────────────────────────────────────────────────
    today_sessions = _date_sessions(conn, today)
    section(f"🕐  TODAY  ({len(today_sessions)} session{'s' if len(today_sessions) != 1 else ''})")
    if today_sessions:
        for r in today_sessions:
            title = (r["title"] or "Untitled")[:45]
            msgs  = r["msg_count"] or 0
            tags  = _fmt_tags(r["project_tags"])
            start = _ts(r["started_at"])
            hist  = " \033[2m[prompts only]\033[0m" if r["kind"] == "history-only" else ""
            lines.append(f"    {start}  {title}{tags}  \033[2m{msgs} msgs\033[0m{hist}")
    else:
        lines.append("    \033[2mNothing yet today.\033[0m")

    # ── Yesterday ─────────────────────────────────────────────────────────────
    yday_sessions = _date_sessions(conn, yesterday)
    yday_label    = yesterday.strftime("%A, %B %d")
    section(f"📅  YESTERDAY — {yday_label}  ({len(yday_sessions)} session{'s' if len(yday_sessions) != 1 else ''})")
    if yday_sessions:
        for r in yday_sessions:
            title = (r["title"] or "Untitled")[:45]
            msgs  = r["msg_count"] or 0
            tags  = _fmt_tags(r["project_tags"])
            start = _ts(r["started_at"])
            lines.append(f"    {start}  {title}{tags}  \033[2m{msgs} msgs\033[0m")
    else:
        lines.append(f"    \033[2mNo sessions on {yday_label}.\033[0m")

    # ── This week ─────────────────────────────────────────────────────────────
    ws = _week_stats(conn)
    section("📊  THIS WEEK")
    lines.append(f"    {ws['sessions']} sessions  ·  {ws['messages']} prompts  ·  Top project: \033[35m{ws['top_project']}\033[0m")

    lines.append("")
    hr("─")

    output = "\n".join(lines)
    print("\n" + output + "\n")

    # Copy a clean (no ANSI) version to clipboard for pasting into Claude
    import re
    clean = re.sub(r"\033\[[0-9;]*m", "", output)
    try:
        subprocess.run(["pbcopy"], input=clean.encode(), check=True)
        print("  ✓ Copied to clipboard\n")
    except Exception:
        pass

    return 0
