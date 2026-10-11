# TerminalMind

Search, browse, and extract context from your Claude, Codex, and AGY history — plus every shell command you've ever run. Local dashboard, semantic search, smart context extraction.

Your AI clients already save messages, tool calls, and prompts to disk. TerminalMind indexes those local stores, gives sessions readable titles, embeds visible conversation turns for semantic search, and makes everything instantly queryable from the terminal or a browser dashboard.

---

## What it does

**Browse all sessions** — Claude, Codex, and AGY sessions carry source badges and readable titles. Click any card to read the full conversation inline. Pin sessions, add notes, export as markdown.

**Semantic search** — find sessions by meaning, not just keywords. "How did we handle the pipeline issue" finds the right session even if those exact words never appeared. Three modes: Hybrid (default), Semantic, Keyword.

**Smart context for every agent** — `tm context "question"` extracts only the relevant message excerpts and copies them to the clipboard. Add `--agent claude|codex|agy` when the source matters.

**Shell command history** — log every terminal command (timestamp, duration, exit code, project) and browse it in the CLI or the dashboard Shell tab.

**Narrative summaries** — `tm digest` generates Ollama narrative paragraphs for recent sessions: what was worked on, what problems came up, what got resolved.

**Activity reports** — `tm report` shows per-project session counts, message counts, and actual working time from shell command spans.

**Zero cloud** — all data stays local. No API keys. Ollama runs on your GPU for titles and summaries. Embeddings run on your CPU.

---

## Requirements

