"""Settings window (PhotoCraft-styled): ComfyUI address, model files, quality, PhotoCraft connection,
and the compatibility check. Slow calls run on threads; results come back through `after()`."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import __version__, setup_env, theme
from .comfy import MODEL_SLOTS, ComfyClient, ComfyError
from .compat import run_checks
from .settings import Settings, find_photocraft_cli

LABELS = {"unet": "Flux 2 Klein model", "clip": "Text encoder", "vae": "VAE"}


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Tk, settings: Settings, palette: theme.Palette, fonts: dict, on_save):
        super().__init__(master)
        self.s, self.p, self.f, self.on_save = settings, palette, fonts, on_save
        self.title("Settings – Generative Fill for PhotoCraft")
        self.configure(bg=palette.chrome)
        self.attributes("-topmost", True)
        self.resizable(True, False)
        self.minsize(520, 0)
        self.transient(master)
        theme.title_bar(self, palette)
        p, f = palette, fonts
        fr = tk.Frame(self, bg=p.chrome, padx=16, pady=14)
        fr.pack(fill="both", expand=True)
        fr.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.StringVar] = {}
        row = 0

        def label(text, r, dim=False):
            tk.Label(fr, text=text, bg=p.chrome, fg=p.text_faint if dim else p.text_dim, font=f["small"]).grid(
                row=r, column=0, sticky="w", padx=(0, 12), pady=4)

        def heading(text, r):
            tk.Label(fr, text=text, bg=p.chrome, fg=p.text, font=f["button"]).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(10 if r else 0, 4))

        def entry(key, value, r, width=None):
            v = self.vars[key] = tk.StringVar(value=str(value))
            ttk.Entry(fr, textvariable=v, style="PC.TEntry", font=f["body"], width=width).grid(
                row=r, column=1, sticky="ew", pady=4)

        heading("ComfyUI", row); row += 1
        label("Address", row); entry("comfy_url", settings.comfy_url, row)
        theme.PillButton(fr, "Test", self.test_comfy, p, f["small"], height=24, min_width=60).grid(
            row=row, column=2, padx=(8, 0)); row += 1
        self.combos = {}
        for slot in MODEL_SLOTS:
            label(LABELS[slot], row)
            v = self.vars[slot] = tk.StringVar(value=settings.models.get(slot, ""))
            cb = ttk.Combobox(fr, textvariable=v, style="PC.TCombobox", font=f["body"])
            cb.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            self.combos[slot] = cb
            row += 1

        heading("Quality", row); row += 1
        label("Fill steps (4 = fast, 8 = finer)", row); entry("steps", settings.steps, row, width=6); row += 1
        label("Upscale strength (0.2 subtle – 0.4 strong)", row)
        entry("upscale_denoise", settings.upscale_denoise, row, width=6); row += 1

        heading("PhotoCraft", row); row += 1
        label("Control port", row); entry("port", settings.port, row, width=8); row += 1
        label("photocraft-cli (empty = find it)", row); entry("photocraft_cli", settings.photocraft_cli, row)
        row += 1
        tk.Label(fr, text=f"Found: {find_photocraft_cli()}", bg=p.chrome, fg=p.text_faint, font=f["small"]).grid(
            row=row, column=1, columnspan=2, sticky="w"); row += 1

        heading("Updates", row); row += 1
        self.check_updates = tk.BooleanVar(value=settings.check_updates)
        tk.Checkbutton(fr, text=f"Check for a new version at startup (this is {__version__})",
                       variable=self.check_updates, bg=p.chrome, fg=p.text_dim, activebackground=p.chrome,
                       activeforeground=p.text, selectcolor=p.field, font=f["small"], bd=0,
                       highlightthickness=0).grid(row=row, column=0, columnspan=3, sticky="w"); row += 1

        self.msg = tk.StringVar()
        tk.Label(fr, textvariable=self.msg, bg=p.chrome, fg=p.text_dim, font=f["small"], justify="left",
                 anchor="w", wraplength=480).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        row += 1
        tk.Frame(fr, bg=p.separator, height=1).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10); row += 1
        bottom = tk.Frame(fr, bg=p.chrome)
        bottom.grid(row=row, column=0, columnspan=3, sticky="ew")
        theme.PillButton(bottom, "Check compatibility", self.check_compat, p, f["small"], kind="quiet",
                         height=24).pack(side="left")
        theme.PillButton(bottom, "Undo PhotoCraft setup", self.undo_setup, p, f["small"], kind="quiet",
                         height=24).pack(side="left", padx=(4, 0))
        theme.PillButton(bottom, "Save", self.save, p, f["button"], kind="primary", min_width=80).pack(side="right")
        theme.PillButton(bottom, "Cancel", self.destroy, p, f["button"], min_width=80).pack(side="right", padx=8)
        self.load_models()

    # -- ComfyUI ---------------------------------------------------------------------------------
    def load_models(self) -> None:
        url = self.vars["comfy_url"].get().strip()
        self.msg.set("Asking ComfyUI which model files it has…")

        def work():
            try:
                c = ComfyClient(url)
                have = c.available_models()
                problems = c.check_setup({slot: self.vars[slot].get() for slot in MODEL_SLOTS})
                self.after(0, lambda: self._models_loaded(have, problems))
            except ComfyError as e:
                self.after(0, lambda: self.msg.set(str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _models_loaded(self, have: dict, problems: list[str]) -> None:
        for slot, cb in self.combos.items():
            cb.configure(values=have.get(slot, []))
        self.msg.set("\n".join(problems) if problems else "ComfyUI is ready: all three model files were found.")

    def test_comfy(self) -> None:
        self.load_models()

    # -- PhotoCraft ------------------------------------------------------------------------------
    def check_compat(self) -> None:
        win = tk.Toplevel(self)
        win.title("PhotoCraft compatibility")
        win.configure(bg=self.p.chrome)
        win.attributes("-topmost", True)
        theme.title_bar(win, self.p)
        text = tk.Text(win, width=78, height=22, bg=self.p.field, fg=self.p.text, font=self.f["body"],
                       relief="flat", padx=10, pady=8, insertbackground=self.p.text)
        text.pack(fill="both", expand=True, padx=12, pady=12)
        text.insert("end", "Running… (a test tab may open and close in PhotoCraft; your documents are not touched)\n\n")
        settings = self._collect() or self.s

        def out(line: str) -> None:
            self.after(0, lambda: (text.insert("end", line + "\n"), text.see("end")))
        threading.Thread(target=lambda: run_checks(settings, out), daemon=True).start()

    def undo_setup(self) -> None:
        if not messagebox.askyesno("Undo PhotoCraft setup",
                                   "Remove the connection settings that let this window talk to PhotoCraft? "
                                   "PhotoCraft itself is not changed. You can set it up again any time.",
                                   parent=self):
            return
        setup_env.uninstall()
        self.msg.set("Removed. Restart PhotoCraft for it to take effect. You can now delete this app.")

    # -- save ------------------------------------------------------------------------------------
    def _collect(self) -> Settings | None:
        try:
            s = Settings(**{**self.s.__dict__})
            s.comfy_url = self.vars["comfy_url"].get().strip() or s.comfy_url
            s.models = {slot: self.vars[slot].get().strip() for slot in MODEL_SLOTS}
            s.steps = max(1, min(50, int(self.vars["steps"].get())))
            s.upscale_denoise = max(0.05, min(0.8, float(self.vars["upscale_denoise"].get())))
            s.port = max(1, min(65535, int(self.vars["port"].get())))
            s.photocraft_cli = self.vars["photocraft_cli"].get().strip()
            s.check_updates = bool(self.check_updates.get())
            return s
        except ValueError:
            self.msg.set("Steps and port must be whole numbers, strength a number like 0.3.")
            return None

    def save(self) -> None:
        s = self._collect()
        if s is None:
            return
        s.save()
        self.on_save(s)
        self.destroy()
