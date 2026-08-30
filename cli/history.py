"""tm history — chronological session timeline, filterable by project or date."""
from datetime import datetime, timedelta


def _ts_ms(ts) -> int:
    """Convert any timestamp to epoch ms."""
    if not ts:
        return 0
    if isinstance(ts, int):
        return ts
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        return int(d.timestamp() * 1000)
    except Exception:
        return 0


def _fmt_time(ts_ms: int, seconds: bool = False) -> str:
    """Format epoch ms as HH:MM or HH:MM:SS."""
    if not ts_ms:
        return "?"
    try:
        d = datetime.fromtimestamp(ts_ms / 1000)
        return d.strftime("%H:%M:%S") if seconds else d.strftime("%H:%M")
    except Exception:
        return "?"


def _fmt_date(ts_ms: int) -> str:
    """Format epoch ms as Today / Yesterday / Aug 24."""
    if not ts_ms:
        return "?"
    try:
        d = datetime.fromtimestamp(ts_ms / 1000)
        now = datetime.now()
        if d.date() == now.date():
            return "Today"
        if d.date() == (now - timedelta(days=1)).date():
            return "Yesterday"
        return d.strftime("%b %d")
    except Exception:
        return "?"


def _ts(ts) -> str:
    """Original formatter for non-interleaved display."""
    ms = _ts_ms(ts)
    if not ms:
        return "?"
    d = _fmt_date(ms)
    t = _fmt_time(ms)
    pad = "      " if d in ("Today", "Yesterday") else "  "
    return f"{d}{pad}{t}"


def _duration(start, end) -> str:
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


def _shell_dur(ms) -> str:
    if ms is None or ms < 0:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms // 60000}m {(ms % 60000) // 1000}s"


def run(conn, args: list[str]) -> int:
    # ── Parse flags ───────────────────────────────────────────────────────────
    project_filter = None
    days_filter    = None
    limit          = 50
    show_prompts   = False
    show_shell     = False

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
        elif args[i] == "--shell":
            show_shell = True; i += 1
        else:
            i += 1

    # ── Fetch Claude sessions ─────────────────────────────────────────────────
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

    session_rows = conn.execute(sql, params).fetchall()

    # ── Header ────────────────────────────────────────────────────────────────
    filter_parts = []
    if project_filter: filter_parts.append(f"project: {project_filter}")
    if days_filter:    filter_parts.append(f"last {days_filter} days")
    if show_shell:     filter_parts.append("+ shell commands")
    filter_str = "  —  " + ", ".join(filter_parts) if filter_parts else ""

    print()

    if not session_rows and not show_shell:
        print("  No sessions found.\n")
        return 0

    # ── Interleaved mode (--shell) ────────────────────────────────────────────
    if show_shell:
        return _render_interleaved(conn, session_rows, project_filter, days_filter, filter_str)

    # ── Standard mode ─────────────────────────────────────────────────────────
    print(f"  \033[1mSession History{filter_str}\033[0m  ({len(session_rows)} sessions)\n")
    print(f"  {'DATE & TIME':<20} {'TITLE':<40} {'MSGS':>5}  {'DUR':>5}  ID")
    print("  " + "─" * 82)

    current_date = None
    for r in session_rows:
        ts_str    = _ts(r["started_at"])
        date_part = ts_str[:10].strip()

        if date_part != current_date and date_part not in ("Today", "Yesterday"):
            if current_date is not None:
                print()
            current_date = date_part

        title     = (r["title"] or "Untitled session")[:40]
        msgs      = r["msg_count"] or 0
        dur       = _duration(r["first_msg"], r["last_msg"])
        sid       = r["session_id"][:8]
        is_pinned = r["is_pinned"]
        has_note  = bool(r["note"])
        kind      = r["kind"]

        badges = ""
        if is_pinned:             badges += " 📌"
        if has_note:              badges += " ✎"
        if kind == "history-only": badges += " \033[2m[prompts only]\033[0m"

        tags = ""
        if r["project_tags"]:
            tag_list = [t.strip() for t in r["project_tags"].split(",") if t.strip()][:2]
            tags = "  \033[2m" + " ".join(f"[{t}]" for t in tag_list) + "\033[0m"

        print(f"  {ts_str:<20} {title:<40} {msgs:>5}  {dur:>5}  \033[2m{sid}\033[0m{badges}{tags}")

        if has_note and r["note"]:
            note = r["note"][:60] + ("…" if len(r["note"]) > 60 else "")
            print(f"  {'':20} \033[33m↳ {note}\033[0m")

        if show_prompts and kind == "history-only":
            pc = conn.execute(
                "SELECT COUNT(*) FROM claude_prompts WHERE session_id = ?",
                (r["session_id"],)
            ).fetchone()[0]
            if pc:
                print(f"  {'':20} \033[2m{pc} prompts saved\033[0m")

    print()
    hints = []
    if not project_filter: hints.append("\033[2m--project NAME\033[0m to filter")
    if not days_filter:    hints.append("\033[2m--days 7\033[0m for last week")
    hints.append("\033[2m--shell\033[0m to include terminal commands")
    if hints:
        print("  " + "  ·  ".join(hints))
    print()
    return 0


