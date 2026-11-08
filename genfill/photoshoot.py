"""Photoshoot: one photo of a product or a person, one set, a run of different shots of it, opened in PhotoCraft
as one document with a layer per shot.

The subject is cut out on plain grey (so the model copies the subject, not the photo's surroundings). Shot 1 (the
hero) sees only the subject and fixes the set; every later shot sees the subject and the hero, so they share the
set and the light. Each shot is kept in memory until the whole run is placed, one file at a time (the exchange
folder keeps only the newest 10 pictures)."""
from __future__ import annotations

import random
import time
from pathlib import Path

from PIL import Image, ImageFilter

from . import imaging, shoot_store, shoots
from .comfy import ComfyError, build_create_workflow
from .photocraft_client import PhotoCraftError
from .pipeline import (Progress, Status, UserFacingError, _clients, _exchange, _fill_holes, _reference_head,
                       _segment, create_size, load_reference)
from .settings import Settings

SHOT_COUNTS = (4, 8, 12)
SUBJECT_SIDE = 1024
GREY = (128, 128, 128)
NOT_FOUND = "Couldn't find the product in the photo; using the whole photo."


def _square(img: Image.Image) -> Image.Image:
    side = max(img.size)
    square = Image.new("RGB", (side, side), GREY)
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return square.resize((SUBJECT_SIDE, SUBJECT_SIDE), Image.LANCZOS)


def prepare_subject(photo: Image.Image, shoot: shoots.Shoot, settings: Settings, status: Status, cancel,
                    comfy) -> Image.Image:
    """The subject of the photo as a 1024×1024 RGB cut-out on grey. A product is found with SAM 3 (nothing
    found: the whole photo is used); a person is cut out as the head, as in Fix faces."""
    rgb = photo.convert("RGB")
    if max(rgb.size) < SUBJECT_SIDE:  # small photos are enlarged so the model sees labels at a usable size
        k = SUBJECT_SIDE / max(rgb.size)
        rgb = rgb.resize((round(rgb.width * k), round(rgb.height * k)), Image.LANCZOS)
    if shoot.kind == "people":
        return _reference_head(rgb, settings, cancel, comfy).resize((SUBJECT_SIDE, SUBJECT_SIDE), Image.LANCZOS)
    mask = _segment(rgb, f"{shoot.subject or 'product'}:1", settings, lambda _f: None, cancel, comfy)
    mask = _fill_holes(mask).filter(ImageFilter.MaxFilter(5))
    box = mask.getbbox()
    if not box:
        status(NOT_FOUND)
        return _square(rgb)
    cut = Image.composite(rgb, Image.new("RGB", rgb.size, GREY), mask.filter(ImageFilter.GaussianBlur(1)))
    x0, y0, x1, y1 = box
    pad = max(x1 - x0, y1 - y0) // 8
    return _square(cut.crop((max(0, x0 - pad), max(0, y0 - pad), min(rgb.width, x1 + pad),
                             min(rgb.height, y1 + pad))))


def _make(comfy, settings: Settings, prompt: str, refs: list[str], size: tuple[int, int], cancel) -> Image.Image:
    """One picture of `size`, generated at the working size and resized back."""
    ww, wh = imaging.working_size(size[0], size[1], settings.megapixels)
    wf = build_create_workflow(prompt, random.randint(0, 2**32 - 1), settings.steps, ww, wh,
                               settings.workflow_models, refs)
    return comfy.run(wf, lambda _f: None, cancel).resize(size, Image.LANCZOS).convert("RGB")


def _place_all(pc, settings: Settings, run_id: str, images: dict[int, Image.Image], names: list[str]) -> None:
    """Shot 1 opens a new document, the rest are layers; each is saved, placed and tagged before the next."""
    with pc:
        pc.compatibility()
        for k in sorted(images):
            path, rel = _exchange(settings, "shoot")
            images[k].save(path)
            if k == 1:
                pc.open_document(rel)
                pc.rename_layer(f"Shot 1 – {names[0]}")
            else:
                pc.place_layer(rel, f"Shot {k} – {names[k - 1]}", masked=False)
            pc.set_layer_metadata(pc.active_layer(), shoot_store.tag_packet(run_id, k))


def run_photoshoot(shoot_name: str, photo_path: str | Path, settings: Settings, status: Status, progress: Progress,
                   cancel, pc=None, comfy=None) -> str:
    shoot = shoots.by_name(shoot_name)
    if shoot is None:
        raise UserFacingError(f"There's no photoshoot called '{shoot_name}'.")
    photo = load_reference(photo_path)
    pc, comfy = _clients(settings, pc, comfy)
    planned = [(s.name, s.text) for s in shoots.shots_for(shoot)]
    count = settings.shoot_count if settings.shoot_count in SHOT_COUNTS else 8
    planned = planned[:count]
    n = len(planned)
    size = create_size(settings.create_choice, settings.megapixels)
    try:
        status("Sending to ComfyUI…")
        comfy.check()
        status("Cutting out the subject…")
        subject = prepare_subject(photo, shoot, settings, status, cancel, comfy)
        subject_name = comfy.upload(subject, "genfill_shoot_subject.png")
        images: dict[int, Image.Image] = {}
        skipped: list[str] = []
        hero_name = ""
        for i, (name, text) in enumerate(planned, 1):
            status(f"Shot {i} of {n} – {name}…")
            progress((i - 1) / n)
            if cancel.is_set():
                break
            if i == 1:
                prompt, refs = shoots.hero_prompt(shoot.kind, shoot.set, text), [subject_name]
            else:
                prompt, refs = shoots.follow_prompt(shoot.kind, text), [subject_name, hero_name]
            image, error = None, None
            for _attempt in range(2):  # one retry; a cancel is never retried
                try:
                    image = _make(comfy, settings, prompt, refs, size, cancel)
                    if i == 1:
                        hero_name = comfy.upload(image.resize(imaging.working_size(*size, settings.megapixels),
                                                              Image.LANCZOS), "genfill_shoot_hero.png")
                    break
                except Exception as e:  # whatever goes wrong with one shot must not lose the finished ones
                    image, error = None, e
                    if cancel.is_set():
                        break
            if image is not None:
                images[i] = image
            elif cancel.is_set():
                break
            elif i == 1:
                raise UserFacingError(f"The first shot failed: {error}") from error
            else:
                skipped.append(str(i))
        if not images:
            raise UserFacingError("Cancelled.")
        run = shoot_store.ShootRun(shoot.name, shoot.kind, shoot.set, planned, settings.create_choice, size,
                                   time.time())
        run_id = shoot_store.save_run(Path(settings.shoots_dir), run, subject, images[1], settings.keep_results)
        status("Opening the shots in PhotoCraft…")
        _place_all(pc, settings, run_id, images, [name for name, _ in planned])
        progress(1.0)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    msg = f"Photoshoot ready: {len(images)} shots as layers. Select a layer and click Reshoot to redo one."
    if skipped:
        msg += f" Shots {', '.join(skipped)} failed."
    if cancel.is_set():
        msg += " (cancelled)"
    return msg
