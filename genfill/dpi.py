"""Sharp on high-DPI screens. By default Windows draws a Tk app at 100% and stretches the picture to the
display scaling (150%, 200% …), which blurs every letter. `enable()` tells Windows the app scales itself;
every size in the UI is then written at 100% and passed through `px()`."""
from __future__ import annotations

import ctypes
import sys

SCALE = 1.0  # the screen's scaling (1.5 at 150%); stays 1 until enable() runs, e.g. in tests


def enable() -> float:
    """Call before the first Tk window. Returns the scale in use."""
    global SCALE
    if sys.platform != "win32":
        return SCALE
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system-DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7/8
        except (AttributeError, OSError):
            return SCALE
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()  # Windows 10+
    except (AttributeError, OSError):
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
    SCALE = max(1.0, dpi / 96.0)
    return SCALE


def px(n: float) -> int:
    """A size at 100% scaling -> pixels on this screen."""
    return int(round(n * SCALE))
