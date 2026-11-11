"""Settings window (PhotoCraft-styled): ComfyUI address, model files, quality, PhotoCraft connection,
and the compatibility check. Slow calls run on threads; results come back through `after()`."""
from __future__ import annotations

import threading
import math
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, custom_workflow, photoshop_install, setup_env, theme
from .comfy import MODEL_SLOTS, ComfyClient, ComfyError
from .families import FAMILIES, family
from .dpi import px
from .compat import run_checks
from .settings import CONFIG_DIR, Settings, find_photocraft_cli

LABELS = {"unet": "Model file", "clip": "Text encoder", "vae": "VAE"}


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Tk, settings: Settings, palette: theme.Palette, fonts: dict, on_save,
                 on_restart=None):
        super().__init__(master)
        self.s, self.p, self.f, self.on_save = settings, palette, fonts, on_save
        self.work = Settings(**{**settings.__dict__, "models": dict(settings.models), "loras": list(settings.loras),
                                "other_families": dict(settings.other_families),
                                "saved_workflows": [dict(w) for w in settings.saved_workflows]})
        self.saved_workflows = self.work.saved_workflows
        self.pending_workflows: dict[str, dict] = {}
        self.active_workflow_id = settings.active_workflow_id
        self.title("Settings – Generative Fill for PhotoCraft")
        self.configure(bg=palette.chrome)
        self.attributes("-topmost", True)
        self.resizable(True, False)
        self.minsize(px(520), 0)
        self.transient(master)
        theme.title_bar(self, palette)
        p, f = palette, fonts
        fr = tk.Frame(self, bg=p.chrome, padx=px(16), pady=px(14))
        fr.pack(fill="both", expand=True)
        fr.columnconfigure(1, weight=1)
        self.vars: dict[str, tk.StringVar] = {}
        row = 0

        def label(text, r, dim=False):
            tk.Label(fr, text=text, bg=p.chrome, fg=p.text_faint if dim else p.text_dim, font=f["small"]).grid(
                row=r, column=0, sticky="w", padx=(0, px(12)), pady=px(4))

        def heading(text, r):
            tk.Label(fr, text=text, bg=p.chrome, fg=p.text, font=f["button"]).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(px(10) if r else 0, px(4)))

        def entry(key, value, r, width=None):
            v = self.vars[key] = tk.StringVar(value=str(value))
            ttk.Entry(fr, textvariable=v, style="PC.TEntry", font=f["body"], width=width).grid(
                row=r, column=1, sticky="ew", pady=px(4))

        heading("ComfyUI", row); row += 1
        label("Address", row); entry("comfy_url", settings.comfy_url, row)
        theme.PillButton(fr, "Test", self.test_comfy, p, f["small"], height=24, min_width=60).grid(
            row=row, column=2, padx=(px(8), 0)); row += 1
        label("Image model", row)
        self.family_var = tk.StringVar(value=family(settings.model_family).label)
        fam_cb = ttk.Combobox(fr, textvariable=self.family_var, style="PC.TCombobox", font=f["body"], state="readonly",
                              values=[fam.label for fam in FAMILIES.values()])
        self.family_box = fam_cb
        fam_cb.grid(row=row, column=1, columnspan=2, sticky="ew", pady=px(4))
        fam_cb.bind("<<ComboboxSelected>>", lambda e: self._switch_family())
        row += 1
        self.combos = {}
        for slot in MODEL_SLOTS:
            label(LABELS[slot], row)
            v = self.vars[slot] = tk.StringVar(value=settings.models.get(slot, ""))
            cb = ttk.Combobox(fr, textvariable=v, style="PC.TCombobox", font=f["body"])
            cb.grid(row=row, column=1, columnspan=2, sticky="ew", pady=px(4))
            self.combos[slot] = cb
            row += 1

        label("Model LoRAs", row)
        self.lora_frame = tk.Frame(fr, bg=p.chrome)
        self.lora_frame.grid(row=row, column=1, columnspan=2, sticky="ew")
        self.lora_frame.columnconfigure(0, weight=1)
        self.lora_choices = ["None"]
        self.lora_names: list[tk.StringVar] = []
        self.lora_strengths: list[tk.StringVar] = []
        self.lora_boxes: list[ttk.Combobox] = []
        self._lora_edited = False
        self._show_loras()
        row += 1
        tk.Label(fr, text="Choose from ComfyUI; None turns these off. Your own workflow and Replace face keep theirs.",
                 bg=p.chrome, fg=p.text_faint, font=f["small"], anchor="w").grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(0, px(4)))
        row += 1

        # Your own workflow for Generate and Create (custom_workflow.py)
        label("Your workflow", row)
        own_row = tk.Frame(fr, bg=p.chrome)
        own_row.grid(row=row, column=1, columnspan=2, sticky="ew", pady=px(4))
        self.own_path = settings.custom_workflow
        self.own_wf = custom_workflow.load_saved(CONFIG_DIR) if settings.custom_workflow else None
        if self.own_wf is None and self.active_workflow_id:
            self.own_wf = custom_workflow.load_saved(CONFIG_DIR, self.active_workflow_id)
        if self.own_wf is not None and not any(w["id"] == self.active_workflow_id for w in self.saved_workflows):
            self.active_workflow_id = uuid.uuid4().hex  # migrate the older single-workflow setting
            self.saved_workflows.append({"id": self.active_workflow_id, "name": Path(self.own_path).stem,
                                         "source": self.own_path, "slots": dict(settings.custom_slots)})
            self.pending_workflows[self.active_workflow_id] = self.own_wf
        self._refresh_model_choices()
        self.own_name = tk.StringVar()
        tk.Label(own_row, textvariable=self.own_name, bg=p.chrome, fg=p.text, font=f["body"], anchor="w").pack(
            side="left", fill="x", expand=True)
        theme.PillButton(own_row, "Clear", self.clear_workflow, p, f["small"], kind="quiet", height=24).pack(
            side="right", padx=(px(4), 0))
        theme.PillButton(own_row, "Choose…", self.choose_workflow, p, f["small"], height=24).pack(side="right")
        row += 1
        self.own_frame = tk.Frame(fr, bg=p.chrome)
        self.own_frame.grid(row=row, column=1, columnspan=2, sticky="ew")
        self.own_frame.columnconfigure(1, weight=1)
        self.slot_vars, self.slot_boxes, self.slot_ids = {}, {}, {}
        for i, slot in enumerate(custom_workflow.SLOTS):
            tk.Label(self.own_frame, text=custom_workflow.SLOT_LABELS[slot], bg=p.chrome, fg=p.text_faint,
                     font=f["small"]).grid(row=i, column=0, sticky="w", padx=(0, px(8)), pady=px(2))
            v = self.slot_vars[slot] = tk.StringVar()
            cb = ttk.Combobox(self.own_frame, textvariable=v, style="PC.TCombobox", font=f["small"], state="readonly")
            cb.grid(row=i, column=1, sticky="ew", pady=px(2))
            self.slot_boxes[slot] = cb
        self.slot_boxes["result"].bind("<<ComboboxSelected>>", lambda _: self._refresh_extra_results())
        option_row = len(custom_workflow.SLOTS)
        self.keep_seeds_var = tk.BooleanVar(value=settings.custom_keep_seeds)
        self.native_size_var = tk.BooleanVar(value=settings.custom_native_size)
        for offset, (title, var) in enumerate((
            ("Keep workflow seeds (repeated runs may match)", self.keep_seeds_var),
            ("Keep workflow output size (opens a new document if needed)", self.native_size_var),
        )):
            tk.Checkbutton(self.own_frame, text=title, variable=var, bg=p.chrome, fg=p.text,
                           activebackground=p.chrome, activeforeground=p.text, selectcolor=p.field,
                           font=f["small"], bd=0, highlightthickness=0).grid(
                row=option_row + offset, column=0, columnspan=2, sticky="w", pady=px(2))
        tk.Label(self.own_frame, text="Additional outputs", bg=p.chrome, fg=p.text_faint,
                 font=f["small"]).grid(row=option_row + 2, column=0, sticky="nw", pady=px(2))
        self.extra_results = tk.Listbox(self.own_frame, height=3, selectmode="multiple", exportselection=False,
                                        bg=p.field, fg=p.text, selectbackground=p.accent, bd=0,
                                        font=f["small"])
        self.extra_results.grid(row=option_row + 2, column=1, sticky="ew", pady=px(2))
        self.extra_result_ids: list[str] = []
        tk.Label(self.own_frame, text="Used by Generate and Create. Everything else keeps the image model above.",
                 bg=p.chrome, fg=p.text_faint, font=f["small"], anchor="w").grid(
            row=option_row + 3, column=0, columnspan=2, sticky="w", pady=(px(2), px(4)))
        self._show_workflow(settings.custom_slots)
        row += 1

        heading("Quality", row); row += 1
        self.steps_label = tk.Label(fr, text=self._steps_text(), bg=p.chrome, fg=p.text_dim, font=f["small"])
        self.steps_label.grid(row=row, column=0, sticky="w", padx=(0, px(12)), pady=px(4))
        entry("steps", settings.steps, row, width=6); row += 1
        label("Upscale strength (0.2 subtle – 0.4 strong)", row)
        entry("upscale_denoise", settings.upscale_denoise, row, width=6); row += 1

        heading("PhotoCraft", row); row += 1
        label("Control port", row); entry("port", settings.port, row, width=8); row += 1
        label("photocraft-cli (empty = find it)", row); entry("photocraft_cli", settings.photocraft_cli, row)
        row += 1
        tk.Label(fr, text=f"Found: {find_photocraft_cli()}", bg=p.chrome, fg=p.text_faint, font=f["small"]).grid(
            row=row, column=1, columnspan=2, sticky="w"); row += 1

        heading("Photoshop", row); row += 1
        tk.Label(fr, text="Install the Photoshop plugin through Adobe Creative Cloud, then copy this pairing token into its panel.",
                 bg=p.chrome, fg=p.text_faint, font=f["small"], anchor="w", justify="left",
                 wraplength=px(470)).grid(row=row, column=0, columnspan=3, sticky="w", pady=px(2)); row += 1
        photoshop_buttons = tk.Frame(fr, bg=p.chrome)
        photoshop_buttons.grid(row=row, column=0, columnspan=3, sticky="w", pady=px(3))
        theme.PillButton(photoshop_buttons, "Install Photoshop plugin", self.install_photoshop, p, f["small"],
                         height=24).pack(side="left")
        theme.PillButton(photoshop_buttons, "Copy pairing token", self.copy_photoshop_token, p, f["small"],
                         height=24).pack(side="left", padx=(px(8), 0))
        row += 1

        heading("Updates", row); row += 1
        self.check_updates = tk.BooleanVar(value=settings.check_updates)
        tk.Checkbutton(fr, text=f"Check for a new version at startup (this is {__version__})",
                       variable=self.check_updates, bg=p.chrome, fg=p.text_dim, activebackground=p.chrome,
                       activeforeground=p.text, selectcolor=p.field, font=f["small"], bd=0,
                       highlightthickness=0).grid(row=row, column=0, columnspan=3, sticky="w"); row += 1

        self.msg = tk.StringVar()
        tk.Label(fr, textvariable=self.msg, bg=p.chrome, fg=p.text_dim, font=f["small"], justify="left",
                 anchor="w", wraplength=px(480)).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(px(10), 0))
        row += 1
        tk.Frame(fr, bg=p.separator, height=1).grid(row=row, column=0, columnspan=3, sticky="ew", pady=px(10)); row += 1
        bottom = tk.Frame(fr, bg=p.chrome)
        bottom.grid(row=row, column=0, columnspan=3, sticky="ew")
        theme.PillButton(bottom, "Check compatibility", self.check_compat, p, f["small"], kind="quiet",
                         height=24).pack(side="left")
        theme.PillButton(bottom, "Undo PhotoCraft setup", self.undo_setup, p, f["small"], kind="quiet",
                         height=24).pack(side="left", padx=(px(4), 0))
        if on_restart:
            theme.PillButton(bottom, "Restart GenFill", lambda: (self.destroy(), on_restart()), p, f["small"],
                             kind="quiet", height=24).pack(side="left", padx=(px(4), 0))
        theme.PillButton(bottom, "Save", self.save, p, f["button"], kind="primary", min_width=80).pack(side="right")
        theme.PillButton(bottom, "Cancel", self.destroy, p, f["button"], min_width=80).pack(side="right", padx=px(8))
        self.load_models()

    # -- ComfyUI ---------------------------------------------------------------------------------
    def load_models(self) -> None:
        url = self.vars["comfy_url"].get().strip()
        self.msg.set("Asking ComfyUI which model files it has…")

        def work():
            try:
                c = ComfyClient(url)
                have = c.available_models()
                try:
                    loras = c.available_loras()
                except ComfyError:
                    loras = []
                problems = c.check_setup({**{slot: self.vars[slot].get() for slot in MODEL_SLOTS},
                                          "family": self.work.model_family})
                self.after(0, lambda: self._models_loaded(have, problems, loras))
            except ComfyError as e:
                self.after(0, lambda: self.msg.set(str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _models_loaded(self, have: dict, problems: list[str], loras: list[str] = ()) -> None:
        for slot, cb in self.combos.items():
            cb.configure(values=have.get(slot, []))
        self.lora_choices = ["None", *loras]
        for name, cb in zip(self.lora_names, self.lora_boxes):
            cb.configure(values=list(dict.fromkeys([*self.lora_choices, name.get()])))
        self.msg.set("\n".join(problems) if problems else "ComfyUI is ready: all three model files were found.")

    def _steps_text(self) -> str:
        return f"Steps ({family(self.work.model_family).steps_hint})"

    def _show_loras(self) -> None:
        """Show the selected model's LoRAs, or an empty picker when they are paused."""
        rows = []
        if self.work.use_loras:
            for lora in self.work.loras:
                rows.append((lora if isinstance(lora, str) else lora.get("name", ""),
                             1.0 if isinstance(lora, str) else lora.get("strength", 1.0)))
        self._render_loras(rows or [("None", 1.0)])
        self._lora_edited = False

    def _render_loras(self, rows) -> None:
        for child in self.lora_frame.winfo_children():
            child.destroy()
        self.lora_names = []
        self.lora_strengths = []
        self.lora_boxes = []
        for row, (name, strength) in enumerate(rows):
            selected = tk.StringVar(value=name or "None")
            selected.trace_add("write", lambda *_: setattr(self, "_lora_edited", True))
            self.lora_names.append(selected)
            cb = ttk.Combobox(self.lora_frame, textvariable=selected, style="PC.TCombobox",
                              font=self.f["body"], state="readonly",
                              values=list(dict.fromkeys([*self.lora_choices, selected.get()])))
            cb.grid(row=row, column=0, sticky="ew", padx=(0, px(8)), pady=px(2))
            self.lora_boxes.append(cb)
            value = tk.StringVar(value=str(strength))
            self.lora_strengths.append(value)
            ttk.Entry(self.lora_frame, textvariable=value, style="PC.TEntry", font=self.f["body"],
                      width=6).grid(row=row, column=1, sticky="e", pady=px(2))
            theme.PillButton(self.lora_frame, "×", lambda i=row: self._remove_lora(i), self.p,
                             self.f["small"], kind="quiet", height=24).grid(row=row, column=2, padx=(px(4), 0))
        theme.PillButton(self.lora_frame, "Add LoRA", self._add_lora, self.p, self.f["small"],
                         kind="quiet", height=24).grid(row=len(rows), column=0, sticky="w", pady=px(2))

    def _add_lora(self) -> None:
        rows = [(name.get(), strength.get()) for name, strength in zip(self.lora_names, self.lora_strengths)]
        if len(rows) == 1 and rows[0][0] == "None":
            return  # the first picker is already empty
        self._render_loras([*rows, ("None", 1.0)])
        self._lora_edited = True

    def _remove_lora(self, index: int) -> None:
        rows = [(name.get(), strength.get()) for name, strength in zip(self.lora_names, self.lora_strengths)]
        rows.pop(index)
        self._render_loras(rows or [("None", 1.0)])
        self._lora_edited = True

    def _read_loras(self) -> list[dict]:
        """Read selected files and strengths, preserving a paused legacy configuration until edited."""
        if not self._lora_edited and not self.work.use_loras:
            return [{"name": lora, "strength": 1.0} if isinstance(lora, str) else dict(lora)
                    for lora in self.work.loras]
        result = []
        for name_var, value in zip(self.lora_names, self.lora_strengths):
            name = name_var.get().strip()
            if name in ("", "None"):
                continue
            try:
                strength = float(value.get())
            except ValueError as e:
                raise ValueError("LoRA strength must be a number from 0 to 2.") from e
            if not math.isfinite(strength) or not 0 <= strength <= 2:
                raise ValueError("LoRA strength must be a number from 0 to 2.")
            result.append({"name": name, "strength": strength})
        return result

    def _switch_family(self) -> None:
        """Choose a built-in image model or a saved custom workflow."""
        workflow_id = self.workflow_labels.get(self.family_var.get())
        if workflow_id:
            self._remember_active_workflow()
            self._activate_workflow(workflow_id)
            return
        name = next((k for k, fam in FAMILIES.items() if fam.label == self.family_var.get()), None)
        if name is None:
            return
        if name == self.work.model_family:
            self._remember_active_workflow()
            self.active_workflow_id, self.own_path, self.own_wf = "", "", None
            self._show_workflow()
            return
        try:
            self.work.loras = self._read_loras()
            if self._lora_edited:
                self.work.use_loras = bool(self.work.loras)
        except ValueError as e:
            self.msg.set(str(e))
            self._refresh_model_choices()
            return
        self._remember_active_workflow()
        self.active_workflow_id, self.own_path, self.own_wf = "", "", None
        self._show_workflow()
        self.work.models = {slot: self.vars[slot].get().strip() for slot in MODEL_SLOTS}
        try:
            self.work.steps = max(1, min(50, int(self.vars["steps"].get())))
        except ValueError:
            pass
        self.work.switch_family(name)
        for slot in MODEL_SLOTS:
            self.vars[slot].set(self.work.models.get(slot, ""))
        self.vars["steps"].set(str(self.work.steps))
        self.steps_label.configure(text=self._steps_text())
        self._show_loras()
        self.load_models()

    def _refresh_model_choices(self) -> None:
        self.workflow_labels = {f"Workflow: {w['name']}": w["id"] for w in self.saved_workflows}
        self.family_box.configure(values=[*[fam.label for fam in FAMILIES.values()], *self.workflow_labels])
        selected = next((label for label, ident in self.workflow_labels.items()
                         if ident == self.active_workflow_id), family(self.work.model_family).label)
        self.family_var.set(selected)

    def _remember_active_workflow(self) -> None:
        if self.active_workflow_id and self.own_wf is not None:
            entry = next((w for w in self.saved_workflows if w["id"] == self.active_workflow_id), None)
            if entry is not None:
                entry["slots"] = self._slots()
                entry["keep_seeds"] = self.keep_seeds_var.get()
                entry["native_size"] = self.native_size_var.get()
                entry["result_nodes"] = self._extra_result_nodes()

    def _activate_workflow(self, workflow_id: str) -> None:
        entry = next((w for w in self.saved_workflows if w["id"] == workflow_id), None)
        if entry is None:
            return
        wf = self.pending_workflows.get(workflow_id) or custom_workflow.load_saved(CONFIG_DIR, workflow_id)
        if wf is None:
            self.msg.set(f"Saved workflow '{entry['name']}' is missing. Choose its file again.")
            self._refresh_model_choices()
            return
        self.active_workflow_id = workflow_id
        self.own_path, self.own_wf = entry["source"], wf
        self.keep_seeds_var.set(bool(entry.get("keep_seeds", False)))
        self.native_size_var.set(bool(entry.get("native_size", False)))
        self.work.custom_result_nodes = list(entry.get("result_nodes", []))
        self._show_workflow(entry.get("slots"))
        self._refresh_model_choices()

    def test_comfy(self) -> None:
        self.load_models()

    def install_photoshop(self) -> None:
        try:
            photoshop_install.open_installer()
            self.msg.set("Adobe Creative Cloud will ask you to install the Photoshop plugin.")
        except (OSError, FileNotFoundError) as exc:
            self.msg.set(str(exc))

    def copy_photoshop_token(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.work.photoshop_token)
        self.msg.set("Pairing token copied. Paste it into the GenFill panel in Photoshop.")

    # -- your workflow ---------------------------------------------------------------------------
    def _show_workflow(self, slots: dict | None = None) -> None:
        """Show the workflow's name and, for each slot, which node GenFill uses (`slots`, else its guess)."""
        wf = self.own_wf
        if wf is None:
            self.own_name.set("None – GenFill's own")
            self.own_frame.grid_remove()
            return
        self.own_name.set(Path(self.own_path).name)
        found = custom_workflow.detect(wf)
        chosen = {**found, **{k: v for k, v in (slots or {}).items() if v is None or v in wf}} if slots else found
        options = custom_workflow.candidates(wf)
        for slot in custom_workflow.SLOTS:
            ids = {custom_workflow.label(wf, None): None,
                   **{custom_workflow.label(wf, i): i for i in options[slot]}}
            self.slot_ids[slot] = ids
            self.slot_boxes[slot].configure(values=list(ids))
            self.slot_vars[slot].set(custom_workflow.label(wf, chosen.get(slot)))
        self._refresh_extra_results(self.work.custom_result_nodes)
        self.own_frame.grid()

    def _refresh_extra_results(self, selected: list[str] | None = None) -> None:
        """Keep extra output choices in sync when the main Result changes."""
        selected = self._extra_result_nodes() if selected is None else selected
        wf = self.own_wf
        primary = self._slots().get("result")
        self.extra_results.delete(0, "end")
        self.extra_result_ids = [n for n in custom_workflow.candidates(wf)["result"] if n != primary]
        for i, nid in enumerate(self.extra_result_ids):
            self.extra_results.insert("end", custom_workflow.label(wf, nid))
            if nid in selected:
                self.extra_results.selection_set(i)

    def _extra_result_nodes(self) -> list[str]:
        return [self.extra_result_ids[i] for i in self.extra_results.curselection()]

    def _slots(self) -> dict:
        return {slot: self.slot_ids.get(slot, {}).get(self.slot_vars[slot].get()) for slot in custom_workflow.SLOTS}

    def choose_workflow(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Your ComfyUI workflow",
                                          filetypes=[("ComfyUI workflow", "*.json"), ("All files", "*.*")])
        if not path:
            return
        url = self.vars["comfy_url"].get().strip()
        self.msg.set("Reading the workflow and asking ComfyUI about its nodes…")

        def work():
            try:
                data = custom_workflow.read(path)
                try:
                    info = ComfyClient(url).object_info()
                except ComfyError as e:
                    if not custom_workflow.is_api(data):
                        raise custom_workflow.WorkflowError(
                            f"{e} GenFill asks ComfyUI how the workflow's nodes are laid out.") from e
                    info = None
                wf = custom_workflow.to_api(data, info or {})
                missing = custom_workflow.missing_nodes(wf, info) if info else []
                self.after(0, lambda: self._workflow_read(path, wf, missing))
            except custom_workflow.WorkflowError as e:
                self.after(0, lambda: self.msg.set(str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _workflow_read(self, path: str, wf: dict, missing: list[str]) -> None:
        self._remember_active_workflow()
        entry = next((w for w in self.saved_workflows if w["source"] == path), None)
        if entry is None:
            names = {w["name"] for w in self.saved_workflows}
            base = Path(path).stem
            name = base
            number = 2
            while name in names:
                name, number = f"{base} ({number})", number + 1
            entry = {"id": uuid.uuid4().hex, "name": name, "source": path, "slots": {}}
            self.saved_workflows.append(entry)
        self.active_workflow_id = entry["id"]
        self.pending_workflows[entry["id"]] = wf
        self.own_path, self.own_wf = path, wf
        self.keep_seeds_var.set(bool(entry.get("keep_seeds", False)))
        self.native_size_var.set(bool(entry.get("native_size", False)))
        self.work.custom_result_nodes = list(entry.get("result_nodes", []))
        self._show_workflow()
        entry["slots"] = self._slots()
        self._refresh_model_choices()
        notes = custom_workflow.problems(self._slots())
        if missing:
            notes.insert(0, "Your ComfyUI doesn't have these nodes: " + ", ".join(missing) + ". Install them "
                         "(ComfyUI Manager › Install Missing Custom Nodes) and restart ComfyUI.")
        self.msg.set("\n".join(notes) if notes else
                     "Workflow read. Check what GenFill found for each part, change any that's wrong, then Save.")

    def clear_workflow(self) -> None:
        self._remember_active_workflow()
        self.active_workflow_id = ""
        self.own_path, self.own_wf = "", None
        self._show_workflow()
        self._refresh_model_choices()
        self.msg.set("Generate and Create will use GenFill's own workflow again (after Save).")

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
            s = Settings(**{**self.work.__dict__})
            s.comfy_url = self.vars["comfy_url"].get().strip() or s.comfy_url
            s.models = {slot: self.vars[slot].get().strip() for slot in MODEL_SLOTS}
            s.steps = max(1, min(50, int(self.vars["steps"].get())))
            s.upscale_denoise = max(0.05, min(0.8, float(self.vars["upscale_denoise"].get())))
            s.port = max(1, min(65535, int(self.vars["port"].get())))
            s.photocraft_cli = self.vars["photocraft_cli"].get().strip()
            s.check_updates = bool(self.check_updates.get())
            s.loras = self._read_loras()
            s.use_loras = bool(s.loras) if self._lora_edited else self.work.use_loras
            s.custom_workflow = self.own_path if self.own_wf is not None else ""
            s.custom_slots = self._slots() if self.own_wf is not None else {}
            s.custom_keep_seeds = self.keep_seeds_var.get() if self.own_wf is not None else False
            s.custom_native_size = self.native_size_var.get() if self.own_wf is not None else False
            s.custom_result_nodes = self._extra_result_nodes() if self.own_wf is not None else []
            self._remember_active_workflow()
            s.active_workflow_id = self.active_workflow_id if self.own_wf is not None else ""
            s.saved_workflows = [dict(w) for w in self.saved_workflows]
            return s
        except ValueError as e:
            self.msg.set(str(e) if str(e).startswith("LoRA strength") else
                         "Steps and port must be whole numbers, strength a number like 0.3.")
            return None

    def save(self) -> None:
        s = self._collect()
        if s is None:
            return
        if self.own_wf is not None:
            if not s.custom_slots.get("result"):
                self.msg.set("Pick which node's picture is the result (a Save Image or Preview Image node), "
                             "or clear the workflow.")
                return
            custom_workflow.save(self.own_wf, CONFIG_DIR)
        for workflow_id, wf in self.pending_workflows.items():
            custom_workflow.save(wf, CONFIG_DIR, workflow_id)
        s.save()
        self.on_save(s)
        self.destroy()
