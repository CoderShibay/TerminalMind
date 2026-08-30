"""Cross-platform clipboard helper."""
import platform
import shutil
import subprocess


def copy(text: str) -> bool:
    """Copy text to clipboard. Returns True on success, False if unavailable."""
    data = text.encode("utf-8")
    system = platform.system()

    try:
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=data, check=True)
            return True

        if system == "Windows":
            # clip.exe is built into every Windows install
            subprocess.run(["clip.exe"], input=data, check=True)
            return True

        # Linux — try xclip, then xsel, then wl-copy (Wayland)
        for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"], ["wl-copy"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, input=data, check=True)
                return True

    except Exception:
        pass

    return False
