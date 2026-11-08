"""Thumbnails for the Style panel: the same scene made in each style, so the look is what differs.

The built-in styles' thumbnails ship in assets/styles (made with tools/make_style_thumbs.py). A style you
add or change in styles.json gets one made by ComfyUI the first time the panel shows it, kept in
style_thumbs next to settings.json. The file name carries a hash of the style's text, so editing a style
makes a new thumbnail."""
from __future__ import annotations

import hashlib
import random
import re
import sys
import threading
from pathlib import Path

from PIL import Image

from . import styles
from .settings import CONFIG_DIR, Settings

SCENE = "a woman in a red coat holding a cup of coffee on a city street next to a café"
# a style that only makes sense with another subject gets its own scene (it's part of the file name's hash)
PRODUCT_SCENE = "a single red ceramic coffee cup with a saucer, a little steam rising, as the product"
FOOD_SCENE = "a bowl of spaghetti with tomato sauce and fresh basil"
ROOM_SCENE = "a living room with a red armchair, a sofa and a large window"
FACE_SCENE = "a close-up head and shoulders portrait of a woman with long brown hair in a red coat"
SCENES = {**{n: PRODUCT_SCENE for n in styles.PRODUCT}, **{n: FACE_SCENE for n in styles.PORTRAIT}, "Food overhead": FOOD_SCENE,
          "Food dark & moody": FOOD_SCENE, "Interior bright": ROOM_SCENE}
SIDE = 176     # shown size
MAKE_SIDE = 512  # generated size (small: a few seconds)
SEED = 20261108
OFF = "Off"

BUILTIN_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "styles"
USER_DIR = CONFIG_DIR / "style_thumbs"


def scene_for(name: str) -> str:
    return SCENES.get(name, SCENE)


def file_name(name: str, text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "style"
    key = text + ("|" + SCENES[name] if name in SCENES else "")
    return f"{slug}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}.jpg"


def find(name: str, text: str, dirs: tuple[Path, ...] | None = None) -> Path | None:
    """The thumbnail for this style (OFF: the scene with no style), or None if none is made yet."""
    for d in dirs or (BUILTIN_DIR, USER_DIR):
        f = Path(d) / file_name(name, text)
        if f.is_file():
            return f
    return None


def make(name: str, text: str, settings: Settings, out_dir: Path | None = None, comfy=None,
         cancel: threading.Event | None = None) -> Path:
    """Make the thumbnail with ComfyUI (the same seed every time) and save it; returns the file."""
    from .pipeline import Job, _candidates  # here: pipeline imports styles
    from .comfy import ComfyClient
    comfy = comfy or ComfyClient(settings.comfy_url)
    scene = scene_for(name)
    prompt = f"{text} {scene}" if text else scene  # as styles.styled does: the look first
    job = Job(Image.new("RGBA", (MAKE_SIDE, MAKE_SIDE), (255, 255, 255, 255)), None, None, prompt, create=True)
    state = random.getstate()
    random.seed(SEED)
    try:
        image = _candidates(job, settings, lambda s: None, lambda f: None, cancel or threading.Event(), comfy, 1)[0]
    finally:
        random.setstate(state)
    out = Path(out_dir or USER_DIR)
    out.mkdir(parents=True, exist_ok=True)
    f = out / file_name(name, text)
    image.convert("RGB").resize((SIDE, SIDE), Image.LANCZOS).save(f, quality=90)
    return f


def missing(dirs: tuple[Path, ...] | None = None) -> list[tuple[str, str]]:
    """(name, text) of the styles, plus OFF, that have no thumbnail yet."""
    every = [(OFF, "")] + styles.load() + styles.ANGLES
    return [(n, t) for n, t in every if find(n, t, dirs) is None]
