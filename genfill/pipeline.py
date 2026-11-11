"""The jobs: Generate (fill the selection, or edit the whole image when nothing is selected; one or
more candidates to choose from) and Upscale (new, larger document)."""
from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from . import custom_workflow, depth, imaging, presets, results_store, settings as settings_file, styles
from .dpi import px
from .families import family
from .comfy import (ComfyClient, ComfyError, build_create_workflow, build_edit_workflow, build_fill_workflow,
                    build_cutout_workflow, build_depth_workflow, build_refine_workflow, build_seedvr_workflow,
                    build_segment_workflow, UPSCALE_MODEL)
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
    create: bool = False              # a new picture from the prompt alone (`base` is a blank of its size)
    custom: bool = False              # may run your own workflow (Settings) instead of GenFill's (Generate, Create)
    second_reference: Image.Image | None = None  # optional Create image 2; `reference` is image 1


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
    new_document: bool = False                # Create: the chosen one opens as a new document
    placed: "Placed | None" = None            # the one in PhotoCraft now (so another one can take its place)
    saved: str | None = None                  # the id it is kept under on disk (lasting results)
    tag: str | None = None                    # a layer tag of its own (a photoshoot shot): written as is, no set kept


@dataclass
class Placed:
    """Which result went into PhotoCraft, and where, so it can be swapped for another one."""
    index: int
    document: tuple                   # (name, path) of the document it went into
    layer: int | None                 # the layer GenFill added (None: opened as a new document)
    shift: tuple[int, int] = (0, 0)   # Expand: how far PhotoCraft's centring moved the old picture


def _shifted(image: Image.Image, shift: tuple[int, int]) -> Image.Image:
    """The result moved to follow the old picture (the edge the shift uncovers keeps generated pixels)."""
    if shift == (0, 0):
        return image
    moved = image.copy()
    moved.paste(image, shift)
    return moved


