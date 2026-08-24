# TerminalMind

Search, browse, and extract context from your entire Claude Code history — plus every shell command you've ever run. Local dashboard, semantic search, smart context extraction.

Claude Code already saves every message, tool call, and prompt to disk. TerminalMind indexes that data, gives sessions readable titles, embeds everything for semantic search, and makes it instantly queryable — from the terminal or a browser dashboard.

---

## What it does

**Browse all sessions** — every Claude Code session with an Ollama-generated title (or heuristic fallback). Click any card to read the full conversation inline. Pin sessions, add notes, export as markdown.

**Semantic search** — find sessions by meaning, not just keywords. "How did we handle the pipeline issue" finds the right session even if those exact words never appeared. Three modes: Hybrid (default), Semantic, Keyword.

**Smart context for Claude** — `tm context "question"` extracts only the relevant message excerpts (~300 tokens) and copies them to clipboard. Paste into any new Claude session. No re-explaining required.

**Shell command history** — log every terminal command (timestamp, duration, exit code, project) and browse it in the CLI or the dashboard Shell tab.

**Narrative summaries** — `tm digest` generates Ollama narrative paragraphs for recent sessions: what was worked on, what problems came up, what got resolved.

**Activity reports** — `tm report` shows per-project session counts, message counts, and actual working time from shell command spans.

**Zero cloud** — all data stays local. No API keys. Ollama runs on your GPU for titles and summaries. Embeddings run on your CPU.

---

## Requirements

