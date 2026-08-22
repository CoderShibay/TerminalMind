# TerminalMind

Search, browse, and extract context from your entire Claude Code conversation history. Local dashboard + smart context extraction for new Claude sessions.

Claude Code already saves every message, tool call, and prompt to disk. TerminalMind indexes that data, gives sessions readable titles, and makes everything searchable — by keyword or by meaning.

---

## What it does

**Browse all sessions** — every Claude Code session with an AI-generated title (via Ollama) or smart heuristic fallback. Grouped by Today / Yesterday / This Week / Older. Click any card to read the full conversation inline.

**Semantic search** — find sessions by meaning, not just keywords. "How did we handle the pipeline issue" finds the relevant session even if those exact words never appeared.

**Smart context for Claude** — `tm context "question"` extracts only the relevant message excerpts from your history (~300 tokens) and copies them to your clipboard. Paste into any new Claude session. No re-explaining required.

**History backfill** — sessions going back to your first Claude Code use, even before full transcripts were saved.

**Zero cloud** — all data stays local. No API keys. Ollama runs on your GPU for titles. Embeddings run on your CPU.

---

## Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/code) installed and used at least once
- Ollama (optional, for AI-generated titles) — [ollama.com](https://ollama.com)

---

## Install

```bash
git clone https://github.com/CoderShibay/TerminalMind.git
cd TerminalMind
bash install.sh
```

Or one-liner:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CoderShibay/TerminalMind/main/install.sh)
```

The installer checks Python, installs dependencies, creates the `tm` command, runs first sync, and optionally installs a background service so the dashboard is always available.

---

## CLI Reference

```bash
# Dashboard
tm serve                              # open browser dashboard at localhost:8888
tm serve --no-browser                 # run server without opening browser

# Context extraction (main feature — use with Claude)
tm context "question"                 # relevant excerpts ~300 tokens, auto-copies to clipboard
tm context "question" --full          # full messages with code/errors preserved
tm context "question" --session ID    # search within one specific session (use first 8 chars of ID)
tm context "question" --top 5         # limit to 5 results

# Indexing
tm sync                               # re-index all Claude files
tm verify                             # health report — what's indexed, what's missing, Ollama status

# Session info
tm today                              # sessions, topics, message count for today
tm today --yesterday                  # same for yesterday
tm sessions                           # list all sessions in terminal
tm status                             # DB stats

# Search (for human browsing)
tm search "query"                     # keyword search
tm search "query" --last 7d           # filter by time window

# Background service
tm service install                    # auto-start server on login (macOS/Linux)
tm service uninstall                  # remove auto-start
tm service status                     # check if running
```

---

## Use Cases

### Debugging a past issue
You tried to fix something two weeks ago. You remember it involved a timeout error but not the details.
```bash
tm context "timeout error" --full
# → gets the actual error, the code, and what was tried
# → paste into Claude to fix it properly this time
```

### Picking up a project after a break
You haven't touched SpotTrader in a week. You don't remember where you left off.
```bash
tm today --yesterday        # see what you worked on
tm sessions                 # find the SpotTrader session
tm context "SpotTrader strategy implementation" --full
# → Claude knows exactly where you stopped
```

### Finding a past architectural decision
You remember deciding something about the database schema but not what or why.
```bash
tm search "database schema"          # browse results in dashboard
tm context "database schema decision" # extract for Claude
```

### Avoiding repeating solved problems
You're about to ask Claude to do something. You suspect it was already done.
```bash
tm context "topic you're about to ask about"
# → if the answer is there, paste it. Save the tokens.
```

### Recovering code that was written and lost
You had working code in a past session that was never saved to a file.
```bash
tm context "the feature name" --full
# → finds the assistant messages where the code was written
# → copy it out
```

### Understanding what you actually did this week
```bash
tm today
tm today --yesterday
# → sessions, titles, message counts, time ranges
```

### Annotating important sessions so you can find them later
You just had a session where a key architectural decision was made.
- Open dashboard → find the session → click to expand → **+ Add note**
- Write: *"This is where we decided to use STAPLE instead of majority voting"*
- Now it's searchable and shows on the card permanently

### Pinning reference sessions
Some sessions are permanent references — the one where you designed the system, the one with the working pipeline command.
- Hover any card → click 📍 → it stays at the top of Browse forever
- Works from search results too

### Finding sessions from a specific day
You remember working intensively on something last Thursday.
- Click that Thursday cell in the heatmap
- Browse filters instantly to just that day's sessions

### Passing complete context to a new Claude session
You want to continue complex work in a fresh session without copy-pasting manually.
```bash
tm context "the specific problem" --session ID_8CHARS --full
# → auto-copied to clipboard
# → open new Claude session → paste → continue
```

### Sharing context across AI tools
The output of `tm context` is plain text — paste it into any AI: Claude, ChatGPT, Gemini, a local model. It's not Claude-specific.

### Verifying everything is indexed
After a crash or first install, check nothing is missing.
```bash
tm verify
# → shows files on disk vs files in DB
# → flags any sessions without titles
# → reports Ollama status
```

---

## Claude Integration

TerminalMind is designed to work with Claude Code sessions, not just as a standalone tool.

### How Claude uses it automatically

Add this to your `~/.claude/CLAUDE.md` and Claude will reach for `tm context` on its own whenever it needs past context:

```markdown
## TerminalMind
`tm` is installed. Use `tm context "question"` whenever you need past session context.
Use `--full` when you need code or errors. Use `--session ID --full` for a specific session.
```

With this in place, when you say *"why did kokoro TTS fail last time?"* in a new session, Claude will run `tm context "kokoro TTS error" --full` via Bash, read the relevant past messages, and answer — without you copying anything.

### Manual workflow

```bash
tm context "your question"
# → excerpts copied to clipboard
# → paste into new Claude session
```

### Use cases

| You say to Claude | Claude runs |
|-------------------|-------------|
| "what did we decide about MedGemma?" | `tm context "MedGemma decision"` |
| "kokoro TTS wasn't working, fix it" | `tm context "kokoro TTS error" --full` |
| "continue the SpotTrader work" | `tm today` then `tm context "SpotTrader strategy"` |
| "why did the segmentation pipeline fail?" | `tm context "segmentation pipeline error" --full` |
| "what approach did we try for the ensemble?" | `tm context "ensemble approach"` |
| "what did I work on this week?" | `tm today` + `tm today --yesterday` |

### `tm context` vs full session export

| | `tm context` | Full export |
|--|--|--|
| Tokens | ~300 | 2,000–8,000 |
| What you get | Most relevant excerpts | Everything |
| Code/errors | With `--full` | Always |
| Best for | Passing context to Claude | Reading yourself |

---

## Dashboard

Open with `tm serve` → `http://localhost:8888`

