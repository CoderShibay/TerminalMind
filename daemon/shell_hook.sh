#!/usr/bin/env zsh
# TerminalMind Shell Hook
# Logs every terminal command to ~/terminalmd/shell_log.jsonl
#
# Setup (add ONE of these to ~/.zshrc, then `source ~/.zshrc`):
#   source ~/terminalmd/daemon/shell_hook.sh
#
# To disable: remove that line and open a new terminal.
# To verify it's working: run a command, then check:
#   tail -1 ~/terminalmd/shell_log.jsonl

_TM_LOG="$HOME/terminalmd/shell_log.jsonl"
_tm_start=0
_tm_last_cmd=""

# Called by zsh immediately before each command runs.
# $1 = the command string as typed.
_tm_preexec() {
    _tm_start=$EPOCHREALTIME
    _tm_last_cmd="$1"
}

# Called by zsh before each prompt (i.e., after the previous command finished).
# $? is the exit code of the just-completed command.
_tm_precmd() {
    local _exit=$?

    # Nothing to log if no command was captured
    [[ -z "$_tm_last_cmd" ]] && return $_exit
    [[ "$_tm_start" == "0" ]] && return $_exit

    # Skip logging tm commands themselves (avoids recursive noise)
    [[ "$_tm_last_cmd" == tm* ]] && { _tm_last_cmd=""; _tm_start=0; return $_exit; }

    # Compute ms timestamps using pure zsh float arithmetic (no subprocesses)
    local ts_ms dur_ms
    ts_ms=$(printf "%.0f" "$(( _tm_start * 1000 ))")
    dur_ms=$(printf "%.0f" "$(( ($EPOCHREALTIME - _tm_start) * 1000 ))")

    local cmd="$_tm_last_cmd"
    local cwd="$PWD"
    local pid="$$"

    # Reset before writing so a slow write doesn't affect next command timing
    _tm_last_cmd=""
    _tm_start=0

    # JSON-escape: backslash first, then double-quote, then control chars
    cmd="${cmd//\\/\\\\}"
    cmd="${cmd//\"/\\\"}"
    cmd="${cmd//$'\n'/\\n}"
    cmd="${cmd//$'\r'/\\r}"
    cmd="${cmd//$'\t'/\\t}"
    cwd="${cwd//\\/\\\\}"
    cwd="${cwd//\"/\\\"}"

    printf '{"ts":%s,"dur":%s,"exit":%d,"cwd":"%s","pid":%s,"cmd":"%s"}\n' \
        "$ts_ms" "$dur_ms" "$_exit" "$cwd" "$pid" "$cmd" \
        >> "$_TM_LOG" 2>/dev/null

    return $_exit
}

# Register with zsh's hook system
autoload -Uz add-zsh-hook 2>/dev/null
add-zsh-hook preexec _tm_preexec
add-zsh-hook precmd  _tm_precmd