- Python 3.10+
- [Claude Code](https://claude.ai/code) installed and used at least once
- Ollama (optional, for AI titles and digest summaries) — [ollama.com](https://ollama.com)

---

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CoderShibay/TerminalMind/main/install.sh)
```

Or manually:

```bash
git clone https://github.com/CoderShibay/TerminalMind.git ~/terminalmd
cd ~/terminalmd && python3 -m venv .venv && source .venv/bin/activate && pip install -e .
sudo tee /usr/local/bin/tm > /dev/null <<'EOF'
#!/bin/bash
exec ~/terminalmd/.venv/bin/python3 ~/terminalmd/main.py "$@"
EOF
sudo chmod +x /usr/local/bin/tm && tm sync
```

---

## Shell Hook Setup (optional)

To log every terminal command you run:

```bash
echo 'source ~/terminalmd/daemon/shell_hook.sh' >> ~/.zshrc
source ~/.zshrc
```

After this, `tm shell`, `tm report` TIME column, and the dashboard Shell tab all have data.

---

## CLI Reference

```bash
# Dashboard
tm serve                              # open browser dashboard at localhost:8888
tm serve --no-browser                 # run server without opening browser

# Context extraction (main feature — use with Claude)
tm context "question"                 # relevant excerpts ~300 tokens, auto-copies
tm context "question" --full          # full messages with code/errors preserved
tm context "question" --session ID    # restrict to one session (8-char prefix)
tm context "question" --top 5         # limit results

# Daily use
tm today                              # sessions and shell commands for today
tm today --yesterday
tm week                               # 7-day summary grouped by day
tm digest                             # Ollama narrative briefing of recent sessions
tm digest --quick                     # titles + stats only, no AI

# Reports
tm report                             # project breakdown with bar charts (last 30 days)
tm report --days 7                    # last 7 days
tm history                            # chronological Claude session timeline
tm history --shell                    # sessions interleaved with shell commands
tm history --project Segmentation     # filter by project

# Session recovery and linking
tm resume                             # minimal restart — ~100 tokens, paste into new session
tm resume --project Segmentation      # last session for a specific project
tm resume --last 3                    # last 3 sessions (when multiple crashed)
tm link ID1 ID2                       # link two sessions — context searches span both
tm link ID1 ID2 --note "continued"    # with a note
tm link unlink ID1 ID2                # remove a link
tm links                              # show all linked session groups

# Shell command history
tm shell                              # newest 100 commands
tm shell --project Segmentation       # filter by working directory
tm shell --failed                     # only commands that exited non-zero
tm shell --days 7                     # last 7 days
tm shell --search "build_ensemble"    # search command text

# Search (human browsing)
tm search "query"                     # keyword search across all conversations
tm search "query" --last 7d           # filter by time

# Maintenance
tm sync                               # re-index all Claude files + shell log
tm verify                             # health report — what's indexed, what's missing
tm status                             # DB stats and active sessions
tm service install                    # auto-start server on login (macOS/Linux)
```

---

## Use Cases

### Continue work from a previous session
```bash
tm context "SpotTrader strategy" --full
# → relevant past decisions copied to clipboard
# → paste into new Claude session
```

### Morning orientation
```bash
tm digest           # Ollama summaries of what you worked on recently
tm week             # full week at a glance, grouped by day
```

### Find a past decision
```bash
tm context "what did we decide about MedGemma"
# → finds the session by meaning, not exact words
```

### See where your time actually went
```bash
tm report --days 7
# → per-project session counts + actual working hours from shell spans
```

### Resume after a session crash
Your sessions ended unexpectedly. Start a new one, run:
```bash
tm resume            # or tm resume --last 3 if multiple crashed
```
Paste the output (~100 tokens) into the new session. Claude knows the session ID and last prompts, and pulls the rest on demand — no bulk context load.

Then link the old session to the new one so context searches span both:
```bash
tm link OLD_ID NEW_ID --note "continued after crash"
# Now: tm context --session NEW_ID searches BOTH sessions automatically
```

### Debug what you ran yesterday
```bash
tm shell --days 1 --failed
# → every command that failed yesterday, with exit code and duration
```

### Full picture of a day
```bash
tm history --shell --days 1
# → Claude sessions and shell commands merged into one chronological timeline
```

### Pass complete context to Claude automatically
Add to `~/.claude/CLAUDE.md`:
```markdown
## TerminalMind
`tm` is installed. Use `tm context "question"` whenever you need past session context.
Use `--full` when you need code or errors. Use `--session ID --full` for a specific session.
```
Claude will then run `tm context` automatically when it needs past context — no manual step required.

---

## Dashboard

Open with `tm serve` → `http://localhost:8888`

### Browse tab
- **Stats panel** — This Week / This Month / All Time: sessions, prompts, top project
- **Activity heatmap** — 52-week grid. Click any cell to filter sessions to that day.
- **Pinned sessions** — pin important sessions to the top. Survive all filters.
- **Session cards** — title (click ✎ to rename inline), project tags, date, message count
- **Expand any card** — full conversation, note editor, export as `.md`, related sessions
- **"prompts only" badge** — older sessions where only prompts were saved

### Search tab
- **Hybrid mode** (default) — keyword + semantic combined
- **Semantic mode** — finds by meaning, not exact words. Purple bar shows relevance score.
- **Keyword mode** — exact FTS5 word matching
- Filter by time window. Pin sessions directly from results.

### Shell tab
- Full terminal command history in the browser
- Filter by project, time range, or failed-only
- Search across command text
- Commands grouped by day: `HH:MM:SS  ✓  3.2s  python3 run.py  [Segmentation]`
- Failed commands shown in red
- Setup instructions shown if hook isn't active yet

### Keyboard shortcuts (Browse tab)

| Key | Action |
|-----|--------|
| `j` / `k` | Navigate between session cards |
| `Space` / `Enter` | Expand / collapse |
| `p` | Pin / unpin |
| `n` | Add / edit note |
| `e` | Export as .md |
| `/` | Jump to Search |
| `Esc` | Back to Browse |

---

## How it works

Claude Code writes your data to disk automatically:

| File | Contents | Indexed as |
|------|----------|------------|
| `~/.claude/history.jsonl` | Every prompt ever sent | `claude_prompts` |
| `~/.claude/projects/**/*.jsonl` | Full conversation transcripts | `claude_messages` |
| `~/.claude/sessions/*.json` | Session metadata | `claude_sessions` |
| `~/terminalmd/shell_log.jsonl` | Shell commands (from hook) | `shell_commands` |

TerminalMind reads these files and builds a local SQLite database with:
- **FTS5 indexes** — fast keyword search across messages, prompts, and shell commands
- **Sentence embeddings** — 384-dim vectors per message (`all-MiniLM-L6-v2`, CPU inference)
- **Session titles** — generated by `llama3.2` via Ollama, or heuristic fallback
- **History backfill** — sessions going back to your first Claude Code use
- **Shell spans** — first-to-last command per day per project = actual working time

Nothing leaves your machine during search, context extraction, or dashboard use.

---

## Ollama (optional but recommended)

```bash
ollama pull llama3.2
tm sync          # generates AI titles for all sessions
tm digest        # generates narrative session summaries
```

Sessions titled by Ollama show `✦`. Without Ollama, titles come from the first user message — still useful.

---

## Data locations

| What | Path |
|------|------|
| Database | `~/terminalmd/db/terminalmd.db` |
| Shell log | `~/terminalmd/shell_log.jsonl` |
| Embedding model | `~/.cache/huggingface/` (87 MB, one-time download) |
| CLI | `/usr/local/bin/tm` |

Claude's original files in `~/.claude/` are never modified.

---

## License

MIT
