"""One-time setup: per-user environment variables that make PhotoCraft open its control channel
however it is launched. Reversible with uninstall().

  Windows  HKCU\\Environment (what Explorer, the ArtCraft Launcher and new programs inherit)
  macOS    `launchctl setenv` now, plus a LaunchAgent that sets them again at every login
  Linux    ~/.config/environment.d/ (systemd user environment; applies after logging in again)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ENV_KEY = r"Environment"
AGENT_LABEL = "com.photocraftgenfill.env"
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"
LINUX_ENV_FILE = Path.home() / ".config" / "environment.d" / "photocraft-genfill.conf"


def _app_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PhotoCraftGenFill"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PhotoCraftGenFill"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "photocraft-genfill"


APP_DIR = _app_dir()

RESTART_HINT = {
    "win32": "Now save your work, fully quit PhotoCraft and the ArtCraft Launcher (also from the system "
             "tray), and start them again. This is needed only once.",
    "darwin": "Now save your work, quit PhotoCraft completely (PhotoCraft › Quit, or Cmd+Q) and open it "
              "again. This is needed only once.",
}.get(sys.platform, "Now log out and back in, then open PhotoCraft. If your compositor does not use the systemd "
                   "user environment, add the variables from ~/.config/environment.d/photocraft-genfill.conf "
                   "to PhotoCraft's launcher instead.")


def wanted(port: int = 7878, app_dir: Path = APP_DIR) -> dict[str, str]:
    exchange = app_dir / "exchange"
    return {
        "PHOTOCRAFT_CONTROL_PORT": str(port),
        "PHOTOCRAFT_CONTROL_TOKEN_FILE": str(app_dir / "control.token"),
        "PHOTOCRAFT_AUTOMATION_READ_ROOT": str(exchange),
        "PHOTOCRAFT_AUTOMATION_WRITE_ROOT": str(exchange),
    }


def status(port: int = 7878, app_dir: Path = APP_DIR, **kw) -> dict[str, bool]:
    """For each variable: is it set to the wanted value for this user?"""
    if sys.platform == "win32":
        return _win_status(port, app_dir, **kw)
    if sys.platform == "darwin":
        return _mac_status(port, app_dir, **kw)
    return _linux_status(port, app_dir, **kw)


def install(port: int = 7878, app_dir: Path = APP_DIR, **kw) -> None:
    (app_dir / "exchange").mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        _win_install(port, app_dir, **kw)
    elif sys.platform == "darwin":
        _mac_install(port, app_dir, **kw)
    else:
        _linux_install(port, app_dir, **kw)


def uninstall(**kw) -> None:
    if sys.platform == "win32":
        _win_uninstall(**kw)
    elif sys.platform == "darwin":
        _mac_uninstall(**kw)
    else:
        _linux_uninstall(**kw)


# -- Windows ---------------------------------------------------------------------------------------
def _win_status(port, app_dir, key: str = ENV_KEY) -> dict[str, bool]:
    import winreg
    out = {}
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as k:
        for name, value in wanted(port, app_dir).items():
            try:
                out[name] = winreg.QueryValueEx(k, name)[0] == value
            except FileNotFoundError:
                out[name] = False
    return out


def _win_install(port, app_dir, key: str = ENV_KEY) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        for name, value in wanted(port, app_dir).items():
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    _broadcast()


def _win_uninstall(key: str = ENV_KEY) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        for name in wanted():
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass
    _broadcast()


def _broadcast() -> None:
    """Tell Explorer (and so newly started programs) that the environment changed."""
    import ctypes
    HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG = 0xFFFF, 0x001A, 0x0002
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                                             SMTO_ABORTIFHUNG, 3000, ctypes.byref(result))


# -- macOS -----------------------------------------------------------------------------------------
def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout.strip()


def mac_agent_plist(env: dict[str, str]) -> str:
    """A LaunchAgent that runs `launchctl setenv` for each variable at login."""
    from xml.sax.saxutils import escape
    script = "; ".join(f"/bin/launchctl setenv {n} '{v}'" for n, v in env.items())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string>-c</string><string>{escape(script)}</string></array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
"""


def _mac_status(port, app_dir, run=_run, agent: Path = MAC_AGENT) -> dict[str, bool]:
    return {n: agent.is_file() and run(["launchctl", "getenv", n]) == v for n, v in wanted(port, app_dir).items()}


def _mac_install(port, app_dir, run=_run, agent: Path = MAC_AGENT) -> None:
    env = wanted(port, app_dir)
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(mac_agent_plist(env), encoding="utf-8")
    for n, v in env.items():
        run(["launchctl", "setenv", n, v])  # for apps started from now on, without logging out


def _mac_uninstall(run=_run, agent: Path = MAC_AGENT) -> None:
    for n in wanted():
        run(["launchctl", "unsetenv", n])
    agent.unlink(missing_ok=True)


# -- Linux -----------------------------------------------------------------------------------------
def _linux_status(port, app_dir, env_file: Path = LINUX_ENV_FILE) -> dict[str, bool]:
    try:
        lines = dict(l.split("=", 1) for l in env_file.read_text(encoding="utf-8").splitlines() if "=" in l)
    except OSError:
        lines = {}
    return {n: lines.get(n) == v for n, v in wanted(port, app_dir).items()}


def _linux_install(port, app_dir, env_file: Path = LINUX_ENV_FILE) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("".join(f"{n}={v}\n" for n, v in wanted(port, app_dir).items()), encoding="utf-8")


def _linux_uninstall(env_file: Path = LINUX_ENV_FILE) -> None:
    env_file.unlink(missing_ok=True)
