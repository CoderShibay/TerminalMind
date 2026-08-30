# TerminalMind — Windows installer
# Usage:  powershell -ExecutionPolicy Bypass -File install.ps1
# Remote: iwr https://raw.githubusercontent.com/CoderShibay/terminalmd/main/install.ps1 | iex
#
# Requirements: Python 3.10+, Git (optional — falls back to zip download)

$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\.terminalmd"
$BinDir     = "$env:USERPROFILE\.local\bin"
$BinPath    = "$BinDir\tm.cmd"
$RepoUrl    = "https://github.com/CoderShibay/terminalmd"
$ZipUrl     = "https://github.com/CoderShibay/terminalmd/archive/refs/heads/main.zip"
$Port       = 8888

# ── Helpers ───────────────────────────────────────────────────────────────────
function Ok($msg)   { Write-Host "  " -NoNewline; Write-Host "[check]" -ForegroundColor Green -NoNewline; Write-Host "  $msg" }
function Warn($msg) { Write-Host "  " -NoNewline; Write-Host "[!]    " -ForegroundColor Yellow -NoNewline; Write-Host "  $msg" }
function Info($msg) { Write-Host "  " -NoNewline; Write-Host "[->]   " -ForegroundColor DarkGray -NoNewline; Write-Host "  $msg" }
function Err($msg)  { Write-Host "  " -NoNewline; Write-Host "[X]    " -ForegroundColor Red -NoNewline; Write-Host "  $msg"; exit 1 }
function Hr()       { Write-Host "  ────────────────────────────────────────" }

Write-Host ""
Write-Host "  TerminalMind  —  Claude session search & dashboard" -ForegroundColor White
Hr
Write-Host ""

# ── 1. Python ─────────────────────────────────────────────────────────────────
# Windows may have 'python' or 'python3' — try both
$PythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($ver -match "^\d+\.\d+$") { $PythonCmd = $cmd; $PyVer = $ver; break }
    } catch {}
}
if (-not $PythonCmd) {
    Err "Python 3.10+ required. Download from https://www.python.org/downloads/"
}
$parts = $PyVer.Split(".")
$pyMajor = [int]$parts[0]; $pyMinor = [int]$parts[1]
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 10)) {
    Err "Python $PyVer found — need 3.10+. Download from https://www.python.org/downloads/"
}
Ok "Python $PyVer  ($PythonCmd)"

# Warn if Python is too new for sentence-transformers/torch wheels
if ($pyMajor -eq 3 -and $pyMinor -ge 13) {
    Warn "Python $PyVer detected — sentence-transformers/torch may not have wheels for 3.13+ yet."
    Write-Host "     Semantic search will be disabled if install fails. Keyword search still works."
    Write-Host "     For full features, install Python 3.10-3.12 from https://www.python.org/downloads/"
    Write-Host ""
}

# ── 2. Claude Code check ──────────────────────────────────────────────────────
$ClaudeDir = $null
$ClaudePaths = @(
    "$env:USERPROFILE\.claude",
    "$env:APPDATA\Claude"
)
foreach ($p in $ClaudePaths) {
    if (Test-Path "$p\history.jsonl") { $ClaudeDir = $p; break }
}

if ($ClaudeDir) {
    $PromptCount = (Get-Content "$ClaudeDir\history.jsonl" -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
    Ok "Claude Code data found  ($PromptCount prompts)  at $ClaudeDir"
} else {
    Warn "Claude Code history not found. Make sure Claude Code is installed and has been run at least once."
    Write-Host "     Checked: $($ClaudePaths -join ', ')"
    Write-Host ""
}

# ── 3. Install files ──────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (Test-Path "$ScriptDir\main.py") {
    # Running from a local clone
    if ($ScriptDir -ne $InstallDir) {
        Info "Copying files to $InstallDir"
        if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
        Copy-Item $ScriptDir $InstallDir -Recurse
        Ok "Files installed"
    } else {
        Ok "Already in install directory"
    }
} elseif (Get-Command git -ErrorAction SilentlyContinue) {
    Info "Cloning from GitHub..."
    if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
    git clone --quiet $RepoUrl $InstallDir
    if ($LASTEXITCODE -ne 0) { Err "git clone failed. Check your internet connection." }
    Ok "Cloned from GitHub"
} else {
    # No git — download zip
    Info "Downloading from GitHub (no git found)..."
    $ZipPath = "$env:TEMP\terminalmd.zip"
    $ExtractPath = "$env:TEMP\terminalmd-extract"
    try {
        Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
        if (Test-Path $ExtractPath) { Remove-Item $ExtractPath -Recurse -Force }
        Expand-Archive -Path $ZipPath -DestinationPath $ExtractPath
        $extracted = Get-ChildItem $ExtractPath | Select-Object -First 1
        if (Test-Path $InstallDir) { Remove-Item $InstallDir -Recurse -Force }
        Move-Item $extracted.FullName $InstallDir
        Remove-Item $ZipPath -Force
        Ok "Downloaded and extracted"
    } catch {
        Err "Download failed: $_`nInstall git for Windows from https://git-scm.com or download manually from $RepoUrl"
    }
}

