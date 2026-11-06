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
    comfy_url: str = "http://127.0.0.1:8188"
    photocraft_cli: str = ""  # empty = find it automatically
    steps: int = 4
    megapixels: float = 1.0
    upscale_factor: int = 2
    upscale_denoise: float = 0.3
    upscale_steps: int = 8
    max_upscaled_side: int = 8192
    last_theme: str = "ProMedium"  # PhotoCraft's theme last time we saw it (window opens in it)
    models: dict = field(default_factory=lambda: dict(DEFAULT_MODELS))

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
        s.models = {**DEFAULT_MODELS, **(s.models if isinstance(s.models, dict) else {})}
        return s

    def save(self, path: Path = SETTINGS_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
