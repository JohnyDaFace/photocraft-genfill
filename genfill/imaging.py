"""Pixel maths for generative fill: working size, fill mask, source prep, final merge."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageFilter

Bounds = tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)

OPAQUE = 250  # alpha at or above this counts as original pixels


def working_size(w: int, h: int, megapixels: float = 1.0) -> tuple[int, int]:
    """Size to generate at: about `megapixels`, sides multiples of 16, never larger than the doc."""
    scale = min(1.0, math.sqrt(megapixels * 1_000_000 / max(1, w * h)))
    return max(16, int(round(w * scale / 16)) * 16), max(16, int(round(h * scale / 16)) * 16)


def _clip(bounds: Bounds, w: int, h: int) -> Bounds:
    x0, y0, x1, y1 = bounds
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def fill_mask(rgba: Image.Image, selection: Bounds, grow: int = 24, feather: int = 16) -> Image.Image:
    """L mask, 255 = generate. Inside the selection bounds, the transparent pixels are generated
    (canvas expansion); when the bounds hold no transparent pixels the whole rectangle is (inpaint).
    `grow` pushes the mask that many pixels into the original so the fill overlaps the seam."""
    w, h = rgba.size
    x0, y0, x1, y1 = _clip(selection, w, h)
    mask = np.zeros((h, w), np.uint8)
    if x1 <= x0 or y1 <= y0:
        return Image.fromarray(mask, "L")
    alpha = np.asarray(rgba.getchannel("A"))[y0:y1, x0:x1]
    holes = alpha < OPAQUE
    mask[y0:y1, x0:x1] = np.where(holes, 255, 0) if holes.any() else 255
    img = Image.fromarray(mask, "L")
    if grow > 0:
        # cheap dilation: box-blur the mask and keep anything it reached
        img = img.filter(ImageFilter.BoxBlur(grow)).point(lambda v: 255 if v > 0 else 0)
        # growing must not leak outside the selection bounds
        clipped = np.zeros((h, w), np.uint8)
        clipped[y0:y1, x0:x1] = np.asarray(img)[y0:y1, x0:x1]
        img = Image.fromarray(clipped, "L")
    if feather > 0:
        img = img.filter(ImageFilter.GaussianBlur(feather / 2))
    return img


def prepare_source(rgba: Image.Image) -> Image.Image:
    """RGB with transparent areas filled by colours pulled in from the nearest picture content,
    so the model sees a plausible blurry continuation instead of black."""
    arr = np.asarray(rgba.convert("RGBA")).astype(np.float32) / 255.0
    rgb, a = arr[..., :3], arr[..., 3:4]
    if (a >= 1.0).all():
        return rgba.convert("RGB")
    if not (a > 0).any():
        return Image.new("RGB", rgba.size, (128, 128, 128))
    filled = _push_pull(rgb * a, a)
    out = rgb * a + filled * (1.0 - a)
    img = Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    # soften the blocky fill only where there was no picture
    soft = img.filter(ImageFilter.GaussianBlur(8))
    hole = Image.fromarray(((a[..., 0] < 1.0) * 255).astype(np.uint8), "L")
    return Image.composite(soft, img, hole)


def _push_pull(premult: np.ndarray, a: np.ndarray) -> np.ndarray:
    """Hole filling: average premultiplied colour down a 2x pyramid, then fill holes on the way up."""
    h, w = a.shape[:2]
    if h <= 1 and w <= 1:
        return premult / np.maximum(a, 1e-6)
    ph, pw = h + (h % 2), w + (w % 2)
    p = np.pad(premult, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    q = np.pad(a, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")
    p2 = p.reshape(ph // 2, 2, pw // 2, 2, 3).mean(axis=(1, 3))
    q2 = q.reshape(ph // 2, 2, pw // 2, 2, 1).mean(axis=(1, 3))
    coarse = _push_pull(p2, q2)
    up = coarse.repeat(2, axis=0).repeat(2, axis=1)[:h, :w]
    fine = premult / np.maximum(a, 1e-6)
    wgt = np.clip(a, 0, 1)
    return fine * wgt + up * (1.0 - wgt)


def tile_boxes(w: int, h: int, tile: int = 1024, overlap: int = 128) -> list[Bounds]:
    """Overlapping tiles covering the image; every tile is `tile` wide/high (or the whole side)."""
    def starts(n: int) -> list[int]:
        if n <= tile:
            return [0]
        count = math.ceil((n - overlap) / (tile - overlap))
        step = (n - tile) / (count - 1)
        return [round(i * step) for i in range(count)]
    return [(x, y, min(w, x + tile), min(h, y + tile)) for y in starts(h) for x in starts(w)]


def blend_tiles(size: tuple[int, int], tiles: list[tuple[Bounds, Image.Image]], overlap: int = 128) -> Image.Image:
    """Feathered average of RGB tiles (each resized to its box) into one image of `size`."""
    w, h = size
    acc = np.zeros((h, w, 3), np.float32)
    wsum = np.zeros((h, w, 1), np.float32)
    for (x0, y0, x1, y1), img in tiles:
        tw, th = x1 - x0, y1 - y0
        px = np.asarray(img.convert("RGB").resize((tw, th), Image.LANCZOS)).astype(np.float32)
        ramp = lambda n: np.clip((np.minimum(np.arange(n), np.arange(n)[::-1]) + 1) / max(1, overlap), 1e-3, 1)
        wt = (ramp(th)[:, None] * ramp(tw)[None, :])[..., None]
        acc[y0:y1, x0:x1] += px * wt
        wsum[y0:y1, x0:x1] += wt
    return Image.fromarray(np.clip(acc / np.maximum(wsum, 1e-6) + 0.5, 0, 255).astype(np.uint8), "RGB")


# Seam handling. The PhotoCraft layer mask is the selection expanded by LAYER_MASK_EXPAND and
# feathered by LAYER_MASK_FEATHER, so it reaches into the original; inside the layer, the pixels go
# from exact original to generated over the BLEND_GROW + BLEND_FEATHER band, which must stay inside
# the fully opaque part of that layer mask, or the hard edge comes back.
BLEND_GROW = 24
BLEND_FEATHER = 32
LAYER_MASK_EXPAND = 64
LAYER_MASK_FEATHER = 8
SEAM_BAND = 96  # how far into the original we look to measure the colour drift


def blend_mask(rgba: Image.Image, selection: Bounds) -> Image.Image:
    """Where the placed layer shows generated pixels (255) vs exact original ones (0)."""
    m = fill_mask(rgba, selection, grow=BLEND_GROW, feather=0)
    return m.filter(ImageFilter.GaussianBlur(BLEND_FEATHER / 3))


def match_colors(original: Image.Image, generated: Image.Image, mask: Image.Image) -> Image.Image:
    """Remove the model's colour/brightness drift: measure (original - generated) on original pixels
    near the fill, spread that difference smoothly over the whole image and add it to the generated
    pixels, so both sides of the seam agree."""
    size = original.size
    gen = np.asarray(generated.convert("RGB").resize(size, Image.LANCZOS)).astype(np.float32)
    orig = np.asarray(original.convert("RGBA")).astype(np.float32)
    m = np.asarray(mask.convert("L").resize(size)).astype(np.float32) / 255.0
    near = np.asarray(Image.fromarray(((m > 0) * 255).astype(np.uint8), "L")
                      .filter(ImageFilter.BoxBlur(SEAM_BAND))) > 0
    ref = ((orig[..., 3] >= OPAQUE) & (m < 0.01) & near).astype(np.float32)[..., None]
    if ref.sum() < 16:
        return Image.fromarray(np.clip(gen + 0.5, 0, 255).astype(np.uint8), "RGB")
    delta = _push_pull((orig[..., :3] - gen) * ref, ref)
    # smooth it: only the low-frequency drift should be corrected, never detail
    shifted = Image.fromarray(np.clip(delta + 128, 0, 255).astype(np.uint8), "RGB")
    smooth = np.asarray(shifted.filter(ImageFilter.GaussianBlur(24))).astype(np.float32) - 128
    return Image.fromarray(np.clip(gen + smooth + 0.5, 0, 255).astype(np.uint8), "RGB")


def merge(original: Image.Image, generated: Image.Image, mask: Image.Image) -> Image.Image:
    """Opaque doc-sized RGBA: original pixels where they exist and the mask is 0, generated elsewhere."""
    size = original.size
    gen = np.asarray(generated.convert("RGB").resize(size, Image.LANCZOS)).astype(np.float32)
    orig = np.asarray(original.convert("RGBA")).astype(np.float32)
    keep = (orig[..., 3:4] / 255.0) * (1.0 - np.asarray(mask.resize(size)).astype(np.float32)[..., None] / 255.0)
    rgb = orig[..., :3] * keep + gen * (1.0 - keep)
    out = np.concatenate([rgb, np.full(rgb.shape[:2] + (1,), 255.0, np.float32)], axis=-1)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")
