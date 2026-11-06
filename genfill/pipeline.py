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
SIMILAR_DENOISE = 0.7   # "More like this": redraw this much of the chosen result (keeps its composition)

Bounds = tuple[int, int, int, int]


@dataclass
class Job:
    """Everything needed to (re)generate: kept with the results so "More like this" can run again."""
    base: Image.Image                 # document-sized RGBA the results are merged into (expand: the bigger canvas)
    mask: Image.Image | None          # where to generate (None = edit the whole image)
    selection: Bounds | None          # selection bounds in `base` (None = edit)
    prompt: str                       # as sent to the model
    reference: Image.Image | None = None


@dataclass
class Results:
    """Finished candidates for one Generate, waiting to be placed. `images` are the size of `job.base`."""
    images: list[Image.Image]
    previews: list[Image.Image]
    layer: str
    masked: bool                      # fill: masked to the selection; edit/expand: whole layer
    size: tuple[int, int]             # the document's size when generating
    selection: Bounds | None          # the document's selection when generating
    job: Job | None = None
    expand_to: tuple[int, int] | None = None  # Expand: grow the canvas to this before placing…
    offset: tuple[int, int] = (0, 0)          # …with the old picture here (canvas anchored in the centre)


def _preview(img: Image.Image, box: Bounds | None) -> Image.Image:
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


def _flat(img: Image.Image) -> Image.Image:
    flat = Image.new("RGB", img.size, (255, 255, 255))
    flat.paste(img, mask=img.getchannel("A"))
    return flat


def _read(pc, need_selection: str | None = None):
    """(doc, image) from PhotoCraft after the compatibility check."""
    with pc:
        problems = pc.compatibility()
        if problems:
            raise UserFacingError("This PhotoCraft version isn't compatible yet: " + " ".join(problems))
        doc = pc.document()
        if need_selection and doc.selection is None:
            raise UserFacingError(need_selection)
        return doc, pc.render()


def _candidates(job: Job, settings: Settings, status: Status, progress: Progress, cancel, comfy, count: int,
                start: Image.Image | None = None, denoise: float = 1.0) -> list[Image.Image]:
    """Run the model `count` times with different seeds. `start` (a previous result) + `denoise` < 1
    redraws only part of it: variations close to that result."""
    src = start if start is not None else job.base
    ww, wh = imaging.working_size(src.width, src.height, settings.megapixels)
    status("Sending to ComfyUI…")
    comfy.check()
    edit = job.mask is None
    prepared = _flat(src) if edit else imaging.prepare_source(src)
    src_name = comfy.upload(prepared.resize((ww, wh), Image.LANCZOS), "genfill_src.png")
    if not edit:
        mask_name = comfy.upload(job.mask.resize((ww, wh), Image.BILINEAR).convert("RGB"), "genfill_mask.png")
    refs = [comfy.upload(_reference_upload(job.reference, settings.megapixels), "genfill_ref.png")] \
        if job.reference is not None else []
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
        on_progress = progress if count == 1 else (lambda _f: None)
        if edit:
            wf = build_edit_workflow(src_name, job.prompt, seed, settings.steps, ww, wh, settings.models, refs,
                                     denoise=denoise)
            out = comfy.run(wf, on_progress, cancel).resize(job.base.size, Image.LANCZOS).convert("RGBA")
            out.putalpha(job.base.getchannel("A"))  # transparent stays transparent
        else:
            wf = build_fill_workflow(src_name, mask_name, job.prompt, seed, settings.steps, ww, wh,
                                     settings.models, extra_refs=refs, denoise=denoise)
            out = comfy.run(wf, on_progress, cancel)
            out = imaging.match_colors(job.base, out, job.mask)
            out = imaging.merge(job.base, out, imaging.blend_mask(job.base, job.selection))
        images.append(out)
    progress(1.0)
    return images


def generate(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
             pc=None, comfy=None, count: int | None = None, reference: Image.Image | None = None,
             remove: bool = False) -> Results:
    """With a selection: fill it. Without one: edit the whole picture as the prompt says.
    Makes `count` (default settings.variations) candidates with different seeds. `reference` is an
    extra picture the model can take things or a style from (it calls it "image 2"). `remove`: take
    away what's in the selection; `prompt` then names it ("the basket") or may be empty. (Blanking the
    area first was tried and is worse: Klein invents a new object to fill the hole.)"""
    pc, comfy = _clients(settings, pc, comfy)
    label = prompt.strip()
    count = max(1, min(MAX_VARIATIONS, count or settings.variations))
    try:
        status("Reading the image from PhotoCraft…")
        doc, image = _read(pc, "Select what to remove first." if remove else None)
        if doc.selection is None and not label:
            raise UserFacingError("Make a selection first to fill it, or type what to change (for example "
                                  "'make it night') to edit the whole image.")
        edit = doc.selection is None
        mask = None if edit else imaging.fill_mask(image, doc.selection)
        if mask is not None and not mask.getbbox():
            raise UserFacingError("The selection is outside the canvas.")
        job = Job(image, mask, doc.selection, remove_prompt(label) if remove else label,
                  None if remove else reference)
        images = _candidates(job, settings, status, progress, cancel, comfy, count)
        name = ("Edit" if edit else "Remove" if remove else "Generative Fill") + (f" – {label[:40]}" if label else "")
        return Results(images, [_preview(im, doc.selection) for im in images], name, not edit,
                       (image.width, image.height), doc.selection, job)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e


