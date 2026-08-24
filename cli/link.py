"""tm link — link sessions together so context searches span all of them."""
import time
from datetime import datetime


def _resolve(conn, partial_id: str) -> str | None:
    """Resolve a partial session ID (first 8 chars) to the full ID."""
    row = conn.execute(
        "SELECT session_id FROM claude_sessions WHERE session_id LIKE ?",
        (partial_id + "%",)
    ).fetchone()
    return row["session_id"] if row else None


def _title(conn, sid: str) -> str:
    row = conn.execute(
        "SELECT title FROM session_titles WHERE session_id = ?", (sid,)
    ).fetchone()
    return (row["title"] if row else None) or sid[:8]


def _linked_ids(conn, session_id: str) -> list[str]:
    """Return all session IDs linked to the given session (both directions)."""
    rows = conn.execute(
        """SELECT linked_to FROM session_links WHERE session_id = ?
           UNION
           SELECT session_id FROM session_links WHERE linked_to = ?""",
        (session_id, session_id)
    ).fetchall()
    return [r[0] for r in rows]


def _all_in_group(conn, session_id: str) -> list[str]:
    """Walk the full link graph and return all session IDs in the group."""
    visited = set()
    queue   = [session_id]
    while queue:
        sid = queue.pop()
        if sid in visited:
            continue
        visited.add(sid)
        for linked in _linked_ids(conn, sid):
            if linked not in visited:
                queue.append(linked)
    return list(visited)


def run(conn, args: list[str]) -> int:
    if not args:
        return _show_all(conn)

    sub = args[0]

    if sub == "unlink" and len(args) >= 3:
        return _unlink(conn, args[1], args[2])

    if sub in ("list", "ls") or (sub.startswith("-") and "session" in args):
        session_filter = None
        for i, a in enumerate(args):
            if a in ("--session", "-s") and i + 1 < len(args):
                session_filter = args[i + 1]
        return _show_all(conn, session_filter)

    # tm link ID1 ID2 [--type TYPE] [--note "text"]
    if len(args) < 2:
        print("\nUsage:")
        print("  tm link ID1 ID2                    link two sessions")
        print("  tm link ID1 ID2 --type related     mark as related (not continuation)")
        print("  tm link ID1 ID2 --note \"text\"      add a note about the link")
        print("  tm link unlink ID1 ID2             remove a link")
        print("  tm link list                       show all linked groups")
        print("  tm link list --session ID          show links for one session\n")
        return 1

    id1_partial = args[0]
    id2_partial = args[1]
    link_type   = "continuation"
    note        = None

    i = 2
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            link_type = args[i + 1]; i += 2
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]; i += 2
        else:
            i += 1

    # Resolve partial IDs
    sid1 = _resolve(conn, id1_partial)
    sid2 = _resolve(conn, id2_partial)

    if not sid1:
        print(f"\n  Session not found: {id1_partial}\n")
        return 1
    if not sid2:
        print(f"\n  Session not found: {id2_partial}\n")
        return 1
    if sid1 == sid2:
        print("\n  Cannot link a session to itself.\n")
        return 1

    now = int(time.time() * 1000)

    # Store bidirectionally so graph walks work in both directions
    conn.execute(
        """INSERT OR IGNORE INTO session_links
           (session_id, linked_to, link_type, note, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (sid1, sid2, link_type, note, now)
    )
    conn.execute(
        """INSERT OR IGNORE INTO session_links
           (session_id, linked_to, link_type, note, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (sid2, sid1, link_type, note, now)
    )
    conn.commit()

    t1 = _title(conn, sid1)
    t2 = _title(conn, sid2)

    print()
    print(f"  \033[32m✓\033[0m  Linked:")
    print(f"     \033[1m{t1}\033[0m  \033[2m({sid1[:8]})\033[0m")
    print(f"     \033[1m{t2}\033[0m  \033[2m({sid2[:8]})\033[0m")
    if note:
        print(f"     \033[33m↳ {note}\033[0m")
    print()
    print(f"  \033[2mtm context --session {sid1[:8]} will now search both sessions.\033[0m\n")

    return 0


def _unlink(conn, id1_partial: str, id2_partial: str) -> int:
    sid1 = _resolve(conn, id1_partial)
    sid2 = _resolve(conn, id2_partial)

    if not sid1 or not sid2:
        print(f"\n  Session not found.\n")
        return 1

    conn.execute(
        "DELETE FROM session_links WHERE session_id=? AND linked_to=?", (sid1, sid2)
    )
    conn.execute(
        "DELETE FROM session_links WHERE session_id=? AND linked_to=?", (sid2, sid1)
    )
    conn.commit()

    print(f"\n  Unlinked {sid1[:8]} ↔ {sid2[:8]}\n")
    return 0


def _show_all(conn, session_filter: str | None = None) -> int:
    if session_filter:
        sid = _resolve(conn, session_filter)
        if not sid:
            print(f"\n  Session not found: {session_filter}\n")
            return 1
        group = _all_in_group(conn, sid)
        groups = [group]
    else:
        # Find all distinct link groups
        all_rows = conn.execute(
            "SELECT DISTINCT session_id FROM session_links"
        ).fetchall()
        seen: set[str] = set()
        groups = []
        for row in all_rows:
            sid = row[0]
            if sid in seen:
                continue
            group = _all_in_group(conn, sid)
            groups.append(group)
            seen.update(group)

    if not groups:
        print("\n  No linked sessions yet.")
        print("  Use \033[1mtm link ID1 ID2\033[0m to link two sessions.\n")
        return 0

    print()
    for i, group in enumerate(groups):
        # Sort by session start time
        session_info = []
        for sid in group:
            row = conn.execute(
                """SELECT s.started_at, t.title, t.project_tags
                   FROM claude_sessions s
                   LEFT JOIN session_titles t ON t.session_id = s.session_id
                   WHERE s.session_id = ?""",
                (sid,)
            ).fetchone()
            if row:
                session_info.append((row["started_at"] or 0, sid, row["title"], row["project_tags"]))

        session_info.sort(key=lambda x: x[0])

        print(f"  \033[1mGroup {i + 1}\033[0m  ({len(group)} sessions)")
        for started_at, sid, title, tags in session_info:
            title_str = title or "Untitled session"
            tag_str   = f"  \033[2m[{tags}]\033[0m" if tags else ""
            try:
                d = datetime.fromtimestamp(started_at / 1000).strftime("%b %d %H:%M") if started_at else "?"
            except Exception:
                d = "?"
            print(f"    \033[2m{d}\033[0m  {title_str[:50]}{tag_str}  \033[2m{sid[:8]}\033[0m")

        # Check for note on any link in this group
        note_row = conn.execute(
            "SELECT note FROM session_links WHERE session_id = ? AND note IS NOT NULL LIMIT 1",
            (group[0],)
        ).fetchone()
        if note_row and note_row["note"]:
            print(f"    \033[33m↳ {note_row['note']}\033[0m")

        print()

    return 0
