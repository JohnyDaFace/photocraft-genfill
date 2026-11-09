"""Depth tools: the effects worked out from a depth map (no model needed)."""
import numpy as np
from PIL import Image

from genfill import depth


def _scene():
    """Left half near (a sharp checkerboard 'subject'), right half far (a checkerboard 'background')."""
    h, w = 120, 200
    y, x = np.mgrid[0:h, 0:w]
    checks = (((x // 4) + (y // 4)) % 2 * 255).astype(np.uint8)
    img = Image.fromarray(np.stack([checks] * 3, -1), "RGB")
    d = np.where(x < w // 2, 0.9, 0.1).astype(np.float32)
    return img, d


def test_foreground_is_the_near_part():
    _, d = _scene()
    m = np.asarray(depth.foreground(d))
    assert m[:, :80].mean() > 240 and m[:, 120:].mean() < 15


def test_lens_blur_keeps_the_subject_sharp_and_blurs_the_background():
    img, d = _scene()
    out = np.asarray(depth.lens_blur(img, d, depth.focus_range(d)).convert("L"), np.float32)
    src = np.asarray(img.convert("L"), np.float32)
    assert np.abs(out[:, :80] - src[:, :80]).mean() < 2          # in focus: unchanged
    assert out[:, 130:].std() < src[:, 130:].std() * 0.3         # far: the checks are smoothed out


def test_lens_blur_focuses_on_a_masked_thing():
    img, d = _scene()
    far = Image.fromarray(np.where(d < 0.5, 255, 0).astype(np.uint8), "L")
    out = np.asarray(depth.lens_blur(img, d, depth.focus_range(d, far)).convert("L"), np.float32)
    src = np.asarray(img.convert("L"), np.float32)
    assert np.abs(out[:, 120:] - src[:, 120:]).mean() < 2
    assert out[:, :70].std() < src[:, :70].std() * 0.3


def test_haze_thickens_with_distance_and_keeps_transparency():
    img, d = _scene()
    rgba = img.convert("RGBA")
    rgba.putalpha(200)
    for kind in ("haze", "fog"):
        out = depth.haze(rgba, d, kind)
        assert out.mode == "RGBA" and out.getchannel("A").getextrema() == (200, 200)
        diff = np.abs(np.asarray(out.convert("RGB"), np.float32) - np.asarray(img, np.float32)).mean(-1)
        assert diff[:, 120:].mean() > diff[:, :80].mean() * 3


def test_as_depth_spans_near_and_far():
    raw = Image.linear_gradient("L").resize((64, 48))
    d = depth.as_depth(raw, (128, 96))
    assert d.shape == (96, 128) and d.min() == 0.0 and d.max() == 1.0
