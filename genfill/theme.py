"""PhotoCraft's Pro theme tokens (crates/ui-egui/src/theme.rs) and Tk widgets drawn to match them."""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk

from .dpi import px


@dataclass(frozen=True)
class Palette:
    chrome: str        # panel background
    field: str         # text field background
    field_border: str
    hover: str
    pressed: str
    text: str
    text_dim: str
    text_faint: str
    separator: str
    accent: str = "#378ef0"   # Spectrum blue (primary buttons, progress)
    accent_hover: str = "#4b9cf5"
    accent_pressed: str = "#2680eb"
    dark: bool = True


PALETTES = {
    "Pro": Palette("#323232", "#242424", "#4a4a4a", "#424242", "#4e4e4e", "#dedede", "#b2b2b2", "#808080", "#1e1e1e"),
    "ProMedium": Palette("#535353", "#454545", "#686868", "#626262", "#707070", "#eeeeee", "#d4d4d4", "#a0a0a0", "#3e3e3e"),
    "ProLightGray": Palette("#b8b8b8", "#d6d6d6", "#8c8c8c", "#c6c6c6", "#aaaaaa", "#141414", "#343434", "#606060", "#9e9e9e",
                            dark=False),
    "ProLight": Palette("#f0f0f0", "#ffffff", "#bababa", "#e2e2e2", "#cecece", "#141414", "#404040", "#787878", "#d6d6d6",
                        dark=False),
}
DEFAULT_THEME = "ProMedium"


def palette_for(theme: str | None) -> Palette:
    """PhotoCraft's `ui.inspect` theme name -> palette (Studio/Classic/other themes fall back to Pro)."""
    if not theme:
        return PALETTES[DEFAULT_THEME]
    key = {k.lower(): k for k in PALETTES}.get(theme.replace(" ", "").replace("(", "").replace(")", "").lower())
    return PALETTES[key] if key else PALETTES["Pro"]


def fonts(root: tk.Misc) -> dict[str, tkfont.Font]:
    families = set(tkfont.families(root))
    fallback = {"win32": "Segoe UI", "darwin": ".AppleSystemUIFont"}.get(sys.platform, "TkDefaultFont")
    family = "Inter" if "Inter" in families else fallback
    return {
        "body": tkfont.Font(root, family=family, size=-px(12)),
        "small": tkfont.Font(root, family=family, size=-px(11)),
        "button": tkfont.Font(root, family=family, size=-px(12), weight="bold"),
    }


def style_ttk(root: tk.Misc, p: Palette, f: dict) -> None:
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=p.chrome, foreground=p.text, font=f["body"])
    s.configure("PC.TEntry", fieldbackground=p.field, foreground=p.text, bordercolor=p.field_border,
                lightcolor=p.field_border, darkcolor=p.field_border, insertcolor=p.text, padding=(px(6), px(4)))
    s.map("PC.TEntry", bordercolor=[("focus", p.accent)], lightcolor=[("focus", p.accent)])
    s.configure("PC.TCombobox", fieldbackground=p.field, background=p.field, foreground=p.text,
                bordercolor=p.field_border, lightcolor=p.field_border, darkcolor=p.field_border,
                arrowcolor=p.text_dim, padding=(px(6), px(3)))
    s.map("PC.TCombobox", fieldbackground=[("readonly", p.field)], foreground=[("readonly", p.text)],
          background=[("active", p.hover)], selectbackground=[("readonly", p.field)],
          selectforeground=[("readonly", p.text)])
    root.option_add("*TCombobox*Listbox.background", p.field)
    root.option_add("*TCombobox*Listbox.foreground", p.text)
    root.option_add("*TCombobox*Listbox.selectBackground", p.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", f["body"])
    s.configure("PC.Horizontal.TProgressbar", troughcolor=p.field, background=p.accent, bordercolor=p.chrome,
                lightcolor=p.accent, darkcolor=p.accent, thickness=px(4))


def title_bar(root: tk.Tk, p: Palette) -> None:
    """Windows 10/11: match the title bar to the theme. Dark mode flag (Windows 10+), then on
    Windows 11 the exact caption, text and border colours (overrides the user's accent colour)."""
    def colorref(hex_color: str) -> ctypes.c_uint:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return ctypes.c_uint(r | (g << 8) | (b << 16))

    if sys.platform == "darwin":  # macOS draws its own title bar; just match light/dark
        try:
            root.tk.call("::tk::unsupported::MacWindowStyle", "appearance", root,
                         "darkaqua" if p.dark else "aqua")
        except tk.TclError:
            pass
        return
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        dark = ctypes.c_int(1 if p.dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 now, 19 on early Windows 10
            if set_attr(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break
        for attr, color in ((35, p.chrome), (36, p.text), (34, p.separator)):  # caption, text, border
            value = colorref(color)
            set_attr(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))  # ignored before Windows 11
    except (AttributeError, OSError):
        pass


class PillButton(tk.Canvas):
    """PhotoCraft's rounded pill button. kind: 'primary' (blue fill), 'secondary' (outlined), 'quiet' (text)."""

    def __init__(self, master, text: str, command, palette: Palette, font: tkfont.Font,
                 kind: str = "secondary", height: int = 26, min_width: int = 72):
        self.text, self.command, self.p, self.font, self.kind = text, command, palette, font, kind
        height, min_width = px(height), px(min_width)
        width = max(min_width, font.measure(text) + px(28))
        super().__init__(master, width=width, height=height, highlightthickness=0, bd=0,
                         bg=palette.chrome, cursor="hand2")
        self._state, self._enabled = "normal", True
        self.bind("<Enter>", lambda e: self._set("hover"))
        self.bind("<Leave>", lambda e: self._set("normal"))
        self.bind("<ButtonPress-1>", lambda e: self._set("pressed"))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _set(self, state: str) -> None:
        self._state = state
        self._draw()

    def _release(self, event) -> None:
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._set("hover" if inside else "normal")
        if inside and self._enabled and self.command:
            self.command()

    def _colors(self) -> tuple[str, str, str]:
        p, st = self.p, (self._state if self._enabled else "disabled")
        if self.kind == "primary":
            if st == "disabled":
                return p.hover, p.hover, p.text_faint
            fill = {"hover": p.accent_hover, "pressed": p.accent_pressed}.get(st, p.accent)
            return fill, fill, "#ffffff"
        if self.kind == "quiet":
            fill = {"hover": p.hover, "pressed": p.pressed}.get(st, p.chrome)
            return fill, fill, p.text_faint if st == "disabled" else p.text_dim
        fill = {"hover": p.hover, "pressed": p.pressed}.get(st, p.chrome)
        border = p.text_faint if st == "disabled" else p.text_dim
        return fill, border, p.text_faint if st == "disabled" else p.text

    def _draw(self) -> None:
        self.delete("all")
        w, h = int(self.winfo_width() or self["width"]), int(self.winfo_height() or self["height"])
        if w <= 1:
            w, h = int(self["width"]), int(self["height"])
        fill, border, fg = self._colors()
        r = (h - 2) / 2
        x0, y0, x1, y1 = 1, 1, w - 1, h - 1
        for shape in (("oval", x0, y0, x0 + 2 * r, y1), ("oval", x1 - 2 * r, y0, x1, y1)):
            self.create_oval(*shape[1:], fill=fill, outline=border, width=1)
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline="")
        self.create_line(x0 + r, y0, x1 - r, y0, fill=border)
        self.create_line(x0 + r, y1, x1 - r, y1, fill=border)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)