### Browse tab
- **Stats panel** — This Week / This Month / All Time: sessions, prompts, top project
- **Activity heatmap** — 52-week grid. Click any cell to filter sessions to that day.
- **Pinned sessions** — pin important sessions to the top. Available from Browse and Search.
- **Session cards** — title (click ✎ to rename), project tags, date, message count
- **Expand any card** — see full conversation, add a note, export as `.md`, see related sessions
- **"prompts only" badge** — older sessions where only prompts were saved, not full transcripts

### Search tab
- **Hybrid mode** (default) — keyword + semantic combined
- **Semantic mode** — finds by meaning, not exact words
- **Keyword mode** — exact word matching
- Filter by time window (24h / 7d / 30d)
- Pin sessions directly from search results
- Expand any result to read the full conversation inline

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate between cards |
| `Space` / `Enter` | Expand / collapse |
| `p` | Pin / unpin |
| `n` | Add / edit note |
| `e` | Export as .md |
| `/` | Jump to Search |
| `Esc` | Back to Browse |

---

## How it works

Claude Code writes your data to disk automatically:

| File | Contents |
|------|----------|
| `~/.claude/history.jsonl` | Every prompt ever sent |
| `~/.claude/projects/**/*.jsonl` | Full conversation transcripts |
| `~/.claude/sessions/*.json` | Session metadata |

TerminalMind reads these files and builds a local SQLite database with:
- **FTS5 index** — fast keyword search
- **Sentence embeddings** — 384-dim vectors per message (`all-MiniLM-L6-v2`, runs locally)
- **Session titles** — generated by llama3.2 via Ollama, or heuristic fallback
- **History backfill** — sessions reconstructed from `history.jsonl` going back to your first Claude Code use

Everything runs on your machine. No network calls during search or context extraction.

---

## Ollama (better titles, optional)

```bash
# Install Ollama: https://ollama.com
ollama pull llama3.2
tm sync   # generates AI titles for all sessions
```

Sessions titled by Ollama show a `✦` badge. Without Ollama, titles come from the first user message — works fine.

---

## Data privacy

- SQLite DB: `~/terminalmd/db/terminalmd.db`
- Embedding model cache: `~/.cache/huggingface/`
- Claude's original files in `~/.claude/` are never modified
- No network calls during normal use (Ollama and sentence-transformers run locally)

---

## Roadmap

- [ ] Shell hook — log every terminal command to SQLite (Phase 4)
- [ ] `tm history` / `tm report` — per-project command log and daily digest (Phase 5)
- [ ] `tm suggest` — Ollama prompt suggestions from recent context (Phase 6)
- [ ] File watcher — auto-sync when `~/.claude/` changes
- [ ] `pip install terminalmd` on PyPI

---

## License

MIT
