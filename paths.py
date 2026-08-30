"""Resolve Claude Code data directory across platforms.

Claude Code stores its data at:
  ~/.claude/          — macOS and Linux
  %APPDATA%\Claude\   — Windows (possible alternate location)

Use claude_dir() everywhere instead of hardcoding Path.home() / ".claude".
"""
import os
import platform
from pathlib import Path


def claude_dir() -> Path:
    """Return the Claude Code data directory, detecting platform differences."""
    # Standard path used on macOS and Linux
    standard = Path.home() / ".claude"
    if standard.exists():
        return standard

    # Windows: Claude Code may store data under %APPDATA%\Claude
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            win_path = Path(appdata) / "Claude"
            if win_path.exists():
                return win_path

    # Return standard path even if it doesn't exist yet —
    # installer / first run will create it
    return standard


def install_dir() -> Path:
    """Return the TerminalMind install directory."""
    # Hidden dir install (install.sh / install.ps1)
    hidden = Path.home() / ".terminalmd"
    if hidden.exists():
        return hidden
    # Legacy: cloned directly as ~/terminalmd
    legacy = Path.home() / "terminalmd"
    if legacy.exists():
        return legacy
    # Default to hidden dir (new installs)
    return hidden


def shell_log_path() -> Path:
    """Return the shell_log.jsonl path regardless of install location."""
    return install_dir() / "shell_log.jsonl"


def projects_config_path() -> Path:
    """Return the path to the ~/.tm_projects config file."""
    return Path.home() / ".tm_projects"


def load_project_paths() -> dict[str, Path]:
    """Read ~/.tm_projects and return {name: path} mapping.

    File format (one per line, # for comments):
        SpotTrader = /Users/alice/Projects/SpotTrader
        PKB = D:\\Projects\\PKB

    Names are matched case-insensitively. Paths may use ~ for home dir.
    """
    config = projects_config_path()
    if not config.exists():
        return {}

    result: dict[str, Path] = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, raw_path = line.partition("=")
        name = name.strip()
        raw_path = raw_path.strip()
        if name and raw_path:
            result[name.lower()] = Path(raw_path).expanduser()

    return result
