"""Find spelling mistakes in user prompts with local llama3.2 (Ollama) and log them.

Runs incrementally: each prompt/message id is checked once (watermark per source),
and identical text is never sent to the model twice (text hash).
Llama output is filtered so only real typos are kept: the wrong word must appear in
the prompt, must not be a dictionary word, and the correction must be one.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2"
MAXLEN = 1200
LOCK_PATH = Path(__file__).parent.parent / "db" / "spelling.lock"
DICT_PATH = Path("/usr/share/dict/words")

# Project names, tools and slang llama tends to "correct" — seeded into spelling_ignore
DEFAULT_IGNORE = """claude tm agy alisyed codex exa herdr colab api diplome ecole fgd medsam cimea mcp cli etc app
wanna gonna nop pre alis dov trai mcps gemini ollama llama pkb json jsonl github repo repos config ui url sql
okay ok pls plz yeah yep gpt vs dicom ct segmentation wikifm hemavision bybit kokoro tts obsidian vault
gmail google youtube whatsapp facebook instagram ssd voxels apps auth async vps soundpeats ghostty terminalmd
markitdown polytechnique haematological emptied aaa azp strats gotta infor apostile""".split()

PROMPT = """You are a strict spell checker. Find ONLY misspelled English words in the user's text below.
Rules:
- Only real spelling mistakes/typos (e.g. "teh" -> "the", "recieve" -> "receive").
- Ignore code, file paths, URLs, commands, product names, acronyms, usernames, and technical terms.
- Ignore capitalization and punctuation and grammar.
- The "wrong" word must appear exactly in the text.
Return JSON: {"mistakes": [{"wrong": "...", "correct": "..."}]}. Return {"mistakes": []} if none.

TEXT:
"""

_dict: set[str] | None = None


def _words() -> set[str]:
    global _dict
    if _dict is None:
        try:
            _dict = {w.strip().lower() for w in DICT_PATH.open()}
        except OSError:
            _dict = set()
    return _dict


def in_dict(w: str) -> bool:
    d = _words()
    return (w in d or w.rstrip("s") in d
            or (w.endswith("es") and w[:-2] in d)
            or (w.endswith("ed") and w[:-2] in d)
            or (w.endswith("ing") and w[:-3] in d)
            or (w.endswith("d") and w[:-1] in d))


def clean(text: str) -> str:
    t = re.sub(r"```.*?```", " ", text, flags=re.S)           # code blocks
    t = re.sub(r"<[^>]+>.*?</[^>]+>", " ", t, flags=re.S)     # tagged/pasted blocks
    t = re.sub(r"\S*[/\\]\S*|\S+\.\w{1,4}\b|https?://\S+", " ", t)  # paths, files, urls
    return t.strip()[:MAXLEN]


def text_hash(cleaned: str) -> str:
    return hashlib.sha1(cleaned.encode()).hexdigest()


def keep(wrong: str, correct: str, text_words: set[str]) -> bool:
    """True if llama's suggestion looks like a real typo in this text."""
    if not wrong or not correct or "'" in wrong or "'" in correct or len(wrong) < 3:
        return False
    if wrong not in text_words or wrong == correct:
        return False
    if correct.replace(" ", "").replace("-", "") == wrong:
        return False
    if correct in (wrong + "s", wrong + "es") or wrong in (correct + "s", correct + "es"):
        return False
    if _words():
        if in_dict(wrong):
            return False
        if not all(in_dict(x) for x in re.findall(r"[a-z]+", correct)):
            return False
    return True


def excerpt(text: str, word: str, width: int = 40) -> str:
    m = re.search(r"\b" + re.escape(word) + r"\b", text, flags=re.I)
    if not m:
        return ""
    s = text[max(0, m.start() - width): m.end() + width]
    return " ".join(s.split())


