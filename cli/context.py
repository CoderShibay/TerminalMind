"""tm context — extract relevant excerpts from indexed AI sessions."""
import re
import sys
from datetime import datetime
from cli.clipboard import copy as _copy_to_clipboard


def _ts(ts: str | None) -> str:
    if not ts:
        return "?"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def _has_code_or_errors(text: str) -> bool:
    """Detect if a message contains code, errors, or stack traces."""
    indicators = ["```", "Traceback", "Error:", "error:", "Exception", "  File \"",
                  "def ", "class ", "import ", "    at ", "TypeError", "ValueError",
                  "AttributeError", "ModuleNotFoundError", "$ ", ">>>"]
    return any(ind in text for ind in indicators)


def _format_message(text: str, full: bool, char_limit: int) -> str:
    """Format a message for context output."""
    text = (text or "").strip()
    if full or _has_code_or_errors(text):
        # Preserve formatting — keep newlines, raise limit
        limit = char_limit if full else min(char_limit, 3000)
        if len(text) > limit:
            # Try to cut at a natural boundary (end of a code block or sentence)
            cut = text[:limit]
            last_fence = cut.rfind("```")
            last_nl = cut.rfind("\n")
            cutpoint = max(last_fence, last_nl, limit - 200)
            text = text[:cutpoint].rstrip() + f"\n… [{len(text) - cutpoint} more chars]"
        return text
    else:
        # Prose — flatten and trim
        flat = re.sub(r"\s+", " ", text)
        if len(flat) > char_limit:
            flat = flat[:char_limit - 3] + "…"
        return flat


def _session_match_sql() -> str:
    return "(session_id LIKE ? OR native_session_id LIKE ?)"


def _tail_mode(conn, session_filter: str | None, limit: int, char_limit: int,
               agent_filter: str | None = None) -> int:
    """Return the last N messages of a session in chronological order — no search needed."""
    import re
    import subprocess

    if not session_filter:
        # Default to most recent session
        sql = "SELECT session_id FROM claude_sessions"
        params = []
        if agent_filter:
            sql += " WHERE agent = ?"
            params.append(agent_filter)
        row = conn.execute(sql + " ORDER BY started_at DESC LIMIT 1", params).fetchone()
        if not row:
            print("\n  No sessions found.\n")
            return 0
        session_filter = row["session_id"][:8]

    # Resolve + expand links
    row = conn.execute(
        f"SELECT session_id FROM claude_sessions WHERE {_session_match_sql()}",
        (session_filter + "%", session_filter + "%")
    ).fetchone()
    if not row:
        print(f"\n  Session not found: {session_filter}\n")
        return 1

    root_sid = row["session_id"]
    visited: set[str] = set()
    queue = [root_sid]
    while queue:
        sid = queue.pop()
        if sid in visited:
            continue
        visited.add(sid)
        linked = conn.execute(
            """SELECT linked_to FROM session_links WHERE session_id=?
               UNION SELECT session_id FROM session_links WHERE linked_to=?""",
            (sid, sid)
        ).fetchall()
        for r in linked:
            if r[0] not in visited:
                queue.append(r[0])
    session_ids = list(visited)

    placeholders = ",".join("?" * len(session_ids))
    messages = conn.execute(
        f"""SELECT role, content_text, ts, session_id, agent FROM claude_messages
            WHERE session_id IN ({placeholders})
              AND role IN ('user', 'assistant')
              AND content_text IS NOT NULL
            ORDER BY ts DESC LIMIT ?""",
        (*session_ids, limit)
    ).fetchall()

    if not messages:
        # Fall back to prompts for history-only sessions
        messages = conn.execute(
            f"""SELECT 'user' as role, prompt_text as content_text, ts, session_id,
                       'claude' as agent
                FROM claude_prompts WHERE session_id IN ({placeholders})
                ORDER BY ts DESC LIMIT ?""",
            (*session_ids, limit)
        ).fetchall()

    if not messages:
        print(f"\n  No messages found for session {session_filter}\n")
        return 0

    # Reverse to chronological order
    messages = list(reversed(messages))

    title_row = conn.execute(
        "SELECT title FROM session_titles WHERE session_id=?", (root_sid,)
    ).fetchone()
    title = (title_row["title"] if title_row else None) or root_sid[:8]

    output_lines = []

    def p(text=""):
        clean = re.sub(r"\033\[[0-9;]*m", "", text)
        print(text)
        output_lines.append(clean)

    label = f"last {len(messages)} messages"
    if len(session_ids) > 1:
        label += f" ({len(session_ids)} linked sessions)"

    p()
    p(f'Tail of "{title}" · {root_sid[:8]} [{label}]')
    p()

    for msg in messages:
        role  = "YOU " if msg["role"] == "user" else str(msg["agent"]).upper()
        text  = _format_message(msg["content_text"], True, char_limit)
        ts    = _ts(msg["ts"])
        p(f"\n{role}:  \033[2m{ts}\033[0m")
        p(text)

    p()
    approx = _approx_tokens("\n".join(output_lines))
    p(f"[{len(messages)} message(s) · ~{approx} tokens · tail]")

    output = "\n".join(output_lines)
    print("\n" + output + "\n")

    if _copy_to_clipboard(output):
        print("  ✓ Copied to clipboard — paste into new Claude session\n")

    return 0


