"""Smooth widgets for the contextual bar: line icons, rounded buttons, themed pop-up menus and tooltips.
Shapes are drawn with Pillow at 4x and scaled down (Tk's own canvas drawing has jagged edges)."""
from __future__ import annotations

import ctypes
import math
import os
import socket
import sys
import threading
import tkinter as tk
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from . import svgicon
from .dpi import px
from .theme import Palette

SS = 4  # supersampling factor


def _rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), alpha


# -- icons ------------------------------------------------------------------------------------------
# Each icon is drawn on a 24-unit grid (like the usual line-icon sets) with ~1.7 unit strokes.

def _star(cx, cy, r, inner):
    pts = []
    for i in range(8):
        a = math.pi / 4 * i - math.pi / 2
        rr = r if i % 2 == 0 else r * inner
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


ICONS: dict[str, list[tuple]] = {
    "spark": [("poly", _star(11, 11, 8, 0.32)), ("poly", _star(19, 19, 3.2, 0.36))],
    "eraser": [("line", [(7, 20.5), (18, 20.5)]),
               ("poly", [(4.5, 14.5), (12.5, 6.5), (18.5, 12.5), (12, 19), (9, 19)]),
               ("line", [(8.5, 10.5), (14.5, 16.5)])],
    "image": [("rrect", (3, 4.5, 21, 19.5, 2.5)), ("circle", (9, 10, 1.8)), ("line", [(20.5, 15.5), (16, 11), (7.5, 19.5)])],
    "presets": [("line", [(4, 6), (13, 6)]), ("line", [(19, 6), (20, 6)]), ("circle", (16, 6, 2.2)),
                ("line", [(4, 12), (7, 12)]), ("line", [(13, 12), (20, 12)]), ("circle", (10, 12, 2.2)),
                ("line", [(4, 18), (15, 18)]), ("line", [(21, 18), (20.5, 18)]), ("circle", (18, 18, 2.2))],
    "expand": [("line", [(9, 4), (4, 4), (4, 9)]), ("line", [(15, 4), (20, 4), (20, 9)]),
               ("line", [(9, 20), (4, 20), (4, 15)]), ("line", [(15, 20), (20, 20), (20, 15)]),
               ("rrect", (8.5, 8.5, 15.5, 15.5, 1))],
    "upscale": [("circle", (10.5, 10.5, 6.2)), ("line", [(15.2, 15.2), (20, 20)]),
                ("line", [(10.5, 8), (10.5, 13)]), ("line", [(8, 10.5), (13, 10.5)])],
    "more": [("dot", (5, 12, 1.5)), ("dot", (12, 12, 1.5)), ("dot", (19, 12, 1.5))],
    "minimize": [("line", [(5, 17), (19, 17)])],
    "chev": [("line", [(7, 10), (12, 15), (17, 10)])],
    "x": [("line", [(6.5, 6.5), (17.5, 17.5)]), ("line", [(17.5, 6.5), (6.5, 17.5)])],
    "stack": [("rrect", (8, 8, 20, 20, 2)), ("line", [(4.5, 15.5), (4.5, 6.5), (6.5, 4.5), (15.5, 4.5)])],
    "history": [("arc", (12, 12, 8.5, 210, 520)), ("line", [(3.2, 4.5), (3.2, 9), (7.7, 9)]),
                ("line", [(12, 7.5), (12, 12), (15, 14)])],
    "gear": [("circle", (12, 12, 3)), ("poly", [
        (12 + r * math.cos(a), 12 + r * math.sin(a))
        for i in range(8) for r, a in ((6.4, math.pi / 4 * i - 0.36), (9, math.pi / 4 * i - 0.2),
                                       (9, math.pi / 4 * i + 0.2), (6.4, math.pi / 4 * i + 0.36))])],
    "plug": [("line", [(9, 3), (9, 8)]), ("line", [(15, 3), (15, 8)]),
             ("poly", [(6, 8), (18, 8), (18, 11), (16.5, 14.5), (12, 16.5), (7.5, 14.5), (6, 11)]),
             ("line", [(12, 16.5), (12, 21)])],
    "check": [("line", [(5, 12.5), (10, 17.5), (19, 7)])],
    "target": [("circle", (12, 12, 7)), ("dot", (12, 12, 1.8)), ("line", [(12, 2), (12, 5)]),
               ("line", [(12, 19), (12, 22)]), ("line", [(2, 12), (5, 12)]), ("line", [(19, 12), (22, 12)])],
    "create": [("rrect", (3, 4.5, 21, 19.5, 2.5)), ("line", [(12, 8.5), (12, 15.5)]), ("line", [(8.5, 12), (15.5, 12)])],
    "palette": [("poly", [(12 + 8.5 * math.cos(a / 16 * 2 * math.pi), 12 + 8.5 * math.sin(a / 16 * 2 * math.pi))
                          for a in range(16)]),
                ("dot", (8, 9.5, 1.4)), ("dot", (12, 7.5, 1.4)), ("dot", (16, 9.5, 1.4)), ("dot", (15.5, 14.5, 1.6))],
    "folder": [("poly", [(3, 6.5), (9, 6.5), (11, 8.5), (21, 8.5), (21, 18.5), (3, 18.5)])],
    "coffee": [("poly", [(4, 10), (16, 10), (15, 18.5), (13, 20.5), (7, 20.5), (5, 18.5)]),
               ("arc", (16.5, 13.5, 2.8, 270, 450)),
               ("line", [(8, 3.5), (8, 6.5)]), ("line", [(12, 3.5), (12, 6.5)])],
    "wand": [("line", [(4, 20), (15, 9)]), ("poly", _star(17.5, 6.5, 3.6, 0.4)),
             ("line", [(8, 4), (8, 6)]), ("line", [(20, 13), (20, 15)])],
    "swap": [("line", [(4, 8.5), (19, 8.5)]), ("line", [(15.5, 5), (19, 8.5), (15.5, 12)]),
             ("line", [(20, 15.5), (5, 15.5)]), ("line", [(8.5, 12), (5, 15.5), (8.5, 19)])],
    "camera": [("rrect", (3, 7, 21, 19.5, 2.5)), ("circle", (12, 13.2, 3.6)),
               ("line", [(8.5, 7), (10, 4.5), (14, 4.5), (15.5, 7)])],
}