def ask(cleaned: str) -> list[dict]:
    unknown = sorted({w for w in re.findall(r"[a-z]{3,}", cleaned.lower()) if not in_dict(w)})
    hint = (f"\n\nWords not found in the dictionary (check these first, some may be valid tech terms): "
            f"{', '.join(unknown)}") if unknown else ""
    body = json.dumps({"model": MODEL, "prompt": PROMPT + cleaned + hint, "format": "json",
                       "stream": False, "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(OLLAMA_URL, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(json.loads(r.read())["response"]).get("mistakes", [])
    return [m for m in out if isinstance(m, dict)
            and isinstance(m.get("wrong"), str) and isinstance(m.get("correct"), str)]


def _ms(ts) -> int | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return int(ts)
    try:
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def seed_ignore(conn) -> None:
    conn.executemany("INSERT OR IGNORE INTO spelling_ignore(word) VALUES (?)",
                     [(w,) for w in DEFAULT_IGNORE])
    conn.commit()


def _watermark(conn, source: str) -> int:
    row = conn.execute("SELECT last_id FROM spelling_state WHERE source=?", (source,)).fetchone()
    return row[0] if row else 0


def _set_watermark(conn, source: str, last_id: int) -> None:
    conn.execute("INSERT INTO spelling_state(source,last_id) VALUES (?,?) "
                 "ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id", (source, last_id))


def _pending_query(conn):
    """Yield (source, id, session_id, agent, ts, text) for unchecked rows, oldest first."""
    p = _watermark(conn, "prompts")
    for r in conn.execute("SELECT id, session_id, ts, prompt_text FROM claude_prompts "
                          "WHERE id > ? AND prompt_text IS NOT NULL ORDER BY id", (p,)).fetchall():
        yield "prompts", r[0], r[1], "claude", r[2], r[3]
    m = _watermark(conn, "messages")
    for r in conn.execute("SELECT id, session_id, agent, ts, content_text FROM claude_messages "
                          "WHERE id > ? AND role='user' AND content_text IS NOT NULL ORDER BY id",
                          (m,)).fetchall():
        yield "messages", r[0], r[1], r[2], r[3], r[4]


def pending_count(conn) -> int:
    p, m = _watermark(conn, "prompts"), _watermark(conn, "messages")
    return conn.execute(
        "SELECT (SELECT count(*) FROM claude_prompts WHERE id > ?) + "
        "(SELECT count(*) FROM claude_messages WHERE id > ? AND role='user')", (p, m)).fetchone()[0]


def record(conn, cleaned: str, mistakes: list[dict], session_id, agent, ts) -> int:
    """Store filtered mistakes for one checked text. Returns number stored."""
    h = text_hash(cleaned)
    words = set(re.findall(r"[a-z']+", cleaned.lower()))
    n = 0
    for mk in mistakes:
        wrong, correct = mk["wrong"].lower().strip("'"), mk["correct"].lower().strip()
        if keep(wrong, correct, words):
            n += conn.execute(
                "INSERT OR IGNORE INTO spelling_mistakes"
                "(wrong,correct,session_id,agent,ts,excerpt,text_hash) VALUES (?,?,?,?,?,?,?)",
                (wrong, correct, session_id, agent, _ms(ts), excerpt(cleaned, wrong), h)).rowcount
    conn.execute("INSERT OR IGNORE INTO spelling_checked(text_hash,checked_at) VALUES (?,?)",
                 (h, int(time.time() * 1000)))
    return n


def run(conn, limit: int | None = None, verbose: bool = False) -> int:
    """Check unchecked prompts with llama3.2. Returns number of new mistakes stored."""
    seed_ignore(conn)
    found = done = 0
    for source, row_id, session_id, agent, ts, text in _pending_query(conn):
        cleaned = clean(text)
        if len(cleaned) > 10 and not cleaned.startswith("/"):
            h = text_hash(cleaned)
            if not conn.execute("SELECT 1 FROM spelling_checked WHERE text_hash=?", (h,)).fetchone():
                try:
                    mistakes = ask(cleaned)
                except Exception as e:  # Ollama down or bad JSON: stop, retry next sync
                    if verbose:
                        print(f"  spelling: stopped ({e})")
                    conn.commit()
                    return found
                found += record(conn, cleaned, mistakes, session_id, agent, ts)
                done += 1
                if verbose and done % 25 == 0:
                    print(f"  spelling: {done} prompts checked, {found} mistakes")
        _set_watermark(conn, source, row_id)
        conn.commit()
        if limit and done >= limit:
            break
    return found


# ── Background runner (spawned from sync so tm commands stay fast) ───────────

def _lock_alive() -> bool:
    try:
        pid = int(LOCK_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def acquire_lock() -> bool:
    if _lock_alive():
        return False
    LOCK_PATH.write_text(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        if LOCK_PATH.read_text().strip() == str(os.getpid()):
            LOCK_PATH.unlink()
    except OSError:
        pass


def spawn_background(conn) -> bool:
    """Start `tm spelling scan` detached if there is new text and no scan running."""
    if _lock_alive() or pending_count(conn) == 0:
        return False
    main = Path(__file__).parent.parent / "main.py"
    subprocess.Popen([sys.executable, str(main), "spelling", "scan", "--quiet"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return True
