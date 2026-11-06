"""The 'choose a result' window shown after Generate makes more than one candidate."""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from PIL import ImageTk

from . import theme
from .pipeline import Results


class Picker(tk.Toplevel):
    """Thumbnails side by side; click one to add it to PhotoCraft. Closing the window discards all."""

    def __init__(self, master: tk.Misc, results: Results, p: theme.Palette, fonts: dict,
                 on_pick: Callable[[int], None], on_discard: Callable[[], None],
                 on_similar: Callable[[int], None] | None = None):
        super().__init__(master, bg=p.chrome, padx=14, pady=12)
        self.title("Choose a result")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.transient(master)
        theme.title_bar(self, p)
        self._done = False
        self._on_pick, self._on_discard, self._on_similar = on_pick, on_discard, on_similar
        self._photos = [ImageTk.PhotoImage(im) for im in results.previews]  # keep references alive

        tk.Label(self, text="Click the one you want; it's added to PhotoCraft as a new layer."
                 + ("  'More like this' makes new versions close to one." if on_similar else ""),
                 bg=p.chrome, fg=p.text_dim, font=fonts["small"]).grid(row=0, column=0, columnspan=len(self._photos),
                                                                       sticky="w", pady=(0, 10))
        for i, photo in enumerate(self._photos):
            cell = tk.Frame(self, bg=p.chrome, highlightthickness=2, highlightbackground=p.chrome,
                            highlightcolor=p.accent, cursor="hand2")
            cell.grid(row=1, column=i, padx=4, sticky="n")
            img = tk.Label(cell, image=photo, bg=p.chrome, bd=0)
            img.pack()
            tk.Label(cell, text=str(i + 1), bg=p.chrome, fg=p.text_dim, font=fonts["small"]).pack(pady=(4, 2))
            if on_similar:
                theme.PillButton(cell, "More like this", lambda i=i: self._similar(i), p, fonts["small"],
                                 kind="quiet", height=22).pack(pady=(0, 2))
            for w in (cell, img):
                w.bind("<Button-1>", lambda e, i=i: self._pick(i))
                w.bind("<Enter>", lambda e, c=cell: c.configure(highlightbackground=p.accent))
                w.bind("<Leave>", lambda e, c=cell: c.configure(highlightbackground=p.chrome))
            self.bind(str(i + 1), lambda e, i=i: self._pick(i))  # number keys pick too

        bottom = tk.Frame(self, bg=p.chrome)
        bottom.grid(row=2, column=0, columnspan=len(self._photos), sticky="e", pady=(10, 0))
        theme.PillButton(bottom, "Discard all", self._discard, p, fonts["small"], kind="quiet",
                         height=22, min_width=56).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._discard)
        self.bind("<Escape>", lambda e: self._discard())
        self.focus_force()

    def _pick(self, i: int) -> None:
        if not self._done:
            self._done = True
            self.destroy()
            self._on_pick(i)

    def _similar(self, i: int) -> None:
        if not self._done:
            self._done = True
            self.destroy()
            self._on_similar(i)

    def _discard(self) -> None:
        if not self._done:
            self._done = True
            self.destroy()
            self._on_discard()
