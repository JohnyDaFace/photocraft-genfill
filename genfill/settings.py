"""User settings in settings.json: %APPDATA%\\PhotoCraftGenFill on Windows,
~/Library/Application Support/PhotoCraftGenFill on macOS, ~/.config/photocraft-genfill on Linux."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

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

DEFAULT_MODELS = {
    "unet": "flux-2-klein-9b-fp8.safetensors",
    "clip": "qwen_3_8b_fp8mixed.safetensors",
    "vae": "flux2-vae.safetensors",
}


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
    comfy_url: str = "http://127.0.0.1:8188"
    photocraft_cli: str = ""  # empty = find it automatically
    steps: int = 4
    variations: int = 3  # candidates per Generate (1 = place straight away, no choosing)
    recent_prompts: list = field(default_factory=list)  # newest first, shown in the prompt box
    expand_choice: str = "16:9"  # last shape picked in Expand
    create_choice: str = "1:1"  # last shape picked in Create
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
    models: dict = field(default_factory=lambda: dict(DEFAULT_MODELS))
    loras: list = field(default_factory=list)  # optional, settings file only: file names or {"name", "strength"}

    @property
    def workflow_models(self) -> dict:
        """The model files plus the LoRAs, as the workflow builders take them."""
        return {**self.models, "loras": self.loras if isinstance(self.loras, list) else []}

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
        s.settings_version = 2
        s.models = {**DEFAULT_MODELS, **(s.models if isinstance(s.models, dict) else {})}
        s.recent_prompts = [str(x) for x in s.recent_prompts][:MAX_RECENT] if isinstance(s.recent_prompts, list) else []
        return s

    def remember_prompt(self, prompt: str) -> None:
        prompt = prompt.strip()
        if prompt:
            self.recent_prompts = [prompt] + [x for x in self.recent_prompts if x != prompt][:MAX_RECENT - 1]

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
