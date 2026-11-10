"""The companion as a floating contextual bar, like Photoshop's: one row that changes with PhotoCraft's
selection. Something selected: fill or remove it. Nothing selected: edit, expand or upscale the whole image.
Styled like PhotoCraft's active Pro theme. Work runs on a thread; the UI polls a queue."""
from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import ImageTk

from . import (__version__, autostart, dpi, presets, results_store, setup_env, shoot_store, shoots, style_thumbs, styles,
               theme, updates)
from .comfy import ComfyClient, ComfyError
from .dpi import px
from .families import family
from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .photoshoot import SHOT_COUNTS, reshoot, run_photoshoot
from .picker import Picker
from .style_panel import StylePanel
from .pipeline import (CREATE_CHOICES, EXPAND_CHOICES, FIND, MAX_VARIATIONS, UserFacingError, create, create_size,
                       SWAP_GONE, depth_effect, depth_select, enhance, expand, fix_faces, generate, load_reference, place, remove_background,
                       remove_things, results_for_layer, run_upscale, select_things, similar, sketch_to_image, swap)
from .settings import SETTINGS_FILE, Settings
from .settings_dialog import SettingsDialog
from .widgets import (BarButton, PopupMenu, Tooltip, minimize, round_corners, screen_area, shape_image,
                      show_in_taskbar)

TITLE = "Generative Fill for PhotoCraft"
KOFI_URL = "https://ko-fi.com/T7L52ABIMT"
KOFI_BLUE = "#29abe0"  # Ko-fi's own button colour
BORDERLESS = sys.platform == "win32"  # elsewhere a borderless window can't reliably take keyboard focus
OK, BAD, UNKNOWN = "#2d9d78", "#e68619", None  # connection dot colours (Spectrum green / orange)
PLACEHOLDER = {"selection": "Describe what to fill in, or leave empty…",
               "image": "Describe a change, or name a thing to change: \"make the car red\"…",
               "empty": "Describe a picture to create…"}


class PromptField(tk.Canvas):
    """Rounded text field with a placeholder and a recent-prompts button inside."""

    def __init__(self, master, p, font, width: int, on_enter, on_history):
        width = px(width)
        super().__init__(master, width=width, height=px(32), bg=p.chrome, highlightthickness=0, bd=0)
        self.p, self.w, self._focused, self._showing = p, width, False, False
        self._placeholder = ""
        self.entry = tk.Entry(self, bd=0, relief="flat", bg=p.field, fg=p.text, insertbackground=p.text,
                              font=font, highlightthickness=0, selectbackground=p.accent, selectforeground="#ffffff")
        self.history = BarButton(self, p, font, on_history, icon="history", height=24, bg=p.field,
                                 tooltip="Recent prompts")
        self._bg = None
        self._draw_bg()
        self._entry_win = self.create_window(px(12), px(16), window=self.entry, anchor="w",
                                             width=width - px(12 + 34), height=px(20))
        self._history_win = self.create_window(width - px(5), px(16), window=self.history, anchor="e")
        self.bind("<Configure>", self._resized)  # it stretches to the bar's width
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Return>", lambda e: on_enter())
        self.bind("<Button-1>", lambda e: self.entry.focus_set())

    def _resized(self, e) -> None:
        if e.width != self.w and e.width > px(60):
            self.w = e.width
            self.itemconfigure(self._entry_win, width=self.w - px(12 + 34))
            self.coords(self._history_win, self.w - px(5), px(16))
            self._draw_bg()

    def _draw_bg(self):
        img = ImageTk.PhotoImage(shape_image(self.w, px(32), px(8), self.p.field,
                                             self.p.accent if self._focused else self.p.field_border))
        self.delete("bg")
        self.create_image(0, 0, image=img, anchor="nw", tags="bg")
        self.tag_lower("bg")
        self._bg = img

    def _focus_in(self, _e=None):
        self._focused = True
        if self._showing:
            self._showing = False
            self.entry.delete(0, "end")
            self.entry.configure(fg=self.p.text)
        self._draw_bg()

    def _focus_out(self, _e=None):
        self._focused = False
        self._show_placeholder()
        self._draw_bg()

    def _show_placeholder(self):
        if not self._focused and (self._showing or not self.entry.get()):
            self._showing = True
            self.entry.delete(0, "end")
            self.entry.insert(0, self._placeholder)
            self.entry.configure(fg=self.p.text_faint)

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self._show_placeholder()

    def get(self) -> str:
        return "" if self._showing else self.entry.get()

    def set(self, text: str) -> None:
        self._showing = False
        self.entry.configure(fg=self.p.text)
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self._show_placeholder()


class StatusPill(tk.Toplevel):
    """A small rounded message under the bar. Click it to hide it."""

    def __init__(self, bar: tk.Tk, p, font):
        super().__init__(bar)
        self.bar, self.p = bar, p
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=p.field_border)
        body = tk.Frame(self, bg=p.field, padx=px(10), pady=px(5))
        body.pack(padx=1, pady=1)
        self.dot = tk.Label(body, bg=p.field, bd=0)
        self.dot.pack(side="left", padx=(0, px(8)))
        self.label = tk.Label(body, bg=p.field, fg=p.text, font=font, justify="left", wraplength=px(460))
        self.label.pack(side="left")
        for w in (self, body, self.dot, self.label):
            w.bind("<Button-1>", lambda e: self.hide())
        self._after = None
        self.withdraw()

    def show(self, text: str, color: str | None, hide_after: int | None = None) -> None:
        self.label.configure(text=text)
        self._dot = ImageTk.PhotoImage(shape_image(px(8), px(8), px(4), color or self.p.accent, None))
        self.dot.configure(image=self._dot)
        if self._after:
            self.after_cancel(self._after)
            self._after = None
        self.deiconify()
        self.follow()
        round_corners(self, small=True)
        if hide_after:
            self._after = self.after(hide_after, self.hide)

    def hide(self) -> None:
        if self._after:
            self.after_cancel(self._after)
            self._after = None
        self.withdraw()

    def follow(self) -> None:
        """Sit centred under the bar (above it when the bar is at the bottom of the screen)."""
        if self.state() == "withdrawn":
            return
        self.update_idletasks()
        b = self.bar
        x = b.winfo_rootx() + (b.winfo_width() - self.winfo_width()) // 2
        y = b.winfo_rooty() + b.winfo_height() + px(6)
        _sx, sy, _sw, sh = screen_area(b)
        if y + self.winfo_height() > sy + sh:
            y = b.winfo_rooty() - self.winfo_height() - px(6)
        self.geometry(f"+{x}+{y}")


