"""Make the built-in styles' thumbnails into assets/styles (needs ComfyUI running). Run again after changing
styles.DEFAULT_STYLES or style_thumbs.SCENE. LoRAs from the settings file are left out on purpose: shipped
thumbnails show the plain model."""
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from genfill import style_thumbs, styles  # noqa: E402
from genfill.settings import Settings  # noqa: E402

out = ROOT / "assets" / "styles"
settings = replace(Settings.load(), loras=[])
wanted = [(style_thumbs.OFF, "")] + list(styles.DEFAULT_STYLES) + styles.ANGLES
keep = {style_thumbs.file_name(n, t) for n, t in wanted}
for old in out.glob("*.jpg") if out.exists() else []:
    if old.name not in keep:
        old.unlink()  # a thumbnail for a style text that no longer exists
for name, text in wanted:
    if not (out / style_thumbs.file_name(name, text)).exists():
        print("making", name, "->", style_thumbs.make(name, text, settings, out_dir=out).name, flush=True)