- Python 3.10+
- At least one supported client history: Claude Code, Codex, or AGY/Antigravity
- Ollama (optional, for AI titles and digest summaries) — [ollama.com](https://ollama.com)

---

## Install

**macOS / Linux**
```bash
bash <(curl -fsSL https://raw.githubusercontent.com/CoderShibay/TerminalMind/main/install.sh)
```

**Windows** (PowerShell — run as normal user, no admin needed)
```powershell
iwr https://raw.githubusercontent.com/CoderShibay/TerminalMind/main/install.ps1 -UseBasicParsing | iex
```

**pip (any platform)**
```bash
pip install terminalmd
```

TerminalMind loads its semantic-search model from the local Hugging Face cache
and does not contact the network during normal commands. If the model is not
cached yet, download it once while online:

```bash
TM_ALLOW_MODEL_DOWNLOAD=1 tm sync
```

After that, `tm` runs fully offline. Without the cached model, keyword search
continues to work.

---

## Shell Hook Setup (optional)

To log every terminal command you run:

```bash
echo 'source ~/.terminalmd/daemon/shell_hook.sh' >> ~/.zshrc
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

# Session identity
tm session                            # current session ID + title — copies short ID to clipboard
tm sessions                           # all sessions newest to oldest with titles
tm sessions --simple                  # clean two-column output: ID  Title
tm sessions "SpotTrader"              # search by title keyword
tm sessions "bfad"                    # search by session ID prefix

# Session recovery and linking
tm resume                             # minimal restart — ~100 tokens, paste into new session
tm resume --project Segmentation      # last session for a specific project
tm resume --last 3                    # last 3 sessions (when multiple crashed)
tm link ID1 ID2                       # link two sessions — context searches span both
tm link ID1 ID2 ID3                   # link three or more sessions in one shot
tm link ID1 ID2 --note "continued"    # with a note
tm link unlink ID1 ID2                # remove a link
tm links                              # show all linked session groups

# Project Logger — push session to Obsidian vault (zero tokens)
tm push SESSION_ID PROJECT_NAME       # extract session → append to project Build Log.md
tm push bfada840 SpotTrader           # short 8-char session ID works
tm push bfada840 SpotTrader --dry-run # preview without writing
tm push init PROJECT_NAME             # create vault from template (Home.md + Build Log.md)
tm push init PROJECT_NAME --path PATH # create vault at a custom location
# Add to ~/.tm_projects for vaults outside ~/Documents/:
#   SpotTrader = /custom/path/SpotTrader

# Shell command history
tm shell                              # newest 100 commands
tm shell --project Segmentation       # filter by working directory
tm shell --failed                     # only commands that exited non-zero
tm shell --days 7                     # last 7 days
tm shell --search "build_ensemble"    # search command text

# Search (human browsing)
tm search "query"                     # keyword search across all conversations
tm search "query" --last 7d           # filter by time
tm search "query" --agent codex       # filter by source agent

# Maintenance
tm sync                               # re-index Claude, Codex, AGY + shell log
tm sync --no-embed                    # index text without generating embeddings
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

### Log a session to your Obsidian vault (zero tokens)
After finishing a session, push what was built directly to your project's Build Log — no Claude involved, no extra tokens:
```bash
tm push bfada840 SpotTrader
# → reads ~/.claude/projects/**/bfada840*.jsonl
# → extracts: files written, git commits, errors, decisions
# → prepends formatted entry to ~/Documents/SpotTrader/Build Log.md
```
Use `--dry-run` to preview the entry before writing.

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
- **Session cards** — source-agent badge, title (click ✎ to rename inline), project tags, date, message count
- **Expand any card** — full conversation, note editor, export as `.md`, related sessions
- **"prompts only" badge** — older sessions where only prompts were saved
- Filter sessions by **All agents**, **Claude**, **Codex**, or **AGY**.

### Search tab
- **Hybrid mode** (default) — keyword + semantic combined
- **Semantic mode** — finds by meaning, not exact words. Purple bar shows relevance score.
- **Keyword mode** — exact FTS5 word matching
- Filter by time window, project, and source agent. Pin sessions directly from results.

### Shell tab
- Full terminal command history in the browser
- Filter by project, time range, or failed-only
- Search across command text
- Commands grouped by day: `HH:MM:SS  ✓  3.2s  python3 run.py  [Segmentation]`
- Failed commands shown in red
- Setup instructions shown if hook isn't active yet

### Board tab
- Shows shared MessageBoard tasks, activity, and promoted knowledge
- Preserves agent provenance from `posted_by`, `claimed_by`, `sender`, and `promoted_by`
- Uses the same durable JSONL coordination records as Claude, Codex, and AGY

### Tools tab
- Total calls, unique tools, and the most-used tool
- Filter by time, category (Core / MCP / Agent), source agent, or tool name
- Counts assistant invocations once and excludes tool-result messages
- Normalizes AGY MCP wrapper calls to their underlying `mcp__server__tool` name when available

### Files tab
- Ranks file hotspots by recorded read, edit, write, patch, and command activity
- Extracts paths from Claude, Codex, and AGY tool formats
- Filter by time, project, source agent, or path
- Excludes directories and known path noise; this is an activity view, not a version-control audit

### Errors tab
- Groups normalized failure signatures across tool results
- Filter by time, project, source agent, or text; optionally show recurring patterns only
- Uses full namespaced session IDs so counts do not collide between clients

The **Sync** button refreshes all three transcript sources, shell history, titles,
embeddings, and derived file hotspots through `POST /api/sync`.

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

The supported clients write their data to disk automatically:

| File | Contents | Indexed as |
|------|----------|------------|
| `~/.claude/history.jsonl` | Every prompt ever sent | `claude_prompts` |
| `~/.claude/projects/**/*.jsonl` | Full conversation transcripts | `claude_messages` |
| `~/.claude/sessions/*.json` | Session metadata | `claude_sessions` |
| `~/.codex/sessions/**/*.jsonl` | Codex rollout transcripts | `claude_messages` with `agent=codex` |
| `~/.gemini/antigravity-cli/brain/*/.system_generated/logs/transcript_full.jsonl` | AGY conversations | `claude_messages` with `agent=agy` |
| `~/.gemini/antigravity-cli/conversation_summaries.db` | AGY titles and workspace metadata | `claude_sessions`, `session_titles` |
| `~/.terminalmd/shell_log.jsonl` | Shell commands (from hook) | `shell_commands` |

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
| Install dir | `~/.terminalmd/` (macOS/Linux) · `~\.terminalmd\` (Windows) |
| Database | `~/.terminalmd/db/terminalmd.db` |
| Shell log | `~/.terminalmd/shell_log.jsonl` |
| Embedding model | `~/.cache/huggingface/` (87 MB, one-time download) |
| CLI | `~/.local/bin/tm` (macOS/Linux) · `~\.local\bin\tm.cmd` (Windows) |
| Project config | `~/.tm_projects` (optional — custom vault paths for `tm push`) |

Original client files are never modified. TerminalMind stores namespaced non-Claude IDs such as `codex:<native-id>` and `agy:<native-id>` while accepting native ID prefixes at lookup time.

---

## License

MIT
