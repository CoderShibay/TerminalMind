#!/usr/bin/env python3
"""
tm — TerminalMind CLI
Usage:
    tm serve                         Launch browser dashboard (localhost:8888)
    tm serve --no-browser            Run server only, no browser open
    tm sync                          Re-index all Claude files
    tm verify                        Check what's indexed + health report
    tm status                        Active sessions + DB stats
    tm search <query> [--last Nd]    Full-text search across all conversations
    tm sessions [--project NAME]     List Claude Code sessions
    tm service install               Auto-start server on login (macOS/Linux)
    tm service uninstall             Remove auto-start
    tm service status                Check if background service is running
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import init_db
from indexer import claude_history, claude_sessions, claude_transcripts, title_engine, embedder
from cli import search, sessions, status, verify, today, context

HELP = __doc__


def sync(conn, verbose: bool = True, titles: bool = True, embed: bool = True) -> None:
    h = claude_history.run(conn)
    t = claude_transcripts.run(conn)
    s = claude_sessions.run(conn)
    if titles:
        titled, method = title_engine.run(conn, use_ollama=True)
        title_note = f"{titled} titles via {method}" if titled else "titles up to date"
    else:
        title_note = "titles skipped"
    if embed:
        e = embedder.run(conn, verbose=verbose and (h+t > 0))
        embed_note = f"{e} embedded" if e else "embeddings up to date"
    else:
        embed_note = "embeddings skipped"
    if verbose:
        print(f"  synced  {h} prompts  │  {t} messages  │  {s} sessions  │  {title_note}  │  {embed_note}")


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(HELP)
        return

    conn = init_db()
    cmd  = args[0]
    rest = args[1:]

    if cmd == "serve":
        from server import serve
        no_browser = "--no-browser" in rest
        port_args  = [a for a in rest if a.isdigit()]
        port       = int(port_args[0]) if port_args else 8888
        sync(conn, verbose=False)        # always sync on startup
        serve(port=port, no_browser=no_browser)

    elif cmd == "sync":
        print()
        sync(conn)
        print()

    elif cmd == "verify":
        sync(conn, verbose=False)
        verify.run(conn, rest)

    elif cmd == "status":
        sync(conn, verbose=False)
        status.run(conn, rest)

    elif cmd == "search":
        sync(conn, verbose=False)
        search.run(conn, rest)

    elif cmd == "context":
        sync(conn, verbose=False, embed=False)
        context.run(conn, rest)

    elif cmd == "today":
        sync(conn, verbose=False)
        today.run(conn, rest)

    elif cmd == "sessions":
        sync(conn, verbose=False)
        sessions.run(conn, rest)

    elif cmd == "service":
        from service import install, uninstall, status as svc_status
        sub = rest[0] if rest else "status"
        if sub == "install":
            print()
            install()
            print("  \033[32m✓\033[0m  TerminalMind service installed — server will start automatically on login")
            print(f"  \033[32m✓\033[0m  Dashboard: http://localhost:8888")
            print()
        elif sub == "uninstall":
            uninstall()
            print("  Service removed.")
        elif sub == "status":
            s = svc_status()
            icon = "\033[32m●\033[0m" if s == "running" else "\033[2m○\033[0m"
            print(f"\n  {icon}  Service: {s}\n")
        else:
            print(f"Unknown service subcommand: {sub}")
            sys.exit(1)

    else:
        print(f"Unknown command: {cmd}")
        print(HELP)
        sys.exit(1)


if __name__ == "__main__":
    main()