class App:
    def __init__(self, root: tk.Tk, background: bool = False):
        self.root = root
        self.server: socket.socket | None = None  # the one-bar port, set by main()
        self.background = background  # started with Windows: hidden until PhotoCraft opens
        self.visible = True
        self.photocraft_seen = False  # connected at least once since shown (so a close can hide it)
        self._photocraft_down = 0     # failed checks in a row
        self.settings = Settings.load()
        if not SETTINGS_FILE.exists():
            self.settings.save()
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.picker: Picker | None = None   # the "Choose a result" window, while open
        self.last_results = None            # the last results with one in PhotoCraft (Other results swaps it)
        self.tagged = False                 # the layer selected in PhotoCraft has saved results
        self.shoot_tagged = False           # the layer selected in PhotoCraft is a shot of a photoshoot
        self.theme_name = self.settings.last_theme
        self.mode = "image"            # "selection" / "image" (nothing selected) / "empty" (no document open)
        self.reference: str | None = None   # path of the optional reference picture
        self.update_info: tuple[str, str] | None = None  # (version, url) when a newer release exists
        self.conn = {"PhotoCraft": (UNKNOWN, "checking…"), "ComfyUI": (UNKNOWN, "checking…"),
                     "Select (SAM 3)": (UNKNOWN, "checking…")}
        self.last_message = ""
        self._prompt_text = ""
        self._progress_after = None
        self._progress_pos = 0.0

        root.title(f"{TITLE} {__version__}")
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "icon.png"
        if icon.is_file():
            self._icon = tk.PhotoImage(file=str(icon)).subsample(16)  # 1024 → 64 px
            root.iconphoto(True, self._icon)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        if BORDERLESS:
            root.overrideredirect(True)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self.fonts = theme.fonts(root)
        self.frame: tk.Frame | None = None
        self.pill: StatusPill | None = None
        self.build()
        self._place_window()
        if BORDERLESS:
            show_in_taskbar(root)
            root.after(50, lambda: round_corners(root))
        root.bind("<Configure>", lambda e: self.pill and self.pill.follow())
        root.bind_all("<Escape>", lambda e: self._generate_or_cancel() if self.busy else None)
        if background:
            self.hide()
        else:
            self.say("Checking connections…", None)
        self.root.after(100, self.poll)
        threading.Thread(target=self.check_connections, daemon=True).start()
        threading.Thread(target=self.watch, daemon=True).start()

    # -- layout --------------------------------------------------------------------------------
    def build(self) -> None:
        """(Re)build the bar in the current theme's colours."""
        p = self.p = theme.palette_for(self.theme_name)
        f = self.fonts
        theme.style_ttk(self.root, p, f)  # the Settings dialog still uses ttk
        if not BORDERLESS:
            theme.title_bar(self.root, p)
        if self.frame is not None:
            self._prompt_text = self.prompt.get()
            self.frame.destroy()
        self.root.configure(bg=p.field_border if BORDERLESS else p.chrome)
        outer = self.frame = tk.Frame(self.root, bg=p.chrome)
        outer.pack(fill="both", expand=True, padx=1 if BORDERLESS else 0, pady=1 if BORDERLESS else 0)
        # two rows plus a narrow control rail on the right
        body = tk.Frame(outer, bg=p.chrome)
        body.pack(fill="x")
        rail = tk.Frame(body, bg=p.chrome)
        rail.pack(side="right", fill="y")
        tk.Frame(rail, bg=p.separator, width=max(1, px(1))).pack(side="left", fill="y", padx=(px(2), 0))
        controls = tk.Frame(rail, bg=p.chrome)
        controls.pack(side="right", fill="y", padx=(px(2), px(3)))
        content = tk.Frame(body, bg=p.chrome)
        content.pack(side="left", fill="both", expand=True)
        top = tk.Frame(content, bg=p.chrome, padx=px(6), pady=0)
        top.pack(fill="x", pady=(px(7), 0))
        row = tk.Frame(content, bg=p.chrome, padx=px(6), pady=px(6))
        row.pack(fill="x")

        def sep():
            tk.Frame(row, bg=p.separator, width=max(1, px(1)), height=px(22)).pack(side="left", padx=px(6))

        def button(**kw) -> BarButton:
            return BarButton(row, p, f["button"] if kw.get("kind") == "primary" else f["body"], **kw)

        # drag grip
        grip = self.grip = tk.Canvas(top, width=px(14), height=px(32), bg=p.chrome,
                                     highlightthickness=0, cursor="fleur")
        for cy in (10, 16, 22):
            grip.create_oval(px(5.5), px(cy - 1.5), px(8.5), px(cy + 1.5),
                             fill=p.text_faint, outline="")
        grip.pack(side="left", padx=(px(2), px(4)))
        Tooltip(grip, "Drag to move the bar")
        grip.bind("<ButtonPress-1>", self._drag_start)
        grip.bind("<B1-Motion>", self._drag)
        grip.bind("<ButtonRelease-1>", self._drag_end)

        self.prompt = PromptField(top, p, f["body"], 440, lambda: self.start("fill"), self.history_menu)
        self.prompt.pack(side="left", fill="x", expand=True)
        # what Generate will use: each shows its name, and what is switched on
        self.style_btn = button(icon="palette", text="Style", command=self.style_panel)
        self.style_btn.pack(side="left")
        self.style_off_btn = button(icon="x", command=self._clear_styles, tooltip="Turn the style and angle off")
        self.ref_btn = button(icon="image", text="Reference", command=self.reference_menu)
        self.ref_btn.pack(side="left")
        self.count_btn = button(icon="stack", text=self._count_text(), chevron=True,
                                command=self.count_menu, tooltip="How many results to choose from")
        self.count_btn.pack(side="left")
        sep()

        # actions: the ones that fit the current mode are shown
        self.rm_btn = button(icon="eraser", command=self.remove_menu,
                             tooltip="Remove: what's selected or named, people, cars, the background, "
                                     "reflections")
        self.sel_btn = button(icon="target", command=self.select_menu,
                              tooltip="Select: find things by name (SAM 3) or by distance")
        self.fix_btn = button(icon="wand", command=self.fix_menu,
                              tooltip="Fix: enhance, restore, colorize, faces, light, sky, blur")
        self.size_btn = button(icon="expand", command=self.resize_menu,
                               tooltip="Resize: expand the canvas or upscale to a bigger copy")
        self.create_btn = button(icon="create", command=self.create_menu,
                                 tooltip="Create: a new picture, a sketch, or a photoshoot")
        self.others_btn = button(icon="swap", command=self.show_other_results,
                                 tooltip="Other results: swap for another result from the same Generate")
        self.reshoot_btn = button(icon="camera", command=self.reshoot_shot,
                                  tooltip="Reshoot: make this shot again")
        self.gen_btn = button(icon="spark", text="Generate", kind="primary", command=self._generate_or_cancel)
        self.generate_sep = tk.Frame(row, bg=p.separator, width=max(1, px(1)), height=px(22))
        self.more_btn = BarButton(controls, p, f["body"], icon="more", command=self.more_menu,
                                  tooltip="Settings, connection, version")
        if BORDERLESS:
            self.minimize_btn = BarButton(controls, p, f["body"], icon="minimize",
                                          command=lambda: minimize(self.root), tooltip="Minimize the bar")
        self.actions = [self.rm_btn, self.sel_btn, self.fix_btn, self.size_btn, self.create_btn, self.others_btn,
                        self.reshoot_btn, self.gen_btn]
        self.more_btn.pack(side="bottom", pady=(0, px(6)))
        if BORDERLESS:
            self.minimize_btn.pack(side="top", pady=(px(7), 0))
        if self.update_info:
            self.more_btn.set_on(False, badge=True)

        self.progress = tk.Canvas(outer, height=px(2), bg=p.chrome, highlightthickness=0)
        self.progress.pack(fill="x", side="bottom")

        if self.pill is not None:
            self.pill.destroy()
        self.pill = StatusPill(self.root, p, f["body"])
        self.prompt.set(self._prompt_text)
        self._show_reference()
        self._show_style()
        self._arrange()

    def _arrange(self) -> None:
        """Show the buttons that fit the mode and the busy state."""
        for b in self.actions:
            b.pack_forget()
        self.generate_sep.pack_forget()
        shown = {"selection": [self.rm_btn, self.fix_btn, self.gen_btn],
                 "image": [self.sel_btn, self.rm_btn, self.fix_btn, self.size_btn, self.create_btn, self.gen_btn],
                 "empty": [self.create_btn, self.gen_btn]}[self.mode]
        if (self.last_results is not None or self.tagged) and self.picker is None and not self.busy \
                and self.mode != "empty":
            shown = shown[:-1] + [self.others_btn, shown[-1]]
        if self.shoot_tagged and self.picker is None and not self.busy and self.mode != "empty":
            shown = shown[:-1] + [self.reshoot_btn, shown[-1]]
        for b in reversed(shown):  # right-aligned, with Generate set off from the icon actions
            b.pack(side="right", padx=(px(4), 0))
            if b is self.gen_btn:
                self.generate_sep.pack(side="right", padx=(px(8), px(4)))
        style = self._style_label() if self.mode != "selection" else ""  # fills don't use the style
        if style and self.mode == "image":  # Generate with nothing typed just applies the style
            self.prompt.set_placeholder(f"Describe a change, or leave empty to make it {style}…")
        else:
            self.prompt.set_placeholder(PLACEHOLDER[self.mode] + (f"   Style: {style}" if style else ""))
        empty = self.mode == "empty"
        # nothing open: the menu button only picks the shape; the blue button creates
        self.create_btn.configure_button(
            text="Size" if empty else "", kind="outline" if empty else "icon",
            tooltip=f"Size: choose output shape ({self.settings.create_choice})" if empty
            else "Create: new picture, sketch, or photoshoot")
        if self.busy:
            self.gen_btn.configure_button(icon="x", text="Cancel", kind="outline", tooltip="Stop (Esc)")
        elif empty:
            self.gen_btn.configure_button(icon="spark", text="Create", kind="primary",
                                          tooltip="Create a new picture from the prompt (Enter)")
        else:
            self.gen_btn.configure_button(icon="spark", text="Generate", kind="primary", tooltip=(
                "Fill the selection (Enter)" if self.mode == "selection" else "Change the whole image (Enter)"))
        for b in (self.rm_btn, self.sel_btn, self.fix_btn, self.size_btn, self.create_btn, self.count_btn,
                  self.style_btn, self.style_off_btn, self.ref_btn):
            b.set_enabled(not self.busy)

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._arrange()

    def set_theme(self, name: str) -> None:
        if name and name != self.theme_name:
            self.theme_name = name
            self.settings.last_theme = name
            self.settings.save()
            self.build()

    # -- window position -------------------------------------------------------------------------
    def _place_window(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        sx, sy, sw, sh = screen_area(self.root)
        x, y = self.settings.bar_x, self.settings.bar_y
        if x is None or y is None or not (sx <= x <= sx + sw - px(80) and sy <= y <= sy + sh - h):
            x, y = (self.root.winfo_screenwidth() - w) // 2, px(90)  # top centre of the main screen
        self.root.geometry(f"+{x}+{y}")

    def _drag_start(self, e) -> None:
        self._drag_from = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag(self, e) -> None:
        dx, dy = self._drag_from
        self.root.geometry(f"+{e.x_root - dx}+{e.y_root - dy}")

    def _drag_end(self, _e) -> None:
        s = Settings.load()
        s.bar_x, s.bar_y = self.root.winfo_x(), self.root.winfo_y()
        s.save()
        self.settings.bar_x, self.settings.bar_y = s.bar_x, s.bar_y

    # -- messages and progress ---------------------------------------------------------------------
    def say(self, text: str, color: str | None, hide_after: int | None = None) -> None:
        self.last_message = text
        if self.pill is not None:
            self.pill.show(text, color, hide_after)

    def progress_busy(self) -> None:
        """Unknown length: a short segment sweeping along the bottom edge."""
        self._stop_progress()

        def step():
            w = self.progress.winfo_width()
            self._progress_pos = (self._progress_pos + 0.012) % 1.3
            x = (self._progress_pos - 0.3) * w
            self.progress.delete("all")
            self.progress.create_rectangle(x, 0, x + 0.3 * w, px(2), fill=self.p.accent, outline="")
            self._progress_after = self.root.after(16, step)
        step()

    def progress_set(self, fraction: float) -> None:
        self._stop_progress()
        self.progress.create_rectangle(0, 0, fraction * self.progress.winfo_width(), px(2), fill=self.p.accent,
                                       outline="")

    def _stop_progress(self) -> None:
        if self._progress_after:
            self.root.after_cancel(self._progress_after)
            self._progress_after = None
        self.progress.delete("all")

    # -- menus -----------------------------------------------------------------------------------
    def _menu(self, anchor, items, right: bool = False) -> None:
        if getattr(anchor, "_skip_open", False):  # the anchor press just closed its menu
            anchor._skip_open = False
            return
        PopupMenu(anchor, items, self.p, self.fonts["body"], right=right)

    def history_menu(self) -> None:
        recent = Settings.load().recent_prompts
        items = [("header", "Recent prompts")]
        items += [("item", (t if len(t) <= 60 else t[:57] + "…"), lambda t=t: self._use_prompt(t)) for t in recent]
        if not recent:
            items.append(("info", "Prompts you use appear here"))
        self._menu(self.prompt, items)

    def preset_items(self, group: str) -> list:
        """The presets named "<group>: …" (Sky, Relight, Portrait, Remove reflections), as menu items."""
        items = []
        for name, _ in presets.PRESETS:
            head, _, label = name.partition(": ")
            if label and head == group:
                items.append(("item", label[:1].upper() + label[1:], lambda n=name: self.use_preset(n)))
        return items

    def _set_style(self, name: str) -> None:
        s = Settings.load()
        s.style = self.settings.style = name
        s.save()
        self._show_style()
        self._arrange()
        self.say(f"Style on: {name}. It's added to Create and whole-image edits until you turn it off."
                 if name else "Style off.", None, hide_after=5000)

    def _set_angle(self, name: str) -> None:
        s = Settings.load()
        s.angle = self.settings.angle = name
        s.save()
        self._show_style()
        self._arrange()
        self.say(f"Angle on: {name}. It's added, with the style, to Create and whole-image edits until you turn "
                 "it off." if name else "Angle off.", None, hide_after=5000)

    def _clear_styles(self) -> None:
        s = Settings.load()
        s.style = s.angle = self.settings.style = self.settings.angle = ""
        s.save()
        self._show_style()
        self._arrange()
        self.say("Style and angle off.", None, hide_after=4000)

    def _style_label(self) -> str:
        return " · ".join(x for x in (self.settings.style, self.settings.angle) if x)

    def _edit_styles(self) -> None:
        styles.load()  # writes the file with the defaults if it isn't there yet
        path = str(styles.STYLES_FILE)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except OSError as e:
            self.say(f"Couldn't open {path}: {e}", BAD, hide_after=7000)

    def shoot_items(self) -> list:
        """Photoshoot: the shoots, how many shots, the shape, and editing the shoots."""
        loaded = shoots.load()
        items = [("header", "Photoshoot")]
        for kind, label in (("product", "Product"), ("people", "People")):
            names = [s.name for s in loaded if s.kind == kind]
            if names:
                items.append(("sub", label, [("item", n, lambda n=n: self._pick_shoot(n)) for n in names]))
        items += [("sep",), ("header", "Shots")]
        items += [("check", f"{n} shots", lambda n=n: self._set_shoot_count(n), n == self.settings.shoot_count)
                  for n in SHOT_COUNTS]
        items.append(("sub", "Shape", [("check", c, lambda c=c: self._set_create_choice(c),
                                        c == self.settings.create_choice) for c in CREATE_CHOICES]))
        items += [("sep",), ("item", "Edit shoots…", self._edit_shoots, "folder")]
        return items

    def _pick_shoot(self, name: str) -> None:
        start = str(Path(self.reference).parent) if self.reference else None
        path = filedialog.askopenfilename(
            parent=self.root, title=f"Photoshoot: {name} – choose the photo", initialdir=start,
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("All files", "*.*")])
        if path:
            self.start("photoshoot", choice=name, what=path)

    def _set_shoot_count(self, n: int) -> None:
        s = Settings.load()
        s.shoot_count = self.settings.shoot_count = n
        s.save()

    def _edit_shoots(self) -> None:
        shoots.load()  # writes the file with the defaults if it isn't there yet
        path = str(shoots.SHOOTS_FILE)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except OSError as e:
            self.say(f"Couldn't open {path}: {e}", BAD, hide_after=7000)

    def reshoot_shot(self) -> None:
        self.start("reshoot")

    def _show_style(self) -> None:
        name = self._style_label()
        self.style_btn.set_on(bool(name))
        self.style_btn.configure_button(text=(name if len(name) <= 26 else name[:25] + "…") if name else "Style",
                                        tooltip=f"Style on: {name} (click to change)" if name
                                        else "Style: a look added to Create and whole-image edits")
        self.style_off_btn.pack_forget()
        if name:  # the x right after the style shows it is on and turns it off in one click
            self.style_off_btn.pack(side="left", after=self.style_btn)

    def style_panel(self) -> None:
        if self.busy:
            return

        def make(name, text):
            if self.busy:
                raise RuntimeError("busy")  # don't slow down a job: the next opening makes it
            return style_thumbs.make(name, text, Settings.load())
        StylePanel(self.root, self.p, self.fonts, self.settings.style, self._set_style, make_missing=make,
                   width=self.root.winfo_width(), on_edit=self._edit_styles, angle=self.settings.angle,
                   on_angle=self._set_angle, on_clear=self._clear_styles)

    def _count_text(self) -> str:
        n = self.settings.variations
        return f"{n} result" + ("s" if n > 1 else "")

    def count_menu(self) -> None:
        n = self.settings.variations
        items = [("header", "Results to choose from")]
        items += [("check", f"{i} result" + ("s" if i > 1 else " (placed right away)"),
                   lambda i=i: self._set_count(i), i == n) for i in range(1, MAX_VARIATIONS + 1)]
        self._menu(self.count_btn, items)

    def _typed(self):
        """(typed, short, hint): the prompt box's text, a short form for menus, and what to do when it's empty."""
        typed = self.prompt.get().strip()
        short = (typed if len(typed) <= 28 else typed[:27] + "…")
        hint = lambda: (self.say('Type what to find in the prompt box first, for example "the dog" or "the red '  # noqa: E731
                                 'car", then pick it here.', None, hide_after=7000), self.prompt.entry.focus_set())
        return typed, short, hint

    def select_menu(self) -> None:
        typed, short, hint = self._typed()
        items = [("header", "Select"),
                 ("item", f"“{short}”" if typed else "What you type…",
                  (lambda: self.start("select", what=typed)) if typed else hint),
                 ("item", "Sky", lambda: self.start("select", what=FIND["sky"][0], label=FIND["sky"][1])),
                 ("item", "People", lambda: self.start("select", what=FIND["people"][0], label=FIND["people"][1])),
                 ("sep",), ("header", "By distance"),
                 ("item", "Foreground (what's near)", lambda: self.start("depth_select", choice="foreground")),
                 ("item", "Background (what's far)", lambda: self.start("depth_select", choice="background"))]
        self._menu(self.sel_btn, items)

    def remove_menu(self) -> None:
        """Everything that takes something out of the picture."""
        typed, short, hint = self._typed()
        preset = lambda name: lambda: self.use_preset(name)  # noqa: E731
        if self.mode == "selection":
            items = [("header", "Remove"),
                     ("item", "What's selected", lambda: self.start("remove")),
                     ("info", "Type what it is for a cleaner result")]
        else:
            items = [("header", "Remove"),
                     ("item", f"“{short}”" if typed else "What you type…",
                      (lambda: self.start("remove_things", what=typed)) if typed else hint),
                     ("item", "People", lambda: self.start("remove_things", what=FIND["people"][0],
                                                           label=FIND["people"][1])),
                     ("item", "Cars", lambda: self.start("remove_things", what=FIND["cars"][0],
                                                         label=FIND["cars"][1])),
                     ("sep",), ("header", "Background"),
                     ("item", f"Remove background, keep “{short}”" if typed else "Remove background",
                      lambda: self.start("cutout", what=typed))]  # nothing typed: the main subject
        items += [("sep",), ("header", "Reflections"),
                  ("item", "On windows", preset("Remove reflections: windows")),
                  ("item", "On glasses", preset("Remove reflections: glasses"))]
        self._menu(self.rm_btn, items)

    def fix_menu(self) -> None:
        """What improves the picture or changes its look (taking things out is under Remove)."""
        typed, short, hint = self._typed()
        preset = lambda name: lambda: self.use_preset(name)  # noqa: E731
        faces = [("item", "Sharpen faces", lambda: self.start("faces"))]
        if typed:  # only when picked: text left in the box must never change what plain Fix faces does
            faces.append(("item", f"As typed: “{short}”", lambda: self.start("faces", choice="typed")))
        faces += self.preset_items("Portrait") + [("sep",)]
        if self.reference:  # the picture already chosen, by name, so it's never used by surprise
            name = Path(self.reference).name
            name = name if len(name) <= 28 else name[:27] + "…"
            faces.append(("item", f"Replace with {name}", lambda: self.start("faces", choice="reference")))
        faces.append(("item", "Replace with another photo…" if self.reference else "Replace with a photo…",
                      self._replace_face_with_photo))
        blur = [("item", "Lens blur: subject sharp", lambda: self.start("depth", choice="blur"))]
        if typed:  # focus on the typed thing only when picked
            blur.append(("item", f"Lens blur: “{short}” sharp", lambda: self.start("depth", choice="blur", what=typed)))
        blur += [("item", "Portrait lens (redrawn)", preset("Background blur (portrait lens)")),
                 ("item", "Haze in the distance", lambda: self.start("depth", choice="haze")),
                 ("item", "Fog", lambda: self.start("depth", choice="fog"))]
        items = [("header", "Fix"),
                 ("item", "Enhance: crisper, better light", lambda: self.start("enhance")),
                 ("item", "Restore old photo", preset("Restore old photo")),
                 ("item", "Colorize black & white", preset("Colorize black & white")),
                 ("item", "Harmonize (select the pasted object)", preset("Harmonize (select the pasted object)")),
                 ("sub", "Faces", faces),
                 ("sep",), ("header", "Change the look"),
                 ("sub", "Relight", self.preset_items("Relight")),
                 ("sub", "Sky", self.preset_items("Sky")),
                 ("sub", "Blur & haze", blur)]
        self._menu(self.fix_btn, items)

    def _replace_face_with_photo(self) -> None:
        """Always asks which photo: each person needs their own."""
        if self.choose_reference(title="Photo of the face to use: best turned the same way, with a similar "
                                       "expression"):
            self.start("faces", choice="reference")

    def resize_menu(self) -> None:
        last, factor = self.settings.expand_choice, self.settings.upscale_factor
        items = [("header", "Expand the canvas to")]
        items += [("check", c, lambda c=c: self.start("expand", choice=c), c == last) for c in EXPAND_CHOICES]
        items += [("sep",), ("header", "Upscale to a new document")]
        items += [("check", f"{k}× larger", lambda k=k: self.start("upscale", factor=k), k == factor) for k in (2, 4)]
        self._menu(self.size_btn, items)

    def create_menu(self) -> None:
        last = self.settings.create_choice
        pick_only = self.mode == "empty"  # nothing open: choose the shape, then click Create
        act = self._set_create_choice if pick_only else (lambda c: self.start("create", choice=c))
        items = [("header", "Shape of the new picture" if pick_only else "Create a new picture")]
        items += [("check", f"{c}  ({w}×{h})", lambda c=c: act(c), c == last)
                  for c in CREATE_CHOICES for w, h in [create_size(c, self.settings.megapixels)]]
        items += [("sep",), ("info", "Opens as a new document")]
        items.append(("sep",))
        if not pick_only:  # a sketch needs an open picture; a photoshoot starts from a photo file
            items.append(("item", "From my sketch (the active layer)", lambda: self.start("sketch")))
        items.append(("sub", "Photoshoot", self.shoot_items()))
        self._menu(self.create_btn, items)

    def choose_create_second(self) -> None:
        if self.busy:
            return
        if not self.reference:
            self.say("Choose the main Reference picture first.", None)
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Create image 2 (optional additional reference)",
            initialdir=str(Path(self.settings.create_second_reference).parent) if self.settings.create_second_reference else None,
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("All files", "*.*")])
        if path:
            self.settings.create_second_reference = path
            self.settings.save()
            self._show_reference()
            self.say(f"Create image 2: {Path(path).name}", None)

    def clear_create_second(self) -> None:
        self.settings.create_second_reference = ""
        self.settings.save()
        self._show_reference()
        self.say("Create image 2 cleared.", None)

    def _set_create_choice(self, choice: str) -> None:
        s = Settings.load()
        s.create_choice = self.settings.create_choice = choice
        s.save()
        self._arrange()
        self.prompt.entry.focus_set()

    def reference_menu(self) -> None:
        if not self.reference:
            self.choose_reference()
            return
        name = Path(self.reference).name
        second = self.settings.create_second_reference
        items = [("header", "Main reference"),
                 ("info", (name if len(name) <= 40 else name[:37] + "…")),
                 ("info", "Create: image 1 · Generate: image 2"),
                 ("item", "Change main image…", self.choose_reference, "folder"),
                 ("item", "Clear main image", self.clear_reference, "x"),
                 ("sep",), ("header", "Additional Create reference · image 2"),
                 ("info", Path(second).name if second else "None selected"),
                 ("item", "Choose image 2…", self.choose_create_second, "folder")]
        if second:
            items += [("info", 'Prompt: name both "image 1" and "image 2"'),
                      ("item", "Clear image 2", self.clear_create_second, "x")]
        self._menu(self.ref_btn, items)

    def more_menu(self) -> None:
        items = [("header", "Connections")]
        for name, (ok, text) in self.conn.items():
            items.append(("info", f"●  {name}: {text}", OK if ok else BAD if ok is False else self.p.text_dim))
        items += [("sep",),
                  ("item", "Settings…", self.open_settings, "gear"),
                  ("item", "PhotoCraft connection…", self.setup, "plug"),
                  ("sep",),
                  ("check", "Hide while PhotoCraft is closed", self._toggle_follow, self.settings.follow_photocraft)]
        if autostart.SUPPORTED:
            items.append(("check", "Start with Windows (shows when PhotoCraft opens)", self._toggle_autostart,
                          autostart.enabled()))
        if self.last_message:
            items.append(("item", "Show the last message", lambda: self.say(self.last_message, None), "history"))
        items.append(("sep",))
        if self.update_info:
            version, url = self.update_info
            items.append(("item", f"Version {version} is out: download", lambda: webbrowser.open(url), "spark"))
        items.append(("info", f"Version {__version__}"))
        items.append(("sep",))
        if BORDERLESS:
            items.append(("item", "Minimize", lambda: minimize(self.root), "chev"))
        items.append(("item", "Restart GenFill", self.restart, "history"))
        items.append(("item", "Quit", self.quit, "x"))
        items.append(("button", "Support me on Ko-fi", lambda: webbrowser.open(KOFI_URL), "coffee", KOFI_BLUE))
        self._menu(self.more_btn, items, right=True)

    def _use_prompt(self, text: str) -> None:
        self.prompt.entry.focus_set()
        self.prompt.set(text)
        self.prompt.entry.icursor("end")

    def use_preset(self, name: str) -> None:
        """Runs the preset straight away (the prompt box is left alone)."""
        if presets.prompt_for(name) is None:
            return
        if self.mode == "empty":
            self.say("Open a photo in PhotoCraft first, then pick the preset.", None, hide_after=6000)
            return
        self.start("fill", preset=name)

    def _set_count(self, n: int) -> None:
        s = Settings.load()
        s.variations = self.settings.variations = n
        s.save()
        self.count_btn.configure_button(text=self._count_text())

    # -- background work -----------------------------------------------------------------------
    def check_connections(self) -> None:
        msgs = []
        if not all(setup_env.status(self.settings.port).values()):
            self.conn["PhotoCraft"] = (False, "not set up")
            msgs.append("PhotoCraft connection isn't set up yet: ⋯ › PhotoCraft connection…")
        else:
            try:
                with PhotoCraftClient(self.settings.port, Path(self.settings.token_file), timeout=10) as pc:
                    problems = pc.compatibility()
                    if pc.mode == "desktop":
                        try:
                            self.events.put(("theme", (pc.call("ui.inspect", {}) or {}).get("theme")))
                        except PhotoCraftError:
                            pass
                if problems:
                    self.conn["PhotoCraft"] = (False, "version not compatible")
                    msgs.append("PhotoCraft version not compatible: " + " ".join(problems))
                else:
                    self.conn["PhotoCraft"] = (True, "connected")
            except PhotoCraftError as e:
                self.conn["PhotoCraft"] = (False, "not reachable")
                msgs.append(str(e))
        try:
            comfy = ComfyClient(self.settings.comfy_url)
            comfy.check()
            problems = comfy.check_setup(self.settings.workflow_models)
            model = family(self.settings.model_family).label
            self.conn["ComfyUI"] = (not problems, f"connected, {model}" if not problems else f"{model} files missing")
            msgs += ["ComfyUI: " + " ".join(problems)] if problems else []
            sam = comfy.check_segment()
            self.conn["Select (SAM 3)"] = (not sam, "ready" if not sam else "model missing (see README)")
            if self.settings.seedvr_model and comfy.has_seedvr(self.settings.seedvr_model):
                self.conn["Upscale"] = (True, "SeedVR2 (best)")
            elif self.settings.upscale_model and comfy.has_upscale_model(self.settings.upscale_model):
                self.conn["Upscale"] = (True, "4x-UltraSharp + FLUX (SeedVR2 is sharper, see README)")
            else:
                self.conn["Upscale"] = (False, "no upscale model, soft (see README)")
            if not family(self.settings.model_family).head_swap:
                self.conn["Replace face"] = (None, "face only (the BFS LoRA is for FLUX.2 Klein)")
            elif self.settings.face_swap_lora and comfy.has_lora(self.settings.face_swap_lora):
                self.conn["Replace face"] = (True, "BFS head swap (best)")
            else:
                self.conn["Replace face"] = (False, "face only, no BFS LoRA (see README)")
        except ComfyError as e:
            self.conn["ComfyUI"] = (False, "not reachable")
            self.conn["Select (SAM 3)"] = (False, "needs ComfyUI")
            msgs.append(str(e))
        self.events.put(("connections", "\n".join(msgs)))
        if self.settings.check_updates:
            found = updates.newer_release()
            if found:
                self.events.put(("update", found))

    def watch(self) -> None:
        """Follow PhotoCraft's selection (and theme) so the bar shows the right buttons."""
        pc, n, last, last_layer = None, 0, None, None
        while True:
            time.sleep(0.8)
            if self.busy:
                continue
            try:
                if pc is None:
                    if not all(setup_env.status(self.settings.port).values()):
                        self.events.put(("photocraft_connection", (False, "not set up")))
                        self.events.put(("photocraft", True))  # can't tell: stay visible so it can be set up
                        time.sleep(3)
                        continue
                    pc = PhotoCraftClient(self.settings.port, Path(self.settings.token_file), timeout=5).__enter__()
                    self.events.put(("photocraft_connection", (True, "connected")))
                    self.events.put(("photocraft", True))
                try:
                    doc = pc._inspect()
                    mode = "selection" if doc.get("selectionBounds") else "image"
                    layer = (doc.get("name"), doc.get("path"), doc.get("activeLayer"))
                except PhotoCraftError as e:
                    if "no document open" not in str(e).lower():
                        raise  # the connection went away
                    mode, layer = "empty", None  # PhotoCraft answered with an error: no document open
                if mode != last:
                    self.events.put(("mode", mode))
                    last = mode
                if layer != last_layer:  # another layer selected: is it one with results to swap between?
                    last_layer = layer
                    packet = pc.layer_metadata(layer[2]) if layer and layer[2] is not None else None
                    self.events.put(("tagged", bool(results_store.tag_of(packet))))
                    self.events.put(("shoot_tagged", bool(shoot_store.tag_of(packet))))
                n += 1
                if pc.mode == "desktop" and n % 5 == 0:
                    self.events.put(("theme", (pc.call("ui.inspect", {}) or {}).get("theme")))
            except (PhotoCraftError, OSError):
                if pc is not None:
                    try:
                        pc.__exit__(None, None, None)
                    except OSError:
                        pass
                pc, n, last, last_layer = None, 0, None, None
                self.events.put(("photocraft_connection", (False, "reconnecting")))
                self.events.put(("photocraft", False))
                time.sleep(3)
            except Exception:  # anything unexpected: reconnect, never stop watching, or the bar stays stuck
                try:
                    if pc is not None:
                        pc.__exit__(None, None, None)
                except Exception:
                    pass
                pc, n, last, last_layer = None, 0, None, None
                self.events.put(("photocraft_connection", (False, "reconnecting")))
                self.events.put(("photocraft", False))
                time.sleep(3)

    def start(self, job: str, choice: str | None = None, factor: int | None = None, what: str = "",
              label: str | None = None, preset: str | None = None) -> None:
        if self.busy:
            return
        self._forget_results()  # a new job: the last results can't be swapped in any more
        self.settings = Settings.load()
        self.settings.last_theme = self.theme_name
        if job == "fill" and self.mode == "empty" and not preset:
            job = "create"  # nothing open: Enter creates a picture
        if preset:  # a preset brings its own instruction; the box and the history stay as they are
            prompt = presets.prompt_for(preset) or ""
        else:
            prompt = self.prompt.get() if job in ("fill", "remove", "create", "sketch") else ""  # the prompt belongs to Generate
        if what and not label and job not in ("photoshoot", "depth"):
            self.settings.remember_prompt(what)  # typed words used to find things go in the history too
        if job == "upscale":
            self.settings.upscale_factor = factor or self.settings.upscale_factor
        elif job == "expand":
            self.settings.expand_choice = choice or self.settings.expand_choice
        elif job == "create":
            self.settings.create_choice = choice or self.settings.create_choice
            self.settings.remember_prompt(prompt)
        elif job in ("select", "remove_things", "cutout", "faces", "enhance", "photoshoot", "reshoot", "depth",
                     "depth_select") or preset:
            pass
        else:
            self.settings.remember_prompt(prompt)
        self.settings.save()  # last choices and prompt history are remembered
        self.busy = True
        self.cancel.clear()
        self._arrange()
        self.progress_busy()
        if job == "photoshoot":
            def fn(_prompt, settings, status, progress, cancel):
                return run_photoshoot(choice, what, settings, status, progress, cancel)
            self.say(f"Photoshoot: {choice}…", None)
        elif job == "reshoot":
            def fn(_prompt, settings, status, progress, cancel):
                results = reshoot(settings, status, progress, cancel)
                if len(results.images) > 1:
                    self.events.put(("pick", results))
                    return None  # the picker takes it from here
                status("Swapping the new shot in…")
                return swap(results, 0, settings)
            self.say("Reshooting…", None)
        elif job == "select":
            def fn(_prompt, settings, status, progress, cancel):
                return select_things(what, settings, status, progress, cancel, label=label)
            self.say(f"Looking for {label or what}…", None)
        elif job == "remove_things":
            def fn(_prompt, settings, status, progress, cancel):
                return self._offer(remove_things(what, settings, status, progress, cancel, label=label),
                                   settings, status)
            self.say(f"Looking for {label or what}…", None)
        elif job == "cutout":
            def fn(_prompt, settings, status, progress, cancel):
                return remove_background(what, settings, status, progress, cancel)
            self.say(f"Looking for {what}…", None)
        elif job == "depth_select":
            def fn(_prompt, settings, status, progress, cancel):
                return depth_select(choice, settings, status, progress, cancel)
            self.say(f"Finding the {choice}…", None)
        elif job == "depth":
            def fn(_prompt, settings, status, progress, cancel):
                return self._offer(depth_effect(choice, settings, status, progress, cancel, focus_on=what),
                                   settings, status)
            self.say("Working out the depth…", None)
        elif job == "enhance":
            extra = self.prompt.get().strip()

            def fn(_prompt, settings, status, progress, cancel):
                return self._offer(enhance(settings, status, progress, cancel, prompt=extra), settings, status)
            self.say("Enhancing…", None)
        elif job == "faces":
            reference = self.reference if choice == "reference" else None
            extra = self.prompt.get().strip() if choice == "typed" else ""  # the box is used only when chosen

            def fn(_prompt, settings, status, progress, cancel):
                ref = load_reference(reference) if reference else None
                return self._offer(fix_faces(settings, status, progress, cancel, prompt=extra, reference=ref),
                                   settings, status)
            self.say("Looking for faces…", None)
        elif job == "create":
            shape, reference = self.settings.create_choice, self.reference

            def fn(prompt_, settings, status, progress, cancel):
                ref = load_reference(reference) if reference else None
                return self._offer(create(shape, prompt_, settings, status, progress, cancel, reference=ref),
                                   settings, status)
            self.say(f"Creating a {shape} picture…", None)
        elif job == "sketch":
            def fn(prompt_, settings, status, progress, cancel):
                return self._offer(sketch_to_image(prompt_, settings, status, progress, cancel), settings, status)
            self.say("Turning the sketch into a picture…", None)
        elif job == "upscale":
            fn = run_upscale
            self.say(f"Upscaling {self.settings.upscale_factor}×…", None)
        elif job == "expand":
            shape = self.settings.expand_choice

            def fn(_prompt, settings, status, progress, cancel):
                return self._offer(expand(shape, "", settings, status, progress, cancel), settings, status)
            self.say(f"Expanding to {shape}…", None)
        else:
            remove = job == "remove"
            reference = None if preset else self.reference  # a preset's instruction never mentions image 2

            def fn(*a):
                return self._generate(*a, remove=remove, reference=reference, name=preset)
            self.say(f"{preset}…" if preset else "Starting…", None)
        threading.Thread(target=self._work, args=(fn, prompt), daemon=True).start()

    def _generate_or_cancel(self) -> None:
        if self.busy:
            self.cancel.set()
            self.say("Stopping…", None)
        else:
            self.start("fill")

    def _generate(self, prompt, settings, status, progress, cancel, remove=False, reference=None, name=None):
        ref = load_reference(reference) if reference and not remove else None
        try:
            results = generate(prompt, settings, status, progress, cancel, reference=ref, remove=remove, name=name)
        except UserFacingError as e:
            # The bar thought a picture was open but none is (its view of PhotoCraft can lag): do what Generate
            # does with nothing open and create one, with the same prompt, style, angle and reference.
            if "no document open" not in str(e) or remove or name or not prompt.strip():
                raise
            self.events.put(("mode", "empty"))
            status(f"No picture open: creating a {settings.create_choice} picture…")
            results = create(settings.create_choice, prompt, settings, status, progress, cancel, reference=ref)
        return self._offer(results, settings, status)

    def _offer(self, results, settings, status):
        """Several results: let the user choose; one: place it straight away."""
        if len(results.images) > 1:
            self.events.put(("pick", results))
            return None  # the picker takes it from here
        status("Placing the result in PhotoCraft…")
        return place(results, 0, settings)

    def _place(self, results, index: int) -> None:
        """The first pick adds the result; later picks swap it for the one in PhotoCraft."""
        def fn(*_a):
            try:
                text = swap(results, index, self.settings)
            except UserFacingError as e:
                self.events.put(("placed", (results, str(e) == SWAP_GONE)))
                raise
            self.events.put(("placed", (results, False)))
            return text
        self._work(fn, "")

    def _similar(self, results, index: int) -> None:
        def fn(_prompt, settings, status, progress, cancel):
            more = similar(results, index, settings, status, progress, cancel)
            if results.placed is not None:  # still swappable: picking one replaces what's in PhotoCraft
                more.placed = replace(results.placed, index=0 if results.placed.index == index else -1)
            self.events.put(("pick", more))
        self._work(fn, "")

    def _work(self, fn, prompt: str) -> None:
        try:
            result = fn(prompt, self.settings, lambda s: self.events.put(("status", s)),
                        lambda f: self.events.put(("progress", f)), self.cancel)
            if result is not None:
                hint = "" if result.startswith(("Selected", "Created", "Upscaled", "Photoshoot")) \
                    else " (Ctrl+Z in PhotoCraft undoes it.)"
                self.events.put(("done", (True, f"Done: {result}.{hint}")))
        except UserFacingError as e:
            self.events.put(("done", (False, str(e))))
        except Exception as e:  # never die silently
            self.events.put(("done", (False, f"Unexpected error: {e!r}")))

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.say(value, None)
                elif kind == "connections":
                    if value:
                        self.say(value, BAD)
                    else:
                        self.say("Connected to PhotoCraft and ComfyUI.", OK, hide_after=2500)
                elif kind == "mode":
                    self.set_mode(value)
                elif kind == "photocraft":
                    self.follow_photocraft(value)
                elif kind == "photocraft_connection":
                    if not (value[0] and self.conn.get("PhotoCraft", (None, ""))[1] == "version not compatible"):
                        self.conn["PhotoCraft"] = value
                elif kind == "show":
                    self.background, self.photocraft_seen, self._photocraft_down = False, False, 0
                    self.show()
                elif kind == "theme":
                    self.set_theme(value)
                elif kind == "progress" and value is not None:
                    self.progress_set(value)
                elif kind == "update":
                    self.update_info = value
                    self.more_btn.set_on(False, badge=True)
                elif kind == "pick":
                    self.choose(value)
                elif kind == "tagged":
                    if value != self.tagged:
                        self.tagged = value
                        self._arrange()
                elif kind == "shoot_tagged":
                    if value != self.shoot_tagged:
                        self.shoot_tagged = value
                        self._arrange()
                elif kind == "reopen":
                    self.choose(value)
                    self.say("Click another result to swap it in.", None, hide_after=4000)
                    self._arrange()
                elif kind == "notice":
                    self.say(value, BAD, hide_after=7000)
                elif kind == "placed":
                    results, gone = value
                    if gone:
                        self._forget_results()
                    elif results.placed is not None:
                        self.last_results = results
                    if self.picker is not None and self.picker.results is results:
                        self.picker.set_waiting(False)
                elif kind == "done":
                    ok, text = value
                    self.say(text, OK if ok else BAD, hide_after=6000 if ok else None)
                    self.finish()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self) -> None:
        self.busy = False
        self._stop_progress()
        self._arrange()

    def choose(self, results) -> None:
        self.progress_set(1.0)
        self.say(f"{len(results.images)} results ready: pick one.", OK)

        def busy() -> None:
            self.busy = True
            self.cancel.clear()
            self._arrange()
            self.progress_busy()

        def picked(i: int) -> None:
            swapping = results.placed is not None
            self.say(f"Swapping in result {i + 1}…" if swapping else f"Placing result {i + 1} in PhotoCraft…", None)
            busy()
            threading.Thread(target=self._place, args=(results, i), daemon=True).start()

        def more(i: int) -> None:
            self.picker = None
            self.say(f"Making more like result {i + 1}…", None)
            busy()
            threading.Thread(target=self._similar, args=(results, i), daemon=True).start()

        def discarded() -> None:
            self.picker = None
            self.say("Discarded. Nothing was added to PhotoCraft.", None, hide_after=4000)
            self.finish()

        def closed() -> None:
            self.picker = None
            self.finish()  # a job that ended in this window (Reshoot, More like this) leaves the bar busy
        if self.picker is not None:
            self.picker.dismiss()
        self.picker = Picker(self.root, results, self.p, self.fonts, picked, discarded,
                             on_similar=more if results.job is not None else None, on_close=closed)

    def show_other_results(self) -> None:
        """Bring back the results window to swap the placed result for another one: the selected layer's
        saved results if it has some (even from long ago), else the last ones placed."""
        if self.busy:
            return
        mine = self.last_results

        def find() -> None:
            try:
                found = results_for_layer(self.settings)
                if mine is not None and mine.saved and mine.saved == found.saved:
                    found = mine  # the same set still in memory: it can also make "More like this"
                self.events.put(("reopen", found))
            except UserFacingError as e:
                self.events.put(("reopen", mine) if mine is not None else ("notice", str(e)))
            except Exception as e:  # never die silently
                self.events.put(("notice", f"Unexpected error: {e!r}"))
        self.say("Looking for this layer's results…", None)
        threading.Thread(target=find, daemon=True).start()

    def _forget_results(self) -> None:
        self.last_results = None
        if self.picker is not None:
            self.picker.dismiss()
            self.picker = None

    # -- reference picture -----------------------------------------------------------------------
    def choose_reference(self, title: str = "Reference picture") -> bool:
        """Ask for a reference picture (opening in the last one's folder); True if one was picked."""
        start = str(Path(self.reference).parent) if self.reference else None
        path = filedialog.askopenfilename(
            parent=self.root, title=title, initialdir=start,
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("All files", "*.*")])
        if not path:
            return False
        self.reference = path
        self._show_reference()
        self.say(f'Reference: {Path(path).name}. Create uses it automatically; Generate sees it as image 2.',
                 None, hide_after=6000)
        return True

    def clear_reference(self) -> None:
        self.reference = None
        self.settings.create_second_reference = ""
        self.settings.save()
        self._show_reference()

    def _show_reference(self) -> None:
        self.ref_btn.set_on(bool(self.reference), badge=bool(self.reference))
        second = self.settings.create_second_reference
        self.ref_btn.configure_button(tooltip=(f"Main: {Path(self.reference).name}"
                                               + (f" · Create image 2: {Path(second).name}" if second else "")) if self.reference
                                      else "Add a reference picture (an object or style to use)")

    # -- other windows ---------------------------------------------------------------------------
    def setup(self) -> None:
        if all(setup_env.status(self.settings.port).values()):
            messagebox.showinfo(TITLE, "Already set up. If PhotoCraft still isn't connected: "
                                + setup_env.RESTART_HINT.replace("Now ", "", 1), parent=self.root)
            return
        setup_env.install(self.settings.port)
        messagebox.showinfo(TITLE, "Done. " + setup_env.RESTART_HINT, parent=self.root)
        threading.Thread(target=self.check_connections, daemon=True).start()

    def open_settings(self) -> None:
        def saved(s: Settings) -> None:
            self.settings = s
            self.count_btn.configure_button(text=self._count_text())
            self.say("Settings saved. Checking connections…", None)
            threading.Thread(target=self.check_connections, daemon=True).start()
        SettingsDialog(self.root, Settings.load(), self.p, self.fonts, saved, on_restart=self.restart)

    # -- following PhotoCraft ------------------------------------------------------------------------
    def follow_photocraft(self, running: bool) -> None:
        """Show the bar when PhotoCraft opens; hide it when PhotoCraft closes (after it was seen, or when
        started with Windows). Two failed checks in a row count as closed (one can be a busy moment)."""
        if running:
            self._photocraft_down = 0
            self.photocraft_seen = True
            if not self.visible and self.settings.follow_photocraft:
                self.show()
                threading.Thread(target=self.check_connections, daemon=True).start()
            return
        self._photocraft_down += 1
        if (self.settings.follow_photocraft and self.visible and not self.busy and self._photocraft_down >= 2
                and (self.photocraft_seen or self.background)):
            self.hide()

    def show(self) -> None:
        if self.visible:
            self.root.lift()
            return
        self.visible = True
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        if BORDERLESS:
            self.root.after(50, lambda: round_corners(self.root))

    def hide(self) -> None:
        self.visible = False
        if self.pill is not None:
            self.pill.hide()
        self.root.withdraw()

    def _toggle_follow(self) -> None:
        s = Settings.load()
        s.follow_photocraft = self.settings.follow_photocraft = not self.settings.follow_photocraft
        s.save()

    def _toggle_autostart(self) -> None:
        try:
            if autostart.enabled():
                autostart.disable()
                self.say("It no longer starts with Windows.", None, hide_after=4000)
            else:
                autostart.enable()
                self.say("It now starts with Windows and shows itself when PhotoCraft opens.", OK, hide_after=5000)
        except Exception as e:  # noqa: BLE001  (PowerShell missing or blocked)
            self.say(f"Couldn't change the Windows startup setting: {e}", BAD)

    def quit(self) -> None:
        self.cancel.set()
        self.root.destroy()

    def restart(self) -> None:
        """Close this bar and start a fresh one (it picks up new settings and code)."""
        if self.server is not None:
            self.server.close()  # free the one-bar port so the new bar can claim it
        args = [a for a in sys.argv if a != autostart.BACKGROUND]
        try:
            subprocess.Popen([sys.executable, *args], cwd=os.getcwd(), close_fds=True)
        except OSError as e:
            self.say(f"Couldn't restart: {e}", BAD)
            return
        self.quit()


INSTANCE_PORT = 47861  # one bar per user: a second start asks the running one to show itself


def _first_instance() -> socket.socket | None:
    """Claim the single-instance port. None if another bar already runs (it was asked to show unless
    this start is a hidden Windows start)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", INSTANCE_PORT))
        s.listen(2)
        return s
    except OSError:
        s.close()
        if autostart.BACKGROUND not in sys.argv:
            try:
                with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
                    c.sendall(b"show")
            except OSError:
                pass
        return None


def _listen(server: socket.socket, events: queue.Queue) -> None:
    while True:
        try:
            conn, _ = server.accept()
            with conn:
                if conn.recv(16).startswith(b"show"):
                    events.put(("show", None))
        except OSError:
            return


def main() -> None:
    server = _first_instance()
    if server is None:
        return  # the bar is already running
    dpi.enable()  # sharp text at 125% / 150% / 200% display scaling (before the first window)
    root = tk.Tk()
    app = App(root, background=autostart.BACKGROUND in sys.argv)
    app.server = server
    threading.Thread(target=_listen, args=(server, app.events), daemon=True).start()
    root.mainloop()


if __name__ == "__main__":
    main()
