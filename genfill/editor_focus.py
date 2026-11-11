"""Remember the last editor window the user activated before clicking the bar."""
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path


def classify_editor(process_name: str, title: str = "") -> str | None:
    name = process_name.lower()
    if name == "photoshop.exe" or name.startswith("adobe photoshop"):
        return "photoshop"
    if name in ("photocraft.exe", "photocraft"):
        return "photocraft"
    if name in ("artcraft.exe", "artcraft") and "launcher" not in title.lower():
        return "photocraft"
    return None


def focused_editor() -> str | None:
    if sys.platform == "win32":
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
                                                       ctypes.POINTER(ctypes.c_ulong)]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            path = ctypes.create_unicode_buffer(32768)
            length = ctypes.c_ulong(len(path))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                return None
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            return classify_editor(Path(path.value).name, title.value)
        finally:
            kernel32.CloseHandle(handle)
    if sys.platform == "darwin":
        try:
            name = subprocess.check_output(["osascript", "-e", 'tell application "System Events" to get '
                                            'name of first application process whose frontmost is true'],
                                           text=True, timeout=2).strip()
            return classify_editor(name)
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError):
            return None
    return None
