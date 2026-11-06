"""The jobs: Generate (fill the selection, or edit the whole image when nothing is selected; one or
more candidates to choose from) and Upscale (new, larger document)."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from . import imaging
from .comfy import ComfyClient, ComfyError, build_edit_workflow, build_fill_workflow, build_refine_workflow
from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .settings import Settings

MAX_VARIATIONS = 4

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


PREVIEW_SIDE = 240


@dataclass
class Results:
    """Finished candidates for one Generate, waiting to be placed. `images` are document-sized RGBA."""
    images: list[Image.Image]
    previews: list[Image.Image]
    layer: str
    masked: bool                      # fill: masked to the selection; edit: whole layer
    size: tuple[int, int]
    selection: tuple[int, int, int, int] | None


def _preview(img: Image.Image, box: tuple[int, int, int, int] | None) -> Image.Image:
    """The part worth comparing: the selection plus some surroundings (whole image for an edit)."""
    if box:
        x0, y0, x1, y1 = box
        mx, my = max(32, (x1 - x0) // 4), max(32, (y1 - y0) // 4)
        img = img.crop((max(0, x0 - mx), max(0, y0 - my), min(img.width, x1 + mx), min(img.height, y1 + my)))
    flat = Image.new("RGB", img.size, (128, 128, 128))
    flat.paste(img, mask=img.getchannel("A"))
    flat.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE), Image.LANCZOS)
    return flat


def remove_prompt(what: str) -> str:
    what = what.strip().rstrip(".")
    target = f"the {what}" if what and not what.lower().startswith(("the ", "a ", "an ")) else (what or "the object")
    return (f"Remove {target} completely. Show the empty background that was behind it, continuing the "
            "surrounding surfaces, lighting and shadows. Add nothing new.")


def load_reference(path: str | Path) -> Image.Image:
    """A reference picture from disk, flattened onto white (transparent PNGs)."""
    try:
        im = Image.open(path)
        im.load()
    except (OSError, ValueError) as e:
        raise UserFacingError(f"Can't open the reference image {Path(path).name}: {e}") from e
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        flat = Image.new("RGB", im.size, (255, 255, 255))
        flat.paste(im, mask=im.getchannel("A"))
        return flat
    return im.convert("RGB")


def _reference_upload(ref: Image.Image, megapixels: float) -> Image.Image:
    """About the working size and in multiples of 16 px, which is what Klein expects for every image."""
    w, h = imaging.working_size(ref.width, ref.height, megapixels)
    return ref.resize((w, h), Image.LANCZOS)


def generate(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
             pc=None, comfy=None, count: int | None = None, reference: Image.Image | None = None,
             remove: bool = False) -> Results:
    """With a selection: fill it. Without one: edit the whole picture as the prompt says.
    Makes `count` (default settings.variations) candidates with different seeds. `reference` is an
    extra picture the model can take things or a style from (it calls it "image 2"). `remove`: take
    away what's in the selection; `prompt` then names it ("the basket") or may be empty. (Blanking the
    area first was tried and is worse: Klein invents a new object to fill the hole.)"""
    pc, comfy = _clients(settings, pc, comfy)
    prompt = prompt.strip()
    count = max(1, min(MAX_VARIATIONS, count or settings.variations))
    try:
        status("Reading the image from PhotoCraft…")
        with pc:
            problems = pc.compatibility()
            if problems:
                raise UserFacingError("This PhotoCraft version isn't compatible yet: " + " ".join(problems))
            doc = pc.document()
            if remove and doc.selection is None:
                raise UserFacingError("Select what to remove first.")
            if doc.selection is None and not prompt:
                raise UserFacingError("Make a selection first to fill it, or type what to change (for example "
                                      "'make it night') to edit the whole image.")
            image = pc.render()
        edit = doc.selection is None
        mask = None if edit else imaging.fill_mask(image, doc.selection)
        if mask is not None and not mask.getbbox():
            raise UserFacingError("The selection is outside the canvas.")
        ww, wh = imaging.working_size(image.width, image.height, settings.megapixels)
        status("Sending to ComfyUI…")
        comfy.check()
        if edit:
            flat = Image.new("RGB", image.size, (255, 255, 255))
            flat.paste(image, mask=image.getchannel("A"))
            src_name = comfy.upload(flat.resize((ww, wh), Image.LANCZOS), "genfill_src.png")
        else:
            src_name = comfy.upload(imaging.prepare_source(image).resize((ww, wh), Image.LANCZOS), "genfill_src.png")
            mask_name = comfy.upload(mask.resize((ww, wh), Image.BILINEAR).convert("RGB"), "genfill_mask.png")
        refs = [comfy.upload(_reference_upload(reference, settings.megapixels), "genfill_ref.png")] if reference else []
        label = prompt
        if remove:
            prompt = remove_prompt(prompt)
        images = []
        for i in range(count):
            if cancel.is_set():
                raise UserFacingError("Cancelled.")
            what = "Editing the image" if edit else "Generating"
            status(f"{what} with Flux 2 Klein" + (f" ({i + 1} of {count})" if count > 1 else "") + "…"
                   + (" (first run loads the model)" if i == 0 else ""))
            if count > 1:
                progress(i / count)
            seed = random.randint(0, 2**32 - 1)
            if edit:
                wf = build_edit_workflow(src_name, prompt, seed, settings.steps, ww, wh, settings.models, refs)
                out = comfy.run(wf, progress if count == 1 else (lambda _f: None), cancel)
                out = out.resize(image.size, Image.LANCZOS).convert("RGBA")
                out.putalpha(image.getchannel("A"))  # transparent stays transparent
            else:
                wf = build_fill_workflow(src_name, mask_name, prompt, seed, settings.steps, ww, wh, settings.models,
                                         extra_refs=refs)
                out = comfy.run(wf, progress if count == 1 else (lambda _f: None), cancel)
                out = imaging.match_colors(image, out, mask)
                out = imaging.merge(image, out, imaging.blend_mask(image, doc.selection))
            images.append(out)
        progress(1.0)
        name = ("Edit" if edit else "Remove" if remove else "Generative Fill") + (f" – {label[:40]}" if label else "")
        return Results(images, [_preview(im, doc.selection) for im in images], name, not edit,
                       (image.width, image.height), doc.selection)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e


def place(results: Results, index: int, settings: Settings, pc=None) -> str:
    """Add the chosen candidate to PhotoCraft as a layer. Refuses if the document or the selection
    changed while the user was choosing (the mask is made from the selection at this moment)."""
    pc, _ = _clients(settings, pc, object())
    try:
        with pc:
            pc.compatibility()  # resolves the command names for this PhotoCraft
            doc = pc.document()
            if (doc.width, doc.height) != results.size or (results.masked and doc.selection != results.selection):
                raise UserFacingError("The document or the selection changed since Generate, so the result "
                                      "would land in the wrong place. Put it back or Generate again.")
            path, rel = _exchange(settings, "fill" if results.masked else "edit")
            results.images[index].save(path)
            pc.place_layer(rel, results.layer, masked=results.masked)
        return results.layer
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e


def run_fill(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
             pc=None, comfy=None) -> str:
    """Generate one candidate and place it (no picking)."""
    pc, comfy = _clients(settings, pc, comfy)
    results = generate(prompt, settings, status, progress, cancel, pc=pc, comfy=comfy, count=1)
    status("Placing the result in PhotoCraft…")
    return place(results, 0, settings, pc=pc)


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
