"""The 'choose a result' window shown after Generate makes more than one candidate. It stays open after
a pick, like Photoshop's Properties panel: click another result and it takes the placed one's place."""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from PIL import ImageTk

from . import theme
from .dpi import px
from .pipeline import Results


class Picker(tk.Toplevel):
    """Thumbnails side by side; click one to add it to PhotoCraft, click another to swap it in. Closing
    the window before anything was added discards all; after, it keeps the one in PhotoCraft."""

    def __init__(self, master: tk.Misc, results: Results, p: theme.Palette, fonts: dict,
                 on_pick: Callable[[int], None], on_discard: Callable[[], None],
                 on_similar: Callable[[int], None] | None = None, on_close: Callable[[], None] | None = None):
        super().__init__(master, bg=p.chrome, padx=px(14), pady=px(12))
        self.title("Choose a result")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.transient(master)
        theme.title_bar(self, p)
        self.results, self.p, self.fonts = results, p, fonts
        self._closed, self.waiting = False, False
        self._on_pick, self._on_discard, self._on_similar = on_pick, on_discard, on_similar
        self._on_close = on_close or (lambda: None)
        self._photos = [ImageTk.PhotoImage(im) for im in results.previews]  # keep references alive
        columns = min(4, len(self._photos))
        last_row = (len(self._photos) - 1) // columns + 1

        self.hint = tk.Label(self, bg=p.chrome, fg=p.text_dim, font=fonts["small"], justify="left")
        self.hint.grid(row=0, column=0, columnspan=columns, sticky="w", pady=(0, px(10)))
        self.cells, self.tags = [], []
        for i, photo in enumerate(self._photos):
            cell = tk.Frame(self, bg=p.chrome, highlightthickness=px(2), highlightbackground=p.chrome,
                            highlightcolor=p.accent, cursor="hand2")
            cell.grid(row=1 + i // columns, column=i % columns, padx=px(4), pady=px(4), sticky="n")
            img = tk.Label(cell, image=photo, bg=p.chrome, bd=0)
            img.pack()
            tag = tk.Label(cell, text=str(i + 1), bg=p.chrome, fg=p.text_dim, font=fonts["small"])
            tag.pack(pady=(px(4), px(2)))
            if on_similar:
                theme.PillButton(cell, "More like this", lambda i=i: self._similar(i), p, fonts["small"],
                                 kind="quiet", height=22).pack(pady=(0, px(2)))
            for w in (cell, img):
                w.bind("<Button-1>", lambda e, i=i: self._pick(i))
                w.bind("<Enter>", lambda e, i=i: self._ring(i, hover=True))
                w.bind("<Leave>", lambda e, i=i: self._ring(i))
            self.bind(str(i + 1), lambda e, i=i: self._pick(i))  # number keys pick too
            self.cells.append(cell)
            self.tags.append(tag)

        self.bottom = tk.Frame(self, bg=p.chrome)
        self.bottom.grid(row=last_row + 1, column=0, columnspan=columns, sticky="e", pady=(px(10), 0))
        self.close_btn = None
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self._show_state()
        self.focus_force()

    # -- state -------------------------------------------------------------------------------------
    @property
    def placed_index(self) -> int | None:
        p = self.results.placed
        return p.index if p is not None and 0 <= p.index < len(self.cells) else None

    def _ring(self, i: int, hover: bool = False) -> None:
        on = hover and not self.waiting or i == self.placed_index
        self.cells[i].configure(highlightbackground=self.p.accent if on else self.p.chrome)

    def _show_state(self) -> None:
        placed = self.placed_index
        editor = getattr(self.results, "editor", "photocraft")
        editor_name = "Photoshop" if editor == "photoshop" else "PhotoCraft"
        if self.waiting:
            text = f"Adding it to {editor_name}…"
        elif self.results.new_document:
            text = ("Click the one you want; it opens as a new document." if self.results.placed is None else
                    "Click another one to open it as a new document too. Done closes this window.")
        elif self.results.placed is None:
            text = f"Click the one you want; it's added to {editor_name} as a new layer."
        elif editor == "photoshop":
            text = "Click another result to add it as another layer. Done closes this window."
        else:
            text = "Click another one to swap it in (the same layer is replaced). Done keeps the one in PhotoCraft."
        if self._on_similar and not self.waiting:
            text += "\n'More like this' makes new versions close to one."
        self.hint.configure(text=text)
        for i, tag in enumerate(self.tags):
            tag.configure(text=f"{i + 1}  ·  In {editor_name}" if i == placed else str(i + 1),
                          fg=self.p.accent if i == placed else self.p.text_dim)
            self._ring(i)
        if self.close_btn is not None:
            self.close_btn.destroy()
        label = "Discard all" if self.results.placed is None else "Done"
        self.close_btn = theme.PillButton(self.bottom, label, self.close, self.p, self.fonts["small"],
                                          kind="quiet" if self.results.placed is None else "primary",
                                          height=22, min_width=56)
        self.close_btn.pack(side="right")

    def set_waiting(self, waiting: bool) -> None:
        """While a result is being added: clicks are ignored."""
        if not self._closed:
            self.waiting = waiting
            self._show_state()

    # -- actions -----------------------------------------------------------------------------------
    def _pick(self, i: int) -> None:
        if self._closed or self.waiting or i == self.placed_index:
            return
        self.set_waiting(True)
        self._on_pick(i)

    def _similar(self, i: int) -> None:
        if not self._closed and not self.waiting:
            self._closed = True
            self.destroy()
            self._on_similar(i)

    def close(self) -> None:
        """Before anything was added: discard all. After: keep the one in PhotoCraft."""
        if self._closed or self.waiting:
            return
        self._closed = True
        self.destroy()
        (self._on_discard if self.results.placed is None else self._on_close)()

    def dismiss(self) -> None:
        """Closed by the bar (a new job started): nothing is called back."""
        if not self._closed:
            self._closed = True
            self.destroy()
