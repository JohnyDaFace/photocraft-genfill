"""The Style panel: thumbnails of each style under the bar, in tabs (Photo, Portrait, Product, Art, Design, Eras, Yours, then
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
from .dpi import px
from .widgets import BarButton, monitor_area, round_corners

GAP = 8
TILE_MAX = 140     # thumbnails are this big…
TILE_MIN = 72      # …or smaller, only when the monitor is too narrow for COLS of them
COLS = 6           # tiles to a row: the panel stays about this wide, and longer tabs scroll
MAX_ROWS = 4       # rows shown at once (fewer on a short screen); the rest scroll
LABEL_H = 38       # a tile's name under it (two lines) and the highlight border
CHROME_H = 130     # the panel around the tiles: summary, tabs, footer, padding
SCROLL_W = 10      # the scrollbar beside the tiles
BUILT_IN = {n for n, _ in styles.DEFAULT_STYLES}
ANGLES_TAB = "Angles"


def groups(every: list[tuple[str, str]]) -> list[tuple[str, list[tuple[str, str]]]]:
    """(tab, styles) in panel order, each starting with Off: photo, product, art, then your own."""
    off = [(style_thumbs.OFF, "")]
    kind = lambda n: "Yours" if n not in BUILT_IN else "Product" if n in styles.PRODUCT \
        else "Portrait" if n in styles.PORTRAIT else "Art" if n in styles.ART \
        else "Design" if n in styles.DESIGN else "Eras" if n in styles.ERAS else "Photo"  # noqa: E731
    tabs = [(t, [s for s in every if kind(s[0]) == t])
            for t in ("Photo", "Portrait", "Product", "Art", "Design", "Eras", "Yours")]
    return [(t, off + g) for t, g in tabs if g] + [(ANGLES_TAB, off + styles.ANGLES)]


def grid_size(screen_w: int, room: int, most: int) -> tuple[int, int, int]:
    """(thumbnail side, columns, height of the scrolled view) for a tab of `most` tiles on a monitor
    `screen_w` wide with `room` pixels above or below the bar: COLS full-size tiles to a row, up to
    MAX_ROWS rows in view (less on a short screen, never under 2 rows; a part row shows there's more),
    and no taller than the tab; a longer tab scrolls."""
    gap, side, cols = px(GAP), px(TILE_MAX), COLS
    usable = screen_w - px(24 + 26 + SCROLL_W + GAP)
    while cols * (side + gap) > usable and side > px(TILE_MIN):
        side -= px(4)
    row_h = side + px(LABEL_H) + gap
    view = max(2 * row_h, min(MAX_ROWS * row_h, room - px(CHROME_H)))
    return side, cols, min(view, -(-most // cols) * row_h)


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
        body = tk.Frame(self, bg=p.chrome, padx=px(12), pady=px(10))
        body.pack(padx=1, pady=1)
        self._photos, self.tiles, self.cells = [], {}, []
        every = styles.load()
        tabs = [(t, g) for t, g in groups(every) if t != ANGLES_TAB or on_angle is not None]
        sx, sy, sw, sh = monitor_area(bar)
        top, bottom = bar.winfo_rooty(), bar.winfo_rooty() + bar.winfo_height()
        room = max(top - sy, sy + sh - bottom) - px(12)  # the panel opens below the bar, or above it if more room
        self.side, cols, shown = grid_size(sw, room, max(len(g) for _, g in tabs))
        # what is picked, both parts: a look and an angle are used together
        summary = tk.Frame(body, bg=p.chrome)
        summary.grid(row=0, column=0, sticky="w", pady=(0, px(8)))
        self.look_chip = self._chip(summary, "Look", lambda: self.show_tab(tab_of(self.current, every)))
        if on_angle is not None:
            tk.Label(summary, text="+", bg=p.chrome, fg=p.text_dim, font=fonts["body"]).pack(side="left", padx=px(6))
            self.angle_chip = self._chip(summary, "Angle", lambda: self.show_tab(ANGLES_TAB))
        bar_row = tk.Frame(body, bg=p.chrome)
        bar_row.grid(row=1, column=0, sticky="w", pady=(0, px(8)))
        view = tk.Frame(body, bg=p.chrome)  # the tiles, scrolled when a tab has more rows than fit
        view.grid(row=2, column=0, sticky="nw")
        self.canvas = tk.Canvas(view, bg=p.chrome, highlightthickness=0, bd=0)
        self.canvas.pack(side="left")
        self.scroll = tk.Canvas(view, bg=p.chrome, highlightthickness=0, bd=0, width=px(SCROLL_W))
        self.scroll.pack(side="left", fill="y", padx=(px(GAP), 0))
        stack = tk.Frame(self.canvas, bg=p.chrome)
        self.canvas.create_window(0, 0, window=stack, anchor="nw")
        self.pages, self.tab_labels = {}, {}
        for tab, members in tabs:
            page = tk.Frame(stack, bg=p.chrome)
            page.grid(row=0, column=0, sticky="nw")  # measured below, then only the shown tab stays
            for i, (name, text) in enumerate(members):
                self._tile(page, name, text, tab == ANGLES_TAB).grid(row=i // cols, column=i % cols,
                                                  padx=(0 if i % cols == 0 else px(GAP), 0), pady=(0, px(GAP)), sticky="n")
            self.pages[tab] = page
            if tab == ANGLES_TAB:  # set apart: an angle is added on top of the look, not instead of it
                tk.Frame(bar_row, bg=p.separator, width=max(1, px(1)), height=px(20)).pack(side="left", padx=(px(4), px(8)))
            btn = BarButton(bar_row, p, fonts["body"], text=("+ " + tab) if tab == ANGLES_TAB else tab,
                            kind="outline", height=28,
                            command=lambda t=tab: self.show_tab(t))
            btn.pack(side="left", padx=(0, px(6)))
            self.tab_labels[tab] = btn
        self.update_idletasks()  # the view keeps one size for every tab, so switching tabs doesn't jump
        self.row_h = self.side + px(LABEL_H) + px(GAP)
        self._thumb, self._grab = (0, 0, 0), 0
        self.canvas.configure(width=max(pg.winfo_reqwidth() for pg in self.pages.values()),
                              height=min(shown, max(pg.winfo_reqheight() for pg in self.pages.values())))
        self.scroll.configure(height=self.canvas.winfo_reqheight())
        self.scroll.bind("<ButtonPress-1>", self._scroll_press)
        self.scroll.bind("<B1-Motion>", self._scroll_drag)
        self.bind("<MouseWheel>", self._wheel)
        self.show_tab(tab_of(current, every))
        row = 3
        foot = tk.Frame(body, bg=p.chrome)
        foot.grid(row=row, column=0, sticky="we", pady=(px(4), 0))
        tk.Label(foot, text=("Pick a look, then an angle if you like: they're used together. "
                             "Click a picked one again to turn it off."), bg=p.chrome, fg=p.text_dim,
                 font=fonts["small"]).pack(side="left")
        BarButton(foot, p, fonts["body"], icon="check", text="Done", kind="primary", height=28,
                  command=self.close).pack(side="right")
        if on_edit is not None:
            BarButton(foot, p, fonts["body"], icon="gear", text="Edit styles", kind="outline", height=28,
                      command=lambda: (self.close(), on_edit())).pack(side="right", padx=(0, px(6)))
        if on_clear is not None:
            self.clear_btn = BarButton(foot, p, fonts["body"], icon="x", text="Clear all", kind="outline", height=28,
                                       command=self._clear_all)
            self.clear_btn.pack(side="right", padx=(0, px(6)))
        self._on_clear = on_clear
        self._refresh()

        self.update_idletasks()
        sx, sy, sw, sh = monitor_area(bar)
        x = bar.winfo_rootx() + (bar.winfo_width() - self.winfo_width()) // 2  # centred under the bar
        y = bar.winfo_rooty() + bar.winfo_height() + px(6)
        if y + self.winfo_height() > sy + sh:
            y = bar.winfo_rooty() - self.winfo_height() - px(6)
        self.geometry(f"+{max(sx, min(x, sx + sw - self.winfo_width() - px(4)))}+{max(sy, y)}")
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
        page = self.pages[tab]
        self.canvas.configure(scrollregion=(0, 0, page.winfo_reqwidth(), page.winfo_reqheight()))
        self.canvas.yview_moveto(0)
        self._draw_scroll()

    # -- scrolling ---------------------------------------------------------------------------------
    def _overflow(self) -> int:
        """How many pixels of the shown tab don't fit in the view (0: no scrolling)."""
        return max(0, self.pages[self.tab].winfo_reqheight() - int(self.canvas.cget("height")))

    def _draw_scroll(self) -> None:
        """A slim thumb in the panel's colours (none when the tab fits)."""
        self.scroll.delete("all")
        if not self._overflow():
            return
        h, total = int(self.scroll.cget("height")), self.pages[self.tab].winfo_reqheight()
        top, bottom = self.canvas.yview()
        y0, y1 = top * h, max(top * h + px(24), bottom * h)
        w = px(SCROLL_W)
        self.scroll.create_rectangle(w // 2 - 1, 0, w // 2 + 1, h, fill=self.p.separator, width=0)
        self.scroll.create_rectangle(1, y0, w - 1, y1, fill=self.p.text_faint, width=0)
        self._thumb = (y0, y1, total)

    def _scroll_to(self, fraction: float) -> None:
        total = self.pages[self.tab].winfo_reqheight()
        self.canvas.yview_moveto(max(0.0, min(self._overflow() / total, fraction)))
        self._draw_scroll()

    def _wheel(self, e) -> None:
        over = self._overflow()
        if over:
            total = self.pages[self.tab].winfo_reqheight()
            top = self.canvas.yview()[0] * total + (-1 if e.delta > 0 else 1) * self.row_h / 2
            self._scroll_to(max(0, min(over, top)) / total)

    def _scroll_press(self, e) -> None:
        if not self._overflow():
            return
        y0, y1, _ = self._thumb
        if not y0 <= e.y <= y1:  # a click on the track: jump there
            self._scroll_to((e.y - (y1 - y0) / 2) / int(self.scroll.cget("height")))
            y0, y1, _ = self._thumb
        self._grab = e.y - y0

    def _scroll_drag(self, e) -> None:
        if self._overflow():
            self._scroll_to((e.y - self._grab) / int(self.scroll.cget("height")))

    # -- tiles -------------------------------------------------------------------------------------
    def _picture(self, name: str, text: str) -> Image.Image:
        f = style_thumbs.find(name, text)
        if f is not None:
            with Image.open(f) as im:
                return im.convert("RGB").resize((self.side, self.side), Image.LANCZOS)
        im = Image.new("RGB", (self.side, self.side), self.p.field)
        ImageDraw.Draw(im).text((px(8), self.side // 2 - px(6)), "making preview…", fill=self.p.text_faint)
        return im

    def _tile(self, body, name: str, text: str, is_angle: bool = False) -> tk.Frame:
        p, on = self.p, self._picked(name, is_angle)
        cell = tk.Frame(body, bg=p.chrome, cursor="hand2")
        photo = ImageTk.PhotoImage(self._picture(name, text))
        self._photos.append(photo)
        img = tk.Label(cell, image=photo, bg=p.chrome, bd=0, highlightthickness=px(2),
                       highlightbackground=p.accent if on else p.chrome)
        img.pack()
        label = tk.Label(cell, text=name, bg=p.chrome, fg=p.text if on else p.text_dim, font=self.fonts["small"],
                         wraplength=self.side)
        label.pack(pady=(px(3), 0))
        choose = "" if name == style_thumbs.OFF else name
        for w in (cell, img, label):
            w.bind("<ButtonRelease-1>", lambda e, c=choose, a=is_angle: self._choose(c, a))
            w.bind("<Enter>", lambda e, i=img: i.configure(highlightbackground=p.accent))
            w.bind("<Leave>", lambda e, i=img, n=name, a=is_angle: i.configure(
                highlightbackground=p.accent if self._picked(n, a) else p.chrome))
        self.tiles.setdefault(name, []).append((img, text))  # Off is in every tab
        self.cells.append((name, is_angle, img, label))
        return cell

    def _picked(self, name: str, is_angle: bool) -> bool:
        return name == ((self.angle if is_angle else self.current) or style_thumbs.OFF)

    def _chip(self, parent, what: str, show) -> BarButton:
        chip = BarButton(parent, self.p, self.fonts["body"], text=what, kind="outline", height=28, command=show)
        chip.pack(side="left")
        return chip

    def _refresh(self) -> None:
        """The summary and the tiles' highlight, after a pick."""
        self.look_chip.configure_button(text=f"Look: {self.current or 'none'}")
        self.look_chip.set_on(bool(self.current))
        if hasattr(self, "angle_chip"):
            self.angle_chip.configure_button(text=f"Angle: {self.angle or 'none'}")
            self.angle_chip.set_on(bool(self.angle))
        for name, is_angle, img, label in self.cells:
            on = self._picked(name, is_angle)
            img.configure(highlightbackground=self.p.accent if on else self.p.chrome)
            label.configure(fg=self.p.text if on else self.p.text_dim)

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
        """Picks (or, picked again, turns off) a look or an angle. The panel stays open so the other can be
        added; Done, Esc or a click outside closes it."""
        if is_angle:
            self.angle = toggled(name, self.angle)
            self._on_angle(self.angle)
        else:
            self.current = toggled(name, self.current)
            self._on_choose(self.current)
        if self.winfo_exists():
            self._refresh()
            self.focus_force()

    def _clear_all(self) -> None:
        self.current, self.angle = "", ""
        if self._on_clear is not None:
            self._on_clear()
        if self.winfo_exists():
            self._refresh()
            self.focus_force()

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
