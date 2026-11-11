"""Image-backed PhotoCraft-compatible input for jobs started in Photoshop.

The pipeline needs a rendered image, document bounds, and a place to keep masks.
Actual document changes are sent to the paired Photoshop panel by the bar.
"""
from __future__ import annotations

import base64
from pathlib import Path

from PIL import Image

from . import pipeline
from .photocraft_client import DocInfo


def decode_selection(capture: dict, size: tuple[int, int]) -> Image.Image | None:
    if not capture.get("selection_bounds"):
        return None
    width, height = capture["selection_width"], capture["selection_height"]
    left, top = capture["selection_left"], capture["selection_top"]
    if not all(type(v) is int for v in (width, height, left, top)) or not (0 < width <= size[0] and
            0 < height <= size[1] and 0 <= left <= size[0] - width and 0 <= top <= size[1] - height):
        raise ValueError("Photoshop selection bounds are invalid.")
    pixels = base64.b64decode(capture["selection_mask"], validate=True)
    if len(pixels) != width * height:
        raise ValueError("Photoshop selection mask has the wrong size.")
    mask = Image.new("L", size, 0)
    mask.paste(Image.frombytes("L", (width, height), pixels), (left, top))
    return mask


class CapturedDocument:
    def __init__(self, image: Image.Image, selection: tuple[int, int, int, int] | None,
                 exchange_dir: Path, mask: Image.Image | None = None):
        self.image = image
        self.selection = selection
        self.exchange_dir = exchange_dir
        self.mask = mask

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def compatibility(self):
        return []

    def document(self):
        return DocInfo(self.image.width, self.image.height, self.selection)

    def render(self):
        return self.image.copy()

    def select_mask(self, relative_path: str):
        with Image.open(self.exchange_dir / relative_path) as source:
            self.mask = source.getchannel("A").copy()
        self.selection = self.mask.getbbox()

    def mask_active_layer(self):
        if self.mask is None:
            raise ValueError("Select a subject first.")
        self.result_image = self.image.copy()
        self.result_image.putalpha(self.mask)

    def open_document(self, relative_path: str):
        with Image.open(self.exchange_dir / relative_path) as source:
            self.result_image = source.convert("RGBA")


def run_job(job: str, canvas: CapturedDocument, settings, status, progress, cancel,
            on_selection=None, *, prompt="", what="", label=None, choice=None, factor=None,
            preset=None, reference=None):
    """Run a PhotoCraft image operation against Photoshop's captured pixels."""
    def note(message):
        status(message.replace("PhotoCraft", "Photoshop"))

    pc = canvas
    if job == "select":
        result = pipeline.select_things(what, settings, note, progress, cancel, pc=pc, label=label)
    elif job == "depth_select":
        result = pipeline.depth_select(choice, settings, note, progress, cancel, pc=pc)
    elif job == "remove_things":
        result = pipeline.remove_things(what, settings, note, progress, cancel, pc=pc, label=label)
    elif job == "cutout":
        result = pipeline.remove_background(what, settings, note, progress, cancel, pc=pc)
        image = canvas.result_image
        result = pipeline.Results([image], [image.copy()], "Remove background", False, canvas.image.size,
                                  canvas.selection)
        result.hide_original = True
    elif job == "faces":
        ref = pipeline.load_reference(reference) if reference else None
        result = pipeline.fix_faces(settings, note, progress, cancel, pc=pc, prompt=prompt, reference=ref)
    elif job == "enhance":
        result = pipeline.enhance(settings, note, progress, cancel, pc=pc, prompt=prompt)
    elif job == "depth":
        result = pipeline.depth_effect(choice, settings, note, progress, cancel, pc=pc, focus_on=what)
    elif job == "expand":
        result = pipeline.expand(choice, "", settings, note, progress, cancel, pc=pc)
    elif job == "upscale":
        settings.upscale_factor = factor or settings.upscale_factor
        pipeline.run_upscale("", settings, note, progress, cancel, pc=pc)
        image = canvas.result_image
        result = pipeline.Results([image], [image.copy()], "Upscale", False, canvas.image.size,
                                  canvas.selection, new_document=True)
    elif job in ("fill", "remove"):
        ref = pipeline.load_reference(reference) if reference and not preset and job != "remove" else None
        result = pipeline.generate(prompt, settings, note, progress, cancel, pc=pc, reference=ref,
                                   remove=job == "remove", shape=canvas.mask,
                                   name=preset)
    else:
        raise pipeline.UserFacingError(f"{job} is not available for Photoshop.")
    if canvas.mask is not None and on_selection is not None and job in ("select", "depth_select", "remove_things"):
        on_selection(canvas.mask)
    return result