def _render_interleaved(conn, session_rows, project_filter, days_filter, filter_str) -> int:
    """Render Claude sessions and shell commands merged into one chronological timeline."""

    # ── Fetch shell commands ──────────────────────────────────────────────────
    shell_sql = """
        SELECT ts, duration_ms, exit_code, command, cwd
        FROM shell_commands WHERE 1=1
    """
    shell_params: list = []

    if days_filter:
        cutoff_ms = int((datetime.now() - timedelta(days=days_filter)).timestamp() * 1000)
        shell_sql += " AND ts >= ?"
        shell_params.append(cutoff_ms)

    if project_filter:
        shell_sql += " AND cwd LIKE ?"
        shell_params.append(f"%{project_filter}%")

    shell_sql += " ORDER BY ts DESC LIMIT 500"

    try:
        shell_rows = conn.execute(shell_sql, shell_params).fetchall()
    except Exception:
        shell_rows = []

    # ── Build unified event list ──────────────────────────────────────────────
    events = []

    for r in session_rows:
        events.append({"kind": "session", "ts": r["started_at"] or 0, "row": r})

    for r in shell_rows:
        events.append({"kind": "shell", "ts": r["ts"] or 0, "row": r})

    events.sort(key=lambda e: e["ts"], reverse=True)

    total_sessions = sum(1 for e in events if e["kind"] == "session")
    total_cmds     = sum(1 for e in events if e["kind"] == "shell")

    print(f"  \033[1mTimeline{filter_str}\033[0m  "
          f"({total_sessions} sessions · {total_cmds} commands)\n")

    if not events:
        print("  Nothing found.\n")
        return 0

    current_date = None

    for ev in events:
        date_str = _fmt_date(ev["ts"])
        time_str = _fmt_time(ev["ts"], seconds=(ev["kind"] == "shell"))

        # Date header
        if date_str != current_date:
            if current_date is not None:
                print()
            print(f"  \033[1m{date_str}\033[0m")
            print("  " + "─" * 74)
            current_date = date_str

        if ev["kind"] == "session":
            r       = ev["row"]
            title   = (r["title"] or "Untitled session")[:38]
            msgs    = r["msg_count"] or 0
            dur     = _duration(r["first_msg"], r["last_msg"])
            sid     = r["session_id"][:8]
            tags    = ""
            if r["project_tags"]:
                tag_list = [t.strip() for t in r["project_tags"].split(",") if t.strip()][:2]
                tags = "  \033[2m" + " ".join(f"[{t}]" for t in tag_list) + "\033[0m"
            pin  = " 📌" if r["is_pinned"] else ""
            note = f"\n  {'':10} \033[33m↳ {r['note'][:60]}\033[0m" if r["note"] else ""

            print(f"  {time_str}  \033[1m►\033[0m {title:<38} "
                  f"\033[2m{msgs} msgs  {dur:>4}  {sid}\033[0m{pin}{tags}{note}")

        else:  # shell command
            r        = ev["row"]
            cmd      = (r["command"] or "")
            dur      = _shell_dur(r["duration_ms"])
            exit_c   = r["exit_code"]
            proj     = (r["cwd"] or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            proj_str = f"  \033[2m[{proj}]\033[0m" if proj else ""

            display_cmd = cmd[:60] + ("…" if len(cmd) > 60 else "")

            if exit_c and exit_c != 0:
                status = f"\033[31m✗ {exit_c}\033[0m"
                cmd_color, reset = "\033[31m", "\033[0m"
            else:
                status = "\033[2m✓\033[0m  "
                cmd_color, reset = "\033[2m", "\033[0m"

            print(f"  {time_str}    $ {cmd_color}{display_cmd}{reset}  "
                  f"\033[2m{dur}\033[0m  {status}{proj_str}")

    print()
    hints = []
    if not project_filter: hints.append("\033[2m--project NAME\033[0m")
    if not days_filter:    hints.append("\033[2m--days 7\033[0m for last week")
    if hints:
        print("  Filter: " + "  ·  ".join(hints))
    if total_cmds == 500:
        print("  \033[2m(shell commands capped at 500 — use --days to narrow the range)\033[0m")
    print()
    return 0
