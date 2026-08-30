"""tm digest — narrative briefing of what you worked on, struggled with, and accomplished."""
import json
import re
import subprocess
import time
from datetime import datetime, date, timedelta
from cli.clipboard import copy as _copy_to_clipboard

OLLAMA_MODEL = "llama3.2"


OLLAMA_URL = "http://localhost:11434/api/generate"


def _check_ollama() -> bool:
    """Check if Ollama is running and llama3.2 is available via REST API."""
    import urllib.request
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return any(OLLAMA_MODEL in m for m in models)
    except Exception:
        return False


def _ollama(prompt: str, timeout: int = 60) -> str | None:
    """Call Ollama REST API — returns clean text with no streaming artifacts."""
    import urllib.request
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 300}
    }).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("response", "").strip() or None
    except Exception:
        return None


def _get_session_messages(conn, session_id: str, max_msgs: int = 20) -> list[dict]:
    """Pull the most informative messages from a session."""
    rows = conn.execute(
        """SELECT role, content_text, ts FROM claude_messages
           WHERE session_id = ?
             AND content_text IS NOT NULL
             AND role IN ('user', 'assistant')
             AND length(content_text) > 20
           ORDER BY ts ASC""",
        (session_id,)
    ).fetchall()

    if not rows:
        # History-only session — use prompts
        rows = conn.execute(
            """SELECT 'user' as role, prompt_text as content_text, ts
               FROM claude_prompts WHERE session_id = ? ORDER BY ts ASC""",
            (session_id,)
        ).fetchall()

    msgs = [dict(r) for r in rows]

    if len(msgs) <= max_msgs:
        return msgs

    # Too many messages — sample: take first 4, last 4, and evenly spaced middle
    middle_count = max_msgs - 8
    step = max(1, (len(msgs) - 8) // middle_count)
    middle = msgs[4:-4][::step][:middle_count]
    return msgs[:4] + middle + msgs[-4:]


def _format_for_ollama(msgs: list[dict], title: str) -> str:
    parts = [f'Session: "{title}"\n']
    for m in msgs:
        role = "User" if m["role"] == "user" else "Claude"
        text = (m["content_text"] or "").strip()
        # Keep it concise — truncate long messages
        if len(text) > 400:
            text = text[:400] + "…"
        # Skip pure tool output lines
        if text.startswith("(Bash completed") or text.startswith("Exit code"):
            continue
        parts.append(f"{role}: {text}")
    return "\n".join(parts)


def _summarise_session(conn, session_id: str, title: str, use_ollama: bool) -> dict:
    """Generate or retrieve a narrative summary for a session."""
    # Check cache first
    cached = conn.execute(
        "SELECT summary, highlights FROM session_summaries WHERE session_id = ?",
        (session_id,)
    ).fetchone()
    if cached and cached["summary"]:
        return {
            "summary": cached["summary"],
            "highlights": json.loads(cached["highlights"] or "[]"),
        }

    msgs = _get_session_messages(conn, session_id)
    if not msgs:
        return {"summary": "No messages available.", "highlights": []}

    if use_ollama:
        context = _format_for_ollama(msgs, title)

        summary_prompt = (
            f"{context}\n\n"
            "Write a 2-3 sentence summary of this work session. "
            "Cover: what was being worked on, any problems or blockers encountered, "
            "and what was accomplished or decided. Be specific. No bullet points."
        )
        summary = _ollama(summary_prompt) or _heuristic_summary(msgs, title)

        highlights_prompt = (
            f"{context}\n\n"
            "List up to 3 of the most important moments from this session as short phrases. "
            "Focus on: key decisions made, problems solved, things that failed, breakthroughs. "
            "Format: one per line, starting with a dash. No explanations, just the moment."
        )
        raw_highlights = _ollama(highlights_prompt, timeout=30) or ""
        highlights = [
            h.lstrip("- •").strip()
            for h in raw_highlights.split("\n")
            if h.strip().startswith(("-", "•")) and len(h.strip()) > 5
        ][:3]
    else:
        summary = _heuristic_summary(msgs, title)
        highlights = _heuristic_highlights(msgs)

    # Cache it
    conn.execute(
        """INSERT OR REPLACE INTO session_summaries
           (session_id, summary, highlights, generated_at, model)
           VALUES (?,?,?,?,?)""",
        (session_id, summary, json.dumps(highlights),
         int(time.time() * 1000), OLLAMA_MODEL if use_ollama else "heuristic")
    )
    conn.commit()

    return {"summary": summary, "highlights": highlights}


def _heuristic_summary(msgs: list[dict], title: str) -> str:
    """Fallback: extract key sentences without Ollama."""
    user_msgs = [m["content_text"].strip() for m in msgs if m["role"] == "user" and m["content_text"]]
    asst_msgs = [m["content_text"].strip() for m in msgs if m["role"] == "assistant" and m["content_text"]]

    first_ask = user_msgs[0][:150] if user_msgs else ""
    last_result = asst_msgs[-1][:150] if asst_msgs else ""

    errors = [m for m in user_msgs + asst_msgs if any(w in m.lower() for w in ["error", "failed", "not working", "issue", "traceback"])]
    error_note = f" Hit an issue: {errors[0][:100]}…" if errors else ""

    return f"Worked on: {first_ask}.{error_note} Result: {last_result}."


def _heuristic_highlights(msgs: list[dict]) -> list[str]:
    highlights = []
    error_patterns  = ["error", "failed", "not working", "traceback", "exception", "crash"]
    solved_patterns = ["fixed", "resolved", "working now", "done", "completed", "pushed"]
    decide_patterns = ["decided", "we'll use", "going with", "the plan is", "instead"]

    for m in msgs:
        text = (m["content_text"] or "").lower()
        orig = (m["content_text"] or "").strip()[:100]
        if any(p in text for p in error_patterns) and "Error" not in highlights:
            highlights.append(f"Problem: {orig}…")
        elif any(p in text for p in solved_patterns) and "Fixed" not in highlights:
            highlights.append(f"Resolved: {orig}…")
        elif any(p in text for p in decide_patterns):
            highlights.append(f"Decision: {orig}…")
        if len(highlights) >= 3:
            break
    return highlights


def _date_sessions(conn, target: date) -> list:
    return conn.execute(
        """SELECT s.session_id, s.started_at, s.kind,
                  COUNT(m.id) as msg_count,
                  MIN(m.ts) as first_msg, MAX(m.ts) as last_msg,
                  t.title, t.project_tags, n.note
           FROM claude_sessions s
           LEFT JOIN claude_messages m  ON m.session_id = s.session_id
           LEFT JOIN session_titles t   ON t.session_id = s.session_id
           LEFT JOIN session_notes n    ON n.session_id = s.session_id
           WHERE date(s.started_at/1000,'unixepoch','localtime') = ?
           GROUP BY s.session_id
           ORDER BY s.started_at ASC""",
        (target.isoformat(),)
    ).fetchall()


def _duration(first, last) -> str:
    try:
        s = datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone()
        e = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone()
        mins = int((e - s).total_seconds() / 60)
        if mins < 60:
            return f"{mins}m"
        return f"{mins//60}h {mins%60}m"
    except Exception:
        return ""


def _tags(tag_str: str | None) -> str:
    if not tag_str:
        return ""
    parts = [t.strip() for t in tag_str.split(",") if t.strip()][:2]
    return "  " + "  ".join(f"\033[2m[{t}]\033[0m" for t in parts)


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
    tag_counts: dict[str, int] = {}
    for row in top:
        for tag in (row["project_tags"] or "").split(","):
            t = tag.strip()
            if t:
                tag_counts[t] = tag_counts.get(t, 0) + 1
    top_tag = max(tag_counts, key=tag_counts.get) if tag_counts else "—"
    return {"sessions": sess, "messages": msgs, "top_project": top_tag}


def run(conn, args: list[str]) -> int:
    # Flags
    days_back  = 1            # default: yesterday + today
    no_ollama  = "--no-ai" in args
    force      = "--refresh" in args  # regenerate cached summaries
    quick      = "--quick" in args    # skip summarization entirely

    if force:
        conn.execute("DELETE FROM session_summaries")
        conn.commit()

    use_ollama = not no_ollama and not quick and _check_ollama()

    today     = date.today()
    yesterday = today - timedelta(days=1)
    now_str   = datetime.now().strftime("%A, %B %d %Y")

    output_lines = []  # clean lines for clipboard
    W = 64

    def p(text="", indent=0):
        """Print and accumulate for clipboard."""
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        print((" " * indent) + text)
        output_lines.append((" " * indent) + clean)

    def hr(char="═"):
        p(char * W)

    hr()
    p(f"\033[1m  TerminalMind Digest  —  {now_str}\033[0m")
    if use_ollama:
        p(f"  \033[2mGenerating narrative summaries with {OLLAMA_MODEL}…\033[0m")
    hr()

    # ── Pinned sessions ───────────────────────────────────────────────────────
    pinned = conn.execute(
        """SELECT s.session_id, t.title, t.project_tags, n.note, COUNT(m.id) as msg_count
           FROM session_pins p
           JOIN claude_sessions s ON s.session_id = p.session_id
           LEFT JOIN session_titles t  ON t.session_id = s.session_id
           LEFT JOIN session_notes n   ON n.session_id = s.session_id
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           GROUP BY s.session_id ORDER BY p.pinned_at DESC"""
    ).fetchall()

    p(f"\n\033[1m📌  PINNED  ({len(pinned)})\033[0m")
    if pinned:
        for r in pinned:
            title = r["title"] or r["session_id"][:8]
            tags  = _tags(r["project_tags"])
            p(f"  • \033[1m{title}\033[0m{tags}")
            if r["note"]:
                p(f"    \033[33m↳ {r['note']}\033[0m")
    else:
        p("  \033[2mNo pinned sessions.\033[0m")

    # ── Session summarizer ────────────────────────────────────────────────────
    def render_sessions(sessions, label: str):
        if not sessions:
            p(f"\n\033[1m{label}\033[0m")
            p("  \033[2mNo sessions.\033[0m")
            return
        p(f"\n\033[1m{label}  ({len(sessions)} session{'s' if len(sessions)!=1 else ''})\033[0m")
        for r in sessions:
            title   = r["title"] or "Untitled session"
            msgs    = r["msg_count"] or 0
            tags    = _tags(r["project_tags"])
            dur     = _duration(r["first_msg"], r["last_msg"]) if r["first_msg"] and r["last_msg"] else ""
            dur_str = f"  \033[2m{dur}\033[0m" if dur else ""
            hist    = "  \033[2m[prompts only]\033[0m" if r["kind"] == "history-only" else ""

            p(f"\n  \033[1m► {title}\033[0m{tags}{dur_str}{hist}")
            p(f"  \033[2m{msgs} messages  ·  {r['session_id'][:8]}\033[0m")

            if quick:
                continue

            info = _summarise_session(conn, r["session_id"], title, use_ollama)

            # Narrative summary
            if info["summary"]:
                # Word-wrap at ~58 chars
                words   = info["summary"].split()
                lines   = []
                current = ""
                for w in words:
                    if len(current) + len(w) + 1 > 58:
                        lines.append(current)
                        current = w
                    else:
                        current = f"{current} {w}".strip()
                if current:
                    lines.append(current)
                for line in lines:
                    p(f"  {line}")

            # Highlights
            if info["highlights"]:
                p("")
                for h in info["highlights"]:
                    p(f"  \033[35m•\033[0m {h}")

    # ── Today ─────────────────────────────────────────────────────────────────
    today_sessions = _date_sessions(conn, today)
    render_sessions(today_sessions, "🕐  TODAY")

    # ── Yesterday ─────────────────────────────────────────────────────────────
    yday_sessions = _date_sessions(conn, yesterday)
    yday_label    = yesterday.strftime("%A, %B %d")
    render_sessions(yday_sessions, f"📅  YESTERDAY — {yday_label}")

    # ── Week summary ──────────────────────────────────────────────────────────
    ws = _week_stats(conn)
    p(f"\n\033[1m📊  THIS WEEK\033[0m")
    p(f"  {ws['sessions']} sessions  ·  {ws['messages']} prompts  ·  Top: \033[35m{ws['top_project']}\033[0m")

    p("")
    hr("─")

    # Copy clean version to clipboard
    clean_output = "\n".join(output_lines)
    try:
        if _copy_to_clipboard(clean_output):
            print("\n  ✓ Copied to clipboard\n")
    except Exception:
        pass

    if quick:
        print("  (run without --quick for AI narrative summaries)\n")

    return 0
