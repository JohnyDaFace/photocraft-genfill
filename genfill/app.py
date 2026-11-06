"""The companion as a floating contextual bar, like Photoshop's: one row that changes with PhotoCraft's
selection. Something selected: fill or remove it. Nothing selected: edit, expand or upscale the whole image.
Styled like PhotoCraft's active Pro theme. Work runs on a thread; the UI polls a queue."""
from __future__ import annotations

import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

from PIL import ImageTk

from . import __version__, presets, setup_env, theme, updates
from .comfy import ComfyClient, ComfyError
from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .picker import Picker
from .pipeline import (EXPAND_CHOICES, MAX_VARIATIONS, UserFacingError, expand, generate, load_reference, place,
                       run_upscale, similar)
from .settings import SETTINGS_FILE, Settings
from .settings_dialog import SettingsDialog
from .widgets import (BarButton, PopupMenu, Tooltip, minimize, round_corners, screen_area, shape_image,
                      show_in_taskbar)

TITLE = "Generative Fill for PhotoCraft"
BORDERLESS = sys.platform == "win32"  # elsewhere a borderless window can't reliably take keyboard focus
OK, BAD, UNKNOWN = "#2d9d78", "#e68619", None  # connection dot colours (Spectrum green / orange)
PLACEHOLDER = {"selection": "Describe what to fill in, or leave empty…",
               "image": "Describe a change to the whole image…"}


