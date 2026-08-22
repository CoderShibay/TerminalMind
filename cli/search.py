"""tm search — full-text search across all Claude conversations."""
import sys
from datetime import datetime, timedelta


def _parse_time_filter(flag: str) -> str | None:
    """Convert --last 7d / 30d / 24h to a SQLite datetime string."""
    flag = flag.strip()
    now = datetime.now()
    if flag.endswith("d"):
        days = int(flag[:-1])
        cutoff = now - timedelta(days=days)
    elif flag.endswith("h"):
        hours = int(flag[:-1])
        cutoff = now - timedelta(hours=hours)
    else:
        return None
    return cutoff.isoformat()


def _ts_to_human(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def run(conn, args: list[str]) -> int:
    if not args:
        print("Usage: tm search <query> [--last 7d|30d|24h] [--project NAME]")
        return 1

    # Parse args
    query_parts = []
    time_filter = None
    project_filter = None
    i = 0
    while i < len(args):
        if args[i] == "--last" and i + 1 < len(args):
            time_filter = _parse_time_filter(args[i + 1])
            i += 2
        elif args[i] == "--project" and i + 1 < len(args):
            project_filter = args[i + 1]
            i += 2
        else:
            query_parts.append(args[i])
            i += 1

    query = " ".join(query_parts)
    if not query:
        print("Error: provide a search query.")
        return 1

    results = []

    # Search messages (full conversations)
    msg_sql = """
        SELECT
            m.role,
            m.content_text,
            m.tool_name,
            m.ts,
            m.project,
            m.cwd,
            m.session_id
        FROM messages_fts fts
        JOIN claude_messages m ON m.id = fts.rowid
        WHERE messages_fts MATCH ?
    """
    msg_params = [query]

    if time_filter:
        msg_sql += " AND m.ts >= ?"
        msg_params.append(time_filter)
    if project_filter:
        msg_sql += " AND m.project LIKE ?"
        msg_params.append(f"%{project_filter}%")

    msg_sql += " ORDER BY m.ts DESC LIMIT 30"

    for row in conn.execute(msg_sql, msg_params):
        text = row["content_text"] or f"[tool: {row['tool_name']}]"
        results.append({
            "source": "conversation",
            "role": row["role"],
            "text": text,
            "ts": row["ts"],
            "project": row["project"] or "unknown",
            "session_id": row["session_id"],
        })

    # Search prompts (history.jsonl — deduplicate by session already in messages)
    prompt_sql = """
        SELECT
            p.prompt_text,
            p.ts,
            p.project,
            p.session_id
        FROM prompts_fts fts
        JOIN claude_prompts p ON p.id = fts.rowid
        WHERE prompts_fts MATCH ?
    """
    prompt_params = [query]

    if project_filter:
        prompt_sql += " AND p.project LIKE ?"
        prompt_params.append(f"%{project_filter}%")

    prompt_sql += " ORDER BY p.ts DESC LIMIT 20"

    seen_sessions = {r["session_id"] for r in results}
    for row in conn.execute(prompt_sql, prompt_params):
        if row["session_id"] in seen_sessions:
            continue
        ts_dt = datetime.fromtimestamp(row["ts"] / 1000).isoformat() if row["ts"] else None
        results.append({
            "source": "prompt_history",
            "role": "user",
            "text": row["prompt_text"],
            "ts": ts_dt,
            "project": row["project"] or "?",
            "session_id": row["session_id"],
        })

    if not results:
        print(f"No results for: {query!r}")
        return 0

    # Display
    print(f"\n  {len(results)} result(s) for: {query!r}\n")
    print("─" * 72)

    current_session = None
    for r in results:
        if r["session_id"] != current_session:
            current_session = r["session_id"]
            print(f"\n  SESSION  {r['session_id'][:8]}…  │  {r['project']}  │  {_ts_to_human(r['ts'])}")
            print("  " + "─" * 68)

        role_label = "YOU  " if r["role"] == "user" else "ASST "
        text = r["text"].replace("\n", " ").strip()
        # Highlight query terms in output
        for term in query.split():
            text = text.replace(term, f"\033[1;33m{term}\033[0m")
            text = text.replace(term.lower(), f"\033[1;33m{term.lower()}\033[0m")
            text = text.replace(term.capitalize(), f"\033[1;33m{term.capitalize()}\033[0m")

        # Wrap at 65 chars
        words = text.split(" ")
        lines = []
        current_line = ""
        # approximate wrapping (ignores ANSI escape len)
        for word in words:
            if len(current_line) + len(word) > 65:
                lines.append(current_line)
                current_line = word
            else:
                current_line = f"{current_line} {word}".strip()
        if current_line:
            lines.append(current_line)

        prefix = f"  {role_label}│ "
        blank  = "       │ "
        for j, line in enumerate(lines[:4]):  # max 4 lines per result
            print(f"{prefix if j == 0 else blank}{line}")
        if len(lines) > 4:
            print(f"{blank}\033[2m…{len(lines)-4} more lines\033[0m")

    print("\n" + "─" * 72)
    print(f"  Use `tm sessions --project <name>` for full session list\n")
    return 0