def similar(results: Results, index: int, settings: Settings, status: Status, progress: Progress, cancel,
            comfy=None, count: int | None = None) -> Results:
    """"More like this": new versions close to results.images[index] (it stays as choice 1)."""
    _, comfy = _clients(settings, object(), comfy)
    count = max(1, min(MAX_VARIATIONS - 1, (count or settings.variations)))
    chosen = results.images[index]
    try:
        images = [chosen] + _candidates(results.job, settings, status, progress, cancel, comfy, count,
                                        start=chosen, denoise=SIMILAR_DENOISE)
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    box = results.job.selection
    return Results(images, [results.previews[index]] + [_preview(im, box) for im in images[1:]], results.layer,
                   results.masked, results.size, results.selection, results.job, results.expand_to, results.offset)


EXPAND_CHOICES = {"4:5": 4 / 5, "1:1": 1.0, "3:2": 3 / 2, "16:9": 16 / 9, "9:16": 9 / 16, "2:3": 2 / 3,
                  "+10% each side": 0.10, "+25% each side": 0.25}


def expanded_size(w: int, h: int, choice: str) -> tuple[int, int]:
    """New canvas for an Expand choice; it only ever grows. A ratio adds to the short side only."""
    v = EXPAND_CHOICES[choice]
    if choice.startswith("+"):
        return w + 2 * round(w * v), h + 2 * round(h * v)
    if w / h < v:
        return round(h * v), h
    return w, round(w / v)


def expand(choice: str, prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
           pc=None, comfy=None, count: int | None = None) -> Results:
    """Generative Expand: a bigger canvas (centred) whose new area is filled to match. The document is
    only changed when a result is placed."""
    pc, comfy = _clients(settings, pc, comfy)
    count = max(1, min(MAX_VARIATIONS, count or settings.variations))
    try:
        status("Reading the image from PhotoCraft…")
        doc, image = _read(pc)
        W, H = expanded_size(image.width, image.height, choice)
        if (W, H) == image.size:
            raise UserFacingError(f"The image already has a {choice} shape; pick another one to expand to.")
        ox, oy = (W - image.width + 1) // 2, (H - image.height + 1) // 2  # PhotoCraft rounds the centre up
        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        base.paste(image, (ox, oy))
        box = (0, 0, W, H)
        job = Job(base, imaging.fill_mask(base, box), box, prompt.strip())
        images = _candidates(job, settings, status, progress, cancel, comfy, count)
        return Results(images, [_preview(im, None) for im in images], f"Generative Expand – {choice}", False,
                       (image.width, image.height), doc.selection, job, (W, H), (ox, oy))
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e


def _alpha_origin(img: Image.Image) -> tuple[int, int] | None:
    box = img.getchannel("A").getbbox()
    return (box[0], box[1]) if box else None


def place(results: Results, index: int, settings: Settings, pc=None) -> str:
    """Add the chosen candidate to PhotoCraft as a layer. Refuses if the document or the selection
    changed while the user was choosing (the mask is made from the selection at this moment)."""
    pc, _ = _clients(settings, pc, object())
    image = results.images[index]
    try:
        with pc:
            pc.compatibility()  # resolves the command names for this PhotoCraft
            doc = pc.document()
            if (doc.width, doc.height) != results.size or (results.masked and doc.selection != results.selection):
                raise UserFacingError("The document or the selection changed since Generate, so the result "
                                      "would land in the wrong place. Put it back or Generate again.")
            if results.expand_to:
                W, H = results.expand_to
                pc.execute(pc._cmd("canvas size"), {"width": W, "height": H, "anchor": "center",
                                                "extensionColor": "transparent"})
                # Where did PhotoCraft put the old picture? Follow it if its centring rounds differently.
                landed = _alpha_origin(pc.render())
                expected = _alpha_origin(results.job.base)
                if landed and expected and landed != expected:
                    dx, dy = landed[0] - expected[0], landed[1] - expected[1]
                    moved = image.copy()  # the edge the shift uncovers keeps (generated) pixels, not a gap
                    moved.paste(image, (dx, dy))
                    image = moved
            path, rel = _exchange(settings, "fill" if results.masked else "edit")
            image.save(path)
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
