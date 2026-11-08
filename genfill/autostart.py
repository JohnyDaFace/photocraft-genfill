"""Start with Windows: a shortcut in the user's Startup folder that starts the bar hidden
(`--background`); it shows itself when PhotoCraft opens. Windows only."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SUPPORTED = sys.platform == "win32"
NAME = "Generative Fill for PhotoCraft.lnk"
BACKGROUND = "--background"


def shortcut_path() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / \
        "Programs" / "Startup" / NAME


def command() -> tuple[str, str]:
    """(program, arguments) that start this app hidden."""
    if getattr(sys, "frozen", False):
        return sys.executable, BACKGROUND
    pythonw = Path(sys.prefix) / "Scripts" / "pythonw.exe"
    entry = Path(__file__).resolve().parents[1] / "GenFill.pyw"
    return str(pythonw if pythonw.exists() else sys.executable), f'"{entry}" {BACKGROUND}'


def enabled() -> bool:
    return SUPPORTED and shortcut_path().exists()


def enable() -> None:
    if not SUPPORTED:
        return
    program, args = command()
    link = shortcut_path()
    link.parent.mkdir(parents=True, exist_ok=True)

    def ps(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps(str(link))}); "
              f"$s.TargetPath = {ps(program)}; $s.Arguments = {ps(args)}; "
              f"$s.WorkingDirectory = {ps(str(Path(program).parent))}; "
              f"$s.Description = 'Generative Fill for PhotoCraft (shows when PhotoCraft opens)'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True,
                   capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def disable() -> None:
    try:
        shortcut_path().unlink()
    except FileNotFoundError:
        pass