def run(conn, args: list[str]) -> int:
    if not args:
        print("Usage:")
        print("  tm context \"question\"                  quick excerpts (~240 chars each)")
        print("  tm context \"question\" --full           full messages with code/errors preserved")
        print("  tm context \"question\" --session ID     search within one session only")
        print("  tm context \"question\" --top 5          limit to 5 results")
        print("  tm context \"question\" --agent codex    filter by claude, codex, or agy")
        print("  tm context --session ID --tail         last 20 messages in order (no query needed)")
        return 1

    # Parse args
    query_parts = []
    top_n      = 8
    session_filter = None
    full       = False
    tail       = False
    char_limit = 240
    agent_filter = None

    i = 0
    while i < len(args):
        if args[i] == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1]); i += 2
        elif args[i] == "--session" and i + 1 < len(args):
            session_filter = args[i + 1]; i += 2
        elif args[i] == "--full":
            full = True
            char_limit = 4000
            i += 1
        elif args[i] == "--tail":
            tail = True
            full = True          # tail always preserves full formatting
            char_limit = 4000
            i += 1
        elif args[i] == "--agent" and i + 1 < len(args):
            agent_filter = args[i + 1].lower(); i += 2
            if agent_filter not in {"claude", "codex", "agy"}:
                print("--agent must be claude, codex, or agy")
                return 1
        else:
            query_parts.append(args[i]); i += 1

    query = " ".join(query_parts)

    # --tail mode: no query needed — just return recent messages in order
    if tail:
        return _tail_mode(conn, session_filter, top_n if top_n != 8 else 20, char_limit, agent_filter)

    if not query:
        print("The command needs a query. Did you mean:")
        print("  tm context \"some topic\" --session ID --full")
        print("  tm context --session ID --tail       (no query — last 20 messages in order)")
        return 1

    # ── Expand session filter to include linked sessions ──────────────────────
    session_ids: list[str] | None = None
    if session_filter:
        # Resolve partial ID
        row = conn.execute(
            f"SELECT session_id FROM claude_sessions WHERE {_session_match_sql()}",
            (session_filter + "%", session_filter + "%")
        ).fetchone()
        if row:
            root_sid = row["session_id"]
            # Walk link graph to find all linked sessions
            try:
                visited: set[str] = set()
                queue = [root_sid]
                while queue:
                    sid = queue.pop()
                    if sid in visited:
                        continue
                    visited.add(sid)
                    linked = conn.execute(
                        """SELECT linked_to FROM session_links WHERE session_id = ?
                           UNION
                           SELECT session_id FROM session_links WHERE linked_to = ?""",
                        (sid, sid)
                    ).fetchall()
                    for r in linked:
                        if r[0] not in visited:
                            queue.append(r[0])
                session_ids = list(visited)
            except Exception:
                session_ids = [root_sid]
        else:
            session_ids = [session_filter]

        if len(session_ids) > 1:
            titles = []
            for sid in session_ids:
                t = conn.execute(
                    "SELECT title FROM session_titles WHERE session_id=?", (sid,)
                ).fetchone()
                titles.append((t["title"] if t else None) or sid[:8])
            print(f"\n  \033[2mSearching {len(session_ids)} linked sessions: {', '.join(titles)}\033[0m")

    import numpy as np

    # ── Semantic search ───────────────────────────────────────────────────────
    try:
        sys.path.insert(0, __file__.rsplit("/", 2)[0])
        from indexer.embedder import embed_query, load_matrix
        matrix, ids = load_matrix(conn)
        if matrix is not None:
            qvec = embed_query(query)
            scores = matrix @ qvec
            ranked = [(float(scores[i]), ids[i]) for i in np.argsort(scores)[::-1]]
        else:
            ranked = []
    except Exception:
        ranked = []

    # ── Keyword search ────────────────────────────────────────────────────────
    try:
        kw_rows = conn.execute(
            """SELECT m.id, m.role, m.content_text, m.ts, m.session_id, m.agent
               FROM messages_fts fts
               JOIN claude_messages m ON m.id = fts.rowid
               WHERE messages_fts MATCH ?
                 AND m.content_text IS NOT NULL
                 AND m.role IN ('user','assistant')
               AND (? IS NULL OR m.agent = ?)
               ORDER BY m.ts DESC LIMIT 30""",
            (query, agent_filter, agent_filter)
        ).fetchall()
    except Exception:
        kw_rows = []

    # ── Merge ─────────────────────────────────────────────────────────────────
    seen_ids, selected = set(), []

    for score, msg_id in ranked:
        if score < 0.20:
            break
        if msg_id in seen_ids:
            continue
        row = conn.execute(
            "SELECT id, role, content_text, ts, session_id, agent FROM claude_messages WHERE id=?",
            (msg_id,)
        ).fetchone()
        if not row or not row["content_text"]:
            continue
        if row["role"] not in ("user", "assistant"):
            continue
        if agent_filter and row["agent"] != agent_filter:
            continue
        if session_ids is not None and row["session_id"] not in session_ids:
            continue
        seen_ids.add(msg_id)
        selected.append({"score": score, "source": "semantic", **dict(row)})
        if len(selected) >= top_n:
            break

    for row in kw_rows:
        if row["id"] in seen_ids:
            continue
        if session_ids is not None and row["session_id"] not in session_ids:
            continue
        seen_ids.add(row["id"])
        selected.append({"score": 0.0, "source": "keyword", **dict(row)})
        if len(selected) >= top_n:
            break

    # ── If --full and session filter, also grab surrounding messages ──────────
    # Context window: pull messages before/after each match so Claude
    # sees the full exchange, not just isolated lines.
    if full and session_ids and selected:
        all_match_ids = {m["id"] for m in selected}
        # For each matched message, fetch 2 messages before and 2 after in the session
        extra = []
        for msg in list(selected):
            rows = conn.execute(
                """WITH ordered AS (
                       SELECT id, role, content_text, ts, session_id, agent,
                              ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY id) AS rn
                       FROM claude_messages
                       WHERE session_id IN ({})
                         AND role IN ('user','assistant')
                         AND content_text IS NOT NULL
                   ), target AS (
                       SELECT session_id, rn FROM ordered WHERE id = ?
                   )
                   SELECT o.id, o.role, o.content_text, o.ts, o.session_id, o.agent
                   FROM ordered o JOIN target t ON t.session_id = o.session_id
                   WHERE o.rn BETWEEN t.rn - 2 AND t.rn + 2
                   ORDER BY o.id ASC""".format(",".join("?" * len(session_ids))),
                (*session_ids, msg["id"])
            ).fetchall()
            for r in rows:
                if r["id"] not in all_match_ids and r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    all_match_ids.add(r["id"])
                    extra.append({"score": 0.0, "source": "context", **dict(r)})
        # Insert extras in timestamp order alongside selected
        selected = sorted(selected + extra, key=lambda m: m.get("ts") or "")

    if not selected:
        print(f"\n  No relevant context found for: {query!r}\n")
        return 0

    # ── Group by session ──────────────────────────────────────────────────────
    by_session: dict[str, list] = {}
    for msg in selected:
        sid = msg["session_id"]
        if sid not in by_session:
            by_session[sid] = []
        by_session[sid].append(msg)

    # ── Format ────────────────────────────────────────────────────────────────
    mode_label = "full content" if full else "excerpts"
    source_label = f"your {agent_filter.upper()} history" if agent_filter else "your AI history"
    lines = [f'Context from {source_label} — "{query}" [{mode_label}]', ""]

    for sid, msgs in by_session.items():
        title_row = conn.execute(
            """SELECT t.title, s.agent, s.native_session_id
               FROM claude_sessions s LEFT JOIN session_titles t ON t.session_id=s.session_id
               WHERE s.session_id=?""", (sid,)
        ).fetchone()
        title = title_row["title"] if title_row else sid[:8] + "…"
        agent = title_row["agent"] if title_row else msgs[0].get("agent", "ai")
        native_id = title_row["native_session_id"] if title_row else sid
        first_ts = min((m["ts"] for m in msgs if m["ts"]), default="")
        lines.append(f"─── {agent.upper()} · {title} · {_ts(first_ts)} · {(native_id or sid)[:8]} ───")

        for msg in msgs:
            role = "YOU " if msg["role"] == "user" else str(msg.get("agent") or agent).upper()
            text = _format_message(msg["content_text"], full, char_limit)
            if full or _has_code_or_errors(msg["content_text"] or ""):
                # Multi-line: add a blank line after each message for readability
                lines.append(f"\n{role}:\n{text}\n")
            else:
                lines.append(f"{role}: {text}")

        lines.append("")

    approx_tok = _approx_tokens("\n".join(lines))
    lines.append(f"[{len(selected)} message(s) · ~{approx_tok} tokens · {mode_label}]")

    output = "\n".join(lines)
    print("\n" + output + "\n")

    if _copy_to_clipboard(output):
        print("  ✓ Copied to clipboard\n")

    return 0
