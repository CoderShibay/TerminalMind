"""tm context — extract relevant message excerpts for pasting into Claude."""
import re
import sys
from datetime import datetime


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


def run(conn, args: list[str]) -> int:
    if not args:
        print("Usage:")
        print("  tm context \"question\"                  quick excerpts (~240 chars each)")
        print("  tm context \"question\" --full           full messages with code/errors preserved")
        print("  tm context \"question\" --session ID     search within one session only")
        print("  tm context \"question\" --top 5          limit to 5 results")
        return 1

    # Parse args
    query_parts = []
    top_n = 8
    session_filter = None
    full = False
    char_limit = 240

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
        else:
            query_parts.append(args[i]); i += 1

    query = " ".join(query_parts)
    if not query:
        print("Error: provide a search query.")
        return 1

    # ── Expand session filter to include linked sessions ──────────────────────
    session_ids: list[str] | None = None
    if session_filter:
        # Resolve partial ID
        row = conn.execute(
            "SELECT session_id FROM claude_sessions WHERE session_id LIKE ?",
            (session_filter + "%",)
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
            """SELECT m.id, m.role, m.content_text, m.ts, m.session_id
               FROM messages_fts fts
               JOIN claude_messages m ON m.id = fts.rowid
               WHERE messages_fts MATCH ?
                 AND m.content_text IS NOT NULL
                 AND m.role IN ('user','assistant')
               ORDER BY m.ts DESC LIMIT 30""",
            (query,)
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
            "SELECT id, role, content_text, ts, session_id FROM claude_messages WHERE id=?",
            (msg_id,)
        ).fetchone()
        if not row or not row["content_text"]:
            continue
        if row["role"] not in ("user", "assistant"):
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
                """SELECT id, role, content_text, ts, session_id
                   FROM claude_messages
                   WHERE session_id IN ({})
                     AND role IN ('user','assistant')
                     AND content_text IS NOT NULL
                     AND ABS(id - ?) <= 3
                   ORDER BY id ASC""".format(",".join("?" * len(session_ids))),
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
    lines = [f'Context from your Claude history — "{query}" [{mode_label}]', ""]

    for sid, msgs in by_session.items():
        title_row = conn.execute(
            "SELECT title FROM session_titles WHERE session_id=?", (sid,)
        ).fetchone()
        title = title_row["title"] if title_row else sid[:8] + "…"
        first_ts = min((m["ts"] for m in msgs if m["ts"]), default="")
        lines.append(f"─── {title} · {_ts(first_ts)} ───")

        for msg in msgs:
            role = "YOU " if msg["role"] == "user" else "ASST"
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

    try:
        import subprocess
        subprocess.run(["pbcopy"], input=output.encode(), check=True)
        print(f"  ✓ Copied to clipboard — paste into Claude\n")
    except Exception:
        pass

    return 0
