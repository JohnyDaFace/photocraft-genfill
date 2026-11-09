"""Depth tools: what a depth map (Depth Anything V2, brighter = nearer) makes possible without redrawing the
picture. Lens blur that grows with distance from the point in focus, haze or fog that thickens with distance,
and the foreground or background as a mask. Everything here is computed from the picture's own pixels."""
from __future__ import annotations

import numpy as np
from PIL import Image

BLUR_LEVELS = 7          # blur radii worked out between sharp and the strongest; each pixel blends two
BLUR_MAX = 0.014         # the strongest blur, as a share of the picture's long side
BLUR_GAIN = 2.6          # how fast the blur grows with distance (in depth units) from the point in focus
IN_FOCUS = 0.04          # this much depth either side of the focus stays sharp
HAZE = {"haze": (0.55, 1.6), "fog": (0.85, 1.0)}  # how thick, and how it falls off towards the camera


def as_depth(img: Image.Image, size: tuple[int, int]) -> np.ndarray:
    """The model's depth picture as floats 0 (far) … 1 (near), the picture's size, lightly smoothed."""
    d = img.convert("L").resize(size, Image.BILINEAR)
    d = np.asarray(d, np.float32) / 255.0
    d = _blur(d, max(1, round(max(size) / 1500)))  # model noise, without softening edges much
    lo, hi = np.percentile(d, 1), np.percentile(d, 99.5)
    return np.clip((d - lo) / max(hi - lo, 1e-3), 0.0, 1.0)


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Running mean over 2r+1 along an axis, edges repeated."""
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(np.float32)


def _blur(a: np.ndarray, r: float) -> np.ndarray:
    """About a Gaussian of radius r (three box passes); big radii are worked out on a smaller copy."""
    if r < 0.5:
        return a
    step = max(1, int(r // 6))
    small = a[::step, ::step] if step > 1 else a
    k = max(1, round(r / step / 1.7))
    for _ in range(3):
        small = _box(_box(small, k, 0), k, 1)
    if step == 1:
        return small
    h, w = a.shape[:2]
    chans = small if small.ndim == 3 else small[..., None]
    up = np.stack([np.asarray(Image.fromarray(chans[..., i]).resize((w, h), Image.BILINEAR))
                   for i in range(chans.shape[2])], axis=-1)
    return up if a.ndim == 3 else up[..., 0]


def otsu(d: np.ndarray) -> float:
    """The depth that best splits the picture in two (near and far)."""
    hist, edges = np.histogram(d, bins=128, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids)
    mean0 = m0 / np.maximum(w0, 1)
    mean1 = (m0[-1] - m0) / np.maximum(w1, 1)
    between = w0 * w1 * (mean0 - mean1) ** 2
    return float(edges[int(np.argmax(between)) + 1])  # the top of the near-far split's lower bin


def foreground(d: np.ndarray) -> Image.Image:
    """The near part of the picture as a soft-edged mask (L, 255 = foreground)."""
    m = (d > otsu(d)).astype(np.float32)
    m = _blur(m, max(1, max(d.shape) / 1200))
    return Image.fromarray((np.clip(m, 0, 1) * 255).astype(np.uint8), "L")


def focus_range(d: np.ndarray, mask: Image.Image | None = None) -> tuple[float, float]:
    """The depths to keep sharp: those of the thing in `mask`, else of the foreground (the main subject),
    so all of it stays in focus, not just its nearest part."""
    part = None
    if mask is not None:
        m = np.asarray(mask.convert("L").resize((d.shape[1], d.shape[0])), np.uint8) > 127
        if m.any():
            part = d[m]
    if part is None:
        part = d[d > otsu(d)]
    if not part.size:
        v = float(np.percentile(d, 90))
        return v, v
    return float(np.percentile(part, 10)), float(np.percentile(part, 97))


def lens_blur(img: Image.Image, d: np.ndarray, focus: tuple[float, float], strength: float = 1.0) -> Image.Image:
    """Blur that grows with distance from the `focus` depths, like a wide-open lens. Behind the focus, only things
    at least as far away are blurred together, so the sharp subject doesn't smear a halo onto the
    background; in front of it, things blur over what's behind them as they would through a lens."""
    rgb = np.asarray(img.convert("RGB"), np.float32) / 255.0
    h, w = d.shape
    max_r = max(2.0, BLUR_MAX * strength * max(w, h))
    lo, hi = focus
    off = np.maximum(lo - d, 0.0) + np.maximum(d - hi, 0.0)  # how far outside the sharp depths
    amount = np.clip((off - IN_FOCUS) * BLUR_GAIN, 0.0, 1.0)
    level = amount * (BLUR_LEVELS - 1)
    behind = np.clip((lo - IN_FOCUS - d) / IN_FOCUS + 1.0, 0.0, 1.0)[..., None]  # 1 = behind the focus
    out = np.zeros_like(rgb)
    for i in range(BLUR_LEVELS):
        weight = np.clip(1.0 - np.abs(level - i), 0.0, 1.0)[..., None]
        if not weight.any():
            continue
        r = max_r * i / (BLUR_LEVELS - 1)
        if i == 0:
            out += weight * rgb
            continue
        plain = _blur(rgb, r)
        wb = _blur(behind[..., 0], r)[..., None]
        back = np.where(wb > 0.02, _blur(rgb * behind, r) / np.maximum(wb, 1e-4), plain)
        out += weight * (behind * back + (1.0 - behind) * plain)
    return _with_alpha(out, img)


def haze(img: Image.Image, d: np.ndarray, kind: str = "haze") -> Image.Image:
    """Air between the camera and the far parts: they fade towards the colour of the distance (fog: a pale
    grey), lighter and lower in contrast, more the further away they are."""
    thick, falloff = HAZE[kind]
    rgb = np.asarray(img.convert("RGB"), np.float32) / 255.0
    far = d <= np.percentile(d, 4)
    sky = rgb[far].mean(axis=0) if far.any() else np.array([0.8, 0.82, 0.85], np.float32)
    pale = np.array([0.86, 0.87, 0.88], np.float32)
    colour = 0.5 * sky + 0.5 * pale if kind == "haze" else 0.25 * sky + 0.75 * pale
    a = (thick * (1.0 - d) ** falloff)[..., None]
    if kind == "fog":  # fog also softens what it covers
        rgb = rgb * (1 - a * 0.6) + _blur(rgb, max(d.shape) / 400) * (a * 0.6)
    return _with_alpha(rgb * (1.0 - a) + colour * a, img)


def _with_alpha(rgb: np.ndarray, like: Image.Image) -> Image.Image:
    out = Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB").convert("RGBA")
    if like.mode == "RGBA":
        out.putalpha(like.getchannel("A"))
    return out
