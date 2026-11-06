"""The companion window: prompt, Generate, Upscale, status. Styled like PhotoCraft's active Pro theme.
Work runs on a thread; the UI polls a queue."""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from . import setup_env, theme
from .comfy import ComfyClient, ComfyError
from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .picker import Picker
from .pipeline import MAX_VARIATIONS, UserFacingError, generate, place, run_upscale
from .settings import SETTINGS_FILE, Settings
from .settings_dialog import SettingsDialog

TITLE = "Generative Fill for PhotoCraft"


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
        self.prompt_text = tk.StringVar()
        self.factor = tk.StringVar(value=f"{self.settings.upscale_factor}×")
        self.variations = tk.StringVar(value=self._variations_label(self.settings.variations))
        self.status = tk.StringVar(value="Checking connections…")

        root.title(TITLE)
        icon = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "icon.png"
        if icon.is_file():
            self._icon = tk.PhotoImage(file=str(icon)).subsample(16)  # 1024 → 64 px
            root.iconphoto(True, self._icon)
        root.attributes("-topmost", True)
        root.resizable(True, False)
        root.minsize(400, 0)
        self.fonts = theme.fonts(root)
        self.frame: tk.Frame | None = None
        self.build()
        self.root.after(100, self.poll)
        threading.Thread(target=self.check_connections, daemon=True).start()

    # -- layout --------------------------------------------------------------------------------
    def build(self) -> None:
        """(Re)build the widgets in the current theme's colours."""
        p = self.p = theme.palette_for(self.theme_name)
        f = self.fonts
        theme.style_ttk(self.root, p, f)
        self.root.configure(bg=p.chrome)
        theme.title_bar(self.root, p)
        if self.frame is not None:
            self.frame.destroy()
        fr = self.frame = tk.Frame(self.root, bg=p.chrome, padx=14, pady=12)
        fr.pack(fill="both", expand=True)
        fr.columnconfigure(0, weight=1)

        def section(row: int, title: str, hint: str) -> tk.Frame:
            head = tk.Frame(fr, bg=p.chrome)
            head.grid(row=row, column=0, sticky="ew")
            tk.Label(head, text=title.upper(), bg=p.chrome, fg=p.text, font=f["button"]).pack(side="left")
            tk.Label(head, text=hint, bg=p.chrome, fg=p.text_faint, font=f["small"]).pack(side="left", padx=(8, 0))
            body = tk.Frame(fr, bg=p.chrome)
            body.grid(row=row + 1, column=0, sticky="ew", pady=(6, 0))
            body.columnconfigure(0, weight=1)
            return body

        def rule(row: int) -> None:
            tk.Frame(fr, bg=p.separator, height=1).grid(row=row, column=0, sticky="ew", pady=12)

        # Generate: fill the selection, or edit the whole image when nothing is selected
        gen = section(0, "Generate", "fills the selection · nothing selected = edits the whole image")
        tk.Label(gen, text="Prompt (optional when filling)", bg=p.chrome, fg=p.text_dim,
                 font=f["small"]).grid(row=0, column=0, columnspan=2, sticky="w")
        self.prompt = ttk.Entry(gen, textvariable=self.prompt_text, style="PC.TEntry", font=f["body"])
        self.prompt.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        self.prompt.bind("<Return>", lambda e: self.start("fill"))
        ttk.Combobox(gen, textvariable=self.variations, width=9, state="readonly", style="PC.TCombobox",
                     font=f["body"], values=[self._variations_label(n) for n in range(1, MAX_VARIATIONS + 1)]
                     ).grid(row=2, column=0, sticky="w")
        self.gen_btn = theme.PillButton(gen, "Generate", lambda: self.start("fill"), p, f["button"], kind="primary",
                                        min_width=96)
        self.gen_btn.grid(row=2, column=1, sticky="e")
        rule(2)

        # Upscale: a bigger copy as a new document
        up = section(3, "Upscale", "bigger copy with new detail, opens as a new document")
        tk.Label(up, text="Size", bg=p.chrome, fg=p.text_dim, font=f["small"]).grid(row=0, column=0, sticky="w")
        ttk.Combobox(up, textvariable=self.factor, values=["2×", "4×"], width=4, state="readonly",
                     style="PC.TCombobox", font=f["body"]).grid(row=0, column=0, sticky="w", padx=(34, 0))
        self.up_btn = theme.PillButton(up, "Upscale", lambda: self.start("upscale"), p, f["button"], min_width=96)
        self.up_btn.grid(row=0, column=1, sticky="e")
        rule(5)

        self.bar = ttk.Progressbar(fr, mode="determinate", value=0, style="PC.Horizontal.TProgressbar")
        self.bar.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        self.status_label = tk.Label(fr, textvariable=self.status, bg=p.chrome, fg=p.text_dim, font=f["small"],
                                     justify="left", anchor="w", wraplength=370)
        self.status_label.grid(row=7, column=0, sticky="ew")
        fr.bind("<Configure>", lambda e: self.status_label.configure(wraplength=max(200, e.width - 30)))

        tk.Frame(fr, bg=p.separator, height=1).grid(row=8, column=0, sticky="ew", pady=(12, 8))
        bottom = tk.Frame(fr, bg=p.chrome)
        bottom.grid(row=9, column=0, sticky="ew")
        self.cancel_btn = theme.PillButton(bottom, "Cancel", self.cancel.set, p, f["small"], kind="quiet",
                                           height=22, min_width=56)
        self.cancel_btn.pack(side="left")
        theme.PillButton(bottom, "Settings…", self.open_settings, p, f["small"], kind="quiet",
                         height=22, min_width=56).pack(side="right")
        theme.PillButton(bottom, "Setup PhotoCraft connection", self.setup, p, f["small"], kind="quiet",
                         height=22).pack(side="right", padx=(0, 4))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.gen_btn.set_enabled(not self.busy)
        self.up_btn.set_enabled(not self.busy)
        self.cancel_btn.set_enabled(self.busy)

    def set_theme(self, name: str) -> None:
        if name and name != self.theme_name:
            self.theme_name = name
            self.settings.last_theme = name
            self.settings.save()
            self.build()

    # -- background work -----------------------------------------------------------------------
    def check_connections(self) -> None:
        msgs = []
        if not all(setup_env.status(self.settings.port).values()):
            msgs.append("PhotoCraft connection isn't set up yet: click 'Setup PhotoCraft connection'.")
        else:
            try:
                with PhotoCraftClient(self.settings.port, Path(self.settings.token_file), timeout=10) as pc:
                    problems = pc.compatibility()
                    if pc.mode == "desktop":
                        try:
                            self.events.put(("theme", (pc.call("ui.inspect", {}) or {}).get("theme")))
                        except PhotoCraftError:
                            pass
                msgs.append("PhotoCraft: connected." if not problems else
                            "PhotoCraft version not compatible: " + " ".join(problems))
            except PhotoCraftError as e:
                msgs.append(str(e))
        try:
            comfy = ComfyClient(self.settings.comfy_url)
            comfy.check()
            problems = comfy.check_setup(self.settings.models)
            msgs.append("ComfyUI: connected." if not problems else "ComfyUI: " + " ".join(problems))
        except ComfyError as e:
            msgs.append(str(e))
        self.events.put(("status", "\n".join(msgs)))

    def start(self, job: str) -> None:
        if self.busy:
            return
        self.settings = Settings.load()
        self.settings.last_theme = self.theme_name
        if job == "upscale":
            self.settings.upscale_factor = int(self.factor.get().rstrip("×"))
        else:
            n = int(self.variations.get().split()[0])
            if n != self.settings.variations:
                self.settings.variations = n
                self.settings.save()  # remembered for next time
        self.busy = True
        self.cancel.clear()
        self._sync_buttons()
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        fn = self._generate if job == "fill" else run_upscale
        prompt = self.prompt_text.get() if job == "fill" else ""  # the prompt belongs to Generate
        threading.Thread(target=self._work, args=(fn, prompt), daemon=True).start()

    @staticmethod
    def _variations_label(n: int) -> str:
        return f"{n} result" + ("s" if n > 1 else "")

    def _generate(self, prompt, settings, status, progress, cancel):
        results = generate(prompt, settings, status, progress, cancel)
        if len(results.images) > 1:
            self.events.put(("pick", results))
            return None  # the picker takes it from here
        status("Placing the result in PhotoCraft…")
        return place(results, 0, settings)

    def _place(self, results, index: int) -> None:
        self._work(lambda *_a: place(results, index, self.settings), "")

    def _work(self, fn, prompt: str) -> None:
        try:
            result = fn(prompt, self.settings, lambda s: self.events.put(("status", s)),
                        lambda f: self.events.put(("progress", f)), self.cancel)
            if result is not None:
                self.events.put(("done", f"Done: {result}. (Ctrl+Z in PhotoCraft removes it.)"))
        except UserFacingError as e:
            self.events.put(("done", str(e)))
        except Exception as e:  # never die silently
            self.events.put(("done", f"Unexpected error: {e!r}"))

    def poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(value)
                elif kind == "theme":
                    self.set_theme(value)
                elif kind == "progress" and value is not None:
                    self.bar.stop()
                    self.bar.configure(mode="determinate", value=value * 100)
                elif kind == "pick":
                    self.choose(value)
                elif kind == "done":
                    self.status.set(value)
                    self.finish()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def finish(self) -> None:
        self.busy = False
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0)
        self._sync_buttons()

    def choose(self, results) -> None:
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100)
        self.status.set(f"{len(results.images)} results ready: pick one in the 'Choose a result' window.")

        def picked(i: int) -> None:
            self.status.set(f"Placing result {i + 1} in PhotoCraft…")
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)
            threading.Thread(target=self._place, args=(results, i), daemon=True).start()

        def discarded() -> None:
            self.status.set("Discarded. Nothing was added to PhotoCraft.")
            self.finish()
        Picker(self.root, results, self.p, self.fonts, picked, discarded)

    # -- buttons -------------------------------------------------------------------------------
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
            self.status.set("Settings saved. Checking connections…")
            threading.Thread(target=self.check_connections, daemon=True).start()
        SettingsDialog(self.root, Settings.load(), self.p, self.fonts, saved)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
