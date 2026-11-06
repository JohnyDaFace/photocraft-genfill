"""The companion window: prompt, Generate, Upscale, status. Styled like PhotoCraft's active Pro theme.
Work runs on a thread; the UI polls a queue."""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, setup_env, theme, updates
from .comfy import ComfyClient, ComfyError
from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .picker import Picker
from .pipeline import MAX_VARIATIONS, UserFacingError, generate, load_reference, place, run_upscale
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
        self.reference: str | None = None   # path of the optional reference picture
        self.update_info: tuple[str, str] | None = None  # (version, url) when a newer release exists

        root.title(f"{TITLE} {__version__}")
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
        gen.columnconfigure(0, weight=1)
        tk.Label(gen, text="Prompt (optional when filling; for Remove, what to remove)", bg=p.chrome,
                 fg=p.text_dim, font=f["small"]).grid(row=0, column=0, columnspan=3, sticky="w")
        self.prompt = ttk.Combobox(gen, textvariable=self.prompt_text, style="PC.TCombobox", font=f["body"],
                                   values=self.settings.recent_prompts)
        self.prompt.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        self.prompt.bind("<Return>", lambda e: self.start("fill"))

        ref = tk.Frame(gen, bg=p.chrome)
        ref.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        tk.Label(ref, text="Reference", bg=p.chrome, fg=p.text_dim, font=f["small"]).pack(side="left")
        self.ref_clear = theme.PillButton(ref, "×", self.clear_reference, p, f["small"], kind="quiet",
                                          height=22, min_width=24)
        self.ref_clear.pack(side="right")
        theme.PillButton(ref, "Choose…", self.choose_reference, p, f["small"], height=22,
                         min_width=64).pack(side="right", padx=(0, 4))
        self.ref_label = tk.Label(ref, bg=p.chrome, font=f["small"], anchor="w")
        self.ref_label.pack(side="left", padx=(8, 8), fill="x", expand=True)
        self._show_reference()

        ttk.Combobox(gen, textvariable=self.variations, width=9, state="readonly", style="PC.TCombobox",
                     font=f["body"], values=[self._variations_label(n) for n in range(1, MAX_VARIATIONS + 1)]
                     ).grid(row=3, column=0, sticky="w")
        self.rm_btn = theme.PillButton(gen, "Remove", lambda: self.start("remove"), p, f["button"], min_width=80)
        self.rm_btn.grid(row=3, column=1, sticky="e", padx=(0, 8))
        self.gen_btn = theme.PillButton(gen, "Generate", lambda: self.start("fill"), p, f["button"], kind="primary",
                                        min_width=96)
        self.gen_btn.grid(row=3, column=2, sticky="e")
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
        if self.update_info:
            version, url = self.update_info
            theme.PillButton(bottom, f"Version {version} available", lambda: webbrowser.open(url), p, f["small"],
                             kind="primary", height=22).pack(side="left", padx=(8, 0))
        theme.PillButton(bottom, "Settings…", self.open_settings, p, f["small"], kind="quiet",
                         height=22, min_width=56).pack(side="right")
        theme.PillButton(bottom, "Setup PhotoCraft connection", self.setup, p, f["small"], kind="quiet",
                         height=22).pack(side="right", padx=(0, 4))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.gen_btn.set_enabled(not self.busy)
        self.rm_btn.set_enabled(not self.busy)
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
        if self.settings.check_updates:
            found = updates.newer_release()
            if found:
                self.events.put(("update", found))

    def start(self, job: str) -> None:
        if self.busy:
            return
        self.settings = Settings.load()
        self.settings.last_theme = self.theme_name
        if job == "upscale":
            self.settings.upscale_factor = int(self.factor.get().rstrip("×"))
        else:
            self.settings.variations = int(self.variations.get().split()[0])
            self.settings.remember_prompt(self.prompt_text.get())
            self.settings.save()  # results count and prompt history are remembered
            self.prompt.configure(values=self.settings.recent_prompts)
        self.busy = True
        self.cancel.clear()
        self._sync_buttons()
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)
        if job == "upscale":
            fn = run_upscale
        else:
            remove, reference = job == "remove", self.reference

            def fn(*a):
                return self._generate(*a, remove=remove, reference=reference)
        prompt = self.prompt_text.get() if job != "upscale" else ""  # the prompt belongs to Generate
        threading.Thread(target=self._work, args=(fn, prompt), daemon=True).start()

    @staticmethod
    def _variations_label(n: int) -> str:
        return f"{n} result" + ("s" if n > 1 else "")

    def _generate(self, prompt, settings, status, progress, cancel, remove=False, reference=None):
        ref = load_reference(reference) if reference and not remove else None
        results = generate(prompt, settings, status, progress, cancel, reference=ref, remove=remove)
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
                elif kind == "update":
                    self.update_info = value
                    self.build()
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

    # -- reference picture -----------------------------------------------------------------------
    def choose_reference(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Reference picture",
            filetypes=[("Pictures", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"), ("All files", "*.*")])
        if path:
            self.reference = path
            self._show_reference()

    def clear_reference(self) -> None:
        self.reference = None
        self._show_reference()

    def _show_reference(self) -> None:
        if self.reference:
            name = Path(self.reference).name
            name = name if len(name) <= 30 else name[:27] + "…"
            self.ref_label.configure(text=f'{name}  (call it "image 2")', fg=self.p.text)
        else:
            self.ref_label.configure(text="none (optional: an object or style to use)", fg=self.p.text_faint)
        self.ref_clear.set_enabled(bool(self.reference))

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
