"""The Style panel: thumbnails of each style under the bar, in tabs (Photo, Portrait, Product, Art, Eras, Yours, then
Angles); click one to turn it on, Off (first in every tab) or clicking it again turns it off, Clear all turns off both. An angle is chosen apart from the style and
adds to it. Opens on the active style's tab. Closes on a choice,
Escape, a click elsewhere or when the app loses focus, like the bar's menus."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Callable

from PIL import Image, ImageDraw, ImageTk

from . import style_thumbs, styles
from .widgets import BarButton, round_corners, screen_area

COLUMNS = 5
GAP = 8
BUILT_IN = {n for n, _ in styles.DEFAULT_STYLES}
ANGLES_TAB = "Angles"


def groups(every: list[tuple[str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    """(tab, styles) in panel order, each starting with Off: photo, product, art, then your own."""
    off = [(style_thumbs.OFF, "")]
    kind = lambda n: "Yours" if n not in BUILT_IN else "Product" if n in styles.PRODUCT \
        else "Portrait" if n in styles.PORTRAIT else "Art" if n in styles.ART \
        else "Eras" if n in styles.ERAS else "Photo"  # noqa: E731
    tabs = [(t, [s for s in every if kind(s[0]) == t])
            for t in ("Photo", "Portrait", "Product", "Art", "Eras", "Yours")]
    return [(t, off + g) for t, g in tabs if g] + [(ANGLES_TAB, off + styles.ANGLES)]


def toggled(name: str, current: str) -> str:
    """What clicking a tile chooses: the style, or nothing when it's the one already on."""
    return "" if name and name == current else name


def tab_of(name: str, every: list[tuple[str, str]]) -> str:
    """The tab a style is in (Off and unknown names: the first)."""
    got = [(t, g) for t, g in groups(every) if t != ANGLES_TAB]
    return next((t for t, g in got if name and name in dict(g)), got[0][0] if got else "Photo")


