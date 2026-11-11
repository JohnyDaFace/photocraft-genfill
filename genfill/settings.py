"""User settings in settings.json: %APPDATA%\\PhotoCraftGenFill on Windows,
~/Library/Application Support/PhotoCraftGenFill on macOS, ~/.config/photocraft-genfill on Linux."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .families import DEFAULT_FAMILY, FAMILIES, family
from .setup_env import APP_DIR


def _config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", str(Path.home()))) / "PhotoCraftGenFill"
    if sys.platform == "darwin":
        return APP_DIR
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "photocraft-genfill"


CONFIG_DIR = _config_dir()
SETTINGS_FILE = CONFIG_DIR / "settings.json"
_OLD_SETTINGS_FILE = Path(__file__).resolve().parents[1] / "settings.json"  # before 2026-11-06

MAX_RECENT = 15  # prompts remembered

DEFAULT_MODELS = FAMILIES[DEFAULT_FAMILY].models


def find_photocraft_cli() -> str:
    """PhotoCraft's command-line engine from the usual install places (used by the compatibility check)."""
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PhotoCraft" / "photocraft-cli.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "PhotoCraft" / "photocraft-cli.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "PhotoCraft" / "photocraft-cli.exe",
        ]
    elif sys.platform == "darwin":  # shipped inside the app bundle (packaging/macos/package.sh)
        candidates = [Path(base) / "PhotoCraft.app" / "Contents" / "MacOS" / "photocraft-cli"
                      for base in ("/Applications", str(Path.home() / "Applications"))]
    else:
        candidates = [Path("/usr/bin/photocraft-cli"), Path("/usr/local/bin/photocraft-cli")]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("photocraft-cli")
    return found or str(candidates[0])


