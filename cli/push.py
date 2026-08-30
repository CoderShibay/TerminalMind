"""tm push — extract a session's work and append it to a project Build Log.md.

Usage:
    tm push SESSION_ID PROJECT_NAME
    tm push bfada840 ProjectLogger
    tm push bfada840 SpotTrader --dry-run
"""
import glob
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from paths import claude_dir, load_project_paths, projects_config_path

DOCS_DIR = Path.home() / "Documents"
PROJECTS_DIR = claude_dir() / "projects"


# ── Session resolution ────────────────────────────────────────────────────────

def _resolve_session(conn, partial: str) -> tuple[str | None, str | None]:
    """Return (full_session_id, title) for a partial ID."""
    row = conn.execute(
        """SELECT s.session_id, t.title
           FROM claude_sessions s
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           WHERE s.session_id LIKE ?""",
        (partial + "%",)
    ).fetchone()
    if row:
        return row["session_id"], row["title"]
    return None, None


def _find_jsonl(session_id: str) -> Path | None:
    """Find the source .jsonl file for a session ID."""
    for f in glob.glob(str(PROJECTS_DIR / "**" / "*.jsonl"), recursive=True):
        p = Path(f)
        if p.stem == session_id or p.stem.startswith(session_id[:8]):
            return p
        # Also check by scanning first line
    # Slower fallback: scan file contents
    for f in glob.glob(str(PROJECTS_DIR / "**" / "*.jsonl"), recursive=True):
        try:
            with open(f) as fp:
                first = fp.readline()
                entry = json.loads(first)
                if entry.get("sessionId", "").startswith(session_id[:8]):
                    return Path(f)
        except Exception:
            pass
    return None


# ── Extraction ────────────────────────────────────────────────────────────────

ERROR_PATTERNS = re.compile(
    r"(Error|error|Traceback|FAILED|not found|No such file|exit code [1-9]|"
    r"ModuleNotFoundError|FileNotFoundError|PermissionError|SyntaxError|"
    r"ImportError|AttributeError|TypeError|KeyError|ValueError)",
    re.IGNORECASE,
)

DECISION_PATTERNS = re.compile(
    r"\b(decided|going with|the approach is|we('ll| will) use|"
    r"I('ll| will) use|settled on|switching to|the plan is)\b",
    re.IGNORECASE,
)

NEXT_PATTERNS = re.compile(
    r"\b(next session|next step|next:|to do next|what('s| is) next|"
    r"pick up|continue with)\b",
    re.IGNORECASE,
)

# Trivial one-word affirmations that aren't real decisions/next steps
TRIVIAL_RE = re.compile(
    r"^(yes|no|agreed|done|good|ok|okay|sure|correct|exactly|right|"
    r"perfect|great|noted|understood|got it|all done|sounds good)[\.\!]?$",
    re.IGNORECASE,
)

GIT_COMMIT_RE = re.compile(r"git commit", re.IGNORECASE)

# Line-numbered file content (Read tool result) — not an error
LINE_NUMBERED_RE = re.compile(r"^\d+\t")

