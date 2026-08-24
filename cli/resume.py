"""tm resume — minimal session starter for lazy context loading.

Outputs just enough to tell Claude which session to reference and where things
were left off — without loading any session content into context. Claude then
pulls what it needs on demand with:
    tm context --session ID "specific question"
"""
import re
import subprocess
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
            return "Today " + d.strftime("%H:%M")
        if d.date() == (now - timedelta(days=1)).date():
            return "Yesterday " + d.strftime("%H:%M")
        return d.strftime("%b %d  %H:%M")
    except Exception:
        return "?"


def run(conn, args: list[str]) -> int:
    project_filter = None
    last_n = 1

    i = 0
    while i < len(args):
        if args[i] in ("--project", "-p") and i + 1 < len(args):
            project_filter = args[i + 1]; i += 2
        elif args[i] == "--last" and i + 1 < len(args):
            last_n = int(args[i + 1]); i += 2
        else:
            i += 1

    # ── Fetch most recent session(s) ─────────────────────────────────────────
    sql = """
        SELECT s.session_id, s.started_at, s.kind,
               COUNT(m.id) as msg_count,
               t.title, t.project_tags
        FROM claude_sessions s
        LEFT JOIN claude_messages m ON m.session_id = s.session_id
        LEFT JOIN session_titles t  ON t.session_id = s.session_id
    """
    params: list = []

    if project_filter:
        sql += " WHERE (t.project_tags LIKE ? OR t.title LIKE ?)"
        params += [f"%{project_filter}%", f"%{project_filter}%"]

    sql += " GROUP BY s.session_id ORDER BY s.started_at DESC LIMIT ?"
    params.append(last_n)

    sessions = conn.execute(sql, params).fetchall()

    if not sessions:
        print("\n  No sessions found.\n")
        return 0

    # ── Render ────────────────────────────────────────────────────────────────
    output_lines: list[str] = []

    def p(text: str = ""):
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        print(text)
        output_lines.append(clean)

    p()

    for idx, s in enumerate(sessions):
        sid    = s["session_id"]
        title  = s["title"] or "Untitled session"
        tags   = s["project_tags"] or ""
        msgs   = s["msg_count"] or 0
        ts     = _ts(s["started_at"])
        kind   = s["kind"]

        tag_str  = f"  \033[2m[{tags}]\033[0m" if tags else ""
        hist_str = "  \033[2m[prompts only]\033[0m" if kind == "history-only" else ""

        p(f"  \033[1m{title}\033[0m{tag_str}")
        p(f"  \033[2mID: {sid[:8]}  ·  {ts}  ·  {msgs} messages\033[0m{hist_str}")

        # ── Last 5 user prompts ───────────────────────────────────────────────
        prompts = conn.execute(
            """SELECT prompt_text FROM claude_prompts
               WHERE session_id = ?
               ORDER BY ts DESC LIMIT 5""",
            (sid,)
        ).fetchall()

        if prompts:
            p()
            p("  \033[2mLast prompts:\033[0m")
            for pr in reversed(prompts):
                text = (pr["prompt_text"] or "").strip().replace("\n", " ")
                if len(text) > 80:
                    text = text[:77] + "…"
                p(f"  \033[2m·\033[0m {text}")

        # ── Shell commands from same time window (if logged) ──────────────────
        try:
            started_ms = s["started_at"] or 0
            # Get commands that ran after this session started
            shell_rows = conn.execute(
                """SELECT command, exit_code FROM shell_commands
                   WHERE ts >= ?
                   ORDER BY ts DESC LIMIT 4""",
                (started_ms,)
            ).fetchall()

            if shell_rows:
                p()
                p("  \033[2mLast shell commands:\033[0m")
                for r in reversed(shell_rows):
                    cmd    = (r["command"] or "")[:72]
                    failed = r["exit_code"] and r["exit_code"] != 0
                    status = "\033[31m✗\033[0m" if failed else "\033[2m✓\033[0m"
                    color  = "\033[31m" if failed else "\033[2m"
                    p(f"  {status} {color}{cmd}\033[0m")
        except Exception:
            pass

        # ── Linked sessions ───────────────────────────────────────────────────
        try:
            linked = conn.execute(
                """SELECT sl.linked_to, t.title FROM session_links sl
                   LEFT JOIN session_titles t ON t.session_id = sl.linked_to
                   WHERE sl.session_id = ?""",
                (sid,)
            ).fetchall()
            if linked:
                p()
                p("  \033[2mLinked sessions (also searched):\033[0m")
                for lk in linked:
                    ltitle = (lk["title"] or lk["linked_to"][:8])[:60]
                    p(f"  \033[2m⟵ {ltitle}  ({lk['linked_to'][:8]})\033[0m")
        except Exception:
            pass

        # ── Usage hint ────────────────────────────────────────────────────────
        p()
        p(f"  \033[35m→ tm context --session {sid[:8]} \"what you need\"\033[0m")

        if idx < len(sessions) - 1:
            p()
            p("  " + "─" * 60)

    p()

    # ── Copy to clipboard ─────────────────────────────────────────────────────
    output = "\n".join(output_lines)
    try:
        subprocess.run(["pbcopy"], input=output.encode(), check=True)
        print("  ✓ Copied — paste into new Claude session as the only starter context\n")
    except Exception:
        pass

    return 0
