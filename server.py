"""FastAPI server for TerminalMind dashboard — `tm serve`."""
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

import sys
sys.path.insert(0, str(Path(__file__).parent))
from db import init_db

app = FastAPI(title="TerminalMind")
_local = threading.local()

HTML_PATH = Path(__file__).parent / "dashboard.html"

# Embedding matrix cache — rebuilt after each sync
_embed_matrix: "np.ndarray | None" = None
_embed_ids:    "list[int] | None"   = None
_embed_dirty   = True   # True = needs rebuild on next semantic search


def get_conn():
    """Return a per-thread SQLite connection (SQLite is not thread-safe)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = init_db()
    return _local.conn


def _get_embed_cache(conn):
    global _embed_matrix, _embed_ids, _embed_dirty
    if _embed_dirty or _embed_matrix is None:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent))
            from indexer.embedder import load_matrix
            import numpy as np
            _embed_matrix, _embed_ids = load_matrix(conn)
            _embed_dirty = False
        except Exception:
            return None, None
    return _embed_matrix, _embed_ids


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    conn = get_conn()
    total_prompts  = conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM claude_messages").fetchone()[0]
    total_sessions = conn.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0]
    user_msgs      = conn.execute("SELECT COUNT(*) FROM claude_messages WHERE role='user'").fetchone()[0]
    asst_msgs      = conn.execute("SELECT COUNT(*) FROM claude_messages WHERE role='assistant'").fetchone()[0]

    db_path = Path(__file__).parent / "db" / "terminalmd.db"
    db_size_kb = db_path.stat().st_size / 1024 if db_path.exists() else 0

    projects = conn.execute("""
        SELECT project, COUNT(*) as c
        FROM claude_messages
        WHERE project IS NOT NULL AND project NOT IN (?, 'home', '')
        GROUP BY project ORDER BY c DESC LIMIT 15
    """, (Path.home().name,)).fetchall()

    # If no project data, derive activity from sessions' first messages
    if not projects:
        projects = []

    active = conn.execute("""
        SELECT session_id, pid, cwd, project, started_at
        FROM claude_sessions WHERE status = 'active'
        ORDER BY started_at DESC
    """).fetchall()

    return {
        "total_prompts": total_prompts,
        "total_messages": total_messages,
        "total_sessions": total_sessions,
        "user_messages": user_msgs,
        "assistant_messages": asst_msgs,
        "db_size_kb": round(db_size_kb, 1),
        "projects": [{"name": r["project"], "count": r["c"]} for r in projects],
        "active_sessions": [
            {"session_id": r["session_id"], "pid": r["pid"],
             "cwd": r["cwd"], "project": r["project"], "started_at": r["started_at"]}
            for r in active
        ],
    }


@app.get("/api/sessions")
def api_sessions(tag: str = Query(default=""), limit: int = Query(default=200)):
    conn = get_conn()
    sql = """
        SELECT s.session_id, s.started_at, s.updated_at, s.status, s.kind,
               COUNT(m.id) as msg_count,
               MIN(m.ts) as first_msg,
               MAX(m.ts) as last_msg,
               t.title, t.summary, t.project_tags, t.method
        FROM claude_sessions s
        LEFT JOIN claude_messages m ON m.session_id = s.session_id
        LEFT JOIN session_titles t ON t.session_id = s.session_id
        LEFT JOIN (SELECT session_id, COUNT(*) as prompt_count
                   FROM claude_prompts GROUP BY session_id) p
                  ON p.session_id = s.session_id
    """
    params = []
    if tag:
        sql += " WHERE t.project_tags LIKE ?"
        params.append(f"%{tag}%")
    sql += " GROUP BY s.session_id ORDER BY s.started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # For history-only sessions, use prompt_count as msg_count
        if not d["msg_count"] and d.get("kind") == "history-only":
            d["msg_count"] = d.get("prompt_count", 0)
        result.append(d)
    return result


@app.get("/api/session/{session_id}")
def api_session_detail(session_id: str):
    conn = get_conn()
    messages = conn.execute(
        """SELECT role, content_text, tool_name, ts
           FROM claude_messages
           WHERE session_id = ?
             AND (content_text IS NOT NULL OR tool_name IS NOT NULL)
           ORDER BY ts ASC""",
        (session_id,),
    ).fetchall()

    if messages:
        return [dict(m) for m in messages]

    # History-only session — return prompts formatted as messages
    prompts = conn.execute(
        """SELECT prompt_text as content_text, ts, 'user' as role, NULL as tool_name
           FROM claude_prompts WHERE session_id = ? ORDER BY ts ASC""",
        (session_id,),
    ).fetchall()
    return [dict(p) for p in prompts]


@app.get("/api/tags")
def api_tags():
    conn = get_conn()
    rows = conn.execute(
        "SELECT project_tags FROM session_titles WHERE project_tags IS NOT NULL AND project_tags != ''"
    ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row["project_tags"].split(","):
            tag = tag.strip()
            if tag:
                counts[tag] = counts.get(tag, 0) + 1
    return sorted([{"tag": k, "count": v} for k, v in counts.items()], key=lambda x: -x["count"])


@app.get("/api/search")
def api_search(
    q: str = Query(default=""),
    last: str = Query(default=""),
    project: str = Query(default=""),
    limit: int = Query(default=40),
):
    if not q:
        return []

    conn = get_conn()
    results = []

    # Messages FTS
    msg_sql = """
        SELECT m.role, m.content_text, m.tool_name, m.ts,
               m.project, m.cwd, m.session_id, m.uuid
        FROM messages_fts fts
        JOIN claude_messages m ON m.id = fts.rowid
        WHERE messages_fts MATCH ?
    """
    params = [q]
    if last:
        from datetime import timedelta
        now = datetime.now()
        if last.endswith("d"):
            cutoff = (now - timedelta(days=int(last[:-1]))).isoformat()
        elif last.endswith("h"):
            cutoff = (now - timedelta(hours=int(last[:-1]))).isoformat()
        else:
            cutoff = None
        if cutoff:
            msg_sql += " AND m.ts >= ?"
            params.append(cutoff)
    if project:
        msg_sql += " AND m.project LIKE ?"
        params.append(f"%{project}%")
    msg_sql += f" ORDER BY m.ts DESC LIMIT {limit}"

    for row in conn.execute(msg_sql, params):
        text = row["content_text"] or f"[tool: {row['tool_name']}]"
        results.append({
            "source": "conversation",
            "role": row["role"],
            "text": text[:500],
            "ts": row["ts"],
            "project": row["project"] or "unknown",
            "session_id": row["session_id"],
            "uuid": row["uuid"],
        })

    # Prompts FTS (for sessions not already in results)
    seen = {r["session_id"] for r in results}
    prompt_sql = """
        SELECT p.prompt_text, p.ts, p.project, p.session_id
        FROM prompts_fts fts
        JOIN claude_prompts p ON p.id = fts.rowid
        WHERE prompts_fts MATCH ?
    """
    p_params = [q]
    if project:
        prompt_sql += " AND p.project LIKE ?"
        p_params.append(f"%{project}%")
    prompt_sql += f" ORDER BY p.ts DESC LIMIT 20"

    for row in conn.execute(prompt_sql, p_params):
        if row["session_id"] in seen:
            continue
        ts_str = None
        if row["ts"]:
            try:
                ts_str = datetime.fromtimestamp(row["ts"] / 1000).isoformat()
            except Exception:
                pass
        results.append({
            "source": "prompt_history",
            "role": "user",
            "text": row["prompt_text"][:500],
            "ts": ts_str,
            "project": row["project"] or "unknown",
            "session_id": row["session_id"],
            "uuid": None,
        })

    return results


@app.post("/api/rename/{session_id}")
def api_rename(session_id: str, body: dict):
    conn = get_conn()
    title = body.get("title", "").strip()
    if not title:
        return {"ok": False, "error": "empty title"}
    conn.execute(
        """INSERT OR REPLACE INTO session_titles
           (session_id, title, summary, project_tags, generated_at, method)
           VALUES (?,?, COALESCE((SELECT summary FROM session_titles WHERE session_id=?), ''),
                   COALESCE((SELECT project_tags FROM session_titles WHERE session_id=?), ''),
                   ?, 'manual')""",
        (session_id, title, session_id, session_id, int(time.time()*1000))
    )
    conn.commit()
    return {"ok": True, "title": title}


@app.get("/api/stats")
def api_stats():
    conn = get_conn()
    from datetime import datetime, timedelta

    def since(days):
        return (datetime.now() - timedelta(days=days)).isoformat()

    def session_stats(cutoff_iso):
        # Sessions started since cutoff
        sess = conn.execute(
            """SELECT COUNT(*) FROM claude_sessions WHERE datetime(started_at/1000,'unixepoch') >= ?""",
            (cutoff_iso,)
        ).fetchone()[0]
        # Messages since cutoff
        msgs = conn.execute(
            """SELECT COUNT(*) FROM claude_messages
               WHERE ts >= ? AND role='user'""",
            (cutoff_iso,)
        ).fetchone()[0]
        # Top project tag
        top = conn.execute(
            """SELECT t.project_tags FROM session_titles t
               JOIN claude_sessions s ON s.session_id = t.session_id
               WHERE datetime(s.started_at/1000,'unixepoch') >= ?
                 AND t.project_tags IS NOT NULL AND t.project_tags != ''
               ORDER BY s.started_at DESC LIMIT 20""",
            (cutoff_iso,)
        ).fetchall()
        tag_counts = {}
        for row in top:
            for tag in (row["project_tags"] or "").split(","):
                tag = tag.strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top_tag = max(tag_counts, key=tag_counts.get) if tag_counts else None
        return {"sessions": sess, "messages": msgs, "top_project": top_tag}

    return {
        "week":  session_stats(since(7)),
        "month": session_stats(since(30)),
        "all":   {
            "sessions": conn.execute("SELECT COUNT(*) FROM claude_sessions").fetchone()[0],
            "messages": conn.execute("SELECT COUNT(*) FROM claude_messages WHERE role='user'").fetchone()[0],
            "prompts":  conn.execute("SELECT COUNT(*) FROM claude_prompts").fetchone()[0],
        }
    }


@app.post("/api/pin/{session_id}")
def api_pin(session_id: str):
    conn = get_conn()
    existing = conn.execute("SELECT 1 FROM session_pins WHERE session_id=?", (session_id,)).fetchone()
    if existing:
        conn.execute("DELETE FROM session_pins WHERE session_id=?", (session_id,))
        conn.commit()
        return {"pinned": False}
    else:
        conn.execute("INSERT OR REPLACE INTO session_pins (session_id, pinned_at) VALUES (?,?)",
                     (session_id, int(time.time()*1000)))
        conn.commit()
        return {"pinned": True}


@app.get("/api/pins")
def api_pins():
    conn = get_conn()
    rows = conn.execute(
        """SELECT s.session_id, s.started_at, s.status,
                  COUNT(m.id) as msg_count,
                  t.title, t.project_tags, p.pinned_at,
                  n.note
           FROM session_pins p
           JOIN claude_sessions s ON s.session_id = p.session_id
           LEFT JOIN claude_messages m ON m.session_id = s.session_id
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           LEFT JOIN session_notes n ON n.session_id = s.session_id
           GROUP BY s.session_id
           ORDER BY p.pinned_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/note/{session_id}")
