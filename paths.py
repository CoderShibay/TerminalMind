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
