#!/usr/bin/env bash
# TerminalMind — one-command installer
# Usage:  bash install.sh
# Remote: bash <(curl -fsSL https://raw.githubusercontent.com/CoderShibay/terminalmd/main/install.sh)

set -e

INSTALL_DIR="$HOME/.terminalmd"
BIN_DIR="$HOME/.local/bin"
BIN_PATH="$BIN_DIR/tm"
REPO_URL="https://github.com/CoderShibay/terminalmd"
PORT=8888

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; DIM='\033[2m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC}  $1"; }
warn() { echo -e "  ${YELLOW}!${NC}  $1"; }
info() { echo -e "  ${DIM}→${NC}  $1"; }
err()  { echo -e "  ${RED}✗${NC}  $1"; exit 1; }
hr()   { echo "  ────────────────────────────────────────"; }

echo ""
echo -e "  ${BOLD}TerminalMind${NC}  —  Claude session search & dashboard"
hr
echo ""

# ── 1. Python ─────────────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  err "Python 3.10+ required. Install: brew install python3  (Mac) or  apt install python3  (Linux)"
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  err "Python $PY_VER found — need 3.10+. Upgrade: brew install python3"
fi
ok "Python $PY_VER"

# ── 2. Claude Code check ──────────────────────────────────────────────────────
if [ ! -f "$HOME/.claude/history.jsonl" ]; then
  warn "~/.claude/history.jsonl not found."
  echo "     TerminalMind indexes Claude Code history. Make sure Claude Code"
  echo "     has been installed and run at least once before using \`tm\`."
  echo ""
else
  PROMPT_COUNT=$(wc -l < "$HOME/.claude/history.jsonl" | tr -d ' ')
  ok "Claude Code data found  ($PROMPT_COUNT prompts)"
fi

# ── 3. Install files ──────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo ".")"

if [ -f "$SCRIPT_DIR/main.py" ] && [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
  info "Copying files to $INSTALL_DIR"
  rm -rf "$INSTALL_DIR"
  cp -r "$SCRIPT_DIR" "$INSTALL_DIR"
  ok "Files installed"
elif [ "$SCRIPT_DIR" = "$INSTALL_DIR" ]; then
  ok "Already in install directory"
elif command -v git &>/dev/null; then
  info "Cloning from GitHub…"
  rm -rf "$INSTALL_DIR"
  git clone --quiet "$REPO_URL" "$INSTALL_DIR" || err "Clone failed. Check your internet connection."
  ok "Cloned from GitHub"
else
  err "No source files found and git not available. Clone manually:\n  git clone $REPO_URL $INSTALL_DIR"
fi

# ── 4. Python dependencies ────────────────────────────────────────────────────
info "Installing Python dependencies…"
python3 -m pip install --quiet fastapi uvicorn 2>/dev/null \
  || python3 -m pip install fastapi uvicorn \
  || err "pip install failed. Try: pip3 install fastapi uvicorn"
ok "fastapi + uvicorn installed"

# ── 5. tm command ─────────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_PATH" << EOF
#!/usr/bin/env bash
exec python3 "$INSTALL_DIR/main.py" "\$@"
EOF
chmod +x "$BIN_PATH"
ok "Created tm command  ($BIN_PATH)"

# PATH
SHELL_RC=""
[[ "$SHELL" == *zsh*  ]] && SHELL_RC="$HOME/.zshrc"
[[ "$SHELL" == *bash* ]] && SHELL_RC="$HOME/.bashrc"
NEEDS_PATH=false
echo "$PATH" | grep -q "$BIN_DIR" || NEEDS_PATH=true

if $NEEDS_PATH && [ -n "$SHELL_RC" ]; then
  if ! grep -q "$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
    { echo ""; echo "# TerminalMind"; echo "export PATH=\"$BIN_DIR:\$PATH\""; } >> "$SHELL_RC"
  fi
fi

# ── 6. Ollama check (optional) ────────────────────────────────────────────────
echo ""
if command -v ollama &>/dev/null; then
  if ollama list 2>/dev/null | grep -q "llama3.2"; then
    ok "Ollama ready  (llama3.2 found — AI-generated session titles enabled)"
  else
    warn "Ollama installed but llama3.2 not pulled."
    echo "     Run: \033[1mollama pull llama3.2\033[0m  for better session titles (optional)"
  fi
else
  echo -e "  ${DIM}○${NC}  Ollama not installed — heuristic titles will be used (works fine)"
  echo "     Optional: install from https://ollama.com then  ollama pull llama3.2"
fi

# ── 7. First sync ─────────────────────────────────────────────────────────────
echo ""
info "Indexing your Claude sessions…"
python3 "$INSTALL_DIR/main.py" sync
echo ""

# ── 8. Run verify ─────────────────────────────────────────────────────────────
python3 "$INSTALL_DIR/main.py" verify

# ── 9. Background service ─────────────────────────────────────────────────────
hr
echo ""
echo -e "  ${BOLD}Install background service?${NC}"
echo "  The service starts the TerminalMind server automatically on login."
echo "  Without it, you must run \`tm serve\` manually each time."
echo ""
read -r -p "  Install background service? [Y/n] " REPLY
REPLY="${REPLY:-Y}"
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
  python3 "$INSTALL_DIR/main.py" service install && true || {
    warn "Service install failed. You can run \`tm serve\` manually instead."
  }
else
  echo "  Skipped. Run \`tm serve\` to start the dashboard manually."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
hr
echo ""
echo -e "  ${BOLD}${GREEN}TerminalMind installed.${NC}"
echo ""
echo -e "  ${BOLD}tm serve${NC}          open dashboard in browser  →  http://localhost:$PORT"
echo -e "  ${BOLD}tm search \"...\"${NC}   search from terminal"
echo -e "  ${BOLD}tm verify${NC}         check everything is indexed"
echo -e "  ${BOLD}tm sync${NC}           pick up new sessions"
echo ""

if $NEEDS_PATH; then
  echo -e "  ${YELLOW}Restart your terminal${NC} (or run \`source $SHELL_RC\`) before using \`tm\`."
  echo ""
fi
