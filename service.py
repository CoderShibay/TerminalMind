"""Install/uninstall TerminalMind as a background service (macOS launchd / Linux systemd)."""
import os
import platform
import subprocess
import sys
from pathlib import Path


PLIST_LABEL = "com.terminalmd.server"
PLIST_PATH  = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
SYSTEMD_PATH = Path.home() / ".config" / "systemd" / "user" / "terminalmd.service"


def _python() -> str:
    return sys.executable


def _install_dir() -> str:
    return str(Path(__file__).parent)


def install_macos(port: int = 8888) -> None:
    python = _python()
    install_dir = _install_dir()
    log = "/tmp/terminalmd.log"

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>             <string>{PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{install_dir}/main.py</string>
        <string>serve</string>
        <string>{port}</string>
        <string>--no-browser</string>
    </array>
    <key>RunAtLoad</key>         <true/>
    <key>KeepAlive</key>         <true/>
    <key>StandardOutPath</key>   <string>{log}</string>
    <key>StandardErrorPath</key> <string>{log}</string>
    <key>ThrottleInterval</key>  <integer>10</integer>
</dict>
</plist>"""

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)

    # Unload if already loaded, then load fresh
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(f"launchctl load failed: {result.stderr}")


def install_linux(port: int = 8888) -> None:
    python = _python()
    install_dir = _install_dir()

    unit = f"""[Unit]
Description=TerminalMind — Claude session search dashboard
After=network.target

[Service]
Type=simple
ExecStart={python} {install_dir}/main.py serve {port} --no-browser
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""
    SYSTEMD_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_PATH.write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"])
    subprocess.run(["systemctl", "--user", "enable", "--now", "terminalmd"])


def uninstall() -> None:
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        PLIST_PATH.unlink(missing_ok=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "terminalmd"], capture_output=True)
        SYSTEMD_PATH.unlink(missing_ok=True)


def status() -> str:
    """Return 'running', 'stopped', or 'not installed'."""
    if platform.system() == "Darwin":
        if not PLIST_PATH.exists():
            return "not installed"
        r = subprocess.run(
            ["launchctl", "list", PLIST_LABEL],
            capture_output=True, text=True,
        )
        return "running" if r.returncode == 0 else "stopped"
    else:
        if not SYSTEMD_PATH.exists():
            return "not installed"
        r = subprocess.run(
            ["systemctl", "--user", "is-active", "terminalmd"],
            capture_output=True, text=True,
        )
        return "running" if r.stdout.strip() == "active" else "stopped"


def install(port: int = 8888) -> None:
    if platform.system() == "Darwin":
        install_macos(port)
    elif platform.system() == "Linux":
        install_linux(port)
    else:
        raise NotImplementedError(f"Auto-service not supported on {platform.system()}. Run `tm serve` manually.")
