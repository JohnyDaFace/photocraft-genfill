import numpy as np
from PIL import Image

from genfill.imaging import blend_tiles, fill_mask, merge, prepare_source, tile_boxes, working_size


def test_tile_boxes_cover_image_with_overlap():
    boxes = tile_boxes(2160, 3840, tile=1024, overlap=128)
    cover = np.zeros((3840, 2160), bool)
    for x0, y0, x1, y1 in boxes:
        assert x1 - x0 == 1024 and y1 - y0 == 1024
        cover[y0:y1, x0:x1] = True
    assert cover.all()


def test_tile_boxes_small_image_is_one_tile():
    assert tile_boxes(800, 600) == [(0, 0, 800, 600)]


def test_blend_tiles_of_flat_colour_is_flat():
    boxes = tile_boxes(1500, 1500, tile=1024, overlap=128)
    out = np.asarray(blend_tiles((1500, 1500), [(b, Image.new("RGB", (1024, 1024), (10, 200, 30))) for b in boxes]))
    assert out.shape == (1500, 1500, 3)
    assert np.abs(out.astype(int) - (10, 200, 30)).max() <= 1


def _doc(w=1080, h=1920, top=285, pic_h=1350):
    a = np.zeros((h, w, 4), np.uint8)
    a[top:top + pic_h, :, :3] = (200, 150, 100)
    a[top:top + pic_h, :, 3] = 255
    return Image.fromarray(a, "RGBA")


def test_working_size_multiple_of_16_about_one_megapixel():
    w, h = working_size(1080, 1920)
    assert w % 16 == 0 and h % 16 == 0
    assert 0.9e6 <= w * h <= 1.1e6
    assert abs(w / h - 1080 / 1920) < 0.02


def test_working_size_never_upscales_small_docs():
    assert working_size(512, 512) == (512, 512)


def test_fill_mask_uses_transparent_pixels_inside_bounds():
    m = np.asarray(fill_mask(_doc(), (0, 0, 1080, 1920), grow=0, feather=0))
    assert m[100, 500] == 255 and m[1800, 500] == 255  # empty bands
    assert m[900, 500] == 0                             # original picture


def test_fill_mask_whole_rect_when_nothing_transparent():
    m = np.asarray(fill_mask(_doc(top=0, pic_h=1920), (100, 100, 300, 300), grow=0, feather=0))
    assert m[200, 200] == 255 and m[50, 50] == 0


def test_fill_mask_clipped_to_canvas():
    m = fill_mask(_doc(), (-10, -10, 50, 50), grow=0, feather=0)
    assert m.size == (1080, 1920)
    assert np.asarray(m)[0, 0] == 255


def test_fill_mask_grows_into_original_edge():
    m = np.asarray(fill_mask(_doc(), (0, 0, 1080, 1920), grow=24, feather=0))
    assert m[285 + 10, 500] == 255


def test_prepare_source_has_no_black_holes():
    src = np.asarray(prepare_source(_doc()))
    assert src.shape == (1920, 1080, 3)
    assert src[50, 500].sum() > 100  # pulled from the picture colour, not black


def test_merge_keeps_original_pixels_outside_mask_and_is_doc_sized():
    doc = _doc(1081, 1921)
    gen = Image.new("RGB", (1081, 1921), (0, 255, 0))
    mask = fill_mask(doc, (0, 0, 1081, 1921), grow=0, feather=0)
    out = merge(doc, gen, mask)
    assert out.size == (1081, 1921) and out.mode == "RGBA"
    o = np.asarray(out)
    assert tuple(o[900, 500]) == (200, 150, 100, 255)
    assert tuple(o[50, 500]) == (0, 255, 0, 255)


def test_match_colors_removes_tone_drift_at_seam():
    from genfill.imaging import match_colors
    doc = _doc()
    gen = np.asarray(prepare_source(doc)).astype(int)
    gen = Image.fromarray(np.clip(gen + 15, 0, 255).astype(np.uint8), "RGB")  # drifted brighter everywhere
    mask = fill_mask(doc, (0, 0, 1080, 1920), grow=24, feather=16)
    fixed = np.asarray(match_colors(doc, gen, mask)).astype(int)
    # just above the seam (generated area) the colour now matches the original just below it
    assert np.abs(fixed[270, 540] - np.array([200, 150, 100])).max() <= 3
    assert np.abs(fixed[1650, 540] - np.array([200, 150, 100])).max() <= 3


def test_blend_mask_transition_stays_inside_layer_mask():
    from genfill.imaging import BLEND_FEATHER, BLEND_GROW, LAYER_MASK_EXPAND, blend_mask
    m = np.asarray(blend_mask(_doc(), (0, 0, 1080, 1920))).astype(int)
    assert m[100, 540] == 255                    # deep in the empty band: all generated
    assert m[285 + LAYER_MASK_EXPAND, 540] == 0  # at the layer mask's reach: all original
    assert BLEND_GROW + BLEND_FEATHER <= LAYER_MASK_EXPAND


def test_keep_colors_undoes_a_tint_but_keeps_what_the_edit_changed():
    from genfill.imaging import keep_colors
    rng = np.random.default_rng(1)
    orig = rng.integers(20, 230, (120, 160, 3)).astype(np.float32)
    edited = orig * np.array([1.1, 0.9, 0.8]) + np.array([12, -5, -10])    # warmer, darker drift…
    edited[:40, :40] = (250, 250, 250)                                     # …and one real change
    out = np.asarray(keep_colors(Image.fromarray(orig.astype(np.uint8)),
                                 Image.fromarray(np.clip(edited, 0, 255).astype(np.uint8)).convert("RGBA")))
    assert out.shape[2] == 4
    assert np.abs(out[60:, 60:, :3].astype(float) - orig[60:, 60:]).mean() < 2.5   # tint gone
    assert out[:40, :40, :3].mean() > 200                                          # the change stays