@dataclass
class Settings:
    port: int = 7878
    token_file: str = str(APP_DIR / "control.token")
    exchange_dir: str = str(APP_DIR / "exchange")
    results_dir: str = str(APP_DIR / "results")  # lasting results: sets of results to swap between later
    keep_results: int = 50                        # that many sets are kept; older ones are deleted
    shoots_dir: str = str(APP_DIR / "shoots")     # lasting photoshoots: runs to reshoot from later
    shoot_count: int = 8                          # shots per photoshoot (4, 8 or 12)
    comfy_url: str = "http://127.0.0.1:8188"
    photocraft_cli: str = ""  # empty = find it automatically
    steps: int = 4
    variations: int = 3  # candidates per Generate (1 = place straight away, no choosing)
    recent_prompts: list = field(default_factory=list)  # newest first, shown in the prompt box
    expand_choice: str = "16:9"  # last shape picked in Expand
    create_choice: str = "1:1"  # last shape picked in Create
    create_second_reference: str = ""  # optional image 2 for a two-image FLUX.2 Create
    angle: str = ""  # camera angle that's on (styles.ANGLES): added before the style, until turned off
    style: str = ""  # style that's on (styles.py): added to Create and whole-image edit prompts until turned off
    check_updates: bool = True  # ask GitHub once at startup whether a newer version exists
    follow_photocraft: bool = True  # hide the bar while PhotoCraft is closed, show it when it opens
    megapixels: float = 2.0  # generation size; lower (1.0) is about twice as fast, softer
    settings_version: int = 2  # 2: megapixels went from 1.0 to 2.0
    upscale_factor: int = 2
    upscale_denoise: float = 0.3
    upscale_steps: int = 8
    upscale_model: str = "4x-UltraSharp.pth"  # enlarges before the redraw ("" = plain resize, softer)
    seedvr_model: str = "seedvr2_7b_int8_convrot.safetensors"  # Upscale with SeedVR2 when installed ("" = off)
    seedvr_tile: int = 2048  # SeedVR2 piece size in output pixels (smaller if the GPU runs out of memory)
    max_upscaled_side: int = 8192
    face_strength: float = 0.5 # Fix faces: how much of each face is redrawn (more = sharper, less like before)
    # Replace face swaps the whole head with this face-swap LoRA when ComfyUI has it ("" = off)
    face_swap_lora: str = "bfs_head_v1_flux-klein_9b_step3750_rank64.safetensors"
    enhance_strength: float = 0.35  # Enhance: how far the light and colour may change (only those are kept)
    last_theme: str = "ProMedium"  # PhotoCraft's theme last time we saw it (window opens in it)
    bar_x: int | None = None  # where the bar was dragged to (None = top centre of the screen)
    bar_y: int | None = None
    model_family: str = DEFAULT_FAMILY  # families.py: which image model draws (FLUX.2 Klein, FLUX 3 Klein…)
    models: dict = field(default_factory=lambda: dict(DEFAULT_MODELS))  # its files
    loras: list = field(default_factory=list)  # optional, settings file only: file names or {"name", "strength"}
    use_loras: bool = True  # switch the configured model LoRAs off without forgetting them
    other_families: dict = field(default_factory=dict)  # the files, steps and LoRAs of the families not in use
    custom_workflow: str = ""  # your own ComfyUI workflow for Generate and Create: the file it came from ("" = off)
    custom_slots: dict = field(default_factory=dict)  # which of its nodes take the picture, prompt, result…
    custom_keep_seeds: bool = False  # leave numeric seed fields from the selected workflow alone
    custom_native_size: bool = False  # keep the workflow's generated pixels and dimensions
    custom_result_nodes: list[str] = field(default_factory=list)  # additional output nodes to collect per run
    saved_workflows: list = field(default_factory=list)  # named workflow choices kept beside the built-in models
    active_workflow_id: str = ""  # selected saved workflow; legacy custom_workflow can have no id

    @property
    def workflow_models(self) -> dict:
        """The model files plus the LoRAs and the family, as the workflow builders take them."""
        return {**self.models, "loras": self.loras if self.use_loras and isinstance(self.loras, list) else [],
                "family": self.model_family}

    def switch_family(self, name: str) -> None:
        """Draw with another model family: this one's files, steps and LoRAs are kept for coming back
        (LoRAs belong to one model), the other's come back, or its defaults the first time."""
        if name == self.model_family or name not in FAMILIES:
            return
        self.other_families[self.model_family] = {"models": dict(self.models), "steps": self.steps,
                                                   "loras": list(self.loras)}
        saved = self.other_families.pop(name, None) or {}
        fam = FAMILIES[name]
        self.model_family = name
        self.models = {**fam.models, **(saved.get("models") or {})}
        self.steps = int(saved.get("steps") or fam.steps)
        self.loras = list(saved.get("loras") or [])

    @property
    def cli_path(self) -> str:
        return self.photocraft_cli or find_photocraft_cli()

    @classmethod
    def load(cls, path: Path = SETTINGS_FILE) -> "Settings":
        if path == SETTINGS_FILE and not path.exists() and _OLD_SETTINGS_FILE.exists():
            path = _OLD_SETTINGS_FILE  # carry settings over from the old location once
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            s = cls(**known)
        except TypeError:
            return cls()
        if data.get("settings_version", 1) < 2 and s.megapixels == 1.0:
            s.megapixels = 2.0  # the old default: move to the sharper new one (a value set by hand stays)
        if data.get("settings_version", 1) < 3:  # FLUX 3's old default of 8 steps: move to the official 15
            if s.model_family == "flux3" and s.steps == 8:
                s.steps = FAMILIES["flux3"].steps
            saved = s.other_families.get("flux3") if isinstance(s.other_families, dict) else None
            if isinstance(saved, dict) and saved.get("steps") == 8:
                saved["steps"] = FAMILIES["flux3"].steps
        s.settings_version = 3
        if s.model_family not in FAMILIES:
            s.model_family = DEFAULT_FAMILY
        s.models = {**family(s.model_family).models, **(s.models if isinstance(s.models, dict) else {})}
        if not isinstance(s.other_families, dict):
            s.other_families = {}
        if not isinstance(s.custom_slots, dict):
            s.custom_slots = {}
        if not isinstance(s.saved_workflows, list):
            s.saved_workflows = []
        if not isinstance(s.custom_result_nodes, list):
            s.custom_result_nodes = []
        s.custom_result_nodes = [str(node) for node in s.custom_result_nodes if isinstance(node, str)]
        s.saved_workflows = [w for w in s.saved_workflows if isinstance(w, dict)
                             and isinstance(w.get("id"), str) and isinstance(w.get("name"), str)
                             and isinstance(w.get("source"), str) and isinstance(w.get("slots"), dict)]
        if not isinstance(s.active_workflow_id, str):
            s.active_workflow_id = ""
        s.recent_prompts = [str(x) for x in s.recent_prompts][:MAX_RECENT] if isinstance(s.recent_prompts, list) else []
        return s

    def remember_prompt(self, prompt: str) -> None:
        prompt = prompt.strip()
        if prompt:
            self.recent_prompts = [prompt] + [x for x in self.recent_prompts if x != prompt][:MAX_RECENT - 1]

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
