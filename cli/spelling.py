"""tm spelling — spelling mistakes found in your prompts by local llama3.2."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from indexer import spelling as sp

USAGE = """
  tm spelling                     Your misspellings, most frequent first (top 50)
  tm spelling --all               Show every misspelling
  tm spelling --recent            Latest mistakes with date and context
  tm spelling --days N            Only mistakes from the last N days
  tm spelling scan                Check new prompts now (foreground)
  tm spelling ignore WORD         Stop listing WORD (not a mistake)
  tm spelling unignore WORD       List WORD again
  tm spelling fix WRONG CORRECT   Change the suggested correction
  tm spelling export [PATH]       Write the list as markdown (default ~/Documents/spelling_mistakes.md)
"""

VISIBLE = "wrong NOT IN (SELECT word FROM spelling_ignore)"


def _where(rest):
    if "--days" in rest:
        i = rest.index("--days")
        if i + 1 < len(rest) and rest[i + 1].isdigit():
            since = (datetime.now().timestamp() - int(rest[i + 1]) * 86400) * 1000
            return f"{VISIBLE} AND ts >= {int(since)}"
    return VISIBLE


def _rows(conn, where):
    return conn.execute(f"""
        SELECT wrong,
               (SELECT correct FROM spelling_mistakes s2 WHERE s2.wrong = s.wrong
                GROUP BY correct ORDER BY count(*) DESC LIMIT 1) AS correct,
               count(*) AS n, max(ts) AS last
        FROM spelling_mistakes s WHERE {where}
        GROUP BY wrong ORDER BY n DESC, wrong""").fetchall()


def _date(ts):
    return datetime.fromtimestamp(ts / 1000).strftime("%b %d") if ts else ""


def run(conn, rest):
    sub = rest[0] if rest and not rest[0].startswith("-") else ""

    if sub in ("help", "-h"):
        print(USAGE); return

    if sub == "scan":
        quiet = "--quiet" in rest
        if not sp.acquire_lock():
            if not quiet:
                print("\n  A spelling scan is already running in the background.\n")
            return
        try:
            if not quiet:
                print(f"\n  Checking {sp.pending_count(conn)} new prompts/messages with llama3.2 …")
            n = sp.run(conn, verbose=not quiet)
            if not quiet:
                print(f"  \033[32m✓\033[0m  {n} new mistakes logged\n")
        finally:
            sp.release_lock()
        return

    if sub in ("ignore", "unignore"):
        words = [w.lower() for w in rest[1:]]
        if not words:
            print(USAGE); return
        sql = ("INSERT OR IGNORE INTO spelling_ignore(word) VALUES (?)" if sub == "ignore"
               else "DELETE FROM spelling_ignore WHERE word=?")
        conn.executemany(sql, [(w,) for w in words]); conn.commit()
        print(f"\n  {'Ignoring' if sub == 'ignore' else 'Listing again'}: {', '.join(words)}\n")
        return

    if sub == "fix":
        if len(rest) < 3:
            print(USAGE); return
        wrong, correct = rest[1].lower(), " ".join(rest[2:]).lower()
        n = conn.execute("UPDATE spelling_mistakes SET correct=? WHERE wrong=?", (correct, wrong)).rowcount
        conn.commit()
        print(f"\n  {wrong} → {correct}  ({n} entries updated)\n")
        return

    if sub == "export":
        path = Path(rest[1]).expanduser() if len(rest) > 1 else Path.home() / "Documents" / "spelling_mistakes.md"
        rows = _rows(conn, VISIBLE)
        total = sum(r["n"] for r in rows)
        lines = ["# Your spelling mistakes (TerminalMind, checked by local llama3.2)\n",
                 f"{len(rows)} distinct misspellings, {total} occurrences — updated "
                 f"{datetime.now():%Y-%m-%d %H:%M}\n",
                 "| Wrong | Correct | Times | Last seen |", "|---|---|---|---|"]
        lines += [f"| {r['wrong']} | {r['correct']} | {r['n']} | {_date(r['last'])} |" for r in rows]
        path.write_text("\n".join(lines) + "\n")
        print(f"\n  \033[32m✓\033[0m  {len(rows)} misspellings written to {path}\n")
        return

    where = _where(rest)
    note = "  \033[2m(checking new prompts in the background)\033[0m" if sp._lock_alive() else ""

    if "--recent" in rest:
        rows = conn.execute(f"""SELECT wrong, correct, ts, agent, excerpt FROM spelling_mistakes
                                WHERE {where} ORDER BY ts DESC LIMIT 30""").fetchall()
        print(f"\n  \033[1mRecent spelling mistakes\033[0m{note}\n")
        for r in rows:
            print(f"  {_date(r['ts']):<7} \033[31m{r['wrong']:<16}\033[0m → \033[32m{r['correct']:<16}\033[0m"
                  f" \033[2m{(r['excerpt'] or '')[:60]}\033[0m")
        print()
        return

    rows = _rows(conn, where)
    total = sum(r["n"] for r in rows)
    shown = rows if "--all" in rest else rows[:50]
    print(f"\n  \033[1mSpelling mistakes\033[0m  {len(rows)} distinct, {total} times{note}\n")
    print(f"  {'WRONG':<18}{'CORRECT':<20}{'TIMES':>5}  LAST")
    for r in shown:
        print(f"  \033[31m{r['wrong']:<18}\033[0m\033[32m{r['correct']:<20}\033[0m{r['n']:>5}  {_date(r['last'])}")
    if len(shown) < len(rows):
        print(f"\n  \033[2m… {len(rows) - len(shown)} more — tm spelling --all\033[0m")
    print()
