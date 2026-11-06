"""The two jobs: Generate (fill the selection) and Upscale (new, larger document)."""
from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from . import imaging
from .comfy import ComfyClient, ComfyError, build_fill_workflow, build_refine_workflow
from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .settings import Settings

Status = Callable[[str], None]
Progress = Callable[[float | None], None]


class UserFacingError(Exception):
    """Something the user can fix; the message is shown as-is."""


def _clients(settings: Settings, pc, comfy):
    pc = pc or PhotoCraftClient(settings.port, Path(settings.token_file), exchange_dir=Path(settings.exchange_dir))
    comfy = comfy or ComfyClient(settings.comfy_url)
    return pc, comfy


def _exchange(settings: Settings, prefix: str) -> tuple[Path, str]:
    """A fresh file name in the exchange folder (= PhotoCraft's read root); old files are pruned."""
    d = Path(settings.exchange_dir)
    d.mkdir(parents=True, exist_ok=True)
    for old in sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime)[:-10]:
        try:
            old.unlink()
        except OSError:
            pass
    name = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{random.randint(0, 9999):04d}.png"
    return d / name, name


def run_fill(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
             pc=None, comfy=None) -> str:
    pc, comfy = _clients(settings, pc, comfy)
    try:
        status("Reading the image from PhotoCraft…")
        with pc:
            problems = pc.compatibility()
            if problems:
                raise UserFacingError("This PhotoCraft version isn't compatible yet: " + " ".join(problems))
            doc = pc.document()
            if doc.selection is None:
                raise UserFacingError("Make a selection first (for example, the empty area of the canvas).")
            image = pc.render()
        mask = imaging.fill_mask(image, doc.selection)
        if not mask.getbbox():
            raise UserFacingError("The selection is outside the canvas.")
        ww, wh = imaging.working_size(image.width, image.height, settings.megapixels)
        status("Sending to ComfyUI…")
        comfy.check()
        src_name = comfy.upload(imaging.prepare_source(image).resize((ww, wh), Image.LANCZOS), "genfill_src.png")
        mask_name = comfy.upload(mask.resize((ww, wh), Image.BILINEAR).convert("RGB"), "genfill_mask.png")
        status("Generating with Flux 2 Klein… (first run loads the model)")
        wf = build_fill_workflow(src_name, mask_name, prompt.strip(), random.randint(0, 2**32 - 1),
                                 settings.steps, ww, wh, settings.models)
        generated = comfy.run(wf, progress, cancel)
        status("Placing the result in PhotoCraft…")
        path, rel = _exchange(settings, "fill")
        generated = imaging.match_colors(image, generated, mask)
        imaging.merge(image, generated, imaging.blend_mask(image, doc.selection)).save(path)
        layer = "Generative Fill" + (f" – {prompt.strip()[:40]}" if prompt.strip() else "")
        with pc:
            pc.place_layer(rel, layer)
        return layer
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e


def run_upscale(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
                pc=None, comfy=None) -> str:
    pc, comfy = _clients(settings, pc, comfy)
    try:
        status("Reading the image from PhotoCraft…")
        with pc:
            image = pc.render()
        f = settings.upscale_factor
        tw, th = image.width * f, image.height * f
        if max(tw, th) > settings.max_upscaled_side:
            raise UserFacingError(f"The result would be {tw}×{th}, too large (limit {settings.max_upscaled_side} px "
                                  "per side). Use a smaller factor in Settings.")
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel("A"))
        big = flat.resize((tw, th), Image.LANCZOS)
        comfy.check()
        boxes = imaging.tile_boxes(tw, th)
        tiles = []
        for i, box in enumerate(boxes, 1):
            if cancel.is_set():
                raise UserFacingError("Cancelled.")
            status(f"Upscaling tile {i} of {len(boxes)}…" + (" (loading the model)" if i == 1 else ""))
            progress((i - 1) / len(boxes))
            crop = big.crop(box)
            cw, ch = (crop.width // 16) * 16 or 16, (crop.height // 16) * 16 or 16
            name = comfy.upload(crop.resize((cw, ch), Image.LANCZOS), f"genfill_tile_{i}.png")
            wf = build_refine_workflow(name, prompt.strip(), random.randint(0, 2**32 - 1), settings.upscale_steps,
                                       settings.upscale_denoise, cw, ch, settings.models)
            tiles.append((box, comfy.run(wf, lambda _f: None, cancel)))
        progress(1.0)
        status("Opening the upscaled image in PhotoCraft…")
        path, rel = _exchange(settings, f"upscaled_{f}x")
        imaging.blend_tiles((tw, th), tiles).save(path)
        with pc:
            pc.open_document(rel)
        return f"Upscaled {f}× to {tw}×{th}"
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
