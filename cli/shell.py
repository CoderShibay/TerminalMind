"""tm shell — shell command history, filterable by project, time, exit code."""
import platform
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import shell_log_path


def _ts(ts_ms) -> str:
    if not ts_ms:
        return "?"
    try:
        d = datetime.fromtimestamp(ts_ms / 1000)
        now = datetime.now()
        if d.date() == now.date():
            return "Today     " + d.strftime("%H:%M:%S")
        if d.date() == (now - timedelta(days=1)).date():
            return "Yesterday " + d.strftime("%H:%M:%S")
        return d.strftime("%b %d      %H:%M:%S")
    except Exception:
        return str(ts_ms)


def _dur(ms) -> str:
    if ms is None or ms < 0:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60000:
        return f"{ms / 1000:.1f}s"
    return f"{ms // 60000}m {(ms % 60000) // 1000}s"


def _project(cwd: str | None) -> str:
    if not cwd:
        return ""
    return cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _not_set_up() -> None:
    print()
    print("  Shell command logging is not active yet.")
    print()
    if platform.system() == "Windows":
        print("  Add this line to your PowerShell profile ($PROFILE):")
        print("    \033[1m. \"$env:USERPROFILE\\.terminalmd\\daemon\\shell_hook.ps1\"\033[0m")
        print()
        print("  Then reload your profile:")
        print("    \033[1m. $PROFILE\033[0m")
        print()
        print("  Run a few commands, then check it worked:")
        print("    \033[1mGet-Content \"$env:USERPROFILE\\.terminalmd\\shell_log.jsonl\" -Tail 3\033[0m")
    else:
        print("  Add this line to ~/.zshrc (or ~/.bashrc):")
        print("    \033[1msource ~/.terminalmd/daemon/shell_hook.sh\033[0m")
        print()
        print("  Then activate it:")
        print("    \033[1msource ~/.zshrc\033[0m")
        print()
        print("  Run a few commands, then check it worked:")
        print("    \033[1mtail -3 ~/.terminalmd/shell_log.jsonl\033[0m")
    print("    \033[1mtm shell\033[0m")
    print()


def run(conn, args: list[str]) -> int:
    # ── Parse flags ──────────────────────────────────────────────────────────
    project_filter = None
    days_filter    = None
    limit          = 100
    failed_only    = False
    search_query   = None
    show_today     = False

    i = 0
    while i < len(args):
        if args[i] in ("--project", "-p") and i + 1 < len(args):
            project_filter = args[i + 1]; i += 2
        elif args[i] in ("--days", "--last") and i + 1 < len(args):
            days_filter = int(args[i + 1]); i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--failed":
            failed_only = True; i += 1
        elif args[i] == "--today":
            show_today = True; i += 1
        elif args[i] in ("--search", "-s") and i + 1 < len(args):
            search_query = args[i + 1]; i += 2
        else:
            i += 1

    # ── Check hook is installed ───────────────────────────────────────────────
    log_path = shell_log_path()
    try:
        total = conn.execute("SELECT COUNT(*) FROM shell_commands").fetchone()[0]
    except Exception:
        _not_set_up()
        return 0

    if total == 0 and not log_path.exists():
        _not_set_up()
        return 0

    if show_today:
        days_filter = 1

    # ── Build query ───────────────────────────────────────────────────────────
    if search_query:
        sql = """
            SELECT sc.id, sc.ts, sc.duration_ms, sc.exit_code, sc.command, sc.cwd
            FROM shell_commands_fts fts
            JOIN shell_commands sc ON sc.id = fts.rowid
            WHERE shell_commands_fts MATCH ?
        """
        params: list = [search_query]
    else:
        sql = """
            SELECT id, ts, duration_ms, exit_code, command, cwd
            FROM shell_commands WHERE 1=1
        """
        params = []

    if days_filter:
        cutoff_ms = int((datetime.now() - timedelta(days=days_filter)).timestamp() * 1000)
        sql += " AND ts >= ?"
        params.append(cutoff_ms)

    if project_filter:
        sql += " AND cwd LIKE ?"
        params.append(f"%{project_filter}%")

    if failed_only:
        sql += " AND exit_code != 0 AND exit_code IS NOT NULL"

    sql += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()

    # ── Header ────────────────────────────────────────────────────────────────
    filter_parts = []
    if project_filter: filter_parts.append(f"project: {project_filter}")
    if days_filter:    filter_parts.append(f"last {days_filter} day{'s' if days_filter != 1 else ''}")
    if failed_only:    filter_parts.append("failed only")
    if search_query:   filter_parts.append(f"search: {search_query!r}")
    filter_str = "  —  " + ", ".join(filter_parts) if filter_parts else ""

    print()
    if not rows:
        print(f"  No commands found{filter_str}.\n")
        return 0

    print(f"  \033[1mShell History{filter_str}\033[0m  ({len(rows)} commands)\n")
    print(f"  {'WHEN':<22}  {'EXIT':>4}  {'DUR':>7}  COMMAND")
    print("  " + "─" * 80)

    # ── Rows ──────────────────────────────────────────────────────────────────
    current_date = None
    for r in rows:
        ts_str    = _ts(r["ts"])
        date_part = ts_str.split()[0]

        if date_part != current_date:
            if current_date is not None:
                print()
            current_date = date_part

        exit_code = r["exit_code"]
        dur       = _dur(r["duration_ms"])
        cmd       = (r["command"] or "")
        proj      = _project(r["cwd"])

        # Truncate long commands, show continuation marker
        display_cmd = cmd[:72] + ("\033[2m…\033[0m" if len(cmd) > 72 else "")

        if exit_code and exit_code != 0:
            exit_str = f"\033[31m{exit_code:>4}\033[0m"
            cmd_color = "\033[31m"
            reset     = "\033[0m"
        else:
            exit_str  = f"\033[2m   0\033[0m"
            cmd_color = ""
            reset     = ""

        proj_str = f"  \033[2m[{proj}]\033[0m" if proj else ""

        print(f"  {ts_str:<22}  {exit_str}  {dur:>7}  {cmd_color}{display_cmd}{reset}{proj_str}")

    # ── Footer hints ──────────────────────────────────────────────────────────
    print()
    hints = []
    if not project_filter: hints.append("\033[2m--project NAME\033[0m")
    if not days_filter:    hints.append("\033[2m--days N\033[0m")
    if not failed_only:    hints.append("\033[2m--failed\033[0m for errors")
    if not search_query:   hints.append("\033[2m--search QUERY\033[0m")
    if hints:
        print("  Filter: " + "  ·  ".join(hints))
    print(f"  Total logged: {total:,} commands\n")

    return 0