def _preview(img: Image.Image, box: Bounds | None) -> Image.Image:
    """The part worth comparing: the selection plus some surroundings (whole image for an edit)."""
    if box:
        x0, y0, x1, y1 = box
        mx, my = max(32, (x1 - x0) // 4), max(32, (y1 - y0) // 4)
        img = img.crop((max(0, x0 - mx), max(0, y0 - my), min(img.width, x1 + mx), min(img.height, y1 + my)))
    flat = Image.new("RGB", img.size, (128, 128, 128))
    flat.paste(img, mask=img.getchannel("A"))
    flat.thumbnail((px(PREVIEW_SIDE), px(PREVIEW_SIDE)), Image.LANCZOS)
    return flat


INSTRUCTION_VERBS = {"add", "put", "place", "insert", "make", "change", "replace", "turn", "remove", "give",
                     "draw", "fill", "show", "keep", "create", "let", "move", "swap", "paint"}


def fill_text(label: str, settings: Settings) -> str:
    """What a fill asks for. FLUX 3 only follows an instruction there: "a dog" repaints what was behind the
    selection, "Add a dog" draws one; FLUX.2 takes either."""
    words = label.split()
    if not words or not family(settings.model_family).fill_instruction \
            or words[0].lower().strip(",.:;!") in INSTRUCTION_VERBS:
        return label
    return "Add " + label


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


def _own_workflow(settings: Settings) -> tuple[dict, dict] | None:
    """Your workflow from Settings and which of its nodes take what (None: GenFill's own is used)."""
    if not settings.custom_workflow:
        return None
    wf = custom_workflow.load_saved(settings_file.CONFIG_DIR)
    if wf is None:
        raise UserFacingError(f"Your workflow ({Path(settings.custom_workflow).name}) can't be read any more. "
                              "Pick it again in Settings, or clear it to use GenFill's own.")
    slots = {k: v for k, v in settings.custom_slots.items() if v in wf}
    if not slots.get("result"):
        raise UserFacingError("Your workflow has no result picked. In Settings, choose which node's picture "
                              "is the result (a Save Image or Preview Image node).")
    return wf, slots


def _candidates(job: Job, settings: Settings, status: Status, progress: Progress, cancel, comfy, count: int,
                start: Image.Image | None = None, denoise: float = 1.0) -> list[Image.Image]:
    """Run the model `count` times with different seeds. `start` (a previous result) + `denoise` < 1
    redraws only part of it: variations close to that result."""
    src = start if start is not None else job.base
    edit = job.mask is None
    # A fill works on a crop around the selection when that gives it more pixels than the whole picture
    # (a small selection in a big photo comes back as sharp as the photo instead of soft).
    preserve_source = bool(job.custom and settings.custom_workflow and settings.custom_native_size)
    crop = None if edit or preserve_source else imaging.fill_crop(src.size, job.mask, settings.megapixels)
    if crop:
        cw, ch = crop[2] - crop[0], crop[3] - crop[1]
        ww, wh = imaging.crop_working_size(cw, ch, settings.megapixels)
    else:
        ww, wh = imaging.working_size(src.width, src.height, settings.megapixels)
    fam = family(settings.model_family)
    own = _own_workflow(settings) if job.custom else None  # (workflow, slots): yours from Settings
    slots = own[1] if own else {}
    native_size = bool(own and settings.custom_native_size)
    if native_size:
        ww, wh = (cw, ch) if crop else src.size
    if edit and not job.create and fam.encoder == "text" and not own:  # Create (and its Similar) is fine
        raise UserFacingError(f"{fam.label.split(' (')[0]} can only make new pictures, not edit one. "
                              "Select an area to fill, or pick another image model in Settings.")
    status("Sending to ComfyUI…")
    comfy.check()
    src_name = mask_name = None
    if not (job.create and start is None) or slots.get("picture"):  # creating needs no picture to start from
        prepared = _flat(src) if edit or own else imaging.prepare_source(src)
        sent = prepared.crop(crop) if crop else prepared
        if not own or slots.get("picture"):
            sent = sent.resize((ww, wh), Image.LANCZOS)
            if own and not edit and not slots.get("mask") and custom_workflow.picture_carries_mask(own[0], slots):
                sent_mask = (job.mask.crop(crop) if crop else job.mask).resize((ww, wh), Image.BILINEAR)
                sent = sent.convert("RGBA")  # the picture, with the selection in its alpha
                sent.putalpha(custom_workflow.mask_upload(sent_mask).getchannel("A"))
            src_name = comfy.upload(sent, "genfill_src.png")
    if not edit and (not own or slots.get("mask")):
        sent_mask = job.mask.crop(crop) if crop else job.mask
        sent_mask = sent_mask.resize((ww, wh), Image.BILINEAR)
        mask_name = comfy.upload(custom_workflow.mask_upload(sent_mask) if own else sent_mask.convert("RGB"),
                                 "genfill_mask.png")
    create_image_refs = job.create and start is None and not own and fam.encoder == "flux"
    raw_create_ref = create_image_refs and settings.model_family == "flux2"
    refs = []
    if job.reference is not None and (not own or slots.get("reference")):
        refs.append(comfy.upload(job.reference if raw_create_ref else _reference_upload(job.reference, settings.megapixels),
                                 "genfill_ref.png"))
    if create_image_refs and job.second_reference is not None:
        refs.append(comfy.upload(job.second_reference if raw_create_ref else
                                 _reference_upload(job.second_reference, settings.megapixels), "genfill_ref2.png"))
    who = "your workflow" if own else fam.label.split(' (')[0]
    left_out = "" if job.reference is None else \
        ("It takes no reference picture, so the reference is left out." if own and not refs else
         "It can't see pictures, so the reference is left out." if not own and fam.encoder == "text" else "")
    images = []
    for i in range(count):
        if cancel.is_set():
            raise UserFacingError("Cancelled.")
        what = "Creating" if job.create else "Editing the image" if edit else "Generating"
        status(f"{what} with {who}" + (f" ({i + 1} of {count})" if count > 1 else "") + "…"
               + (" (first run loads the model)" if i == 0 else "") + (" " + left_out if left_out else ""))
        if count > 1:
            progress(i / count)
        seed = random.randint(0, 2**32 - 1)
        on_progress = progress if count == 1 else (lambda _f: None)
        if own:
            wf = custom_workflow.prepare(own[0], slots, job.prompt, seed, src_name, mask_name,
                                         refs[0] if refs else None, None if native_size else (ww, wh),
                                         keep_seeds=settings.custom_keep_seeds)
            nodes = [slots["result"], *(n for n in settings.custom_result_nodes if n in own[0] and n != slots["result"])]
            outputs = (comfy.run_selected(wf, on_progress, cancel, nodes) if len(nodes) > 1 else
                       [comfy.run(wf, on_progress, cancel, node=slots.get("result"))])
            for out in outputs:
                out = out.convert("RGB")
                if native_size:
                    out = out.convert("RGBA")
                    if not job.create and out.size == job.base.size:
                        out.putalpha(job.base.getchannel("A"))
                elif job.create or edit:
                    out = (ImageOps.fit(out, job.base.size, method=Image.LANCZOS) if job.create
                           else out.resize(job.base.size, Image.LANCZOS)).convert("RGBA")
                    if not job.create:
                        out.putalpha(job.base.getchannel("A"))
                else:  # only the selection changes, whatever the workflow drew elsewhere
                    out = out.resize((ww, wh), Image.LANCZOS)
                    if crop:
                        full = prepared.copy()
                        full.paste(out.resize((cw, ch), Image.LANCZOS), crop[:2])
                        out = full
                    else:
                        out = out.resize(job.base.size, Image.LANCZOS)
                    out = imaging.match_colors(job.base, out, job.mask)
                    out = imaging.merge(job.base, out, imaging.blend_mask(job.base, job.selection))
                images.append(out)
            continue
        elif job.create and start is None:
            wf = build_create_workflow(job.prompt, seed, settings.steps, ww, wh, settings.workflow_models, refs,
                                       scale_references=raw_create_ref)
            out = ImageOps.fit(comfy.run(wf, on_progress, cancel), job.base.size,
                               method=Image.LANCZOS).convert("RGBA")
        elif edit:
            wf = build_edit_workflow(src_name, job.prompt, seed, settings.steps, ww, wh, settings.workflow_models, refs,
                                     denoise=denoise)
            out = comfy.run(wf, on_progress, cancel).resize(job.base.size, Image.LANCZOS).convert("RGBA")
            out.putalpha(job.base.getchannel("A"))  # transparent stays transparent
        else:
            wf = build_fill_workflow(src_name, mask_name, job.prompt, seed, settings.steps, ww, wh,
                                     settings.workflow_models, extra_refs=refs, denoise=denoise)
            out = comfy.run(wf, on_progress, cancel)
            if crop:  # back into the full picture; outside the crop it is the picture itself
                full = prepared.copy()
                full.paste(out.convert("RGB").resize((cw, ch), Image.LANCZOS), crop[:2])
                out = full
            out = imaging.match_colors(job.base, out, job.mask)
            out = imaging.merge(job.base, out, imaging.blend_mask(job.base, job.selection))
        images.append(out)
    progress(1.0)
    return images


def generate(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
             pc=None, comfy=None, count: int | None = None, reference: Image.Image | None = None,
             remove: bool = False, shape: Image.Image | None = None, name: str | None = None) -> Results:
    """With a selection: fill it. Without one: edit the whole picture as the prompt says.
    Makes `count` (default settings.variations) candidates with different seeds. `reference` is an
    extra picture the model can take things or a style from (it calls it "image 2"). `remove`: take
    away what's in the selection; `prompt` then names it ("the basket") or may be empty. (Blanking the
    area first was tried and is worse: Klein invents a new object to fill the hole.) `name`: what the
    layer's name says instead of the prompt (a preset's name)."""
    pc, comfy = _clients(settings, pc, comfy)
    if name in presets.NO_LORAS:
        settings = replace(settings, loras=[])
    label = prompt.strip()
    count = max(1, min(MAX_VARIATIONS, count or settings.variations))
    try:
        status("Reading the image from PhotoCraft…")
        doc, image = _read(pc, "Select what to remove first." if remove else None)
        edit = doc.selection is None
        styled = edit and not remove and name is None and bool(styles.text_for(settings.style)
                                                                or styles.angle_text(settings.angle))  # whole edits only
        named = None
        if edit and not remove and name is None and shape is None:
            found = _find_named(image, label, settings, status, cancel, comfy)
            if found is not None:  # edit only the thing the prompt names: select it, then fill it
                named, mask_shape = found
                status(f"Found {named}: selecting it in PhotoCraft…")
                _select(pc, settings, mask_shape)
                doc, image = _read(pc)
                edit, shape = doc.selection is None, mask_shape
                styled = False  # a style or angle is a look for the whole picture, not for one thing in it
        if edit and not label and not styled:
            raise UserFacingError("Make a selection first to fill it, or type what to change (for example "
                                  "'make it night') to edit the whole image.")
        mask = None if edit else imaging.grow_mask(shape) if shape is not None \
            else imaging.fill_mask(image, doc.selection)
        if mask is not None and not mask.getbbox():
            raise UserFacingError("The selection is outside the canvas.")
        if remove:
            told = remove_prompt(label)
        elif styled:  # with no prompt: just the style, keeping everything else
            told = styles.styled(label, settings.style, restyle=True, edit=True, angle=settings.angle,
                                 model_family=settings.model_family)
        else:
            told = label if edit else fill_text(label, settings)
        job = Job(image, mask, doc.selection, told, None if remove else reference, custom=not remove)
        images = _candidates(job, settings, status, progress, cancel, comfy, count)
        if edit and name in presets.KEEP_COLOURS:
            images = [imaging.keep_colors(image, im) for im in images]
        shown = name or label or (" · ".join(x for x in (settings.style, settings.angle) if x) if styled else "")
        name = ("Edit" if edit or named else "Remove" if remove else "Generative Fill")             + (f" – {shown[:40]}" if shown else "")
        native_document = bool(job.custom and settings.custom_workflow and settings.custom_native_size and
                               (mask is not None or any(out.size != image.size for out in images)))
        return Results(images, [_preview(im, None if native_document else doc.selection) for im in images], name, not edit,
                       (image.width, image.height), doc.selection, job, new_document=native_document)
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
    box = None if results.new_document else results.job.selection
    return Results(images, [results.previews[index]] + [_preview(im, box) for im in images[1:]], results.layer,
                   results.masked, results.size, results.selection, results.job, results.expand_to, results.offset,
                   results.new_document)


# -- smart selections (SAM 3) ---------------------------------------------------------------------
SEGMENT_MAX = 20  # objects found per word; SAM 3 stops at 1 unless told more
FIND = {"sky": ("sky", "the sky"), "people": ("person", "people"), "cars": ("car, truck, bus, motorcycle", "cars")}


def segment_query(what: str) -> str:
    """'people, cars' -> 'people:20, cars:20' (SAM 3 reads ':N' as how many to find)."""
    parts = [p.strip() for p in what.split(",") if p.strip()]
    return ", ".join(p if re.search(r":\s*\d+$", p) else f"{p}:{SEGMENT_MAX}" for p in parts)


def _mask_layer(mask: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", mask.size, (255, 255, 255, 0))
    layer.putalpha(mask)
    return layer


def find(what: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None,
         label: str | None = None):
    """(doc, image, mask): everything in the picture that matches `what`, found by SAM 3."""
    what, label = what.strip(), (label or what).strip()
    if not what:
        raise UserFacingError("Type what to select first, for example 'the dog', 'sky' or 'people'.")
    pc, comfy = _clients(settings, pc, comfy)
    try:
        status("Reading the image from PhotoCraft…")
        doc, image = _read(pc)
        status(f"Looking for {label} with SAM 3… (the first time loads the model)")
        mask = _segment(_flat(image), what, settings, progress, cancel, comfy)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    if not mask.point(lambda v: 255 if v > 127 else 0).getbbox():
        raise UserFacingError(f"Couldn't find {label} in the picture. Try a simpler word, like 'person', 'car', "
                              "'dog' or 'sky'.")
    return doc, image, mask


def _segment(rgb: Image.Image, what: str, settings: Settings, progress: Progress, cancel, comfy) -> Image.Image:
    """SAM 3's mask (picture-sized L) of everything matching `what` in an RGB picture."""
    comfy.check()
    problems = comfy.check_segment()
    if problems:
        raise UserFacingError(problems[0])
    ww, wh = imaging.working_size(rgb.width, rgb.height, settings.megapixels)
    name = comfy.upload(rgb.resize((ww, wh), Image.LANCZOS), "genfill_seg.png")
    out = comfy.run(build_segment_workflow(name, segment_query(what)), progress, cancel)
    return imaging.fill_small_holes(out.getchannel("R").resize(rgb.size, Image.BILINEAR))


def _segment_each(rgb: Image.Image, what: str, settings: Settings, cancel, comfy) -> list[Image.Image]:
    """SAM 3's masks (picture-sized L), one per thing matching `what` that it found (maybe none)."""
    comfy.check()
    problems = comfy.check_segment()
    if problems:
        raise UserFacingError(problems[0])
    ww, wh = imaging.working_size(rgb.width, rgb.height, settings.megapixels)
    name = comfy.upload(rgb.resize((ww, wh), Image.LANCZOS), "genfill_seg.png")
    try:
        outs = comfy.run_all(build_segment_workflow(name, segment_query(what), individual=True),
                             lambda _f: None, cancel)
    except ComfyError as e:
        if "out of bounds" in str(e):  # SAM 3 found nothing: its empty batch of masks fails MaskToImage
            return []
        raise
    masks = [imaging.fill_small_holes(o.getchannel("R").resize(rgb.size, Image.BILINEAR)) for o in outs]
    return [m for m in masks if m.getbbox()]


# -- edit by naming: "make the car red" with nothing selected edits only the car ---------------------------
NAMING = re.compile(r"^(?:please\s+)?(?:make|turn|change|paint|colou?r|recolou?r|replace|swap|give|dress)\s+"
                    r"(?:the|his|her|their|my|its|that|this|those|these)\s+(.+)$")
NAME_ENDS = {"to", "into", "with", "look", "looks", "more", "less", "a", "an", "so", "as", "and", "but", "in",
             "on", "at", "from", "be", "much", "very", "slightly", "bit", "same", "like", "for", "of", "all"}
NOT_A_THING = {"whole", "entire", "picture", "photo", "image", "scene", "everything", "lighting", "light", "mood",
               "weather", "colours", "colors", "style", "time", "season", "atmosphere", "background", "photograph",
               "shot", "composition", "contrast", "tone", "tones", "exposure"}
NAMED_MAX_COVER = 0.85  # a "thing" covering more of the picture than this is the picture: edit it all


def named_target(prompt: str) -> list[str]:
    """What an edit prompt names, as SAM 3 queries to try, longest first: 'make the red car bright blue'
    -> ['red car bright blue', 'red car bright', 'red car', 'red', ...] cut at words that end a name
    ('change the sofa to leather' -> ['sofa']). [] when it names no thing ('make it night')."""
    m = NAMING.match(re.sub(r"[.!?]+$", "", prompt.strip().lower()))
    if not m:
        return []
    words = []
    for w in m.group(1).replace(",", " ").split():
        if w in NAME_ENDS:
            break
        words.append(w)
    words = words[:4]
    if not words or words[0] in NOT_A_THING:
        return []
    return [" ".join(words[:n]) for n in range(len(words), 0, -1)]


def _find_named(image: Image.Image, prompt: str, settings: Settings, status: Status, cancel, comfy):
    """(what, mask) of the thing an edit prompt names, found by SAM 3, or None (nothing named, not found,
    too big, or SAM 3 not installed): then the whole picture is edited as before. 'the car' is the most
    likely subject among the cars; 'the cars' is all of them."""
    tries = named_target(prompt)
    if not tries:
        return None
    rgb = _flat(image)
    for what in tries:
        if what.split()[-1] in NOT_A_THING:
            continue
        status(f"Looking for the {what} with SAM 3… (the first time loads the model)")
        try:
            masks = _segment_each(rgb, what, settings, cancel, comfy)
        except (UserFacingError, ComfyError):
            if cancel.is_set():
                raise
            return None  # no SAM 3: edit the whole picture as before
        if not masks:
            continue
        last = what.split()[-1]
        if last.endswith("s") and not last.endswith("ss"):  # plural: every one of them
            mask = masks[0]
            for m in masks[1:]:
                mask = ImageChops.lighter(mask, m)
        else:
            from .photoshoot import _main_score
            mask = max(masks, key=_main_score)
        cover = sum(mask.convert("L").histogram()[128:]) / (mask.width * mask.height)
        if cover > NAMED_MAX_COVER:
            return None
        return f"the {what}", mask
    status("Didn't find what the prompt names: editing the whole picture…")
    return None


# -- sketch to image: rough strokes on a layer become real content --------------------------------------
# A plain outline drawn in black reads to the model as scribbles to clean away ("no drawn lines remain" made it
# erase the drawing), so the prompt says the lines are the object's outline and the object must be there.
SKETCH_PROMPT = ("In this area there is a rough hand-drawn sketch, simple lines or shapes, of what must be here: "
                 "{what}. Replace the sketch with the real thing, photographed: the same shape, size, position and "
                 "angle as the sketch, its lines becoming the real object's edges, in real materials and the sketch's "
                 "colours if it has any. Match the photo's light, shadows, perspective, focus and grain.")
SKETCH_PAD = 0.12      # the redrawn area reaches this far (of the sketch's longer side) around the strokes
SKETCH_MIN_PAD = 24    # pixels
SKETCH_MAX_COVER = 0.9  # a layer covering more of the picture than this is a photo, not a sketch
SKETCH_HOW = ("draw on a new empty layer (Layer › New › Layer), keep it the active layer, type what it is, then "
              "choose Create › From my sketch.")


def sketch_area(bounds, size: tuple[int, int]) -> tuple[int, int, int, int]:
    """The rectangle redrawn for a sketch with these pixel bounds: the strokes plus a margin, inside the picture."""
    x0, y0, x1, y1 = (int(v) for v in bounds)
    pad = max(SKETCH_MIN_PAD, round(max(x1 - x0, y1 - y0) * SKETCH_PAD))
    return max(0, x0 - pad), max(0, y0 - pad), min(size[0], x1 + pad), min(size[1], y1 + pad)


def sketch_to_image(prompt: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None,
                    comfy=None, count: int | None = None) -> Results:
    """The active layer holds a rough drawing: select the area around it and redraw that area as real content
    shaped like the drawing (the model sees the drawing in the picture). The sketch layer is left as it is,
    under the result."""
    pc, comfy = _clients(settings, pc, comfy)
    # Without the user's LoRAs: with one on, a thin outline sketch was erased every time (0 of 3), without it
    # 2 of 3 became the drawn thing. A LoRA trained on something else holds the picture as it was.
    settings = replace(settings, loras=[])
    what = prompt.strip()
    try:
        status("Reading the sketch layer from PhotoCraft…")
        with pc:
            problems = pc.compatibility()
            if problems:
                raise UserFacingError("This PhotoCraft version isn't compatible yet: " + " ".join(problems))
            doc = pc.document()
            layer = pc.active_layer_info()
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e
    if not layer or not layer.get("bounds"):
        raise UserFacingError("The active layer is empty. To turn a sketch into a picture, " + SKETCH_HOW)
    if not layer.get("visible", True):
        raise UserFacingError("The sketch layer is hidden: show it (the eye in the Layers panel) and try again.")
    x0, y0, x1, y1 = layer["bounds"]
    if layer.get("name") == "Background" or (x1 - x0) * (y1 - y0) > SKETCH_MAX_COVER * doc.width * doc.height:
        raise UserFacingError("The active layer looks like the photo, not a sketch. " + SKETCH_HOW[0].upper()
                              + SKETCH_HOW[1:])
    area = sketch_area(layer["bounds"], (doc.width, doc.height))
    mask = Image.new("L", (doc.width, doc.height), 0)
    ImageDraw.Draw(mask).rectangle((area[0], area[1], area[2] - 1, area[3] - 1), fill=255)
    status("Selecting the sketch's area in PhotoCraft…")
    _select(pc, settings, mask)
    told = SKETCH_PROMPT.format(what=what if what else "what the sketch shows")
    return generate(told, settings, status, progress, cancel, pc=pc, comfy=comfy, count=count,
                    name=f"Sketch – {what}" if what else "Sketch")


def _reference_face(reference: Image.Image, settings: Settings, progress: Progress, cancel, comfy) -> Image.Image:
    """The biggest face in the reference picture with some surroundings (what the model copies from)."""
    rgb = reference.convert("RGB")
    try:
        mask = _segment(rgb, "face", settings, lambda _f: None, cancel, comfy)
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    boxes = imaging.shapes(mask)
    if not boxes:
        raise UserFacingError("Couldn't find a face in the reference picture. Use a photo where the face is "
                              "clearly visible.")
    return rgb.crop(imaging.around(boxes[0], rgb.size, 0.5, 256))


def _fill_holes(mask: Image.Image) -> Image.Image:
    """Solid shapes: whatever the picture's border can't reach is inside (SAM can miss bits of skin)."""
    solid = mask.point(lambda v: 255 if v > 127 else 0)
    outside = solid.copy()
    for xy in ((0, 0), (solid.width - 1, 0), (0, solid.height - 1), (solid.width - 1, solid.height - 1)):
        if outside.getpixel(xy) == 0:
            ImageDraw.floodfill(outside, xy, 128)
    return outside.point(lambda v: 0 if v == 128 else 255)


def _reference_head(reference: Image.Image, settings: Settings, cancel, comfy) -> Image.Image:
    """The biggest head in the reference picture with its hair, cut out on plain grey in a square: with no
    shoulders, clothes or background in it, a head swap takes only the head."""
    rgb = reference.convert("RGB")
    try:
        face = _segment(rgb, "face", settings, lambda _f: None, cancel, comfy)
        boxes = imaging.shapes(face)
        if not boxes:
            raise UserFacingError("Couldn't find a face in the reference picture. Use a photo where the face is "
                                  "clearly visible.")
        head = _segment(rgb, "head:1, hair:1", settings, lambda _f: None, cancel, comfy)
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    f = boxes[0]
    # keep only the shape around the biggest face (other people's heads are left out)
    near = Image.new("L", rgb.size, 0)
    fw, fh = f[2] - f[0], f[3] - f[1]
    ImageDraw.Draw(near).rectangle((f[0] - fw, f[1] - fh, f[2] + fw, f[3] + int(0.6 * fh)), fill=255)
    m = ImageChops.multiply(_fill_holes(ImageChops.lighter(head, face)), near).filter(ImageFilter.MaxFilter(5))
    box = m.getbbox() or f
    cut = Image.composite(rgb, Image.new("RGB", rgb.size, (128, 128, 128)), m.filter(ImageFilter.GaussianBlur(2)))
    cut = cut.crop(box)
    side = max(cut.size)
    square = Image.new("RGB", (side, side), (128, 128, 128))
    square.paste(cut, ((side - cut.width) // 2, (side - cut.height) // 2))
    return square.resize((HEAD_SIDE, HEAD_SIDE), Image.LANCZOS)


def _square_edge(size: tuple[int, int]) -> Image.Image:
    """The whole crop, fading out only at its border (a head swap also redraws the hair around the face)."""
    w, h = size
    inset = max(6, min(w, h) // 40)
    edge = Image.new("L", size, 0)
    ImageDraw.Draw(edge).rectangle((inset, inset, w - 1 - inset, h - 1 - inset), fill=255)
    return edge.filter(ImageFilter.GaussianBlur(inset * 0.7))


def _select(pc, settings: Settings, mask: Image.Image) -> None:
    path, rel = _exchange(settings, "mask")
    _mask_layer(mask).save(path)
    try:
        with pc:
            pc.compatibility()
            pc.select_mask(rel)
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e


def select_things(what: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None,
                  label: str | None = None) -> str:
    """Select everything that matches `what` in PhotoCraft."""
    pc, comfy = _clients(settings, pc, comfy)
    _, _, mask = find(what, settings, status, progress, cancel, pc, comfy, label)
    status("Selecting in PhotoCraft…")
    _select(pc, settings, mask)
    return f"Selected {label or what.strip()}"


def remove_things(what: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None,
                  label: str | None = None, count: int | None = None) -> Results:
    """Find everything that matches `what`, select it, and make versions with it removed (Distraction
    Removal). Only the found shapes are redrawn, not the rectangle around them."""
    pc, comfy = _clients(settings, pc, comfy)
    label = (label or what).strip()
    _, _, mask = find(what, settings, status, progress, cancel, pc, comfy, label)
    status("Selecting in PhotoCraft…")
    _select(pc, settings, mask)
    return generate(label, settings, status, progress, cancel, pc=pc, comfy=comfy, count=count, remove=True,
                    shape=mask)


def remove_background(what: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None,
                      comfy=None) -> str:
    """Keep only `what` (nothing typed: the main subject, found by BiRefNet): a layer mask on the active
    layer hides the rest (nothing is deleted)."""
    pc, comfy = _clients(settings, pc, comfy)
    if what.strip():
        _, _, mask = find(what, settings, status, progress, cancel, pc, comfy)
    else:
        mask = _main_subject(settings, status, progress, cancel, pc, comfy)
    status("Masking the layer in PhotoCraft…")
    _select(pc, settings, mask)
    try:
        with pc:
            pc.compatibility()
            pc.mask_active_layer()
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e
    return f"Background removed, kept {what.strip() or 'the main subject'}"


def _main_subject(settings: Settings, status: Status, progress: Progress, cancel, pc, comfy) -> Image.Image:
    """BiRefNet's mask (picture-sized L) of the picture's main subject."""
    try:
        status("Reading the image from PhotoCraft…")
        _, image = _read(pc)
        comfy.check()
        problems = comfy.check_cutout()
        if problems:
            raise UserFacingError(problems[0])
        status("Finding the main subject… (the first time loads the model)")
        rgb = _flat(image)
        ww, wh = imaging.working_size(rgb.width, rgb.height, settings.megapixels)
        name = comfy.upload(rgb.resize((ww, wh), Image.LANCZOS), "genfill_cutout.png")
        mask = comfy.run(build_cutout_workflow(name), progress, cancel).getchannel("R").resize(rgb.size, Image.BILINEAR)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    if not mask.point(lambda v: 255 if v > 127 else 0).getbbox():
        raise UserFacingError("Couldn't find a main subject. Type what to keep (for example 'the woman') and pick "
                              "Remove background again.")
    return mask


# -- depth tools: a depth map from Depth Anything V2, the effects worked out from the picture's own pixels ---
DEPTH_EFFECTS = {"blur": "Lens blur", "haze": "Haze", "fog": "Fog"}


def _depth(rgb: Image.Image, cancel, comfy) -> "depth.np.ndarray":
    comfy.check()
    if not comfy.has_depth():
        raise UserFacingError("Depth tools need Depth Anything V2 in ComfyUI (the comfyui_controlnet_aux nodes).")
    side = 1024 / max(rgb.size)
    small = rgb.resize((max(64, round(rgb.width * side)), max(64, round(rgb.height * side))), Image.LANCZOS)         if side < 1 else rgb
    name = comfy.upload(small, "genfill_depth.png")
    out = comfy.run(build_depth_workflow(name), lambda _f: None, cancel)
    return depth.as_depth(out, rgb.size)


def depth_select(which: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None,
                 comfy=None) -> str:
    """Select the near part of the picture ("foreground") or the rest ("background")."""
    pc, comfy = _clients(settings, pc, comfy)
    try:
        status("Reading the image from PhotoCraft…")
        _, image = _read(pc)
        status("Working out the depth… (the first time loads the model)")
        d = _depth(_flat(image), cancel, comfy)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    mask = depth.foreground(d)
    if which == "background":
        mask = ImageChops.invert(mask)
    status("Selecting in PhotoCraft…")
    _select(pc, settings, mask)
    return f"Selected the {which}"


def depth_effect(kind: str, settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None,
                 focus_on: str = "") -> Results:
    """Lens blur ("blur": sharp at the main subject, or at `focus_on` found by SAM 3), "haze" or "fog", as a
    whole-picture layer. Nothing is redrawn."""
    pc, comfy = _clients(settings, pc, comfy)
    focus_on = focus_on.strip()
    try:
        status("Reading the image from PhotoCraft…")
        doc, image = _read(pc)
        flat = _flat(image)
        status("Working out the depth… (the first time loads the model)")
        progress(0.1)
        d = _depth(flat, cancel, comfy)
        mask = None
        if kind == "blur" and focus_on:
            status(f"Looking for {focus_on} to focus on…")
            progress(0.4)
            mask = _segment(flat, focus_on, settings, lambda _f: None, cancel, comfy)
            if not mask.point(lambda v: 255 if v > 127 else 0).getbbox():
                raise UserFacingError(f"Couldn't find {focus_on} in the picture. Try a simpler word, like "
                                      "'person' or 'dog'.")
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    if cancel.is_set():
        raise UserFacingError("Cancelled.")
    status(f"{DEPTH_EFFECTS[kind]}…")
    progress(0.7)
    if kind == "blur":
        out = depth.lens_blur(image, d, depth.focus_range(d, mask))
    else:
        out = depth.haze(image, d, kind)
    progress(1.0)
    layer = DEPTH_EFFECTS[kind] + (f" – {focus_on[:30]}" if kind == "blur" and focus_on else "")
    return Results([out], [_preview(out, None)], layer, False, image.size, doc.selection)


FACE_PROMPT = ("Same person, same face, same expression, pose, lighting and colours. A sharp, detailed, natural "
               "face in focus: clear eyes, natural skin texture, crisp features, high-resolution photo.")
REPLACE_FACE_PROMPT = ("Replace the face of the person in image 1 with the face of the person in image 2: their "
                       "facial features, eyes, nose, mouth, face shape and skin. Keep image 1's expression, head "
                       "angle, hair, lighting and colours exactly. A sharp, natural photo.")
# Replace face with the BFS head-swap LoRA (its author's prompt): image 1 = the picture, image 2 = the head
HEAD_SWAP_PROMPT = ("head_swap: start with Picture 1 as the base image, keeping its lighting, environment, and "
                    "background. remove the head from Picture 1 completely and replace it with the head from "
                    "Picture 2, strictly preserving the hair, eye color, and nose structure of Picture 2. copy the "
                    "direction of the eye, head rotation, and micro-expressions from Picture 1. Keep the body, "
                    "clothing, pose, framing, and background intact.")
HEAD_SIDE = 512        # the reference head is sent as a square this big (as in the LoRA author's workflow)
FACE_CONTEXT = 0.6   # around each face, this much of its size is shown too (hair, neck, light)
FACE_MIN_SIDE = 96     # faces smaller than this many pixels are left alone
FACE_MAX = 12          # at most this many faces (the biggest) per picture


def _face_to_replace(boxes: list, selection) -> tuple:
    """Replace face changes one face: the one the selection covers most, else the biggest (boxes come
    largest first)."""
    if not selection:
        return boxes[0]

    def covered(b):
        w = min(b[2], selection[2]) - max(b[0], selection[0])
        h = min(b[3], selection[3]) - max(b[1], selection[1])
        return max(0, w) * max(0, h)
    best = max(boxes, key=covered)
    if not covered(best):
        raise UserFacingError("There's no face inside the selection. Select around the face to replace, or "
                              "deselect to replace the biggest face.")
    return best


def fix_faces(settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None,
              count: int | None = None, prompt: str = "", reference: Image.Image | None = None) -> Results:
    """Face detailer: find the faces (SAM 3), redraw each one up close at high resolution but only
    partly (settings.face_strength), so they get sharper and stay the same people. The result is a
    layer holding just the faces with soft edges. `prompt` adds to the instruction ("smiling").
    `reference` (Replace face): a photo of the person the face should become. Only one face changes (the
    one in the selection, else the biggest), as a Klein edit: the face crop is image 1 (expression, angle,
    hair and light come from it), the reference face image 2 (who it is). With the same person it comes
    back sharper and more like the photo. With the face-swap LoRA (settings.face_swap_lora, when ComfyUI
    has it) the whole head is swapped instead, hair included: image 2 is the reference's head cut out on
    grey (no body to copy), and the whole square around the face is replaced."""
    pc, comfy = _clients(settings, pc, comfy)
    count = max(1, min(MAX_VARIATIONS, count or settings.variations))
    doc, image, mask = find("face", settings, status, progress, cancel, pc, comfy, "faces")
    boxes = [b for b in imaging.shapes(mask) if min(b[2] - b[0], b[3] - b[1]) >= FACE_MIN_SIDE // 4][:FACE_MAX]
    if not boxes:
        raise UserFacingError("Couldn't find faces big enough to fix in the picture.")
    if reference is not None:
        boxes = [_face_to_replace(boxes, doc.selection)]
    flat = _flat(image)
    refs, models, head_swap = [], settings.workflow_models, False
    if reference is not None:
        try:
            head_swap = family(settings.model_family).head_swap and bool(settings.face_swap_lora) \
                and comfy.has_lora(settings.face_swap_lora)
        except ComfyError as e:
            raise UserFacingError(str(e)) from e
        if head_swap:
            # the face-swap LoRA instead of the user's own LoRAs: it swaps the whole head, hair included
            status("Cutting out the head in the reference picture…")
            refs = [comfy.upload(_reference_head(reference, settings, cancel, comfy), "genfill_face_ref.png")]
            models = {**settings.workflow_models, "loras": [{"name": settings.face_swap_lora, "strength": 1.0}]}
            text = (HEAD_SWAP_PROMPT + " " + prompt.strip()).strip()
        else:
            status("Looking for the face in the reference picture…")
            ref_face = _reference_face(reference, settings, progress, cancel, comfy)
            # full size: a smaller reference than the face being changed loses to it (the old face comes back)
            refs = [comfy.upload(_reference_upload(ref_face, settings.megapixels), "genfill_face_ref.png")]
            text = (REPLACE_FACE_PROMPT + " " + prompt.strip()).strip()
        strength = 1.0
    else:
        text = f"{FACE_PROMPT} {prompt.strip()}".strip()
        strength = min(0.9, max(0.1, float(settings.face_strength)))
    # SeedVR2 restores what is there (sharper, the same people); FLUX redraws, which a reference picture or
    # a typed wish ("smiling") needs. SeedVR2 gives practically the same result each time: one result.
    try:
        seedvr = (reference is None and not prompt.strip() and bool(settings.seedvr_model)
                  and comfy.has_seedvr(settings.seedvr_model))
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    if seedvr:
        count = 1
    jobs = []  # (crop, face area in the crop's coordinates as a soft mask)
    for box in boxes:
        crop = imaging.around(box, image.size, FACE_CONTEXT, FACE_MIN_SIDE * 2)
        face = mask.crop(crop)
        fw = box[2] - box[0]
        soft = _square_edge(face.size) if head_swap else imaging.grow_mask(face, grow=max(4, fw // 12),
                                                                             feather=max(4, fw // 8))
        jobs.append((crop, soft))
    try:
        comfy.check()
        uploads = []
        for i, (crop, _) in enumerate(jobs):
            cw, ch = crop[2] - crop[0], crop[3] - crop[1]
            ww, wh = imaging.crop_working_size(cw, ch, settings.megapixels)
            uploads.append((comfy.upload(flat.crop(crop).resize((ww, wh), Image.LANCZOS), f"genfill_face{i}.png"),
                            ww, wh))
        images, total, done = [], count * len(jobs), 0
        for k in range(count):
            layer = flat.copy()
            alpha = Image.new("L", image.size, 0)
            for (crop, soft), (name, ww, wh) in zip(jobs, uploads):
                if cancel.is_set():
                    raise UserFacingError("Cancelled.")
                status(f"Fixing face {done % len(jobs) + 1} of {len(jobs)}"
                       + (f" (result {k + 1} of {count})" if count > 1 else "") + "…")
                progress(done / total)
                if seedvr:
                    wf = build_seedvr_workflow(name, random.randint(0, 2**32 - 1), settings.seedvr_model)
                else:
                    # Replace face: a full edit that shows the crop (keeps the scene); Fix faces: a partial
                    # redraw that doesn't (Klein would copy the blur)
                    wf = build_edit_workflow(name, text, random.randint(0, 2**32 - 1), settings.upscale_steps, ww,
                                             wh, models, refs, denoise=strength, self_reference=bool(refs))
                out = comfy.run(wf, lambda _f: None, cancel).convert("RGB")
                cw, ch = crop[2] - crop[0], crop[3] - crop[1]
                out = out.resize((cw, ch), Image.LANCZOS)
                if not head_swap:  # (a head swap redraws the whole square: no outside left to match to)
                    out = imaging.match_colors(image.crop(crop), out, soft)
                layer.paste(out, crop[:2], soft)
                alpha.paste(ImageChops.lighter(alpha.crop(crop), soft), crop[:2])
                done += 1
            result = layer.convert("RGBA")
            result.putalpha(alpha)  # just the faces, soft edges: paint on it or lower its opacity
            images.append(result)
        progress(1.0)
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    xs = [c for c, _ in jobs]
    show = (min(c[0] for c in xs), min(c[1] for c in xs), max(c[2] for c in xs), max(c[3] for c in xs))
    previews = []
    for im in images:
        seen = image.copy()
        seen.alpha_composite(im)
        previews.append(_preview(seen, show))
    name = "Replace face" if reference is not None else f"Fix faces ({len(jobs)})"
    return Results(images, previews, name, False, image.size, doc.selection)


ENHANCE_PROMPT = ("The same photo taken by a professional with a high-end camera: crisp fine detail, sharp focus, "
                  "clean natural textures, beautiful balanced lighting, rich natural colours, high dynamic range. "
                  "Same composition, same people, same objects, same place.")
ENHANCE_DETAIL = 0.3   # the detail pass redraws this much, guided by the picture itself (stays faithful)
ENHANCE_MAX_RESULTS = 2


def enhance(settings: Settings, status: Status, progress: Progress, cancel, pc=None, comfy=None,
            count: int | None = None, prompt: str = "") -> Results:
    """The same picture, crisper and better lit, in two passes:
    1. light and colour: a small partial redraw of the whole picture (settings.enhance_strength), of
       which only the light and colour are kept (imaging.transfer_look), so it can't change what's in it;
    2. detail: that is lightly redrawn (ENHANCE_DETAIL) with the picture itself as Klein's reference, so
       it follows the original closely, in tiles at full size (small pictures enlarged for it).
    The result is a whole-picture layer "Enhance"."""
    pc, comfy = _clients(settings, pc, comfy)
    count = max(1, min(ENHANCE_MAX_RESULTS, count or settings.variations))
    try:
        status("Reading the image from PhotoCraft…")
        doc, image = _read(pc)
        flat = _flat(image)
        W, H = image.size
        text = f"{ENHANCE_PROMPT} {prompt.strip()}".strip()
        strength = min(0.8, max(0.1, float(settings.enhance_strength)))
        big = W * H > settings.megapixels * 1_000_000
        boxes = imaging.tile_boxes(W, H) if big else [(0, 0, W, H)]
        comfy.check()
        gw, gh = imaging.working_size(W, H, settings.megapixels)
        whole = comfy.upload(flat.resize((gw, gh), Image.LANCZOS), "genfill_enhance.png")
        images, total, done = [], count * (1 + len(boxes)), 0

        def redraw(name, w, h, denoise, follow):
            if cancel.is_set():
                raise UserFacingError("Cancelled.")
            progress(done / total)
            wf = build_edit_workflow(name, text, random.randint(0, 2**32 - 1), settings.upscale_steps, w, h,
                                     settings.workflow_models, denoise=denoise, self_reference=follow)
            return comfy.run(wf, lambda _f: None, cancel).convert("RGB")

        for k in range(count):
            res = f" (result {k + 1} of {count})" if count > 1 else ""
            status(f"Enhancing the light and colour{res}…" + (" (first run loads the model)" if k == 0 else ""))
            looked = redraw(whole, gw, gh, strength, False)
            done += 1
            base = imaging.transfer_look(flat, looked)  # only its light and colour
            tiles = []
            for i, box in enumerate(boxes, 1):
                status(f"Enhancing detail" + (f", part {i} of {len(boxes)}" if len(boxes) > 1 else "") + f"{res}…")
                crop = base.crop(box)
                cw, ch = imaging.crop_working_size(crop.width, crop.height, settings.megapixels) if not big \
                    else (max(16, (crop.width // 16) * 16), max(16, (crop.height // 16) * 16))
                name = comfy.upload(crop.resize((cw, ch), Image.LANCZOS), f"genfill_enhance_{i}.png")
                tiles.append((box, redraw(name, cw, ch, ENHANCE_DETAIL, True)))
                done += 1
            out = imaging.blend_tiles((W, H), tiles) if big else tiles[0][1].resize((W, H), Image.LANCZOS)
            result = out.convert("RGBA")
            result.putalpha(image.getchannel("A"))
            images.append(result)
        progress(1.0)
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
    return Results(images, [_preview(im, None) for im in images], "Enhance" + (f" – {prompt.strip()[:30]}"
                   if prompt.strip() else ""), False, image.size, doc.selection)


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


CREATE_CHOICES = {"1:1": 1.0, "4:5": 4 / 5, "3:2": 3 / 2, "16:9": 16 / 9, "9:16": 9 / 16, "2:3": 2 / 3}


CREATE_MIN_AREA = 1920 * 1080  # a created picture has at least Full HD's pixels (16:9 comes out exactly 1920×1080)


def create_size(choice: str, megapixels: float) -> tuple[int, int]:
    """The new picture's size in the chosen shape: about `megapixels`, at least Full HD's pixel count, even
    sides (it is generated at the nearest multiples of 16, which Klein expects, and resized to this)."""
    r = CREATE_CHOICES.get(choice, 1.0)
    h = (max(megapixels * 1e6, CREATE_MIN_AREA) / r) ** 0.5
    return round(h * r / 2) * 2, round(h / 2) * 2


def create(choice: str, prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
           comfy=None, count: int | None = None, reference: Image.Image | None = None) -> Results:
    """A new picture from the prompt (text to image). Nothing is read from PhotoCraft; the chosen
    result opens there as a new document."""
    _, comfy = _clients(settings, object(), comfy)
    label = prompt.strip()
    restyle_reference = not label and reference is not None and bool(settings.style) and not settings.create_second_reference
    if restyle_reference and family(settings.model_family).encoder == "text" and not settings.custom_workflow:
        raise UserFacingError("This image model can't see the reference picture. Choose a reference-capable model "
                              "to create from the selected style without a prompt.")
    if not label and not restyle_reference:
        raise UserFacingError("Describe the picture to create first, for example 'a lighthouse at dusk, "
                              "film photo'.")
    second = (load_reference(settings.create_second_reference)
              if reference is not None and settings.create_second_reference else None)
    if second is not None and not all(re.search(rf"\bimage\s*{n}\b", label, flags=re.IGNORECASE)
                                  for n in (1, 2)):
        raise UserFacingError('With two Create images, mention both "image 1" and "image 2" in the prompt '
                              'so the model knows what to take from each.')
    if reference is not None and second is None:  # with one picture, an old "image 2" prompt means image 1
        label_for_model = re.sub(r"\bimage\s*2\b", "image 1", label, flags=re.IGNORECASE)
    else:
        label_for_model = label
    if restyle_reference:
        label_for_model = (styles.styled("", settings.style, restyle=True, angle=settings.angle,
                                         model_family=settings.model_family)
                           + " Use image 1 as the source picture.")
    else:
        label_for_model = styles.styled(label_for_model, settings.style, angle=settings.angle,
                                        reference_image=1 if reference is not None else None,
                                        model_family=settings.model_family)
    count = max(1, min(MAX_VARIATIONS, count or settings.variations))
    W, H = create_size(choice, settings.megapixels)
    base = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    job = Job(base, None, None, label_for_model, reference, create=True, custom=True, second_reference=second)
    try:
        images = _candidates(job, settings, status, progress, cancel, comfy, count)
    except ComfyError as e:
        raise UserFacingError(str(e)) from e
    return Results(images, [_preview(im, None) for im in images],
                   f"Create – {(label or settings.style)[:40]}", False, (W, H), None,
                   job, new_document=True)


def _alpha_origin(img: Image.Image) -> tuple[int, int] | None:
    box = img.getchannel("A").getbbox()
    return (box[0], box[1]) if box else None


def place(results: Results, index: int, settings: Settings, pc=None) -> str:
    """Add the chosen candidate to PhotoCraft as a layer. Refuses if the document or the selection
    changed while the user was choosing (the mask is made from the selection at this moment)."""
    pc, _ = _clients(settings, pc, object())
    image = results.images[index]
    if results.new_document:
        path, rel = _exchange(settings, "created")
        image.convert("RGB").save(path)
        try:
            with pc:
                pc.compatibility()
                pc.open_document(rel)
        except PhotoCraftError as e:
            raise UserFacingError(str(e)) from e
        results.placed = Placed(index, (None, None), None)
        return f"Created {image.width}×{image.height}, opened as a new document"
    try:
        with pc:
            pc.compatibility()  # resolves the command names for this PhotoCraft
            doc = pc.document()
            if (doc.width, doc.height) != results.size or (results.masked and doc.selection != results.selection):
                raise UserFacingError("The document or the selection changed since Generate, so the result "
                                      "would land in the wrong place. Put it back or Generate again.")
            shift = (0, 0)
            if results.expand_to:
                W, H = results.expand_to
                pc.execute(pc._cmd("canvas size"), {"width": W, "height": H, "anchor": "center",
                                                "extensionColor": "transparent"})
                # Where did PhotoCraft put the old picture? Follow it if its centring rounds differently.
                landed = _alpha_origin(pc.render())
                expected = _alpha_origin(results.job.base)
                if landed and expected and landed != expected:
                    shift = (landed[0] - expected[0], landed[1] - expected[1])
            path, rel = _exchange(settings, "fill" if results.masked else "edit")
            _shifted(image, shift).save(path)
            pc.place_layer(rel, results.layer, masked=results.masked)
            results.placed = Placed(index, pc._identity(), _quiet(pc.active_layer), shift)
            _keep(results, settings, pc)
        return results.layer
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e


def _quiet(fn):
    try:
        return fn()
    except PhotoCraftError:
        return None


NO_RESULTS = ("Select a layer GenFill added from several results (in PhotoCraft's Layers panel) to see its other "
              "results.")


def _keep(results: Results, settings: Settings, pc) -> None:
    """Lasting results: keep the set on disk and tag the layer with it (best effort: a failure here never
    undoes the placing)."""
    p = results.placed
    if results.tag is not None:
        if p is not None and p.layer is not None:
            _quiet(lambda: pc.set_layer_metadata(p.layer, results.tag))
        return
    if len(results.images) < 2 or results.new_document or p is None or p.layer is None:
        return
    root = Path(settings.results_dir)
    try:
        if results.saved is None:
            results.saved = results_store.save(results, root, settings.keep_results)
        else:
            results_store.set_index(root, results.saved, p.index)
    except OSError:
        return
    _quiet(lambda: pc.set_layer_metadata(p.layer, results_store.tag_packet(results.saved)))


def results_for_layer(settings: Settings, pc=None) -> Results:
    """The saved results of the layer selected in PhotoCraft, ready to swap another one in."""
    pc, _ = _clients(settings, pc, object())
    try:
        with pc:
            pc.compatibility()
            layer = pc.active_layer()
            set_id = results_store.tag_of(pc.layer_metadata(layer)) if layer is not None else None
            identity = pc._identity()
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e
    if not set_id:
        raise UserFacingError(NO_RESULTS)
    got = results_store.load(Path(settings.results_dir), set_id)
    if got is None:
        raise UserFacingError(f"This layer's other results are no longer kept (GenFill keeps the last "
                              f"{settings.keep_results} sets).")
    results, index, shift = got
    results.placed = Placed(index, identity, layer, shift)
    return results


SWAP_GONE = ("The layer GenFill added isn't in the document any more, so there's nothing to swap. "
             "Generate again to get new results.")


def swap(results: Results, index: int, settings: Settings, pc=None) -> str:
    """Put results.images[index] in PhotoCraft instead of the one placed before: the layer GenFill
    added is replaced in the same spot (a fill keeps its exact mask). Refuses, changing nothing, if
    that layer is gone or another document is in front. Create opens another new document."""
    p = results.placed
    if p is None:
        return place(results, index, settings, pc)
    if results.new_document:
        return place(results, index, settings, pc)
    pc, _ = _clients(settings, pc, object())
    try:
        with pc:
            pc.compatibility()
            if pc._identity() != p.document:
                raise UserFacingError(f"Switch back to {p.document[0] or 'the document'} in PhotoCraft first: "
                                      "that's where the result is.")
            if p.layer is None or p.layer not in pc.layer_ids():
                raise UserFacingError(SWAP_GONE)
            doc = pc.document()
            if (doc.width, doc.height) != (results.expand_to or results.size):
                raise UserFacingError("The document's size changed since the result was added, so another one "
                                      "would land in the wrong place.")
            path, rel = _exchange(settings, "fill" if results.masked else "edit")
            _shifted(results.images[index], p.shift).save(path)
            new = pc.replace_layer(p.layer, rel, results.layer, results.masked)
            results.placed = Placed(index, p.document, new, p.shift)
            _keep(results, settings, pc)
        return f"{results.layer} (result {index + 1})"
    except PhotoCraftError as e:
        raise UserFacingError(str(e)) from e


def run_fill(prompt: str, settings: Settings, status: Status, progress: Progress, cancel,
             pc=None, comfy=None) -> str:
    """Generate one candidate and place it (no picking)."""
    pc, comfy = _clients(settings, pc, comfy)
    results = generate(prompt, settings, status, progress, cancel, pc=pc, comfy=comfy, count=1)
    status("Placing the result in PhotoCraft…")
    return place(results, 0, settings, pc=pc)


SEEDVR_OVERLAP = 128


def _seedvr_upscale(flat: Image.Image, size: tuple[int, int], settings: Settings, status: Status,
                    progress: Progress, cancel, comfy) -> Image.Image:
    """SeedVR2 restores real detail in one step. It works on the picture resized to the target size,
    in overlapping pieces so big photos fit in GPU memory; the pieces are blended back together."""
    tw, th = size
    big = flat.resize((tw, th), Image.LANCZOS)
    boxes = imaging.tile_boxes(tw, th, tile=max(512, settings.seedvr_tile), overlap=SEEDVR_OVERLAP)
    tiles = []
    for i, box in enumerate(boxes, 1):
        if cancel.is_set():
            raise UserFacingError("Cancelled.")
        status(f"Upscaling with SeedVR2, piece {i} of {len(boxes)}…" + (" (loading the model)" if i == 1 else ""))
        progress((i - 1) / len(boxes))
        name = comfy.upload(big.crop(box), f"genfill_tile_{i}.png")
        wf = build_seedvr_workflow(name, random.randint(0, 2**32 - 1), settings.seedvr_model)
        tiles.append((box, comfy.run(wf, lambda _f: None, cancel)))
    progress(1.0)
    return imaging.blend_tiles((tw, th), tiles, overlap=SEEDVR_OVERLAP)


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
        comfy.check()
        if settings.seedvr_model and comfy.has_seedvr(settings.seedvr_model):
            result = _seedvr_upscale(flat, (tw, th), settings, status, progress, cancel, comfy)
            status("Opening the upscaled image in PhotoCraft…")
            path, rel = _exchange(settings, f"upscaled_{f}x")
            result.save(path)
            with pc:
                pc.open_document(rel)
            return f"Upscaled {f}× to {tw}×{th}"
        # An upscale model (4x-UltraSharp) draws real edges; a plain resize is soft, and Klein copies
        # the picture it is shown, so without the model the result is barely sharper than a resize.
        model = settings.upscale_model if settings.upscale_model and comfy.has_upscale_model(
            settings.upscale_model) else None
        big = None if model else flat.resize((tw, th), Image.LANCZOS)
        boxes = imaging.tile_boxes(tw, th)
        tiles = []
        for i, box in enumerate(boxes, 1):
            if cancel.is_set():
                raise UserFacingError("Cancelled.")
            status(f"Upscaling tile {i} of {len(boxes)}…" + (" (loading the model)" if i == 1 else ""))
            progress((i - 1) / len(boxes))
            if model:  # the matching piece of the original; the model enlarges it inside ComfyUI
                sx0, sy0 = box[0] // f, box[1] // f
                sx1, sy1 = -(-box[2] // f), -(-box[3] // f)
                box = (sx0 * f, sy0 * f, sx1 * f, sy1 * f)
                crop = flat.crop((sx0, sy0, sx1, sy1))
                cw, ch = ((box[2] - box[0]) // 16) * 16 or 16, ((box[3] - box[1]) // 16) * 16 or 16
                name = comfy.upload(crop, f"genfill_tile_{i}.png")
            else:
                crop = big.crop(box)
                cw, ch = (crop.width // 16) * 16 or 16, (crop.height // 16) * 16 or 16
                name = comfy.upload(crop.resize((cw, ch), Image.LANCZOS), f"genfill_tile_{i}.png")
            wf = build_refine_workflow(name, prompt.strip(), random.randint(0, 2**32 - 1), settings.upscale_steps,
                                       settings.upscale_denoise, cw, ch, settings.workflow_models,
                                       upscale_model=model)
            tiles.append((box, comfy.run(wf, lambda _f: None, cancel)))
        progress(1.0)
        status("Opening the upscaled image in PhotoCraft…")
        path, rel = _exchange(settings, f"upscaled_{f}x")
        imaging.blend_tiles((tw, th), tiles).save(path)
        with pc:
            pc.open_document(rel)
        if not model:
            return (f"Upscaled {f}× to {tw}×{th}. For a much sharper result put {UPSCALE_MODEL} in "
                    "ComfyUI/models/upscale_models (see README)")
        return f"Upscaled {f}× to {tw}×{th}"
    except (PhotoCraftError, ComfyError) as e:
        raise UserFacingError(str(e)) from e