ICON_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "icons"
LUCIDE = {  # our icon name -> its Lucide file in assets/icons (drawn ones below are the fallback)
    "spark": "sparkles", "eraser": "eraser", "image": "image", "presets": "sliders-horizontal",
    "expand": "scaling", "upscale": "maximize-2", "more": "ellipsis", "chev": "chevron-down", "x": "x",
    "stack": "copy", "history": "history", "gear": "settings", "plug": "plug", "check": "check",
    "target": "lasso-select", "create": "image-plus", "palette": "palette", "folder": "folder-open",
    "coffee": "coffee", "wand": "wand-sparkles", "swap": "arrow-left-right", "camera": "camera",
}

SOLID_BAR_ICONS = {"target", "eraser", "wand", "expand", "create", "swap", "camera",
                   "palette", "image", "stack", "spark"}


@lru_cache(maxsize=256)
def solid_bar_icon_image(name: str, size: int, color: str) -> Image.Image:
    """Filled 24-unit glyphs for the floating bar, drawn at 4× for crisp small icons."""
    if name not in SOLID_BAR_ICONS:
        raise ValueError(f"No filled bar icon: {name}")
    scale = size * SS / 24
    mask = Image.new("L", (size * SS, size * SS), 0)
    d = ImageDraw.Draw(mask)

    def box(coords):
        return tuple(round(v * scale) for v in coords)

    def polygon(points, fill=255):
        d.polygon([(round(x * scale), round(y * scale)) for x, y in points], fill=fill)

    def rect(coords, fill=255, radius=0):
        d.rounded_rectangle(box(coords), radius=round(radius * scale), fill=fill)

    def ellipse(coords, fill=255):
        d.ellipse(box(coords), fill=fill)

    if name == "target":
        ellipse((3, 3, 21, 21))
        ellipse((7, 7, 17, 17), 0)
        ellipse((10, 10, 14, 14))
        rect((11, 1, 13, 6))
        rect((11, 18, 13, 23))
        rect((1, 11, 6, 13))
        rect((18, 11, 23, 13))
    elif name == "eraser":
        polygon([(3, 14), (12, 4), (21, 13), (14, 20), (9, 20)])
        polygon([(4, 14), (9, 19), (13, 19), (7, 12)], 0)
        rect((14, 20, 22, 22), radius=1)
    elif name == "wand":
        polygon([(3, 18), (13, 8), (16, 11), (6, 21)])
        polygon([(17, 2), (18.5, 5.5), (22, 7), (18.5, 8.5), (17, 12), (15.5, 8.5), (12, 7),
                 (15.5, 5.5)])
        polygon([(7, 2), (7.7, 3.7), (9.5, 4.5), (7.7, 5.3), (7, 7), (6.3, 5.3), (4.5, 4.5), (6.3, 3.7)])
        polygon([(20, 14), (20.7, 15.3), (22, 16), (20.7, 16.7), (20, 18), (19.3, 16.7), (18, 16),
                 (19.3, 15.3)])
    elif name == "expand":
        rect((3, 3, 21, 21), radius=2)
        rect((6, 6, 18, 18), 0, radius=1)
        rect((9, 9, 15, 15), radius=1)
        polygon([(3, 3), (9, 3), (3, 9)])
        polygon([(21, 3), (15, 3), (21, 9)])
        polygon([(3, 21), (9, 21), (3, 15)])
        polygon([(21, 21), (15, 21), (21, 15)])
    elif name == "create":
        rect((2, 4, 22, 20), radius=2)
        polygon([(5, 17), (10, 11), (13, 14), (16, 10), (20, 17)], 0)
        ellipse((5, 7, 8, 10), 0)
        ellipse((14, 2, 23, 11))
        rect((17.5, 3.5, 19.5, 9.5), 0)
        rect((15.5, 5.5, 21.5, 7.5), 0)
    elif name == "swap":
        polygon([(3, 6), (16, 6), (16, 3), (22, 9), (16, 15), (16, 12), (3, 12)])
        polygon([(21, 18), (8, 18), (8, 21), (2, 15), (8, 9), (8, 12), (21, 12)])
    elif name == "camera":
        rect((2, 7, 22, 20), radius=2)
        polygon([(7, 8), (9, 4), (15, 4), (17, 8)])
        ellipse((8, 9, 16, 17), 0)
        ellipse((10, 11, 14, 15))
    elif name == "palette":
        ellipse((2, 2, 22, 22))
        ellipse((13, 12, 22, 21), 0)
        for x, y in ((7, 7), (12, 5), (17, 8), (7, 14)):
            ellipse((x - 1.25, y - 1.25, x + 1.25, y + 1.25), 0)
    elif name == "image":
        rect((2, 3, 22, 21), radius=2)
        ellipse((6, 6, 10, 10), 0)
        polygon([(4, 18), (10, 11), (13, 14), (16, 10), (20, 17), (20, 19), (4, 19)], 0)
    elif name == "stack":
        rect((3, 3, 18, 18), radius=2)
        rect((6, 6, 21, 21), radius=2)
        rect((9, 9, 18, 18), 0, radius=1)
    elif name == "spark":
        polygon([(10, 1), (12.5, 7.5), (19, 10), (12.5, 12.5), (10, 19), (7.5, 12.5), (1, 10),
                 (7.5, 7.5)])
        polygon([(19, 14), (20, 17), (23, 18), (20, 19), (19, 22), (18, 19), (15, 18), (18, 17)])

    mask = mask.resize((size, size), Image.LANCZOS)
    rgba = _rgba(color)
    im = Image.new("RGBA", (size, size), rgba[:3] + (0,))
    im.putalpha(mask)
    return im


