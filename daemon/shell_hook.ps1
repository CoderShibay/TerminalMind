# TerminalMind Shell Hook — PowerShell
# Logs every terminal command to shell_log.jsonl
#
# Setup: add this line to your PowerShell profile ($PROFILE):
#   . "$env:USERPROFILE\.terminalmd\daemon\shell_hook.ps1"
#
# To find your profile path:  echo $PROFILE
# To edit it:                 notepad $PROFILE
# To disable: remove the line above and open a new terminal.
#
# To verify it's working: run a command, then:
#   Get-Content "$env:USERPROFILE\.terminalmd\shell_log.jsonl" -Tail 1

# Resolve log path — hidden install (.terminalmd) or legacy (terminalmd)
if (Test-Path "$env:USERPROFILE\.terminalmd") {
    $script:_TmLog = "$env:USERPROFILE\.terminalmd\shell_log.jsonl"
} else {
    $script:_TmLog = "$env:USERPROFILE\terminalmd\shell_log.jsonl"
}

$script:_TmLastHistoryId = 0

# Save reference to original prompt so we can chain it
$script:_TmOriginalPrompt = $function:prompt

function global:prompt {
    # Capture exit state before anything else modifies it
    $lastExit = $LASTEXITCODE
    $lastSuccess = $?

    # Get the most recent history entry
    $history = Get-History -Count 1 -ErrorAction SilentlyContinue

    if ($history -and $history.Id -ne $script:_TmLastHistoryId) {
        $script:_TmLastHistoryId = $history.Id
        $cmd = $history.CommandLine

        # Skip tm commands to avoid recursive noise
        if ($cmd -notmatch '^tm(\s|$)') {
            $startMs  = [DateTimeOffset]::new($history.StartExecutionTime.ToUniversalTime(), [TimeSpan]::Zero).ToUnixTimeMilliseconds()
            $durMs    = [Math]::Round(($history.EndExecutionTime - $history.StartExecutionTime).TotalMilliseconds)
            $exitCode = if ($lastSuccess) { 0 } else { if ($lastExit) { $lastExit } else { 1 } }
            $cwd      = $PWD.Path
            $pidVal   = $PID

            # JSON-escape cmd and cwd
            $cmd = $cmd -replace '\\', '\\' -replace '"', '\"' -replace "`n", '\n' -replace "`r", '\r' -replace "`t", '\t'
            $cwd = $cwd -replace '\\', '\\' -replace '"', '\"'

            $line = "{`"ts`":$startMs,`"dur`":$durMs,`"exit`":$exitCode,`"cwd`":`"$cwd`",`"pid`":$pidVal,`"cmd`":`"$cmd`"}"

            try {
                Add-Content -Path $script:_TmLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
            } catch {}
        }
    }

    # Restore LASTEXITCODE so the prompt doesn't swallow it
    $global:LASTEXITCODE = $lastExit

    # Call original prompt or fall back to default
    if ($script:_TmOriginalPrompt) {
        & $script:_TmOriginalPrompt
    } else {
        "PS $($PWD.Path)> "
    }
}
