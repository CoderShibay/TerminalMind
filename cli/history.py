"""tm history — chronological session timeline, filterable by project or date."""
from datetime import datetime, timedelta


def _ts(ts) -> str:
    if not ts:
        return "?"
    try:
        if isinstance(ts, int):
            d = datetime.fromtimestamp(ts / 1000)
        else:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        now = datetime.now()
        if d.date() == now.date():
            return "Today     " + d.strftime("%H:%M")
        if d.date() == (now - timedelta(days=1)).date():
            return "Yesterday " + d.strftime("%H:%M")
        return d.strftime("%b %d      %H:%M")
    except Exception:
        return str(ts)


def _duration(start, end) -> str:
    """Approximate session duration from first to last message."""
    try:
        if isinstance(start, str):
            s = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            e = datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        else:
            return ""
        mins = int((e - s).total_seconds() / 60)
        if mins < 1:
            return ""
        if mins < 60:
            return f"{mins}m"
        return f"{mins // 60}h {mins % 60}m"
    except Exception:
        return ""


def run(conn, args: list[str]) -> int:
    # Parse flags
    project_filter = None
    days_filter    = None
    limit          = 50
    show_prompts   = False

    i = 0
    while i < len(args):
        if args[i] in ("--project", "-p") and i + 1 < len(args):
            project_filter = args[i + 1]; i += 2
        elif args[i] in ("--days", "--last") and i + 1 < len(args):
            days_filter = int(args[i + 1]); i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--prompts":
            show_prompts = True; i += 1
        else:
            i += 1

    # Build query
    sql = """
        SELECT s.session_id, s.started_at, s.kind,
               COUNT(m.id) as msg_count,
               MIN(m.ts)   as first_msg,
               MAX(m.ts)   as last_msg,
               t.title, t.project_tags, t.method,
               n.note,
               EXISTS(SELECT 1 FROM session_pins p WHERE p.session_id = s.session_id) as is_pinned
        FROM claude_sessions s
        LEFT JOIN claude_messages m ON m.session_id = s.session_id
        LEFT JOIN session_titles t  ON t.session_id = s.session_id
        LEFT JOIN session_notes n   ON n.session_id = s.session_id
    """
    params = []
    wheres = []

    if days_filter:
        cutoff = (datetime.now() - timedelta(days=days_filter)).isoformat()
        wheres.append("datetime(s.started_at/1000,'unixepoch') >= ?")
        params.append(cutoff)

    if project_filter:
        wheres.append("(t.project_tags LIKE ? OR t.title LIKE ?)")
        params += [f"%{project_filter}%", f"%{project_filter}%"]

    if wheres:
        sql += " WHERE " + " AND ".join(wheres)

    sql += " GROUP BY s.session_id ORDER BY s.started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("\n  No sessions found.\n")
        return 0

    # Header
    filter_parts = []
    if project_filter:
        filter_parts.append(f"project: {project_filter}")
    if days_filter:
        filter_parts.append(f"last {days_filter} days")
    filter_str = "  —  " + ", ".join(filter_parts) if filter_parts else ""

    print()
    print(f"  \033[1mSession History{filter_str}\033[0m  ({len(rows)} sessions)\n")
    print(f"  {'DATE & TIME':<20} {'TITLE':<40} {'MSGS':>5}  {'DUR':>5}  ID")
    print("  " + "─" * 82)

    current_date = None
    for r in rows:
        ts_str  = _ts(r["started_at"])
        date_part = ts_str[:10].strip()

        # Date group separator
        if date_part != current_date and date_part not in ("Today", "Yesterday"):
            if current_date is not None:
                print()
            current_date = date_part

        title   = (r["title"] or "Untitled session")[:40]
        msgs    = r["msg_count"] or 0
        dur     = _duration(r["first_msg"], r["last_msg"])
        sid     = r["session_id"][:8]
        is_pinned = r["is_pinned"]
        has_note  = bool(r["note"])
        kind      = r["kind"]

        # Badges
        badges = ""
        if is_pinned:     badges += " 📌"
        if has_note:      badges += " ✎"
        if kind == "history-only": badges += " \033[2m[prompts only]\033[0m"

        # Project tags (first 2 only)
        tags = ""
        if r["project_tags"]:
            tag_list = [t.strip() for t in r["project_tags"].split(",") if t.strip()][:2]
            tags = "  \033[2m" + " ".join(f"[{t}]" for t in tag_list) + "\033[0m"

        print(f"  {ts_str:<20} {title:<40} {msgs:>5}  {dur:>5}  \033[2m{sid}\033[0m{badges}{tags}")

        # Show note if present
        if has_note and r["note"]:
            note = r["note"][:60] + ("…" if len(r["note"]) > 60 else "")
            print(f"  {'':20} \033[33m↳ {note}\033[0m")

        # Show prompt count for history-only sessions
        if show_prompts and kind == "history-only":
            pc = conn.execute(
                "SELECT COUNT(*) FROM claude_prompts WHERE session_id = ?",
                (r["session_id"],)
            ).fetchone()[0]
            if pc:
                print(f"  {'':20} \033[2m{pc} prompts saved\033[0m")

    print()
    hints = []
    if not project_filter:
        hints.append("\033[2m--project NAME\033[0m to filter")
    if not days_filter:
        hints.append("\033[2m--days 7\033[0m for last week")
    if hints:
        print("  " + "  ·  ".join(hints))
    print()

    return 0