@lru_cache(maxsize=512)
def icon_image(name: str, size: int, color: str) -> Image.Image:
    """An RGBA icon `size` px square in `color` (shared: don't draw on it)."""
    svg = ICON_DIR / f"{LUCIDE.get(name, name)}.svg"
    if svg.exists():
        try:
            return svgicon.render(svg, size, _rgba(color), stroke=1.75)
        except (ValueError, OSError, ET.ParseError):
            pass  # a file it can't read: the drawn icon instead
    S = size * SS
    k = S / 24.0
    w = max(1, round(1.7 * k))
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    col = _rgba(color)

    def cap(x, y):
        d.ellipse((x * k - w / 2, y * k - w / 2, x * k + w / 2, y * k + w / 2), fill=col)

    for kind, data in ICONS[name]:
        if kind == "line":
            pts = [(x * k, y * k) for x, y in data]
            d.line(pts, fill=col, width=w, joint="curve")
            for x, y in (data[0], data[-1]):
                cap(x, y)
        elif kind == "poly":
            pts = [(x * k, y * k) for x, y in data]
            d.line(pts + [pts[0], pts[1]], fill=col, width=w, joint="curve")
        elif kind == "circle":
            x, y, r = data
            d.ellipse(((x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k), outline=col, width=w)
        elif kind == "dot":
            x, y, r = data
            d.ellipse(((x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k), fill=col)
        elif kind == "rrect":
            x0, y0, x1, y1, r = data
            d.rounded_rectangle((x0 * k, y0 * k, x1 * k, y1 * k), radius=r * k, outline=col, width=w)
        elif kind == "arc":
            x, y, r, a0, a1 = data
            d.arc(((x - r) * k, (y - r) * k, (x + r) * k, (y + r) * k), a0, a1, fill=col, width=w)
    return im.resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=256)
def shape_image(w: int, h: int, radius: float, fill: str | None, border: str | None) -> Image.Image:
    """A smooth rounded rectangle (radius = h/2 for a pill) with an optional 1 px border."""
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    box = (SS * 0.5, SS * 0.5, w * SS - SS * 0.5 - 1, h * SS - SS * 0.5 - 1)
    d.rounded_rectangle(box, radius=radius * SS, fill=_rgba(fill) if fill else None,
                        outline=_rgba(border) if border else None, width=SS if border else 0)
    return im.resize((w, h), Image.LANCZOS)


# -- Windows window helpers -------------------------------------------------------------------------

def _hwnd(win: tk.Misc) -> int:
    win.update_idletasks()
    return ctypes.windll.user32.GetParent(win.winfo_id())


def round_corners(win: tk.Misc, small: bool = False) -> None:
    """Windows 11: rounded corners for a borderless window (ignored elsewhere and on Windows 10)."""
    if sys.platform != "win32":
        return
    try:
        v = ctypes.c_int(3 if small else 2)  # DWMWA_WINDOW_CORNER_PREFERENCE: ROUNDSMALL / ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(_hwnd(win), 33, ctypes.byref(v), ctypes.sizeof(v))
    except (AttributeError, OSError):
        pass


def show_in_taskbar(win: tk.Tk) -> None:
    """A borderless (override-redirect) window normally has no taskbar button; give it one."""
    if sys.platform != "win32":
        return
    try:
        hwnd, u = _hwnd(win), ctypes.windll.user32
        style = u.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        u.SetWindowLongW(hwnd, -20, (style & ~0x80) | 0x40000)  # -WS_EX_TOOLWINDOW +WS_EX_APPWINDOW
        win.withdraw()
        win.after(10, win.deiconify)
    except (AttributeError, OSError):
        pass


def minimize(win: tk.Tk) -> None:
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.ShowWindow(_hwnd(win), 6)  # SW_MINIMIZE (iconify() refuses borderless)
            return
        except (AttributeError, OSError):
            pass
    win.iconify()


def screen_area(win: tk.Misc) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the whole desktop, all monitors on Windows."""
    if sys.platform == "win32":
        try:
            g = ctypes.windll.user32.GetSystemMetrics
            return g(76), g(77), g(78), g(79)
        except (AttributeError, OSError):
            pass
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def monitor_area(win: tk.Misc) -> tuple[int, int, int, int]:
    """(x, y, width, height) of the work area (no taskbar) of the monitor `win` is on."""
    if sys.platform == "win32":
        try:
            class RECT(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("size", ctypes.c_ulong), ("monitor", RECT), ("work", RECT), ("flags", ctypes.c_ulong)]

            u = ctypes.windll.user32
            u.MonitorFromWindow.restype = ctypes.c_void_p
            u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
            info = MONITORINFO()
            info.size = ctypes.sizeof(MONITORINFO)
            if u.GetMonitorInfoW(u.MonitorFromWindow(_hwnd(win), 2), ctypes.byref(info)):  # 2: nearest
                r = info.work
                return r.l, r.t, r.r - r.l, r.b - r.t
        except (AttributeError, OSError):
            pass
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


# -- buttons ----------------------------------------------------------------------------------------

class BarButton(tk.Canvas):
    """Icon and/or text button. kind: 'primary' (blue pill), 'outline' (pill with a border),
    'icon' (square, background only on hover). `chevron` adds a small ▾ for buttons that open a menu."""

    def __init__(self, master, p: Palette, font, command: Callable | None = None, icon: str | None = None,
                 text: str = "", kind: str = "icon", chevron: bool = False, tooltip: str = "", height: int = 32,
                 bg: str | None = None):
        height = px(height)
        super().__init__(master, height=height, highlightthickness=0, bd=0, bg=bg or p.chrome, cursor="hand2")
        self.p, self.font, self.command, self.kind = p, font, command, kind
        self.icon, self.text, self.chevron, self.h = icon, text, chevron, height
        self._state, self._enabled, self._on, self._badge = "normal", True, False, False
        self._imgs: list = []
        self.bind("<Enter>", lambda e: self._set("hover"))
        self.bind("<Leave>", lambda e: self._set("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set("pressed"))
        self.bind("<ButtonRelease-1>", self._release)
        self.tip = Tooltip(self, tooltip)
        self._layout()

    # public
    def configure_button(self, icon: str | None = None, text: str | None = None, kind: str | None = None,
                         command: Callable | None = None, tooltip: str | None = None) -> None:
        if icon is not None:
            self.icon = icon or None
        if text is not None:
            self.text = text
        if kind is not None:
            self.kind = kind
        if command is not None:
            self.command = command
        if tooltip is not None:
            self.tip.text = tooltip
        self._layout()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def set_on(self, on: bool, badge: bool = False) -> None:
        self._on, self._badge = on, badge
        self._draw()

    # internals
    def _layout(self) -> None:
        tw = self.font.measure(self.text) if self.text else 0
        if self.kind == "icon" and not self.text:
            w = self.h + px(12 if self.chevron else 0)
        else:
            w = px(12 if self.kind != "icon" else 6) + px(18 if self.icon else 0) + px(6 if self.icon and tw else 0) \
                + tw + px(14 if self.chevron else 0) + px(14 if self.kind != "icon" else 8)
        self.w = int(w)
        self.configure(width=self.w)
        self._draw()

    def _set(self, state: str) -> None:
        self._state = state
        self._draw()

    def _release(self, event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._set("hover" if inside else "normal")
        if inside and self._enabled and self.command:
            self.command()

    def _colors(self) -> tuple[str | None, str | None, str]:
        p, st = self.p, (self._state if self._enabled else "disabled")
        if self.kind == "primary":
            if st == "disabled":
                return p.hover, None, p.text_faint
            return {"hover": p.accent_hover, "pressed": p.accent_pressed}.get(st, p.accent), None, "#ffffff"
        if self.kind == "outline":
            fill = {"hover": p.hover, "pressed": p.pressed}.get(st)
            return fill, p.text_faint, (p.text_faint if st == "disabled" else p.text)
        fill = {"hover": p.hover, "pressed": p.pressed}.get(st) or (p.hover if self._on else None)
        return fill, (p.accent if self._on else None), \
            (p.text_faint if st == "disabled" else p.text if st in ("hover", "pressed") or self._on else p.text_dim)

    def _draw(self) -> None:
        self.delete("all")
        self._imgs.clear()
        w, h = self.w, self.h
        fill, border, fg = self._colors()
        radius = h / 2 if self.kind in ("primary", "outline") else px(7)
        icon, chev = px(18), px(12)
        if fill or border:
            bg = ImageTk.PhotoImage(shape_image(w, h, radius, fill, border))
            self._imgs.append(bg)
            self.create_image(0, 0, image=bg, anchor="nw")
        x = px(12 if self.kind != "icon" else 6)
        if self.kind == "icon" and not self.text:
            x = (h - icon) / 2
        if self.icon:
            render_icon = solid_bar_icon_image if self.icon in SOLID_BAR_ICONS else icon_image
            ic = ImageTk.PhotoImage(render_icon(self.icon, icon, fg))
            self._imgs.append(ic)
            self.create_image(x, (h - icon) / 2, image=ic, anchor="nw")
            x += icon + px(6 if self.text else 0)
        if self.text:
            self.create_text(x, h / 2, text=self.text, fill=fg, font=self.font, anchor="w")
            x += self.font.measure(self.text) + px(2)
        if self.chevron:
            ch = ImageTk.PhotoImage(icon_image("chev", chev, fg))
            self._imgs.append(ch)
            self.create_image(x + px(1 if self.text else 0), (h - chev) / 2, image=ch, anchor="nw")
        if self._badge:
            self.create_oval(w - px(11), h - px(11), w - px(4), h - px(4), fill=self.p.accent, outline=self.p.chrome,
                             width=px(1.5))


# -- tooltip -----------------------------------------------------------------------------------------

class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay: int = 550):
        self.widget, self.text, self.delay = widget, text, delay
        self._after = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        if self.text:
            self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if not self.text or not self.widget.winfo_viewable():
            return
        p = getattr(self.widget, "p", None)
        self._win = tw = tk.Toplevel(self.widget)
        tw.withdraw()  # place before mapping so XWayland compositors honour geometry
        tw.overrideredirect(True)
        tw.attributes("-topmost", True)
        try:
            tw.attributes("-type", "tooltip")  # X11 window type avoids focus stealing
        except tk.TclError:
            pass
        bg, fg = (p.field, p.text) if p else ("#222", "#eee")
        tk.Label(tw, text=self.text, bg=bg, fg=fg, font=getattr(self.widget, "font", None), padx=px(8), pady=px(4),
                 bd=0, highlightthickness=1, highlightbackground=p.field_border if p else "#555").pack()
        tw.update_idletasks()
        w, h = tw.winfo_reqwidth(), tw.winfo_reqheight()
        sx, sy, sw, sh = monitor_area(self.widget)
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2 - w // 2
        y = self.widget.winfo_rooty() - h - px(6)
        if y < sy:
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(6)
        x = max(sx, min(x, sx + sw - w))
        y = max(sy, min(y, sy + sh - h))
        tw.geometry(f"+{x}+{y}")
        tw.deiconify()
        tw.update_idletasks()
        round_corners(tw, small=True)

    def _hide(self, _e=None):
        self._cancel()
        if self._win is not None:
            self._win.destroy()
            self._win = None


# -- pop-up menu -------------------------------------------------------------------------------------
# items: ("item", label, callback[, icon[, right_text]]), ("sub", label, items), ("sep",),
#        ("header", text), ("info", text[, color]), ("check", label, callback, checked)

def _hyprland_event_socket() -> Path | None:
    """Hyprland's compositor events, available to XWayland apps in a Wayland session."""
    if not sys.platform.startswith("linux"):
        return None
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    if not signature or "/" in signature or "\\" in signature:
        return None
    for base in (os.environ.get("XDG_RUNTIME_DIR"), "/tmp"):
        if base:
            path = Path(base) / "hypr" / signature / ".socket2.sock"
            if path.is_socket():
                return path
    return None


def _listen_hyprland_focus(conn: socket.socket, changed: threading.Event, stop: threading.Event) -> None:
    """Watch compositor focus changes without asking Tk's stale XWayland focus state."""
    with conn:
        conn.settimeout(0.25)
        pending = b""
        while not stop.is_set():
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                if line.startswith(b"activewindowv2>>"):
                    changed.set()
                    return


class PopupMenu(tk.Toplevel):
    """A themed menu that opens next to a widget. Closes on a choice, Escape, a click elsewhere or
    when the app loses focus."""

    def __init__(self, anchor: tk.Widget, items: list[tuple], p: Palette, font, parent_menu=None,
                 x: int | None = None, y: int | None = None, above: bool = False, right: bool = False):
        super().__init__(parent_menu or anchor)
        self.p, self.font, self.parent_menu, self.child = p, font, parent_menu, None
        self.anchor = anchor
        self._external_focus = threading.Event()
        self._focus_stop = threading.Event()
        self.withdraw()  # XWayland window managers may ignore a move after mapping
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=p.field_border)
        body = tk.Frame(self, bg=p.chrome, padx=px(6), pady=px(6))
        body.pack(padx=1, pady=1)
        self._imgs = []
        for it in items:
            self._add(body, it)
        self.update_idletasks()
        sx, sy, sw, sh = screen_area(self)
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        if x is None:
            x = anchor.winfo_rootx() + (anchor.winfo_width() - w if right else 0)
            below = anchor.winfo_rooty() + anchor.winfo_height() + px(6)
            over = anchor.winfo_rooty() - h - px(6)
            y = over if (above and over >= sy) or below + h > sy + sh else below
        x = min(x, sx + sw - w - px(4))
        y = min(y, sy + sh - h - px(4))
        self.geometry(f"+{max(sx, x)}+{max(sy, y)}")
        self.deiconify()
        self.update_idletasks()
        round_corners(self)
        if parent_menu is None:
            self.bind("<Escape>", lambda e: self.close_all())
            self.bind("<ButtonPress>", self._click)
            self.focus_force()
            try:
                self.grab_set()
            except tk.TclError:
                pass
            path = _hyprland_event_socket()
            if path is not None:
                conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    conn.settimeout(0.2)
                    conn.connect(str(path))
                except OSError:
                    conn.close()
                else:
                    threading.Thread(target=_listen_hyprland_focus,
                                     args=(conn, self._external_focus, self._focus_stop), daemon=True).start()
            self.after(250, self._watch_focus)

    def _row(self, body, text, right="", icon=None, color=None, active=True):
        p = self.p
        row = tk.Frame(body, bg=p.chrome, cursor="hand2" if active else "arrow")
        row.pack(fill="x", pady=px(1))
        marker = tk.Frame(row, bg=p.chrome, width=max(2, px(2)))
        marker.pack(side="left", fill="y")
        row._marker = marker
        kids = []
        if icon:
            normal = ImageTk.PhotoImage(icon_image(icon, px(16), color or p.accent))
            self._imgs.append(normal)
            lab = tk.Label(row, image=normal, bg=p.chrome, width=px(18), bd=0)
            lab.pack(side="left", padx=(px(8), 0), pady=px(6))
            lab.looks = ({"image": normal}, {"image": normal})
            kids.append(lab)
        t = tk.Label(row, text=text, bg=p.chrome, fg=color or (p.text if active else p.text_faint), font=self.font,
                     anchor="w", padx=px(9), pady=px(6))
        t.pack(side="left", fill="x", expand=True)
        t.looks = ({"fg": t.cget("fg")}, {"fg": "#ffffff"})
        kids.append(t)
        if right:
            r = tk.Label(row, text=right, bg=p.chrome, fg=p.text_faint, font=self.font, padx=px(10))
            r.pack(side="right")
            r.looks = ({"fg": p.text_faint}, {"fg": "#ffffff"})
            kids.append(r)
        return row, kids

    def _hover(self, row, kids, on):
        bg = self.p.hover if on else self.p.chrome
        row.configure(bg=bg)
        row._marker.configure(bg=self.p.accent if on else self.p.chrome)
        for k in kids:
            k.configure(bg=bg, **k.looks[0])

    def _add(self, body, it):
        p, kind = self.p, it[0]
        if kind == "sep":
            tk.Frame(body, bg=p.separator, height=max(1, px(1))).pack(fill="x", padx=px(6), pady=px(4))
            return
        if kind == "header":
            tk.Label(body, text=it[1].upper(), bg=p.chrome, fg=p.text_faint, font=self.font, anchor="w",
                     padx=px(8)).pack(fill="x", pady=(px(4), px(2)))
            return
        if kind == "info":
            self._row(body, it[1], color=it[2] if len(it) > 2 else p.text_dim, active=False)
            return
        if kind == "button":  # ("button", text, command, icon, colour): a filled pill as wide as the menu
            self._button(body, *it[1:])
            return
        if kind == "sub":
            row, kids = self._row(body, it[1], right="›")
            for w in [row, row._marker] + kids:
                w.bind("<Enter>", lambda e, r=row, k=kids, sub=it[2]: (self._hover(r, k, True), self._open_sub(r, sub)))
                w.bind("<Leave>", lambda e, r=row, k=kids: self._hover(r, k, False))
            return
        if kind == "check":
            label, cb, checked = it[1], it[2], it[3]
            row, kids = self._row(body, label, icon="check" if checked else None)
            if not checked:
                kids[0].configure(padx=px(8 + 26))
        else:
            label, cb = it[1], it[2]
            icon = it[3] if len(it) > 3 else None
            right = it[4] if len(it) > 4 else ""
            row, kids = self._row(body, label, right=right, icon=icon)
        for w in [row, row._marker] + kids:
            w.bind("<Enter>", lambda e, r=row, k=kids: (self._hover(r, k, True), self._close_child()))
            w.bind("<Leave>", lambda e, r=row, k=kids: self._hover(r, k, False))
            w.bind("<ButtonRelease-1>", lambda e, c=cb: self._choose(c))

    def _button(self, body, text, cb, icon, color):
        h = px(34)
        c = tk.Canvas(body, height=h, width=px(200), bg=self.p.chrome, highlightthickness=0, bd=0, cursor="hand2")
        c.pack(fill="x", padx=px(6), pady=(px(6), px(4)))
        r, g, b, _a = _rgba(color)
        hover_color = "#%02x%02x%02x" % tuple(round(v + (255 - v) * 0.15) for v in (r, g, b))
        bold = self.font.copy()
        bold.configure(weight="bold")
        c.font = bold
        c.state = {"on": False}

        def draw(_e=None):
            w = max(c.winfo_width(), px(120))
            c.delete("all")
            pill = ImageTk.PhotoImage(shape_image(w, h, h / 2, hover_color if c.state["on"] else color, None))
            ic = ImageTk.PhotoImage(icon_image(icon, px(18), "#ffffff"))
            c.imgs = (pill, ic)
            c.create_image(0, 0, image=pill, anchor="nw")
            tid = c.create_text(0, h // 2, text=text, fill="#ffffff", font=c.font, anchor="w")
            x0, _y0, x1, _y1 = c.bbox(tid)
            gap, iw = px(8), px(18)
            left = (w - (iw + gap + x1 - x0)) // 2
            c.create_image(left, h // 2, image=ic, anchor="w")
            c.coords(tid, left + iw + gap, h // 2)

        def hover(on):
            c.state["on"] = on
            draw()

        c.bind("<Configure>", draw)
        c.bind("<Enter>", lambda e: hover(True))
        c.bind("<Leave>", lambda e: hover(False))
        c.bind("<ButtonRelease-1>", lambda e: self._choose(cb))

    def _open_sub(self, row, items):
        if self.child is not None and self.child.items_id == id(items):
            return
        self._close_child()
        self.child = PopupMenu(row, items, self.p, self.font, parent_menu=self,
                               x=self.winfo_rootx() + self.winfo_width() - px(2), y=row.winfo_rooty() - px(5))
        self.child.items_id = id(items)

    def _close_child(self):
        if self.child is not None:
            self.child._close_child()
            self.child.destroy()
            self.child = None

    def _root_menu(self):
        m = self
        while m.parent_menu is not None:
            m = m.parent_menu
        return m

    def _choose(self, cb):
        self._root_menu().close_all()
        if cb:
            cb()

    def _inside(self, x, y) -> bool:
        m = self
        while m is not None:
            if m.winfo_exists() and m.winfo_rootx() <= x < m.winfo_rootx() + m.winfo_width() and \
                    m.winfo_rooty() <= y < m.winfo_rooty() + m.winfo_height():
                return True
            m = m.child
        return False

    def _click(self, e):
        if not self._inside(e.x_root, e.y_root):
            anchor = self._root_menu().anchor
            if anchor.winfo_exists() and anchor.winfo_rootx() <= e.x_root < anchor.winfo_rootx() + anchor.winfo_width() and \
                    anchor.winfo_rooty() <= e.y_root < anchor.winfo_rooty() + anchor.winfo_height():
                anchor._skip_open = True  # the release should close, not reopen, this menu
            self.close_all()

    def _watch_focus(self):
        if not self.winfo_exists():
            return
        if self._external_focus.is_set():
            self.close_all()
            return
        try:
            has_focus = self.focus_displayof() is not None
        except (KeyError, tk.TclError):
            has_focus = False
        if not has_focus:
            self.close_all()
        else:
            self.after(250, self._watch_focus)

    def close_all(self):
        root = self._root_menu()
        root._focus_stop.set()
        try:
            root.grab_release()
        except tk.TclError:
            pass
        root._close_child()
        if root.winfo_exists():
            root.destroy()
