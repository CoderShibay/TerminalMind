"""FastAPI server for TerminalMind dashboard — `tm serve`."""
import threading
import time
import webbrowser
from datetime import datetime
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


def get_conn():
    """Return a per-thread SQLite connection (SQLite is not thread-safe)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = init_db()
    return _local.conn


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
        WHERE project IS NOT NULL AND project != 'alisyed'
        GROUP BY project ORDER BY c DESC LIMIT 15
    """).fetchall()

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
        SELECT s.session_id, s.started_at, s.updated_at, s.status,
               COUNT(m.id) as msg_count,
               MIN(m.ts) as first_msg,
               MAX(m.ts) as last_msg,
               t.title, t.summary, t.project_tags, t.method
        FROM claude_sessions s
        LEFT JOIN claude_messages m ON m.session_id = s.session_id
        LEFT JOIN session_titles t ON t.session_id = s.session_id
    """
    params = []
    if tag:
        sql += " WHERE t.project_tags LIKE ?"
        params.append(f"%{tag}%")
    sql += " GROUP BY s.session_id ORDER BY s.started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


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
    return [dict(m) for m in messages]


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


@app.get("/api/sync")
def api_sync():
    from indexer import claude_history, claude_sessions, claude_transcripts, title_engine
    conn = get_conn()
    h = claude_history.run(conn)
    t = claude_transcripts.run(conn)
    s = claude_sessions.run(conn)
    titled = title_engine.run(conn, use_ollama=False)  # fast sync: heuristic only
    return {"prompts": h, "messages": t, "sessions": s, "titles": titled}


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
