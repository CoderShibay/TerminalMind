"""Generate clean session titles and project tags. Cached in session_titles table."""
import re
import subprocess
import time

# ── Ollama availability (checked once per process) ────────────────────────────
_ollama_ok: bool | None = None

def _check_ollama() -> bool:
    """Return True only if Ollama is running AND llama3.2 is pulled."""
    global _ollama_ok
    if _ollama_ok is not None:
        return _ollama_ok
    try:
        r = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5,
        )
        _ollama_ok = r.returncode == 0 and "llama3.2" in r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        _ollama_ok = False
    return _ollama_ok

# Keywords → project tag
PROJECT_KEYWORDS = {
    "segmentation":  "Segmentation",
    "medsam":        "Segmentation",
    "nnunet":        "Segmentation",
    "gemma4":        "Segmentation",
    "medgemma":      "Segmentation",
    "staple":        "Segmentation",
    "wilms":         "Segmentation",
    "tumor":         "Segmentation",
    "spottrader":    "SpotTrader",
    "bybit":         "SpotTrader",
    "binance":       "SpotTrader",
    "crypto":        "SpotTrader",
    "trading":       "SpotTrader",
    "strategy":      "SpotTrader",
    "pkb":           "PKB",
    "takeout":       "PKB",
    "knowledge base":"PKB",
    "chromadb":      "PKB",
    "dicom":         "DICOM App",
    "flutter":       "DICOM App",
    "android":       "DICOM App",
    "hemavision":    "HemaVision",
    "hematology":    "HemaVision",
    "terminalmd":    "TerminalMind",
    "terminalm":     "TerminalMind",
    "tm search":     "TerminalMind",
    "messageboard":  "MessageBoard",
    "message board": "MessageBoard",
    "obsidian":      "Obsidian",
    "vault":         "Obsidian",
    "commandcenter": "CommandCenter",
    "bmrc":          "Research",
    "proposal":      "Research",
    "radiomics":     "Research",
    "pasha":         "St. Jude",
    "pews":          "St. Jude",
    "pact":          "St. Jude",
    "st jude":       "St. Jude",
    "embedding":     "EmbedAlign",
    "embedalign":    "EmbedAlign",
    "ollama":        "AI Tools",
    "claude":        "AI Tools",
    "python":        "Python",
    "drive":         "Google Drive",
}

# Patterns that indicate the message is pasted file content, not a real question
_FILE_CONTENT_PATTERNS = [
    r"^\d+\t",           # line numbers (cat -n output)
    r"^---\n",           # YAML frontmatter
    r"^```",             # code block
    r"^\| .+ \|",        # markdown table
    r"^#{1,6} ",         # markdown heading (as entire message)
    r"^import ",         # Python import
    r"^def |^class ",    # Python code
    r"^<",               # HTML/XML
]
_FILE_RE = [re.compile(p, re.MULTILINE) for p in _FILE_CONTENT_PATTERNS]

_MARKDOWN_RE = re.compile(r"(\*\*|__|`{1,3}|\[|\]|\(|\)|#{1,6} |>|---)")


def _is_file_content(text: str) -> bool:
    head = text[:300]
    return any(r.search(head) for r in _FILE_RE)


def _clean(text: str) -> str:
    text = _MARKDOWN_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _heuristic_title(messages: list[dict]) -> tuple[str, str]:
    """Extract a title and summary from the first real user message."""
    first_real = None
    for msg in messages:
        if msg["role"] != "user" or not msg["content_text"]:
            continue
        text = msg["content_text"].strip()
        if len(text) < 8:
            continue
        if _is_file_content(text):
            continue
        first_real = text
        break

    if not first_real:
        # Fall back to any user message
        for msg in messages:
            if msg["role"] == "user" and msg["content_text"]:
                first_real = msg["content_text"].strip()
                break

    if not first_real:
        return "Untitled session", ""

    cleaned = _clean(first_real)

    # Title: first sentence or first 60 chars, whichever is shorter
    sentence_end = re.search(r"[.!?]", cleaned[:120])
    if sentence_end and sentence_end.start() > 10:
        title = cleaned[:sentence_end.start()].strip()
    else:
        words = cleaned.split()
        title = " ".join(words[:10])

    if len(title) > 70:
        title = title[:67] + "…"

    # Summary: first 200 chars of cleaned text
    summary = cleaned[:200] + ("…" if len(cleaned) > 200 else "")

    return title, summary