# ── 4. Python dependencies ────────────────────────────────────────────────────
Info "Installing Python dependencies..."
try {
    & $PythonCmd -m pip install --quiet fastapi uvicorn
    Ok "fastapi + uvicorn installed"
} catch {
    Err "pip install failed: $_`nTry running:  $PythonCmd -m pip install fastapi uvicorn"
}

# ── 5. tm.cmd shim ────────────────────────────────────────────────────────────
if (-not (Test-Path $BinDir)) { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null }

$PythonExe = (Get-Command $PythonCmd).Source
@"
@echo off
"$PythonExe" "$InstallDir\main.py" %*
"@ | Set-Content -Path $BinPath -Encoding ASCII
Ok "Created tm command  ($BinPath)"

# ── 6. Add to PATH ────────────────────────────────────────────────────────────
$UserPath = [System.Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath -notlike "*$BinDir*") {
    [System.Environment]::SetEnvironmentVariable("PATH", "$BinDir;$UserPath", "User")
    $env:PATH = "$BinDir;$env:PATH"
    Ok "Added $BinDir to user PATH"
} else {
    Ok "$BinDir already in PATH"
}

# ── 7. Ollama check (optional) ────────────────────────────────────────────────
Write-Host ""
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    $ollamaList = ollama list 2>$null
    if ($ollamaList -match "llama3.2") {
        Ok "Ollama ready  (llama3.2 found — AI session titles enabled)"
    } else {
        Warn "Ollama installed but llama3.2 not pulled."
        Write-Host "     Run:  ollama pull llama3.2  for better session titles (optional)"
    }
} else {
    Write-Host "  [o]    Ollama not installed — heuristic titles will be used (works fine)" -ForegroundColor DarkGray
    Write-Host "         Optional: https://ollama.com then  ollama pull llama3.2"
}

# ── 8. First sync ─────────────────────────────────────────────────────────────
Write-Host ""
Info "Indexing your Claude sessions..."
& $PythonCmd "$InstallDir\main.py" sync
Write-Host ""

# ── 9. Verify ─────────────────────────────────────────────────────────────────
& $PythonCmd "$InstallDir\main.py" verify

# ── 10. Background service (Task Scheduler) ───────────────────────────────────
Hr
Write-Host ""
Write-Host "  Install background service?" -ForegroundColor White
Write-Host "  Starts the TerminalMind server automatically when you log in."
Write-Host "  Without it, run 'tm serve' manually each time."
Write-Host ""
$reply = Read-Host "  Install background service? [Y/n]"
if ($reply -eq "" -or $reply -match "^[Yy]") {
    try {
        $taskAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$InstallDir\main.py`" serve $Port --no-browser"
        $taskTrigger = New-ScheduledTaskTrigger -AtLogOn
        $taskSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName "TerminalMind" -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Force | Out-Null
        # Start it now too
        Start-ScheduledTask -TaskName "TerminalMind" -ErrorAction SilentlyContinue
        Ok "Background service installed (Task Scheduler: 'TerminalMind')"
        Ok "Dashboard running at http://localhost:$Port"
    } catch {
        Warn "Service install failed: $_"
        Write-Host "     Run 'tm serve' manually to start the dashboard."
    }
} else {
    Write-Host "  Skipped. Run 'tm serve' to start the dashboard."
}

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Host ""
Hr
Write-Host ""
Write-Host "  TerminalMind installed." -ForegroundColor Green
Write-Host ""
Write-Host "  tm serve           open dashboard  ->  http://localhost:$Port"
Write-Host "  tm context `"...`"   search from terminal"
Write-Host "  tm verify          check everything is indexed"
Write-Host "  tm sync            pick up new sessions"
Write-Host ""
Write-Host "  Restart your terminal (or open a new PowerShell window) before using 'tm'."
Write-Host ""