# ANSI escape sequences in terminal output
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHFABCDJ]|\[[0-9;]+m")


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _extract_commit_msg(command: str) -> str:
    """Pull the commit message out of a git commit command."""
    # Heredoc style: git commit -m "$(cat <<'EOF'\n   message\n   EOF\n)"
    m = re.search(r"cat <<'?EOF'?\n(.*?)(?:\n\s*Co-Authored|\n\s*EOF)", command, re.DOTALL)
    if m:
        return m.group(1).strip().split("\n")[0][:120]
    # Simple -m "message" — exclude heredoc opener ($)
    m = re.search(r'-m\s+"([^"$][^"]{2,200})"', command)
    if m:
        return m.group(1).strip().split("\n")[0][:120]
    m = re.search(r"-m\s+'([^']{3,200})'", command)
    if m:
        return m.group(1).strip().split("\n")[0][:120]
    return None


def _extract_text_from_content(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", "").strip())
                elif block.get("type") == "tool_result":
                    inner = block.get("content", "")
                    if isinstance(inner, str):
                        parts.append(inner.strip())
                    elif isinstance(inner, list):
                        for b in inner:
                            if isinstance(b, dict) and b.get("type") == "text":
                                parts.append(b.get("text", "").strip())
        return "\n".join(p for p in parts if p)
    return ""


def _first_sentence(text: str, max_len: int = 160) -> str:
    """Extract first meaningful sentence from a text block."""
    text = text.strip()
    # Find sentence boundary
    m = re.search(r"[.!?]\s", text)
    if m and m.start() < max_len:
        return text[: m.start() + 1].strip()
    return text[:max_len].strip()


def _matching_sentence(text: str, pattern: re.Pattern, max_len: int = 160) -> str:
    """Return the sentence that actually contains the pattern match, not just the first."""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        s = sentence.strip()
        if not pattern.search(s):
            continue
        # Skip sentences where the keyword is inside backticks or quotes —
        # those are explanations of the keyword, not actual uses of it
        if re.search(r'["`]([^"`]*' + pattern.pattern + r'[^"`]*)["`]', s, re.IGNORECASE):
            continue
        # Skip markdown list headers (start with ** bold)
        if s.startswith("**"):
            continue
        if len(s) > 20:
            return s[:max_len]
    return ""  # nothing real found


# Git commands whose stdout should never be treated as errors
GIT_OUTPUT_CMDS = re.compile(
    r"\bgit\s+(push|pull|fetch|status|log|diff|show|stash|rebase|merge)\b",
    re.IGNORECASE,
)


def _parse_session(jsonl_path: Path) -> dict:
    """Read a session jsonl and return structured extraction."""
    built_files: list[str] = []
    commits: list[str] = []
    errors: list[dict] = []   # {"error": str, "fix": str}
    decisions: list[str] = []
    next_steps: list[str] = []
    session_date: str = ""

    entries = []
    with open(jsonl_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    seen_files: set[str] = set()
    pending_error: str | None = None   # last error text, waiting for the fix
    last_bash_cmd: str = ""            # track last Bash command to skip git output

    for i, entry in enumerate(entries):
        # Grab date from first timestamped entry
        if not session_date and entry.get("timestamp"):
            try:
                ts = entry["timestamp"].replace("Z", "+00:00")
                d = datetime.fromisoformat(ts)
                session_date = d.strftime("%Y-%m-%d")
            except Exception:
                pass

        msg = entry.get("message", {})
        role = msg.get("role", "")
        content = msg.get("content", [])

        # ── Assistant messages ────────────────────────────────────────────────
        if role == "assistant" and isinstance(content, list):
            # If we had a pending error, the first assistant text after it is the fix
            fix_text = None

            for block in content:
                if not isinstance(block, dict):
                    continue

                btype = block.get("type")

                # Tool use blocks
                if btype == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})

                    if name in ("Edit", "Write"):
                        fp = inp.get("file_path", "")
                        if fp and fp not in seen_files:
                            seen_files.add(fp)
                            # Only show filename + one parent dir to keep it readable
                            p = Path(fp)
                            label = str(p.relative_to(Path.home())) if fp.startswith(str(Path.home())) else fp
                            built_files.append(label)

                    elif name == "Bash":
                        cmd = inp.get("command", "")
                        last_bash_cmd = cmd
                        if GIT_COMMIT_RE.search(cmd) and "git commit" in cmd:
                            msg_text = _extract_commit_msg(cmd)
                            if msg_text and msg_text not in commits:
                                commits.append(msg_text)
                            elif msg_text is None and len(commits) == 0:
                                commits.append("(commit — see transcript)")

                # Text blocks — scan for decisions and next steps
                elif btype == "text":
                    text = block.get("text", "").strip()
                    if not text:
                        continue

                    # Fix for pending error
                    if pending_error and not fix_text:
                        fix_text = _first_sentence(text, 200)

                    # Decisions — extract the sentence that contains the keyword
                    if DECISION_PATTERNS.search(text) and len(decisions) < 5:
                        sentence = _matching_sentence(text, DECISION_PATTERNS, 160)
                        if sentence and len(sentence) > 25 and not TRIVIAL_RE.match(sentence) and sentence not in decisions:
                            decisions.append(sentence)

                    # Next steps — same: find the sentence with the keyword
                    if NEXT_PATTERNS.search(text) and len(next_steps) < 3:
                        sentence = _matching_sentence(text, NEXT_PATTERNS, 160)
                        if sentence and len(sentence) > 20 and not TRIVIAL_RE.match(sentence) and sentence not in next_steps:
                            next_steps.append(sentence)

            # Resolve pending error with the fix we found
            if pending_error:
                errors.append({
                    "error": pending_error[:120],
                    "fix": fix_text or "see transcript",
                })
                pending_error = None

        # ── User messages (tool results = errors) ─────────────────────────────
        if role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue

                raw_text = _extract_text_from_content(block.get("content", ""))
                if not raw_text or len(errors) >= 5:
                    continue

                # Strip ANSI before any analysis — terminal output with escape codes
                # gets flagged as is_error by Claude Code but isn't a real error
                text = _strip_ansi(raw_text)
                first_line = text.split("\n")[0].strip()

                # Skip line-numbered file content (Read tool results)
                if LINE_NUMBERED_RE.match(first_line):
                    continue

                # Skip short/empty lines after stripping
                if len(first_line) < 10:
                    continue

                # Skip output from git push/pull/status/log — contains commit
                # messages that may include the word "error" incidentally
                if GIT_OUTPUT_CMDS.search(last_bash_cmd):
                    continue

                # is_error + tool_use_error tag wrapping the content = definitive
                starts_with_error_tag = raw_text.strip().startswith("<tool_use_error>")
                if block.get("is_error") and starts_with_error_tag:
                    m = re.search(r"<tool_use_error>(.*?)</tool_use_error>", raw_text, re.DOTALL)
                    msg = _strip_ansi(m.group(1).strip() if m else first_line)[:120]
                    if msg:
                        pending_error = msg
                elif block.get("is_error"):
                    # is_error alone fires for ANSI output, large responses, etc.
                    # Only trust it if the FIRST LINE looks like an error — not the full body,
                    # which may contain session excerpts with incidental error keywords.
                    if ERROR_PATTERNS.search(first_line):
                        pending_error = first_line[:120]
                else:
                    # No error flag — only trigger on first line with strong signals
                    if ERROR_PATTERNS.search(first_line) and len(first_line) > 15:
                        pending_error = first_line[:120]

    return {
        "date": session_date or datetime.now().strftime("%Y-%m-%d"),
        "built": built_files,
        "commits": commits,
        "errors": errors,
        "decisions": decisions,
        "next": next_steps,
    }


# ── Entry formatting ──────────────────────────────────────────────────────────

def _derive_title(db_title: str | None, data: dict) -> str:
    """Use first real commit message as title — more accurate than tm's auto-title."""
    for c in data.get("commits", []):
        if c and "see transcript" not in c:
            return c[:70]
    if db_title and len(db_title) > 5:
        return db_title[:70]
    return "session"


def _format_entry(data: dict, session_id: str, title: str) -> str:
    date = data["date"]
    short_title = _derive_title(title, data)

    lines = [f"## {date} — {short_title}"]
    lines.append(f"\n*Session: {session_id[:8]}*\n")

    lines.append("**Built:**")
    if data["built"]:
        for f in data["built"]:
            lines.append(f"- `{f}`")
    else:
        lines.append("- *(no files written)*")

    lines.append("\n**Decisions:**")
    if data["decisions"]:
        for d in data["decisions"]:
            lines.append(f"- {d}")
    else:
        lines.append("- *(none detected)*")

    lines.append("\n**Errors fixed:**")
    if data["errors"]:
        for e in data["errors"]:
            lines.append(f"- {e['error']}")
            if e["fix"] and e["fix"] != "see transcript":
                lines.append(f"  → {e['fix']}")
    else:
        lines.append("- *(none)*")

    lines.append("\n**Commits:**")
    if data["commits"]:
        for c in data["commits"]:
            lines.append(f"- {c}")
    else:
        lines.append("- *(none)*")

    lines.append("\n**Next:**")
    if data["next"]:
        for n in data["next"]:
            lines.append(f"- {n}")
    else:
        lines.append("- *(see Next Steps in Home.md)*")

    return "\n".join(lines)


# ── Vault writer ──────────────────────────────────────────────────────────────

def _find_build_log(project_name: str) -> Path | None:
    """Locate the Build Log.md for a given project name.

    Search order:
    1. ~/.tm_projects config file (explicit path mapping)
    2. ~/Documents/PROJECT_NAME/ (default location)
    3. Case-insensitive scan of ~/Documents/
    """
    # 1. Config file
    project_paths = load_project_paths()
    mapped = project_paths.get(project_name.lower())
    if mapped:
        bl = mapped / "Build Log.md"
        if bl.exists():
            return bl

    # 2. Default Documents location
    candidate = DOCS_DIR / project_name / "Build Log.md"
    if candidate.exists():
        return candidate

    # 3. Case-insensitive scan
    if DOCS_DIR.exists():
        for d in DOCS_DIR.iterdir():
            if d.is_dir() and d.name.lower() == project_name.lower():
                bl = d / "Build Log.md"
                if bl.exists():
                    return bl

    return None


def _append_entry(build_log: Path, entry: str) -> None:
    """Prepend new entry before existing entries (newest first)."""
    content = build_log.read_text(encoding="utf-8")

    # Find the first ## heading — that's where entries start
    m = re.search(r"\n(## )", content)
    if m:
        insert_pos = m.start() + 1
        new_content = content[:insert_pos] + entry + "\n\n---\n\n" + content[insert_pos:]
    else:
        # No entries yet — append after the last --- in the header
        new_content = content.rstrip() + "\n\n---\n\n" + entry + "\n"

    build_log.write_text(new_content, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def run(conn, args: list[str]) -> int:
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if len(args) < 2:
        print()
        print("  Usage:")
        print("    tm push SESSION_ID PROJECT_NAME")
        print("    tm push bfada840 ProjectLogger")
        print("    tm push bfada840 SpotTrader --dry-run")
        print()
        return 1

    partial_id    = args[0]
    project_name  = args[1]

    # Resolve session
    session_id, title = _resolve_session(conn, partial_id)
    if not session_id:
        print(f"\n  Session not found: {partial_id}\n")
        return 1

    # Find jsonl
    jsonl_path = _find_jsonl(session_id)
    if not jsonl_path:
        print(f"\n  Transcript file not found for session {partial_id}\n")
        return 1

    # Find Build Log
    build_log = _find_build_log(project_name)
    if not build_log:
        config_path = projects_config_path()
        print(f"\n  Build Log.md not found for project: {project_name}")
        print(f"  Searched: ~/Documents/{project_name}/Build Log.md")
        print(f"  If your vault is elsewhere, add it to {config_path}:")
        print(f"    {project_name} = /path/to/your/{project_name}")
        print()
        return 1

    print(f"\n  Parsing session {session_id[:8]}…")
    data = _parse_session(jsonl_path)

    entry = _format_entry(data, session_id, title or project_name)

    if dry_run:
        print()
        print("  ── Dry run — entry that would be written ──────────────────")
        print()
        for line in entry.split("\n"):
            print(f"  {line}")
        print()
        print(f"  Would write to: {build_log}")
        print()
        return 0

    _append_entry(build_log, entry)

    built_count    = len(data["built"])
    commit_count   = len(data["commits"])
    decision_count = len(data["decisions"])
    error_count    = len(data["errors"])

    print(f"  \033[32m✓\033[0m  Pushed to {build_log}")
    print(f"     {built_count} files  ·  {commit_count} commits  ·  {decision_count} decisions  ·  {error_count} errors")
    print(f"\n  \033[2mOpen in Obsidian: obsidian://open?vault={project_name}&file=Build Log\033[0m\n")

    return 0