class StylePanel(tk.Toplevel):
    def __init__(self, bar: tk.Misc, p, fonts: dict, current: str, on_choose: Callable[[str], None],
                 make_missing: Callable[[str, str], object] | None = None, width: int = 480,
                 on_edit: Callable[[], None] | None = None, angle: str = "",
                 on_angle: Callable[[str], None] | None = None, on_clear: Callable[[], None] | None = None):
        super().__init__(bar)
        self.p, self.fonts, self.current, self._on_choose = p, fonts, current, on_choose
        self.angle, self._on_angle = angle, on_angle
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=p.field_border)
        body = tk.Frame(self, bg=p.chrome, padx=12, pady=10)
        body.pack(padx=1, pady=1)
        self.side = max(80, (width - 26 - (COLUMNS - 1) * GAP) // COLUMNS)
        self._photos, self.tiles = [], {}
        every = styles.load()
        tabs = [(t, g) for t, g in groups(every) if t != ANGLES_TAB or on_angle is not None]
        bar_row = tk.Frame(body, bg=p.chrome)
        bar_row.grid(row=0, column=0, sticky="w", pady=(0, 8))
        stack = tk.Frame(body, bg=p.chrome)
        stack.grid(row=1, column=0, sticky="nw")
        self.pages, self.tab_labels = {}, {}
        for tab, members in tabs:
            page = tk.Frame(stack, bg=p.chrome)
            page.grid(row=0, column=0, sticky="nw")  # measured below, then only the shown tab stays
            for i, (name, text) in enumerate(members):
                self._tile(page, name, text, tab == ANGLES_TAB).grid(row=i // COLUMNS, column=i % COLUMNS,
                                                  padx=(0 if i % COLUMNS == 0 else GAP, 0), pady=(0, GAP), sticky="n")
            self.pages[tab] = page
            btn = BarButton(bar_row, p, fonts["body"], text=tab, kind="outline", height=28,
                            command=lambda t=tab: self.show_tab(t))
            btn.pack(side="left", padx=(0, 6))
            self.tab_labels[tab] = btn
        self.update_idletasks()  # the panel keeps the biggest tab's size, so switching tabs doesn't jump
        stack.configure(width=max(pg.winfo_reqwidth() for pg in self.pages.values()),
                        height=max(pg.winfo_reqheight() for pg in self.pages.values()))
        stack.grid_propagate(False)
        self.show_tab(tab_of(current, every))
        row = 2
        foot = tk.Frame(body, bg=p.chrome)
        foot.grid(row=row, column=0, sticky="we", pady=(4, 0))
        tk.Label(foot, text="Click again to turn off. An angle adds to the style.", bg=p.chrome, fg=p.text_dim,
                 font=fonts["small"]).pack(side="left")
        if on_edit is not None:
            BarButton(foot, p, fonts["body"], icon="gear", text="Edit styles", kind="outline", height=28,
                      command=lambda: (self.close(), on_edit())).pack(side="right")
        if on_clear is not None and (current or angle):
            self.clear_btn = BarButton(foot, p, fonts["body"], icon="x", text="Clear all", kind="outline", height=28,
                                       command=lambda: (self.close(), on_clear()))
            self.clear_btn.pack(side="right", padx=(0, 6))

        self.update_idletasks()
        sx, sy, sw, sh = screen_area(self)
        x = bar.winfo_rootx()
        y = bar.winfo_rooty() + bar.winfo_height() + 6
        if y + self.winfo_height() > sy + sh:
            y = bar.winfo_rooty() - self.winfo_height() - 6
        self.geometry(f"+{max(sx, min(x, sx + sw - self.winfo_width() - 4))}+{max(sy, y)}")
        round_corners(self)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<ButtonPress>", self._click)
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.after(250, self._watch_focus)

        self._made: queue.Queue = queue.Queue()
        todo = [(n, t) for n, t in [(style_thumbs.OFF, "")] + styles.load() + styles.ANGLES
                if style_thumbs.find(n, t) is None]
        if todo and make_missing is not None:
            threading.Thread(target=self._make_all, args=(todo, make_missing), daemon=True).start()
            self.after(300, self._poll)

    def show_tab(self, tab: str) -> None:
        self.tab = tab
        for t, page in self.pages.items():
            if t == tab:
                page.grid()
            else:
                page.grid_remove()
        for t, btn in self.tab_labels.items():
            btn.configure_button(kind="primary" if t == tab else "outline")

    # -- tiles -------------------------------------------------------------------------------------
    def _picture(self, name: str, text: str) -> Image.Image:
        f = style_thumbs.find(name, text)
        if f is not None:
            with Image.open(f) as im:
                return im.convert("RGB").resize((self.side, self.side), Image.LANCZOS)
        im = Image.new("RGB", (self.side, self.side), self.p.field)
        ImageDraw.Draw(im).text((8, self.side // 2 - 6), "making preview…", fill=self.p.text_faint)
        return im

    def _tile(self, body, name: str, text: str, is_angle: bool = False) -> tk.Frame:
        p, on = self.p, name == ((self.angle if is_angle else self.current) or style_thumbs.OFF)
        cell = tk.Frame(body, bg=p.chrome, cursor="hand2")
        photo = ImageTk.PhotoImage(self._picture(name, text))
        self._photos.append(photo)
        img = tk.Label(cell, image=photo, bg=p.chrome, bd=0, highlightthickness=2,
                       highlightbackground=p.accent if on else p.chrome)
        img.pack()
        label = tk.Label(cell, text=name, bg=p.chrome, fg=p.text if on else p.text_dim, font=self.fonts["small"],
                         wraplength=self.side)
        label.pack(pady=(3, 0))
        choose = "" if name == style_thumbs.OFF else name
        for w in (cell, img, label):
            w.bind("<ButtonRelease-1>", lambda e, c=choose, a=is_angle: self._choose(c, a))
            w.bind("<Enter>", lambda e, i=img: i.configure(highlightbackground=p.accent))
            w.bind("<Leave>", lambda e, i=img, o=on: i.configure(highlightbackground=p.accent if o else p.chrome))
        self.tiles.setdefault(name, []).append((img, text))  # Off is in every tab
        return cell

    def _make_all(self, todo, make_missing) -> None:
        for name, text in todo:
            try:
                make_missing(name, text)
            except Exception:  # ComfyUI not running, busy, …: the tile keeps its name only
                return
            self._made.put(name)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        while not self._made.empty():
            name = self._made.get()
            for img, text in self.tiles.get(name, []):
                photo = ImageTk.PhotoImage(self._picture(name, text))
                self._photos.append(photo)
                img.configure(image=photo)
        self.after(300, self._poll)

    # -- closing -----------------------------------------------------------------------------------
    def _choose(self, name: str, is_angle: bool = False) -> None:
        self.close()
        if is_angle:
            self._on_angle(toggled(name, self.angle))
        else:
            self._on_choose(toggled(name, self.current))

    def _click(self, e) -> None:
        inside = self.winfo_rootx() <= e.x_root < self.winfo_rootx() + self.winfo_width() and \
            self.winfo_rooty() <= e.y_root < self.winfo_rooty() + self.winfo_height()
        if not inside:
            self.close()

    def _watch_focus(self) -> None:
        if not self.winfo_exists():
            return
        try:
            has_focus = self.focus_displayof() is not None
        except (KeyError, tk.TclError):
            has_focus = False
        if has_focus:
            self.after(250, self._watch_focus)
        else:
            self.close()

    def close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        if self.winfo_exists():
            self.destroy()