def _ollama_title(messages: list[dict]) -> tuple[str, str] | None:
    """Ask llama3.2 to generate a short title. Returns None if Ollama unavailable."""
    # Collect first 6 user messages
    user_msgs = []
    for msg in messages:
        if msg["role"] == "user" and msg["content_text"] and not _is_file_content(msg["content_text"]):
            user_msgs.append(_clean(msg["content_text"])[:300])
        if len(user_msgs) >= 4:
            break

    if not user_msgs:
        return None

    context = "\n".join(f"- {m}" for m in user_msgs)
    prompt = (
        f"These are the first few messages from a work session with an AI assistant:\n{context}\n\n"
        "Write ONLY a 5-8 word title that describes what this session was about. "
        "Be specific and concrete. No quotes, no punctuation at the end."
    )

    try:
        result = subprocess.run(
            ["ollama", "run", "llama3.2", prompt],
            capture_output=True, text=True, timeout=30,
        )
        title = result.stdout.strip().split("\n")[0].strip()
        title = re.sub(r'^["\'`]|["\'`]$', "", title).strip()
        if 5 <= len(title) <= 100:
            return title, ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def _detect_tags(messages: list[dict]) -> list[str]:
    """Detect project tags from message content."""
    # Scan first 8 user messages
    combined = ""
    count = 0
    for msg in messages:
        if msg["role"] == "user" and msg["content_text"]:
            combined += " " + msg["content_text"].lower()
            count += 1
        if count >= 8:
            break

    found = {}
    for keyword, tag in PROJECT_KEYWORDS.items():
        if keyword in combined:
            found[tag] = True

    return list(found.keys())[:4]  # max 4 tags


def generate_for_session(conn, session_id: str, use_ollama: bool = True) -> dict:
    """Generate title, summary, and tags for one session. Caches result."""
    # Already generated?
    existing = conn.execute(
        "SELECT title, summary, project_tags FROM session_titles WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if existing:
        return dict(existing)

    # Load messages for this session
    messages = conn.execute(
        """SELECT role, content_text FROM claude_messages
           WHERE session_id = ? AND content_text IS NOT NULL
           ORDER BY ts ASC LIMIT 20""",
        (session_id,),
    ).fetchall()
    messages = [dict(m) for m in messages]

    tags = _detect_tags(messages)

    # Try Ollama first, fall back to heuristic
    method = "heuristic"
    title, summary = _heuristic_title(messages)

    if use_ollama and _check_ollama() and messages:
        result = _ollama_title(messages)
        if result:
            title = result[0]
            summary = result[1] or summary
            method = "ollama"

    tags_str = ",".join(tags)
    conn.execute(
        """INSERT OR REPLACE INTO session_titles
           (session_id, title, summary, project_tags, generated_at, method)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, title, summary, tags_str, int(time.time() * 1000), method),
    )
    conn.commit()

    return {"title": title, "summary": summary, "project_tags": tags_str}


def run(conn, use_ollama: bool = True) -> tuple[int, str]:
    """Generate titles for sessions that don't have one yet.
    Returns (count, method) where method is 'ollama' or 'heuristic'."""
    sessions = conn.execute(
        """SELECT s.session_id FROM claude_sessions s
           LEFT JOIN session_titles t ON t.session_id = s.session_id
           WHERE t.session_id IS NULL"""
    ).fetchall()

    ollama_available = use_ollama and _check_ollama()
    method = "ollama" if ollama_available else "heuristic"

    for row in sessions:
        generate_for_session(conn, row["session_id"], use_ollama=ollama_available)

    return len(sessions), method
