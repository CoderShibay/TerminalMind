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

    # tm link ID1 ID2 [ID3 ...] [--type TYPE] [--note "text"]
    if len(args) < 2:
        print("\nUsage:")
        print("  tm link ID1 ID2 [ID3 ...]          link two or more sessions")
        print("  tm link ID1 ID2 --type related     mark as related (not continuation)")
        print("  tm link ID1 ID2 --note \"text\"      add a note about the link")
        print("  tm link unlink ID1 ID2             remove a link")
        print("  tm link list                       show all linked groups")
        print("  tm link list --session ID          show links for one session\n")
        return 1

    # Split positional IDs from flags
    id_partials: list[str] = []
    link_type = "continuation"
    note      = None

    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            link_type = args[i + 1]; i += 2
        elif args[i] == "--note" and i + 1 < len(args):
            note = args[i + 1]; i += 2
        elif args[i].startswith("--"):
            i += 1  # skip unknown flags
        else:
            id_partials.append(args[i]); i += 1

    if len(id_partials) < 2:
        print("\n  Need at least two session IDs.\n")
        return 1

    # Resolve all partial IDs
    resolved: list[str] = []
    for partial in id_partials:
        sid = _resolve(conn, partial)
        if not sid:
            print(f"\n  Session not found: {partial}\n")
            return 1
        if sid in resolved:
            print(f"\n  Duplicate session ID: {partial}\n")
            return 1
        resolved.append(sid)

    now = int(time.time() * 1000)

    # Insert a link between every pair so the group is fully connected
    import itertools
    pairs_added = 0
    for sid1, sid2 in itertools.combinations(resolved, 2):
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
        pairs_added += 1
    conn.commit()

    print()
    print(f"  \033[32m✓\033[0m  Linked {len(resolved)} sessions:")
    for sid in resolved:
        print(f"     \033[1m{_title(conn, sid)}\033[0m  \033[2m({sid[:8]})\033[0m")
    if note:
        print(f"     \033[33m↳ {note}\033[0m")
    print()
    first = resolved[0]
    print(f"  \033[2mtm context --session {first[:8]} will now search all {len(resolved)} sessions.\033[0m\n")

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