def api_note_save(session_id: str, body: dict):
    conn = get_conn()
    note = body.get("note", "").strip()
    if note:
        conn.execute(
            "INSERT OR REPLACE INTO session_notes (session_id, note, updated_at) VALUES (?,?,?)",
            (session_id, note, int(time.time()*1000))
        )
    else:
        conn.execute("DELETE FROM session_notes WHERE session_id=?", (session_id,))
    conn.commit()
    return {"ok": True}


@app.get("/api/note/{session_id}")
def api_note_get(session_id: str):
    conn = get_conn()
    row = conn.execute("SELECT note FROM session_notes WHERE session_id=?", (session_id,)).fetchone()
    return {"note": row["note"] if row else ""}


@app.get("/api/activity")
def api_activity():
    conn = get_conn()
    rows = conn.execute(
        """SELECT substr(ts, 1, 10) as day, COUNT(*) as count
           FROM claude_messages
           WHERE role='user' AND ts IS NOT NULL AND ts != ''
           GROUP BY day
           ORDER BY day ASC"""
    ).fetchall()
    return [{"day": r["day"], "count": r["count"]} for r in rows]


@app.get("/api/linked-ids")
def api_linked_ids():
    """Return set of all session IDs that have at least one manual link."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM session_links"
    ).fetchall()
    return [r["session_id"] for r in rows]


@app.get("/api/links/{session_id}")
def api_links(session_id: str):
    """Return sessions manually linked via `tm link`, with link metadata."""
    conn = get_conn()

    # Walk the full link graph from this session
    def linked_ids(sid: str) -> list[str]:
        rows = conn.execute(
            """SELECT linked_to FROM session_links WHERE session_id = ?
               UNION
               SELECT session_id FROM session_links WHERE linked_to = ?""",
            (sid, sid)
        ).fetchall()
        return [r[0] for r in rows]

    visited: set[str] = set()
    queue = linked_ids(session_id)
    while queue:
        sid = queue.pop()
        if sid in visited or sid == session_id:
            continue
        visited.add(sid)
        queue.extend(linked_ids(sid))

    if not visited:
        return []

    results = []
    for sid in visited:
        row = conn.execute(
            """SELECT s.session_id, s.started_at, t.title, t.project_tags,
                      COUNT(m.id) as msg_count,
                      sl.link_type, sl.note
               FROM claude_sessions s
               LEFT JOIN session_titles t ON t.session_id = s.session_id
               LEFT JOIN claude_messages m ON m.session_id = s.session_id
               LEFT JOIN session_links sl ON sl.session_id = ? AND sl.linked_to = s.session_id
               WHERE s.session_id = ?
               GROUP BY s.session_id""",
            (session_id, sid)
        ).fetchone()
        if row:
            results.append(dict(row))

    results.sort(key=lambda r: r.get("started_at") or 0)
    return results


@app.get("/api/related/{session_id}")
def api_related(session_id: str):
    conn = get_conn()
    row = conn.execute(
        "SELECT project_tags FROM session_titles WHERE session_id=?", (session_id,)
    ).fetchone()
    if not row or not row["project_tags"]:
        return []
    tags = [t.strip() for t in row["project_tags"].split(",") if t.strip()]
    if not tags:
        return []
    # Find sessions sharing at least one tag, excluding current
    results = []
    seen = set()
    for tag in tags:
        rows = conn.execute(
            """SELECT s.session_id, s.started_at, t.title, t.project_tags,
                      COUNT(m.id) as msg_count
               FROM session_titles t
               JOIN claude_sessions s ON s.session_id = t.session_id
               LEFT JOIN claude_messages m ON m.session_id = s.session_id
               WHERE t.project_tags LIKE ? AND t.session_id != ?
               GROUP BY s.session_id
               ORDER BY s.started_at DESC
               LIMIT 5""",
            (f"%{tag}%", session_id)
        ).fetchall()
        for r in rows:
            if r["session_id"] not in seen:
                seen.add(r["session_id"])
                results.append(dict(r))
    return results[:4]


@app.get("/api/export/{session_id}")
def api_export(session_id: str):
    from fastapi.responses import Response
    conn = get_conn()
    title_row = conn.execute(
        "SELECT title, project_tags FROM session_titles WHERE session_id=?", (session_id,)
    ).fetchone()
    note_row = conn.execute(
        "SELECT note FROM session_notes WHERE session_id=?", (session_id,)
    ).fetchone()
    session_row = conn.execute(
        "SELECT started_at FROM claude_sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    messages = conn.execute(
        """SELECT role, content_text, tool_name, ts FROM claude_messages
           WHERE session_id=? AND (content_text IS NOT NULL OR tool_name IS NOT NULL)
           ORDER BY ts ASC""",
        (session_id,)
    ).fetchall()

    title = (title_row["title"] if title_row else None) or session_id[:8]
    tags  = title_row["project_tags"] if title_row else ""
    note  = note_row["note"] if note_row else ""

    ts = session_row["started_at"] if session_row else None
    date_str = ""
    if ts:
        try:
            date_str = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass

    lines = [f"# {title}", ""]
    if date_str: lines += [f"**Date:** {date_str}", ""]
    if tags:     lines += [f"**Tags:** {tags}", ""]
    if note:     lines += [f"**Note:** {note}", ""]
    lines += ["---", ""]

    for msg in messages:
        if msg["tool_name"] and not msg["content_text"]:
            lines += [f"> `[tool: {msg['tool_name']}]`", ""]
            continue
        role  = "**You**" if msg["role"] == "user" else "**Claude**"
        text  = (msg["content_text"] or "").strip()
        lines += [f"{role}", "", text, ""]

    md = "\n".join(lines)
    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:50]
    return Response(
        content=md,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'}
    )


@app.post("/api/view/{session_id}")
def api_view(session_id: str):
    conn = get_conn()
    conn.execute(
        "INSERT INTO session_views (session_id, viewed_at) VALUES (?,?)",
        (session_id, int(time.time()*1000))
    )
    conn.commit()
    return {"ok": True}


@app.get("/api/semantic")
def api_semantic(q: str = Query(default=""), limit: int = Query(default=30)):
    if not q:
        return []
    import numpy as np
    conn = get_conn()
    matrix, ids = _get_embed_cache(conn)
    if matrix is None:
        return []
    from indexer.embedder import embed_query
    qvec = embed_query(q)
    scores = matrix @ qvec                          # cosine sim (L2-normalized)
    top_idx = np.argsort(scores)[::-1][:limit]

    results = []
    for idx in top_idx:
        score = float(scores[idx])
        if score < 0.25:                            # minimum relevance threshold
            break
        msg_id = ids[idx]
        row = conn.execute(
            """SELECT m.role, m.content_text, m.ts, m.session_id
               FROM claude_messages m WHERE m.id = ?""",
            (msg_id,)
        ).fetchone()
        if row and row["content_text"]:
            session = conn.execute(
                "SELECT title, project_tags FROM session_titles WHERE session_id=?",
                (row["session_id"],)
            ).fetchone()
            results.append({
                "role":       row["role"],
                "text":       row["content_text"][:500],
                "ts":         row["ts"],
                "session_id": row["session_id"],
                "project":    session["project_tags"] if session else "",
                "score":      round(score, 3),
                "source":     "semantic",
                "title":      session["title"] if session else row["session_id"][:8],
            })
    return results


@app.get("/api/shell")
def api_shell(
    project: str = Query(default=""),
    days:    int = Query(default=7),
    failed:  bool = Query(default=False),
    q:       str = Query(default=""),
    limit:   int = Query(default=300),
):
    conn = get_conn()
    try:
        conn.execute("SELECT 1 FROM shell_commands LIMIT 1")
    except Exception:
        return {"commands": [], "total": 0, "failed_count": 0, "has_data": False}

    cutoff_ms = int((datetime.now() - timedelta(days=days)).timestamp() * 1000) if days else 0

    if q:
        sql = """SELECT sc.ts, sc.duration_ms, sc.exit_code, sc.command, sc.cwd
                 FROM shell_commands_fts fts
                 JOIN shell_commands sc ON sc.id = fts.rowid
                 WHERE shell_commands_fts MATCH ?"""
        params: list = [q]
    else:
        sql    = "SELECT ts, duration_ms, exit_code, command, cwd FROM shell_commands WHERE 1=1"
        params = []

    if cutoff_ms:
        sql += " AND ts >= ?"
        params.append(cutoff_ms)
    if project:
        sql += " AND cwd LIKE ?"
        params.append(f"%{project}%")
    if failed:
        sql += " AND exit_code != 0 AND exit_code IS NOT NULL"

    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    # totals for stats bar (unfiltered except time + project)
    count_sql    = "SELECT COUNT(*), SUM(CASE WHEN exit_code!=0 AND exit_code IS NOT NULL THEN 1 ELSE 0 END) FROM shell_commands WHERE 1=1"
    count_params: list = []
    if cutoff_ms:
        count_sql += " AND ts >= ?"; count_params.append(cutoff_ms)
    if project:
        count_sql += " AND cwd LIKE ?"; count_params.append(f"%{project}%")
    total_row = conn.execute(count_sql, count_params).fetchone()

    return {
        "commands":     [dict(r) for r in rows],
        "total":        total_row[0] or 0,
        "failed_count": total_row[1] or 0,
        "has_data":     True,
    }


@app.get("/api/shell/projects")
def api_shell_projects():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT cwd FROM shell_commands WHERE cwd IS NOT NULL ORDER BY cwd"
        ).fetchall()
    except Exception:
        return []
    projects = {}
    for r in rows:
        seg = (r["cwd"] or "").rstrip("/").split("/")[-1]
        if seg:
            projects[seg] = True
    return sorted(projects.keys())


@app.get("/api/sync")
def api_sync():
    global _embed_dirty
    from indexer import claude_history, claude_sessions, claude_transcripts, title_engine, embedder
    conn = get_conn()
    h = claude_history.run(conn)
    t = claude_transcripts.run(conn)
    s = claude_sessions.run(conn)
    titled, _ = title_engine.run(conn, use_ollama=False)
    e = embedder.run(conn)
    _embed_dirty = True   # invalidate cache so next semantic search reloads
    return {"prompts": h, "messages": t, "sessions": s, "titles": titled, "embedded": e}


@app.get("/", response_class=HTMLResponse)
def root():
    return HTML_PATH.read_text(encoding="utf-8")


# ── Runner ────────────────────────────────────────────────────────────────────

def serve(port: int = 8888, open_browser: bool = True, no_browser: bool = False):
    from indexer import claude_history, claude_sessions, claude_transcripts
    conn = get_conn()
    print("\n  Syncing...")
    h = claude_history.run(conn)
    t = claude_transcripts.run(conn)
    s = claude_sessions.run(conn)
    print(f"  {h} prompts  │  {t} messages  │  {s} sessions\n")

    if open_browser and not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    print(f"  TerminalMind → http://localhost:{port}")
    if no_browser:
        print("  Running as background service. Logs → /tmp/terminalmd.log\n")
    else:
        print("  Ctrl+C to stop\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
