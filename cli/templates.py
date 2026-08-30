"""Built-in vault templates for tm push init."""

HOME_TEMPLATE = """\
---
type: home
status: planning
tags: []
date_created: "{date}"
date_updated: "{date}"
priority: medium
---

# {project_name}

One sentence — what it does and why it exists.

---

## Locations

| What | Where |
|------|-------|
| Code | `` |
| Vault | `{vault_path}` |
| GitHub | `` |

---

## Stack

- **Language / Runtime** —
- **Key libraries** —
- **Data storage** —
- **Platform** —

---

## Goals

| Metric | Target |
|--------|--------|
| | |

---

## Current Status

**Phase 0 — Planning** — Not started yet.

| Component | Status |
|-----------|--------|
| | 🔲 Not started |

---

## Blockers

| Priority | Issue | When to Fix |
|----------|-------|-------------|
| | | |

---

## Key Decisions

Settled decisions — do not reopen unless explicitly revisited.

| Decision | Reason |
|----------|--------|
| | |

---

## Session Start Checklist

1. Read this file
2. Read [[Build Log]] — last entry tells you exactly where to pick up
3. Check blockers table above

---

## Vault Contents

| File | What's in it |
|------|-------------|
| [[Build Log]] | One entry per session — what was built, decisions, errors, next |

---

## Next Steps

1.
2.
3.
"""

BUILD_LOG_TEMPLATE = """\
---
type: build-log
tags: [log, build, sessions]
---

# {project_name} — Build Log

One entry per session. Newest first.
Use `tm push SESSION_ID {project_name}` to fill entries automatically.

---

## {date} — Project setup

**Built:**
- Vault created

**Decisions:**
-

**Errors fixed:**
- None

**Commits:**
- None

**Next:**
-
"""