class PromptField(tk.Canvas):
    """Rounded text field with a placeholder and a recent-prompts button inside."""

    def __init__(self, master, p, font, width: int, on_enter, on_history):
        super().__init__(master, width=width, height=32, bg=p.chrome, highlightthickness=0, bd=0)
        self.p, self.w, self._focused, self._showing = p, width, False, False
        self._placeholder = ""
        self.entry = tk.Entry(self, bd=0, relief="flat", bg=p.field, fg=p.text, insertbackground=p.text,
                              font=font, highlightthickness=0, selectbackground=p.accent, selectforeground="#ffffff")
        self.history = BarButton(self, p, font, on_history, icon="history", height=24, bg=p.field,
                                 tooltip="Recent prompts")
        self._bg = None
        self._draw_bg()
        self.create_window(12, 16, window=self.entry, anchor="w", width=width - 12 - 34, height=20)
        self.create_window(width - 5, 16, window=self.history, anchor="e")
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        self.entry.bind("<Return>", lambda e: on_enter())
        self.bind("<Button-1>", lambda e: self.entry.focus_set())

    def _draw_bg(self):
        img = ImageTk.PhotoImage(shape_image(self.w, 32, 8, self.p.field,
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
        body = tk.Frame(self, bg=p.field, padx=10, pady=5)
        body.pack(padx=1, pady=1)
        self.dot = tk.Label(body, bg=p.field, bd=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.label = tk.Label(body, bg=p.field, fg=p.text, font=font, justify="left", wraplength=460)
        self.label.pack(side="left")
        for w in (self, body, self.dot, self.label):
            w.bind("<Button-1>", lambda e: self.hide())
        self._after = None
        self.withdraw()

    def show(self, text: str, color: str | None, hide_after: int | None = None) -> None:
        self.label.configure(text=text)
        self._dot = ImageTk.PhotoImage(shape_image(8, 8, 4, color or self.p.accent, None))
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
        y = b.winfo_rooty() + b.winfo_height() + 6
        _sx, sy, _sw, sh = screen_area(b)
        if y + self.winfo_height() > sy + sh:
            y = b.winfo_rooty() - self.winfo_height() - 6
        self.geometry(f"+{x}+{y}")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        if not SETTINGS_FILE.exists():
            self.settings.save()
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.theme_name = self.settings.last_theme
        self.mode = "image"            # "selection" when PhotoCraft has a selection, else "image"
        self.reference: str | None = None   # path of the optional reference picture
        self.update_info: tuple[str, str] | None = None  # (version, url) when a newer release exists
        self.conn = {"PhotoCraft": (UNKNOWN, "checking…"), "ComfyUI": (UNKNOWN, "checking…")}
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
        row = tk.Frame(outer, bg=p.chrome, padx=6, pady=7)
        row.pack(fill="x")

        def sep():
            tk.Frame(row, bg=p.separator, width=1, height=22).pack(side="left", padx=6)

        def button(**kw) -> BarButton:
            return BarButton(row, p, f["button"] if kw.get("kind") == "primary" else f["body"], **kw)

        # drag grip
        grip = tk.Canvas(row, width=14, height=32, bg=p.chrome, highlightthickness=0, cursor="fleur")
        for cx in (4, 10):
            for cy in (10, 16, 22):
                grip.create_oval(cx - 1.5, cy - 1.5, cx + 1.5, cy + 1.5, fill=p.text_faint, outline="")
        grip.pack(side="left", padx=(2, 4))
        Tooltip(grip, "Drag to move the bar")
        grip.bind("<ButtonPress-1>", self._drag_start)
        grip.bind("<B1-Motion>", self._drag)
        grip.bind("<ButtonRelease-1>", self._drag_end)

        self.prompt = PromptField(row, p, f["body"], 300, lambda: self.start("fill"), self.history_menu)
        self.prompt.pack(side="left", padx=(0, 4))
        self.presets_btn = button(icon="presets", command=self.presets_menu, tooltip="Presets")
        self.presets_btn.pack(side="left")
        self.ref_btn = button(icon="image", command=self.reference_menu)
        self.ref_btn.pack(side="left")
        self.count_btn = button(icon="stack", text=str(self.settings.variations), chevron=True,
                                command=self.count_menu, tooltip="How many results to choose from")
        self.count_btn.pack(side="left")
        sep()

        # actions: the ones that fit the current mode are shown
        self.rm_btn = button(icon="eraser", text="Remove", kind="outline", command=lambda: self.start("remove"),
                             tooltip="Remove what's selected (type what it is for better results)")
        self.exp_btn = button(icon="expand", text="Expand", kind="outline", chevron=True, command=self.expand_menu,
                              tooltip="Bigger canvas, new edges filled to match")
        self.up_btn = button(icon="upscale", text="Upscale", kind="outline", chevron=True, command=self.upscale_menu,
                             tooltip="Bigger copy with new detail, opens as a new document")
        self.gen_btn = button(icon="spark", text="Generate", kind="primary", command=self._generate_or_cancel)
        self.more_btn = button(icon="more", command=self.more_menu, tooltip="Settings, connection, version")
        self.actions = [self.rm_btn, self.exp_btn, self.up_btn, self.gen_btn]
        self.more_btn.pack(side="right", padx=(4, 0))
        if self.update_info:
            self.more_btn.set_on(False, badge=True)

        self.progress = tk.Canvas(outer, height=2, bg=p.chrome, highlightthickness=0)
        self.progress.pack(fill="x", side="bottom")

        if self.pill is not None:
            self.pill.destroy()
        self.pill = StatusPill(self.root, p, f["body"])
        self.prompt.set(self._prompt_text)
        self._show_reference()
        self._arrange()

    def _arrange(self) -> None:
        """Show the buttons that fit the mode and the busy state."""
        for b in self.actions:
            b.pack_forget()
        shown = [self.rm_btn] if self.mode == "selection" else [self.exp_btn, self.up_btn]
        for b in shown + [self.gen_btn]:
            b.pack(side="left", padx=(4, 0))
        self.prompt.set_placeholder(PLACEHOLDER[self.mode])
        if self.busy:
            self.gen_btn.configure_button(icon="x", text="Cancel", kind="outline", tooltip="Stop (Esc)")
        else:
            self.gen_btn.configure_button(icon="spark", text="Generate", kind="primary", tooltip=(
                "Fill the selection (Enter)" if self.mode == "selection" else "Change the whole image (Enter)"))
        for b in (self.rm_btn, self.exp_btn, self.up_btn, self.count_btn, self.presets_btn, self.ref_btn):
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
        if x is None or y is None or not (sx <= x <= sx + sw - 80 and sy <= y <= sy + sh - h):
            x, y = (self.root.winfo_screenwidth() - w) // 2, 90  # top centre of the main screen
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
            self.progress.create_rectangle(x, 0, x + 0.3 * w, 2, fill=self.p.accent, outline="")
            self._progress_after = self.root.after(16, step)
        step()

    def progress_set(self, fraction: float) -> None:
        self._stop_progress()
        self.progress.create_rectangle(0, 0, fraction * self.progress.winfo_width(), 2, fill=self.p.accent,
                                       outline="")

    def _stop_progress(self) -> None:
        if self._progress_after:
            self.root.after_cancel(self._progress_after)
            self._progress_after = None
        self.progress.delete("all")

    # -- menus -----------------------------------------------------------------------------------
    def _menu(self, anchor, items, right: bool = False) -> None:
        PopupMenu(anchor, items, self.p, self.fonts["body"], right=right)

    def history_menu(self) -> None:
        recent = Settings.load().recent_prompts
        items = [("header", "Recent prompts")]
        items += [("item", (t if len(t) <= 60 else t[:57] + "…"), lambda t=t: self._use_prompt(t)) for t in recent]
        if not recent:
            items.append(("info", "Prompts you use appear here"))
        self._menu(self.prompt, items)

    def presets_menu(self) -> None:
        items, groups = [("header", "Presets")], {}
        for name, _ in presets.PRESETS:
            group, _, label = name.partition(": ")
            if label:
                if group not in groups:
                    groups[group] = []
                    items.append(("sub", group, groups[group]))
                groups[group].append(("item", label[:1].upper() + label[1:], lambda n=name: self.use_preset(n)))
            else:
                items.append(("item", name, lambda n=name: self.use_preset(n)))
        self._menu(self.presets_btn, items)

    def count_menu(self) -> None:
        n = self.settings.variations
        items = [("header", "Results to choose from")]
        items += [("check", f"{i} result" + ("s" if i > 1 else " (placed right away)"),
                   lambda i=i: self._set_count(i), i == n) for i in range(1, MAX_VARIATIONS + 1)]
        self._menu(self.count_btn, items)

    def expand_menu(self) -> None:
        last = self.settings.expand_choice
        items = [("header", "Expand the canvas to")]
        items += [("check", c, lambda c=c: self.start("expand", choice=c), c == last) for c in EXPAND_CHOICES]
        self._menu(self.exp_btn, items)

    def upscale_menu(self) -> None:
        last = self.settings.upscale_factor
        items = [("header", "Upscale to a new document")]
        items += [("check", f"{k}× larger", lambda k=k: self.start("upscale", factor=k), k == last) for k in (2, 4)]
        self._menu(self.up_btn, items)

    def reference_menu(self) -> None:
        if not self.reference:
            self.choose_reference()
            return
        name = Path(self.reference).name
        self._menu(self.ref_btn, [("header", "Reference picture"),
                                  ("info", (name if len(name) <= 40 else name[:37] + "…")),
                                  ("info", 'Mention it as "image 2" in the prompt'), ("sep",),
                                  ("item", "Choose another…", self.choose_reference, "folder"),
                                  ("item", "Don't use a reference", self.clear_reference, "x")])

    def more_menu(self) -> None:
        items = [("header", "Connections")]
        for name, (ok, text) in self.conn.items():
            items.append(("info", f"●  {name}: {text}", OK if ok else BAD if ok is False else self.p.text_dim))
        items += [("sep",),
                  ("item", "Settings…", self.open_settings, "gear"),
                  ("item", "PhotoCraft connection…", self.setup, "plug")]
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
        items.append(("item", "Quit", self.quit, "x"))
        self._menu(self.more_btn, items, right=True)

    def _use_prompt(self, text: str) -> None:
        self.prompt.entry.focus_set()
        self.prompt.set(text)
        self.prompt.entry.icursor("end")

    def use_preset(self, name: str) -> None:
        text = presets.prompt_for(name)
        if text:
            self._use_prompt(text)

    def _set_count(self, n: int) -> None:
        s = Settings.load()
        s.variations = self.settings.variations = n
        s.save()
        self.count_btn.configure_button(text=str(n))

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
            problems = comfy.check_setup(self.settings.models)
            self.conn["ComfyUI"] = (not problems, "connected" if not problems else "models missing")
            msgs += ["ComfyUI: " + " ".join(problems)] if problems else []
        except ComfyError as e:
            self.conn["ComfyUI"] = (False, "not reachable")
            msgs.append(str(e))
        self.events.put(("connections", "\n".join(msgs)))
        if self.settings.check_updates:
            found = updates.newer_release()
            if found:
                self.events.put(("update", found))

    def watch(self) -> None:
        """Follow PhotoCraft's selection (and theme) so the bar shows the right buttons."""
        pc, n, last = None, 0, None
        while True:
            time.sleep(0.8)
            if self.busy:
                continue
            try:
                if pc is None:
                    if not all(setup_env.status(self.settings.port).values()):
                        time.sleep(3)
                        continue
                    pc = PhotoCraftClient(self.settings.port, Path(self.settings.token_file), timeout=5).__enter__()
                try:
                    mode = "selection" if pc.document().selection else "image"
                except PhotoCraftError as e:
                    if not str(e).startswith("PhotoCraft:"):
                        raise  # the connection went away
                    mode = "image"  # PhotoCraft answered with an error: no document open
                if mode != last:
                    self.events.put(("mode", mode))
                    last = mode
                n += 1
                if pc.mode == "desktop" and n % 5 == 0:
                    self.events.put(("theme", (pc.call("ui.inspect", {}) or {}).get("theme")))
            except (PhotoCraftError, OSError):
                if pc is not None:
                    pc.__exit__(None, None, None)
                pc, last = None, None
                time.sleep(3)

    def start(self, job: str, choice: str | None = None, factor: int | None = None) -> None:
        if self.busy:
            return
        self.settings = Settings.load()
        self.settings.last_theme = self.theme_name
        prompt = self.prompt.get() if job in ("fill", "remove") else ""  # the prompt belongs to Generate
        if job == "upscale":
            self.settings.upscale_factor = factor or self.settings.upscale_factor
        elif job == "expand":
            self.settings.expand_choice = choice or self.settings.expand_choice
        else:
            self.settings.remember_prompt(prompt)
        self.settings.save()  # last choices and prompt history are remembered
        self.busy = True
        self.cancel.clear()
        self._arrange()
        self.progress_busy()
        if job == "upscale":
            fn = run_upscale
            self.say(f"Upscaling {self.settings.upscale_factor}×…", None)
        elif job == "expand":
            shape = self.settings.expand_choice

            def fn(_prompt, settings, status, progress, cancel):
                return self._offer(expand(shape, "", settings, status, progress, cancel), settings, status)
            self.say(f"Expanding to {shape}…", None)
        else:
            remove, reference = job == "remove", self.reference

            def fn(*a):
                return self._generate(*a, remove=remove, reference=reference)
            self.say("Starting…", None)
        threading.Thread(target=self._work, args=(fn, prompt), daemon=True).start()

    def _generate_or_cancel(self) -> None:
        if self.busy:
            self.cancel.set()
            self.say("Stopping…", None)
        else:
            self.start("fill")

    def _generate(self, prompt, settings, status, progress, cancel, remove=False, reference=None):
        ref = load_reference(reference) if reference and not remove else None
        results = generate(prompt, settings, status, progress, cancel, reference=ref, remove=remove)
        return self._offer(results, settings, status)

    def _offer(self, results, settings, status):
        """Several results: let the user choose; one: place it straight away."""
        if len(results.images) > 1:
            self.events.put(("pick", results))
            return None  # the picker takes it from here
        status("Placing the result in PhotoCraft…")
        return place(results, 0, settings)

    def _place(self, results, index: int) -> None:
        self._work(lambda *_a: place(results, index, self.settings), "")

    def _similar(self, results, index: int) -> None:
        def fn(_prompt, settings, status, progress, cancel):
            more = similar(results, index, settings, status, progress, cancel)
            self.events.put(("pick", more))
        self._work(fn, "")

    def _work(self, fn, prompt: str) -> None:
        try:
            result = fn(prompt, self.settings, lambda s: self.events.put(("status", s)),
                        lambda f: self.events.put(("progress", f)), self.cancel)
            if result is not None:
                self.events.put(("done", (True, f"Done: {result}. (Ctrl+Z in PhotoCraft removes it.)")))
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
                elif kind == "theme":
                    self.set_theme(value)
                elif kind == "progress" and value is not None:
                    self.progress_set(value)
                elif kind == "update":
                    self.update_info = value
                    self.more_btn.set_on(False, badge=True)
                elif kind == "pick":
                    self.choose(value)
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

        def picked(i: int) -> None:
            self.say(f"Placing result {i + 1} in PhotoCraft…", None)
            self.progress_busy()
            threading.Thread(target=self._place, args=(results, i), daemon=True).start()

        def more(i: int) -> None:
            self.say(f"Making more like result {i + 1}…", None)
            self.progress_busy()
            threading.Thread(target=self._similar, args=(results, i), daemon=True).start()

        def discarded() -> None:
            self.say("Discarded. Nothing was added to PhotoCraft.", None, hide_after=4000)
            self.finish()
        Picker(self.root, results, self.p, self.fonts, picked, discarded,
               on_similar=more if results.job is not None else None)

    # -- reference picture -----------------------------------------------------------------------
    def choose_reference(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Reference picture",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("All files", "*.*")])
        if path:
            self.reference = path
            self._show_reference()
            self.say(f'Reference: {Path(path).name}. Mention it as "image 2" in the prompt.', None, hide_after=6000)

    def clear_reference(self) -> None:
        self.reference = None
        self._show_reference()

    def _show_reference(self) -> None:
        self.ref_btn.set_on(bool(self.reference), badge=bool(self.reference))
        self.ref_btn.configure_button(tooltip=f"Reference picture: {Path(self.reference).name}" if self.reference
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
            self.count_btn.configure_button(text=str(s.variations))
            self.say("Settings saved. Checking connections…", None)
            threading.Thread(target=self.check_connections, daemon=True).start()
        SettingsDialog(self.root, Settings.load(), self.p, self.fonts, saved)

    def quit(self) -> None:
        self.cancel.set()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
