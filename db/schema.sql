-- Every prompt sent to Claude Code (from history.jsonl)
CREATE TABLE IF NOT EXISTS claude_prompts (
    id           INTEGER PRIMARY KEY,
    session_id   TEXT NOT NULL,
    ts           INTEGER NOT NULL,  -- epoch ms
    project      TEXT,
    cwd          TEXT,
    prompt_text  TEXT
);

-- Full message turns from transcript jsonl files
CREATE TABLE IF NOT EXISTS claude_messages (
    id           INTEGER PRIMARY KEY,
    uuid         TEXT UNIQUE,
    session_id   TEXT NOT NULL,
    ts           TEXT,              -- ISO timestamp
    role         TEXT,             -- 'user' or 'assistant'
    content_text TEXT,
    tool_name    TEXT,             -- set if role=assistant and it's a tool_use block
    project      TEXT,
    cwd          TEXT,
    source_file  TEXT              -- which .jsonl this came from
);

-- Claude Code sessions (from sessions/*.json)
CREATE TABLE IF NOT EXISTS claude_sessions (
    session_id   TEXT PRIMARY KEY,
    pid          INTEGER,
    cwd          TEXT,
    project      TEXT,
    started_at   INTEGER,          -- epoch ms
    updated_at   INTEGER,
    status       TEXT,             -- 'active', 'idle', 'ended'
    version      TEXT,
    kind         TEXT
);

-- FTS index over messages for fast search
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content_text,
    role,
    project,
    cwd,
    ts,
    uuid,
    session_id,
    content='claude_messages',
    content_rowid='id'
);

-- FTS index over prompts
CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
    prompt_text,
    project,
    cwd,
    session_id,
    content='claude_prompts',
    content_rowid='id'
);

-- Generated titles and summaries per session (cached, computed once)
CREATE TABLE IF NOT EXISTS session_titles (
    session_id   TEXT PRIMARY KEY,
    title        TEXT,              -- 6-8 word clean title
    summary      TEXT,              -- 1-2 sentence summary
    project_tags TEXT,              -- comma-separated detected project tags
    generated_at INTEGER,
    method       TEXT               -- 'heuristic' or 'ollama'
);

-- Sync state: track which files have been indexed and when
CREATE TABLE IF NOT EXISTS sync_state (
    file_path    TEXT PRIMARY KEY,
    last_size    INTEGER,
    last_mtime   REAL,
    indexed_at   INTEGER
);
