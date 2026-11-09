"""Photoshoot: one photo of a product or a person, one set, a run of different shots of it, opened in PhotoCraft
as one document with a layer per shot.

The subject is cut out on plain grey (so the model copies the subject, not the photo's surroundings). Shot 1 (the
hero) sees only the subject and fixes the set; every later shot sees the subject and the hero, so they share the
set and the light. Each shot is kept in memory until the whole run is placed, one file at a time (the exchange
folder keeps only the newest 10 pictures)."""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

from PIL import Image, ImageFilter

from . import imaging, shoot_store, shoots
from .comfy import ComfyError, build_create_workflow
from .photocraft_client import PhotoCraftError
from .pipeline import (MAX_VARIATIONS, Placed, Progress, Results, Status, UserFacingError, _clients, _exchange,
                       _fill_holes, _preview, _reference_head, _segment_each, create_size, load_reference)
from .settings import Settings

SHOT_COUNTS = (4, 8, 12)
SUBJECT_SIDE = 1024
GREY = (128, 128, 128)
NO_SHOT = "Select a Photoshoot layer (\"Shot 3 – …\") in PhotoCraft's Layers panel to reshoot it."
NOT_FOUND = "Couldn't find the product in the photo; using the whole photo."


def _square(img: Image.Image) -> Image.Image:
    side = max(img.size)
    square = Image.new("RGB", (side, side), GREY)
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return square.resize((SUBJECT_SIDE, SUBJECT_SIDE), Image.LANCZOS)


def _main_score(mask: Image.Image) -> float:
    """How likely a found thing is the photo's subject: big and near the middle. In a phone photo of a jar on a
    cluttered shelf, the jar wins over the bottles behind it (SAM 3's own best match may be one of those)."""
    x0, y0, x1, y1 = mask.getbbox()
    w, h = mask.size
    area = (x1 - x0) * (y1 - y0) / (w * h)
    off = math.hypot((x0 + x1) / 2 - w / 2, (y0 + y1) / 2 - h / 2) / math.hypot(w / 2, h / 2)
    return area * (1 - off) ** 2


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
    masks = _segment_each(rgb, shoot.subject or "product", settings, cancel, comfy)
    if not masks:
        status(NOT_FOUND)
        return _square(rgb)
    mask = _fill_holes(max(masks, key=_main_score)).filter(ImageFilter.MaxFilter(5))
    box = mask.getbbox()
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
        with pc:  # PhotoCraft must be there before minutes of work go into the shots
            pc.compatibility()
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
        try:
            _place_all(pc, settings, run_id, images, [name for name, _ in planned])
        except PhotoCraftError as e:  # keep the finished shots as files rather than losing them
            folder = Path(settings.shoots_dir) / run_id
            for k, img in images.items():
                img.save(folder / f"shot_{k}.png")
            raise UserFacingError(f"{e} The {len(images)} shots are saved in {folder}.") from e
        progress(1.0)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    msg = f"Photoshoot ready: {len(images)} shots as layers. Select a layer and click Reshoot to redo one."
    if skipped:
        msg += f" Shots {', '.join(skipped)} failed."
    if cancel.is_set():
        msg += " (cancelled)"
    return msg


def reshoot(settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None) -> Results:
    """New versions of the shot whose layer is selected in PhotoCraft, to choose from like any other results
    (placing one is pipeline.swap). The stored hero is the look reference and is never replaced."""
    pc, comfy = _clients(settings, pc, comfy)
    try:
        with pc:
            pc.compatibility()
            layer = pc.active_layer()
            tag = shoot_store.tag_of(pc.layer_metadata(layer)) if layer is not None else None
            identity = pc._identity()
            doc = pc.document()
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e
    if tag is None:
        raise UserFacingError(NO_SHOT)
    run_id, k = tag
    got = shoot_store.load_run(Path(settings.shoots_dir), run_id)
    if got is None or not 1 <= k <= len(got[0].shots):
        raise UserFacingError(f"This shoot's details are no longer kept; GenFill keeps the last "
                              f"{settings.keep_results}.")
    run, subject, hero = got
    if (doc.width, doc.height) != tuple(run.size):
        raise UserFacingError("The document's size changed since the shoot, so a new shot would land in the "
                              "wrong place.")
    name, text = run.shots[k - 1]
    count = max(1, min(MAX_VARIATIONS, settings.variations))
    try:
        status("Sending to ComfyUI…")
        comfy.check()
        subject_name = comfy.upload(subject, "genfill_shoot_subject.png")
        if k == 1:
            prompt, refs = shoots.hero_prompt(run.kind, run.set_text, text), [subject_name]
        else:
            hero_name = comfy.upload(hero.resize(imaging.working_size(*run.size, settings.megapixels),
                                                 Image.LANCZOS), "genfill_shoot_hero.png")
            prompt, refs = shoots.follow_prompt(run.kind, text), [subject_name, hero_name]
        images = []
        for i in range(count):
            status(f"Shot {k} – {name}: version {i + 1} of {count}…")
            progress(i / count)
            if cancel.is_set():
                break
            try:
                images.append(_make(comfy, settings, prompt, refs, run.size, cancel))
            except ComfyError as e:  # keep the versions already made; only an empty result is an error
                if images:
                    break
                raise UserFacingError("Cancelled." if cancel.is_set() else str(e)) from e
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    if not images:
        raise UserFacingError("Cancelled.")
    progress(1.0)
    return Results(images, [_preview(im.convert("RGBA"), None) for im in images], f"Shot {k} – {name}", False,
                   tuple(run.size), None, tag=shoot_store.tag_packet(run_id, k),
                   placed=Placed(-1, identity, layer))
