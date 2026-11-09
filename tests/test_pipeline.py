import threading

import numpy as np
import pytest
from PIL import Image

from genfill.photocraft_client import DocInfo
from genfill.pipeline import UserFacingError, generate, place, run_fill, run_upscale
from genfill.settings import Settings


class FakePC:
    def __init__(self, doc: Image.Image, selection):
        self.doc, self.selection = doc, selection
        self.placed, self.opened = [], []

    problems: list = []

    def compatibility(self): return self.problems
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def document(self): return DocInfo(self.doc.width, self.doc.height, self.selection)
    def render(self): return self.doc
    def place_layer(self, rel, name, masked=True):
        self.placed.append((rel, name, masked))
        self.layers = getattr(self, "layers", []) + [100 + len(self.placed)]
    def open_document(self, rel): self.opened.append(rel)
    name = ("photo.jpg", "D:/photo.jpg")
    def _identity(self): return self.name
    def set_layer_metadata(self, layer, packet):
        self.xmp = {**getattr(self, "xmp", {}), layer: packet}
        return True
    def layer_metadata(self, layer=None):
        return getattr(self, "xmp", {}).get(self.active_layer() if layer is None else layer)
    def active_layer(self): return self.layers[-1]
    def layer_ids(self): return set(getattr(self, "layers", []))

    def replace_layer(self, old, rel, name, masked):
        self.replaced = getattr(self, "replaced", []) + [(old, rel, name, masked)]
        new = 200 + len(self.replaced)
        self.layers = [new if i == old else i for i in self.layers]
        return new


class FakeComfy:
    upscale_model = False  # ComfyUI has 4x-UltraSharp
    seedvr = False         # ComfyUI has SeedVR2

    def __init__(self): self.runs, self.uploads, self.seeds, self.kinds, self.wfs = 0, [], [], [], []
    def check(self): return {}
    def has_upscale_model(self, model=None): return self.upscale_model
    def has_seedvr(self, model=None, vae=None): return self.seedvr
    loras_installed: tuple = ()
    def has_lora(self, name): return name in self.loras_installed
    def upload(self, img, name): self.uploads.append(img.size); return name
    def run(self, wf, on_progress, cancel):
        self.runs += 1
        self.wfs.append(wf)
        on_progress(None)  # like ComfyUI: "working, no step count yet", then steps
        on_progress(0.5)
        if "post" in wf:  # SeedVR2: same size as it was sent
            self.kinds.append("seedvr")
            return Image.new("RGB", self.uploads[-1], (0, 0, 255))
        self.seeds.append(wf["noise"]["inputs"]["noise_seed"])
        self.kinds.append("fill" if "mask" in wf else "edit" if "sigmas" in wf else "upscale")
        w = wf["sched" if "sched" in wf else "sigmas"]["inputs"]
        return Image.new("RGB", (w["width"], w["height"]), (0, 255, 0))


def _doc():
    d = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d.paste(Image.new("RGB", (1080, 1350), (200, 150, 100)), (0, 285))
    return d


def _settings(tmp_path):
    return Settings(exchange_dir=str(tmp_path), results_dir=str(tmp_path / "results"))


def _go(fn, pc, comfy, tmp_path, prompt=""):
    return fn(prompt, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy)


def test_no_selection_stops_before_comfy(tmp_path):
    comfy = FakeComfy()
    with pytest.raises(UserFacingError, match="Make a selection first"):
        _go(run_fill, FakePC(_doc(), None), comfy, tmp_path)
    assert comfy.runs == 0


def test_incompatible_photocraft_stops_before_comfy(tmp_path):
    pc = FakePC(_doc(), (0, 0, 1080, 1920))
    pc.problems = ["PhotoCraft no longer has 'layer.renameLayer' (rename layer)."]
    comfy = FakeComfy()
    with pytest.raises(UserFacingError, match="isn't compatible"):
        _go(run_fill, pc, comfy, tmp_path)
    assert comfy.runs == 0


def test_fill_places_doc_sized_layer(tmp_path):
    pc = FakePC(_doc(), (0, 0, 1080, 1920))
    name = _go(run_fill, pc, FakeComfy(), tmp_path, "more table")
    assert name.startswith("Generative Fill")
    rel, layer_name, masked = pc.placed[0]
    assert masked
    out = Image.open(tmp_path / rel)
    assert out.size == (1080, 1920)
    a = np.asarray(out)
    assert tuple(a[900, 500][:3]) == (200, 150, 100)  # original untouched
    assert a[50, 500][3] == 255                       # empty band is filled (colour-matched, so not pure green)


def test_upscale_opens_new_document_at_factor(tmp_path):
    pc = FakePC(_doc(), None)
    comfy = FakeComfy()
    _go(run_upscale, pc, comfy, tmp_path)
    out = Image.open(tmp_path / pc.opened[0])
    assert out.size == (2160, 3840)
    assert comfy.runs == len(comfy.uploads) > 1
    assert pc.placed == []


def test_upscale_without_the_model_resizes_and_says_how_to_sharpen(tmp_path):
    pc = FakePC(_doc(), None)
    comfy = FakeComfy()
    msg = _go(run_upscale, pc, comfy, tmp_path)
    assert "4x-UltraSharp" in msg
    assert all("up_model" not in wf for wf in comfy.wfs)
    assert max(max(s) for s in comfy.uploads) > 1000  # enlarged tiles are sent


def test_upscale_with_the_model_sends_the_small_original_and_enlarges_in_comfy(tmp_path):
    pc = FakePC(_doc(), None)
    comfy = FakeComfy()
    comfy.upscale_model = True
    msg = _go(run_upscale, pc, comfy, tmp_path)
    assert msg == "Upscaled 2× to 2160×3840"
    assert max(max(s) for s in comfy.uploads) <= 512 + 1  # half of a 1024 tile
    for wf in comfy.wfs:
        assert wf["up_model"]["inputs"]["model_name"] == "4x-UltraSharp.pth"
        assert wf["encode"]["inputs"]["pixels"] == ["up_size", 0]
        assert wf["up_size"]["inputs"]["width"] == wf["sched"]["inputs"]["width"]
    out = Image.open(tmp_path / pc.opened[0])
    assert out.size == (2160, 3840)


def test_upscale_4x_with_the_model(tmp_path):
    pc = FakePC(Image.new("RGBA", (700, 500), (90, 120, 150, 255)), None)
    comfy = FakeComfy()
    comfy.upscale_model = True
    s = _settings(tmp_path)
    s.upscale_factor = 4
    msg = run_upscale("", s, lambda t: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy)
    assert msg == "Upscaled 4× to 2800×2000"
    assert max(max(sz) for sz in comfy.uploads) <= 256 + 1  # a quarter of a 1024 tile
    assert Image.open(tmp_path / pc.opened[0]).size == (2800, 2000)


def test_upscale_uses_seedvr_when_installed(tmp_path):
    pc = FakePC(_doc(), None)
    comfy = FakeComfy()
    comfy.seedvr = comfy.upscale_model = True
    msg = _go(run_upscale, pc, comfy, tmp_path)
    assert msg == "Upscaled 2× to 2160×3840"
    assert set(comfy.kinds) == {"seedvr"} and comfy.runs == 2 * 2  # 2048 pieces with overlap
    assert all(max(sz) <= 2048 for sz in comfy.uploads)
    assert comfy.wfs[0]["svr_unet"]["inputs"]["unet_name"] == "seedvr2_7b_int8_convrot.safetensors"
    out = np.asarray(Image.open(tmp_path / pc.opened[0]))
    assert out.shape[:2] == (3840, 2160) and tuple(out[1900, 1000]) == (0, 0, 255)  # seams blend to the same


def test_upscale_4x_with_seedvr_and_smaller_pieces(tmp_path):
    comfy = FakeComfy()
    comfy.seedvr = True
    s = _settings(tmp_path)
    s.upscale_factor, s.seedvr_tile = 4, 1024
    pc = FakePC(Image.new("RGBA", (500, 300), (9, 9, 9, 255)), None)
    msg = run_upscale("", s, lambda t: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy)
    assert msg == "Upscaled 4× to 2000×1200"
    assert all(max(sz) <= 1024 for sz in comfy.uploads) and comfy.runs == 3 * 2


def test_upscale_seedvr_turned_off_falls_back(tmp_path):
    comfy = FakeComfy()
    comfy.seedvr = comfy.upscale_model = True
    s = _settings(tmp_path)
    s.seedvr_model = ""
    run_upscale("", s, lambda t: None, lambda f: None, threading.Event(), pc=FakePC(_doc(), None), comfy=comfy)
    assert "seedvr" not in comfy.kinds and all("up_model" in wf for wf in comfy.wfs)


def test_upscale_model_turned_off_in_settings(tmp_path):
    comfy = FakeComfy()
    comfy.upscale_model = True
    s = _settings(tmp_path)
    s.upscale_model = ""
    run_upscale("", s, lambda t: None, lambda f: None, threading.Event(), pc=FakePC(_doc(), None), comfy=comfy)
    assert all("up_model" not in wf for wf in comfy.wfs)


def test_upscale_caps_size(tmp_path):
    pc = FakePC(Image.new("RGBA", (6000, 3000), (9, 9, 9, 255)), None)
    with pytest.raises(UserFacingError, match="too large"):
        _go(run_upscale, pc, FakeComfy(), tmp_path)


def _gen(pc, comfy, tmp_path, prompt="", count=None):
    return generate(prompt, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                    pc=pc, comfy=comfy, count=count)


def test_create_only_model_refuses_whole_image_edit(tmp_path):
    pc, comfy = FakePC(_doc(), None), FakeComfy()
    s = _settings(tmp_path)
    s.switch_family("krea2")
    with pytest.raises(UserFacingError, match="can only make new pictures"):
        generate("make it night", s, lambda m: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy, count=1)
    assert comfy.runs == 0


def test_no_selection_with_prompt_edits_whole_image(tmp_path):
    pc, comfy = FakePC(_doc(), None), FakeComfy()
    name = _go(run_fill, pc, comfy, tmp_path, "make it night")
    assert name == "Edit – make it night" and comfy.kinds == ["edit"]
    rel, _, masked = pc.placed[0]
    assert not masked
    out = Image.open(tmp_path / rel)
    assert out.size == (1080, 1920)
    a = np.asarray(out)
    assert tuple(a[900, 500]) == (0, 255, 0, 255)  # redrawn by the model
    assert a[50, 500][3] == 0                       # transparent canvas stays transparent


def test_variations_make_distinct_candidates_without_placing(tmp_path):
    pc, comfy = FakePC(_doc(), (0, 0, 1080, 285)), FakeComfy()
    r = _gen(pc, comfy, tmp_path, count=3)
    assert len(r.images) == len(r.previews) == comfy.runs == 3
    assert len(set(comfy.seeds)) == 3
    assert all(im.size == (1080, 1920) for im in r.images)
    assert all(max(p.size) <= 240 for p in r.previews)
    assert pc.placed == []


def test_variations_default_comes_from_settings(tmp_path):
    s = _settings(tmp_path)
    s.variations = 2
    comfy = FakeComfy()
    generate("", s, lambda m: None, lambda f: None, threading.Event(), pc=FakePC(_doc(), (0, 0, 1080, 285)), comfy=comfy)
    assert comfy.runs == 2


def test_place_chosen_candidate(tmp_path):
    pc = FakePC(_doc(), (0, 0, 1080, 285))
    r = _gen(pc, FakeComfy(), tmp_path, "sky", count=2)
    assert place(r, 1, _settings(tmp_path), pc=pc) == "Generative Fill – sky"
    assert len(pc.placed) == 1 and pc.placed[0][2]


def test_place_refuses_when_selection_changed(tmp_path):
    pc = FakePC(_doc(), (0, 0, 1080, 285))
    r = _gen(pc, FakeComfy(), tmp_path, count=2)
    pc.selection = (0, 0, 10, 10)
    with pytest.raises(UserFacingError, match="selection changed"):
        place(r, 0, _settings(tmp_path), pc=pc)
    assert pc.placed == []


def test_reference_is_uploaded_and_used(tmp_path):
    pc, comfy = FakePC(_doc(), (0, 0, 1080, 285)), FakeComfy()
    seen = []
    run = comfy.run
    comfy.run = lambda wf, p, c: (seen.append(wf), run(wf, p, c))[1]
    _gen_ref = generate("the logo from image 2", _settings(tmp_path), lambda s: None, lambda f: None,
                        threading.Event(), pc=pc, comfy=comfy, count=1,
                        reference=Image.new("RGB", (3000, 2000), (1, 2, 3)))
    assert len(comfy.uploads) == 3                      # source, mask, reference
    w, h = comfy.uploads[2]
    assert w % 16 == 0 and h % 16 == 0 and w * h <= 2_200_000  # about the working size (2 MP)
    assert "ref2" in seen[0]


def test_remove_needs_a_selection(tmp_path):
    comfy = FakeComfy()
    with pytest.raises(UserFacingError, match="Select what to remove"):
        generate("", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=FakePC(_doc(), None), comfy=comfy, remove=True)
    assert comfy.runs == 0


def test_remove_prompt_and_layer_name(tmp_path):
    from genfill.pipeline import remove_prompt
    assert remove_prompt("basket").startswith("Remove the basket completely")
    assert remove_prompt("").startswith("Remove the object completely")
    assert remove_prompt("a lamp.").startswith("Remove a lamp completely")
    pc = FakePC(_doc(), (0, 300, 500, 600))
    r = generate("basket", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=pc, comfy=FakeComfy(), count=1, remove=True)
    assert r.layer == "Remove – basket" and r.masked


def test_a_preset_names_the_layer_after_itself_not_its_long_instruction(tmp_path):
    from genfill.presets import prompt_for
    r = generate(prompt_for("Restore old photo"), _settings(tmp_path), lambda s: None, lambda f: None,
                 threading.Event(), pc=FakePC(_doc(), None), comfy=FakeComfy(), count=1, name="Restore old photo")
    assert r.layer == "Edit – Restore old photo"


def test_reference_file_with_transparency_is_flattened(tmp_path):
    from genfill.pipeline import load_reference
    f = tmp_path / "logo.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(f)
    assert load_reference(f).getpixel((0, 0)) == (255, 255, 255)
    with pytest.raises(UserFacingError, match="reference image"):
        load_reference(tmp_path / "missing.png")


class ExpandPC(FakePC):
    """Also does Canvas Size (centre anchor, rounding the offset up like PhotoCraft)."""
    def __init__(self, doc, selection, round_up=True):
        super().__init__(doc, selection)
        self.round_up, self.commands = round_up, []

    def execute(self, cmd, params=None):
        self.commands.append((cmd, params))
        if cmd == "image.canvasSize":
            W, H = params["width"], params["height"]
            r = 1 if self.round_up else 0
            big = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            big.paste(self.doc, ((W - self.doc.width + r) // 2, (H - self.doc.height + r) // 2))
            self.doc = big

    def _cmd(self, step): return {"canvas size": "image.canvasSize"}[step]


def _photo(w=455, h=800):
    return Image.new("RGBA", (w, h), (200, 150, 100, 255))


def test_expanded_size_only_grows():
    from genfill.pipeline import expanded_size
    assert expanded_size(455, 800, "16:9") == (1422, 800)
    assert expanded_size(1600, 900, "9:16") == (1600, 2844)
    assert expanded_size(1000, 500, "+10% each side") == (1200, 600)
    assert expanded_size(1000, 1000, "1:1") == (1000, 1000)


def test_expand_fills_new_area_and_places_after_canvas_grows(tmp_path):
    from genfill.pipeline import expand
    pc, comfy = ExpandPC(_photo(), None), FakeComfy()
    r = expand("16:9", "", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
               pc=pc, comfy=comfy, count=2)
    assert r.expand_to == (1422, 800) and r.offset == (484, 0) and comfy.kinds == ["fill", "fill"]
    assert pc.commands == []                      # the document is untouched until a result is placed
    place(r, 0, _settings(tmp_path), pc=pc)
    assert pc.commands[0][0] == "image.canvasSize" and pc.commands[0][1]["extensionColor"] == "transparent"
    rel, name, masked = pc.placed[0]
    assert name == "Generative Expand – 16:9" and not masked
    out = np.asarray(Image.open(tmp_path / rel))
    assert out.shape[:2] == (800, 1422) and out[..., 3].min() == 255   # no gaps
    assert tuple(out[400, 700][:3]) == (200, 150, 100)                  # the photo itself is unchanged


def test_expand_follows_photocraft_if_it_centres_differently(tmp_path):
    from genfill.pipeline import expand
    photo = _photo()
    photo.paste((0, 0, 255, 255), (200, 0, 201, 800))   # a marker line 200 px into the photo
    pc = ExpandPC(photo, None, round_up=False)            # PhotoCraft puts it at x=483, not 484
    r = expand("16:9", "", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
               pc=pc, comfy=FakeComfy(), count=1)
    place(r, 0, _settings(tmp_path), pc=pc)
    out = np.asarray(Image.open(tmp_path / pc.placed[0][0]))
    assert out[..., 3].min() == 255
    assert tuple(out[400, 683][:3]) == (0, 0, 255)       # the layer lines up with the photo in the document


def test_expand_refuses_same_shape(tmp_path):
    from genfill.pipeline import expand
    with pytest.raises(UserFacingError, match="already"):
        expand("1:1", "", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
               pc=ExpandPC(_photo(500, 500), None), comfy=FakeComfy())


def test_more_like_this_starts_from_the_chosen_result(tmp_path):
    from genfill.pipeline import SIMILAR_DENOISE, similar
    pc, comfy = FakePC(_doc(), (0, 0, 1080, 285)), FakeComfy()
    r = _gen(pc, comfy, tmp_path, "sky", count=3)
    seen = []
    run = comfy.run
    comfy.run = lambda wf, p, c: (seen.append(wf), run(wf, p, c))[1]
    more = similar(r, 2, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(), comfy=comfy, count=2)
    assert len(more.images) == 3 and more.images[0] is r.images[2]   # the chosen one stays as choice 1
    assert all(wf["split"]["inputs"]["denoise"] == SIMILAR_DENOISE for wf in seen)
    assert more.layer == r.layer and more.selection == r.selection


def test_presets_fill_real_prompts():
    from genfill import presets
    names = [n for n, _ in presets.PRESETS]
    assert len(names) == len(set(names)) and presets.PLACEHOLDER not in names
    assert "Colorize" in presets.prompt_for("Colorize black & white")
    assert presets.prompt_for(presets.PLACEHOLDER) is None


# -- smart selections (SAM 3) -------------------------------------------------------------------

class SegPC(FakePC):
    """Also takes a mask as the selection (its bounds become the selection) and masks the layer."""
    def __init__(self, doc, selection=None, exchange=None):
        super().__init__(doc, selection)
        self.exchange, self.masked_layer, self.selected = exchange, False, []

    def select_mask(self, rel):
        alpha = Image.open(self.exchange / rel).getchannel("A")
        self.selected.append(rel)
        x0, y0, x1, y1 = alpha.point(lambda v: 255 if v > 127 else 0).getbbox()
        self.selection = (x0, y0, x1, y1)

    def mask_active_layer(self): self.masked_layer = True


class SegComfy(FakeComfy):
    """SAM 3 finds a box at `found` (or nothing); fills/edits return green like FakeComfy."""
    def __init__(self, found=(100, 400, 300, 700), missing_model=False):
        super().__init__()
        self.found, self.missing_model, self.queries, self.fill_masks = found, missing_model, [], []

    def check_segment(self, model=None):
        return ["ComfyUI doesn't have the SAM 3 model"] if self.missing_model else []

    def upload(self, img, name):
        if name == "genfill_mask.png":
            self.fill_masks.append(img.convert("L"))
        return super().upload(img, name)

    def run(self, wf, on_progress, cancel):
        if "detect" not in wf:
            return super().run(wf, on_progress, cancel)
        self.queries.append(wf["seg_text"]["inputs"]["text"])
        w, h = self.uploads[-1]
        m = Image.new("RGB", (w, h), (0, 0, 0))
        if self.found:
            sx, sy = w / 1080, h / 1920  # the doc is 1080x1920; SAM sees the working size
            x0, y0, x1, y1 = self.found
            m.paste((255, 255, 255), (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
        return m

    def run_all(self, wf, on_progress, cancel):  # one mask per thing found; SAM 3 finding nothing fails
        if not self.found:
            self.queries.append(wf["seg_text"]["inputs"]["text"])
            from genfill.comfy import ComfyError
            raise ComfyError("index 0 is out of bounds")
        return [self.run(wf, on_progress, cancel)]


class FaceComfy(FakeComfy):
    """SAM 3 finds faces at fractions of whatever picture it is shown; the redraws record their inputs."""
    def __init__(self, faces=((0.2, 0.2, 0.4, 0.3), (0.6, 0.5, 0.8, 0.6))):
        super().__init__()
        self.faces, self.edits, self.names = faces, [], []

    def check_segment(self, model=None): return []

    def upload(self, img, name):
        self.names.append(name)
        return super().upload(img, name)

    def run(self, wf, on_progress, cancel):
        if "detect" in wf:
            w, h = self.uploads[-1]
            m = Image.new("RGB", (w, h), (0, 0, 0))
            for x0, y0, x1, y1 in self.faces:
                m.paste((255, 255, 255), (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
            return m
        self.edits.append(wf)
        return super().run(wf, on_progress, cancel)


def _faces(tmp_path, comfy, selection=None, **kw):
    from genfill.pipeline import fix_faces
    pc = SegPC(Image.new("RGBA", (2000, 3000), (120, 100, 90, 255)), selection, exchange=tmp_path)
    return fix_faces(_settings(tmp_path), lambda s: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy,
                     **kw)


def test_fix_faces_redraws_each_face_partly_into_a_faces_only_layer(tmp_path):
    comfy = FaceComfy()
    r = _faces(tmp_path, comfy, count=2)
    assert r.layer == "Fix faces (2)" and not r.masked and len(r.images) == 2
    assert len(comfy.edits) == 4                                   # 2 faces x 2 results
    for wf in comfy.edits:
        assert wf["split"]["inputs"]["denoise"] == 0.5             # partly: the same people
        assert "Same person" in wf["text"]["inputs"]["text"]
    a = np.asarray(r.images[0].getchannel("A"))
    assert a[750, 600] == 255 and a[1650, 1400] == 255             # both faces are on the layer
    assert a[2800, 100] == 0                                       # nothing else is
    assert r.images[0].size == (2000, 3000)


def test_fix_faces_uses_seedvr_when_installed_one_result(tmp_path):
    comfy = FaceComfy()
    comfy.seedvr = True
    r = _faces(tmp_path, comfy, count=3)
    assert len(r.images) == 1 and len(comfy.edits) == 2            # 2 faces, once: SeedVR2 barely varies
    assert all(wf["svr_unet"]["inputs"]["unet_name"] == "seedvr2_7b_int8_convrot.safetensors" for wf in comfy.edits)
    a = np.asarray(r.images[0].getchannel("A"))
    assert a[750, 600] == 255 and a[2800, 100] == 0


def test_fix_faces_with_a_typed_wish_or_a_reference_stays_on_flux(tmp_path):
    for kw in ({"prompt": "smiling"}, {"reference": Image.new("RGB", (800, 1000), "white")}):
        comfy = FaceComfy()
        comfy.seedvr = True
        _faces(tmp_path, comfy, count=1, **kw)
        assert all("svr_unet" not in wf for wf in comfy.edits) and comfy.edits


def test_replace_face_is_an_edit_of_the_face_with_the_reference_face_as_image_2(tmp_path):
    comfy = FaceComfy()
    r = _faces(tmp_path, comfy, count=1, reference=Image.new("RGB", (800, 1000), "white"))
    assert "genfill_face_ref.png" in comfy.names
    for wf in comfy.edits:
        assert "ref1" in wf and "ref2" in wf          # image 1 = the face crop, image 2 = the reference
        assert "image 2" in wf["text"]["inputs"]["text"]
        assert "split" not in wf                            # a full edit, not a partial redraw
    assert r.layer == "Replace face"


BFS = "bfs_head_v1_flux-klein_9b_step3750_rank64.safetensors"


def test_replace_face_swaps_the_whole_head_with_the_face_swap_lora_when_installed(tmp_path):
    comfy = FaceComfy(faces=((0.4, 0.3, 0.6, 0.45),))
    comfy.loras_installed = (BFS,)
    s = _settings(tmp_path)
    s.loras = ["my_style.safetensors"]
    from genfill.pipeline import fix_faces
    pc = SegPC(Image.new("RGBA", (2000, 3000), (120, 100, 90, 255)), None, exchange=tmp_path)
    r = fix_faces(s, lambda m: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy, count=1,
                  reference=Image.new("RGB", (800, 1000), "white"))
    wf = comfy.edits[0]
    loras = [n["inputs"]["lora_name"] for n in wf.values() if n.get("class_type") == "LoraLoaderModelOnly"]
    assert loras == [BFS]                                  # the swap LoRA instead of the user's own
    assert wf["text"]["inputs"]["text"].startswith("head_swap:")
    assert "ref2" in wf                                    # image 1 = the picture, image 2 = the head
    assert comfy.uploads[comfy.names.index("genfill_face_ref.png")] == (512, 512)
    a = np.asarray(r.images[0].getchannel("A"))
    assert a[1125, 1000] == 255                            # the face
    assert a[760, 620] == 255                              # and the square around it (hair), not only the face
    assert a[2900, 100] == 0                               # nothing outside the square


def test_replace_face_without_the_swap_lora_keeps_the_face_only_edit(tmp_path):
    comfy = FaceComfy(faces=((0.4, 0.3, 0.6, 0.45),))
    comfy.loras_installed = (BFS,)
    s = _settings(tmp_path)
    s.face_swap_lora = ""                                  # switched off in the settings file
    from genfill.pipeline import fix_faces
    pc = SegPC(Image.new("RGBA", (2000, 3000), (120, 100, 90, 255)), None, exchange=tmp_path)
    fix_faces(s, lambda m: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy, count=1,
              reference=Image.new("RGB", (800, 1000), "white"))
    assert "image 2" in comfy.edits[0]["text"]["inputs"]["text"]


def test_fill_holes_makes_a_head_with_gaps_solid():
    from genfill.pipeline import _fill_holes
    m = Image.new("L", (100, 100), 0)
    m.paste(255, (20, 20, 80, 80))
    m.paste(0, (40, 40, 60, 60))                           # SAM missed a patch of skin
    out = _fill_holes(m)
    assert out.getpixel((50, 50)) == 255 and out.getpixel((5, 5)) == 0


def test_replace_face_changes_only_the_biggest_face_when_nothing_is_selected(tmp_path):
    comfy = FaceComfy(faces=((0.2, 0.2, 0.3, 0.25), (0.6, 0.5, 0.8, 0.6)))  # the second face is bigger
    r = _faces(tmp_path, comfy, count=2, reference=Image.new("RGB", (800, 1000), "white"))
    assert len(comfy.edits) == 2                                   # 1 face x 2 results
    a = np.asarray(r.images[0].getchannel("A"))
    assert a[1650, 1400] == 255                                    # the big face is replaced
    assert a[650, 500] == 0                                        # the small one is left alone


def test_replace_face_changes_the_face_in_the_selection(tmp_path):
    comfy = FaceComfy(faces=((0.2, 0.2, 0.3, 0.25), (0.6, 0.5, 0.8, 0.6)))
    r = _faces(tmp_path, comfy, selection=(300, 500, 700, 850), count=1,  # around the small face
               reference=Image.new("RGB", (800, 1000), "white"))
    assert len(comfy.edits) == 1
    a = np.asarray(r.images[0].getchannel("A"))
    assert a[650, 500] == 255 and a[1650, 1400] == 0


def test_replace_face_explains_a_selection_without_a_face(tmp_path):
    with pytest.raises(UserFacingError, match="no face inside the selection"):
        _faces(tmp_path, FaceComfy(), selection=(0, 2500, 300, 2900), count=1,
               reference=Image.new("RGB", (800, 1000), "white"))


def test_fix_faces_without_a_reference_still_fixes_every_face_even_with_a_selection(tmp_path):
    comfy = FaceComfy()
    r = _faces(tmp_path, comfy, selection=(0, 2500, 300, 2900), count=1)
    assert len(comfy.edits) == 2 and r.layer == "Fix faces (2)"


def test_fix_faces_explains_a_reference_without_a_face(tmp_path):
    class NoFaceInRef(FaceComfy):
        detects = 0

        def run(self, wf, on_progress, cancel):
            if "detect" in wf:
                self.detects += 1
                if self.detects == 2:  # the second search is in the reference picture
                    return Image.new("RGB", self.uploads[-1], (0, 0, 0))
            return super().run(wf, on_progress, cancel)
    with pytest.raises(UserFacingError, match="face in the reference"):
        _faces(tmp_path, NoFaceInRef(), count=1, reference=Image.new("RGB", (600, 800), "white"))


def test_fix_faces_without_faces_says_so(tmp_path):
    with pytest.raises(UserFacingError, match="Couldn't find faces"):
        _faces(tmp_path, FaceComfy(faces=()), count=1)


def _enhance(tmp_path, doc, comfy, **kw):
    from genfill.pipeline import enhance
    return enhance(_settings(tmp_path), lambda s: None, lambda f: None, threading.Event(), pc=FakePC(doc, None),
                   comfy=comfy, **kw)


def test_enhance_small_picture_is_one_partial_redraw_per_result(tmp_path):
    comfy = FaceComfy()
    doc = Image.new("RGBA", (800, 600), (100, 110, 120, 255))
    r = _enhance(tmp_path, doc, comfy, count=2, prompt="golden hour")
    assert len(comfy.edits) == 4 and len(r.images) == 2  # per result: light and colour, then detail
    look, detail = comfy.edits[0], comfy.edits[1]
    assert "ref1" not in look and look["split"]["inputs"]["denoise"] == 0.35
    assert "ref1" in detail and detail["split"]["inputs"]["denoise"] == 0.3   # guided by the picture itself
    for wf in comfy.edits:
        assert "professional" in wf["text"]["inputs"]["text"] and "golden hour" in wf["text"]["inputs"]["text"]
    assert comfy.uploads[1][0] > 800                     # the detail pass enlarges a small picture
    assert r.images[0].size == (800, 600) and not r.masked and r.layer == "Enhance – golden hour"


def test_enhance_big_picture_adds_full_size_detail_tiles(tmp_path):
    from genfill import imaging
    comfy = FaceComfy()
    doc = Image.new("RGBA", (3000, 2000), (100, 110, 120, 255))
    r = _enhance(tmp_path, doc, comfy, count=1)
    tiles = imaging.tile_boxes(3000, 2000)
    assert len(comfy.edits) == 1 + len(tiles)            # the look, then the detail tiles
    assert comfy.edits[1]["split"]["inputs"]["denoise"] == 0.3 and "ref1" in comfy.edits[1]
    assert max(comfy.uploads[1:]) <= (1024, 1024)        # tiles at full size, not shrunk
    assert r.images[0].size == (3000, 2000)


def test_enhance_makes_at_most_two_results(tmp_path):
    comfy = FaceComfy()
    r = _enhance(tmp_path, Image.new("RGBA", (640, 480), "gray"), comfy, count=4)
    assert len(r.images) == 2


def test_transfer_look_keeps_detail_and_takes_the_light():
    from genfill import imaging
    orig = Image.new("RGB", (400, 300), (100, 100, 100))
    for x in range(0, 400, 4):
        orig.paste((140, 140, 140), (x, 0, x + 1, 300))  # fine stripes = detail
    brighter = Image.new("RGB", (100, 75), (150, 150, 150))
    out = np.asarray(imaging.transfer_look(orig, brighter)).astype(int)
    assert abs(out[150, 100:300, 0].mean() - (110 + 40)) < 8  # about 40 brighter on average
    assert out[150, 200, 0] - out[150, 201, 0] >= 30         # the stripes are still there


def test_shapes_finds_separate_shapes_largest_first():
    from genfill import imaging
    m = Image.new("L", (1000, 800), 0)
    m.paste(255, (100, 100, 200, 220))
    m.paste(255, (500, 300, 800, 700))
    boxes = imaging.shapes(m)
    assert len(boxes) == 2
    assert all(abs(a - b) <= 3 for a, b in zip(boxes[0], (500, 300, 800, 700)))
    assert all(abs(a - b) <= 3 for a, b in zip(boxes[1], (100, 100, 200, 220)))


def test_created_pictures_are_at_least_full_hd():
    from genfill.pipeline import CREATE_CHOICES, create_size
    assert create_size("16:9", 2.0) == (1920, 1080) and create_size("9:16", 1.0) == (1080, 1920)
    assert create_size("1:1", 2.0) == (1440, 1440)
    for c, r in CREATE_CHOICES.items():
        w, h = create_size(c, 1.0)
        assert w % 2 == 0 and h % 2 == 0
        assert w * h >= 1920 * 1080 * 0.995 and abs(w / h - r) < 0.01
        w, h = create_size(c, 3.0)
        assert 2.9e6 < w * h < 3.1e6


def test_the_old_default_size_moves_to_the_new_one(tmp_path):
    import json
    from genfill.settings import Settings
    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"megapixels": 1.0}))
    assert Settings.load(f).megapixels == 2.0
    f.write_text(json.dumps({"megapixels": 1.5}))
    assert Settings.load(f).megapixels == 1.5          # set by hand: kept
    f.write_text(json.dumps({"megapixels": 1.0, "settings_version": 2}))
    assert Settings.load(f).megapixels == 1.0          # chosen after the change: kept


def _create(tmp_path, comfy, prompt="a lighthouse at dusk", **kw):
    from genfill.pipeline import create
    return create("4:5", prompt, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                  comfy=comfy, **kw)


def test_create_makes_new_pictures_and_opens_the_chosen_one(tmp_path):
    comfy = FakeComfy()
    results = _create(tmp_path, comfy, count=2)
    assert comfy.runs == 2 and comfy.uploads == []  # nothing to start from
    assert results.new_document and results.images[0].size == (1288, 1610)
    pc = FakePC(_doc(), None)
    msg = place(results, 1, _settings(tmp_path), pc=pc)
    assert msg.startswith("Created 1288×1610") and len(pc.opened) == 1 and not pc.placed
    assert Image.open(tmp_path / pc.opened[0]).size == (1288, 1610)


def test_create_needs_a_prompt(tmp_path):
    comfy = FakeComfy()
    with pytest.raises(UserFacingError, match="Describe the picture"):
        _create(tmp_path, comfy, prompt="  ")
    assert comfy.runs == 0


def test_create_reference_is_image_1_for_the_model(tmp_path):
    seen = []

    class C(FakeComfy):
        def run(self, wf, on_progress, cancel):
            seen.append((wf["text"]["inputs"]["text"], "ref1" in wf))
            return super().run(wf, on_progress, cancel)
    _create(tmp_path, C(), prompt="the shoe from Image 2 on a rock", count=1,
            reference=Image.new("RGB", (300, 200), "red"))
    assert seen == [("the shoe from image 1 on a rock", True)]


def test_more_like_a_created_picture_still_opens_as_a_document(tmp_path):
    from genfill.pipeline import similar
    comfy = FakeComfy()
    results = _create(tmp_path, comfy, count=1)
    more = similar(results, 0, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                   comfy=comfy, count=2)
    assert more.new_document and len(more.images) == 3 and comfy.uploads  # redraws from the chosen one


def test_loras_chain_after_the_model():
    from genfill.comfy import build_edit_workflow
    models = {**Settings().models, "loras": ["a.safetensors", {"name": "b.safetensors", "strength": 0.7}]}
    wf = build_edit_workflow("img.png", "x", 1, 4, 512, 512, models)
    assert wf["lora0"]["inputs"]["model"] == ["unet", 0]
    assert wf["lora1"]["inputs"] == {"model": ["lora0", 0], "lora_name": "b.safetensors", "strength_model": 0.7}
    assert wf["guider"]["inputs"]["model"] == ["lora1", 0]
    plain = build_edit_workflow("img.png", "x", 1, 4, 512, 512, Settings().workflow_models)
    assert plain["guider"]["inputs"]["model"] == ["unet", 0] and "lora0" not in plain


def test_segment_query_asks_for_all_matches():
    from genfill.pipeline import segment_query
    assert segment_query("sky") == "sky:20"
    assert segment_query("person, car") == "person:20, car:20"
    assert segment_query("dog:2") == "dog:2"


def test_select_things_selects_the_found_shape(tmp_path):
    from genfill.pipeline import select_things
    pc, comfy = SegPC(_doc(), exchange=tmp_path), SegComfy()
    msg = select_things("the dog", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                        pc=pc, comfy=comfy)
    assert msg == "Selected the dog"
    assert comfy.queries == ["the dog:20"]
    x0, y0, x1, y1 = pc.selection
    assert abs(x0 - 100) <= 3 and abs(y0 - 400) <= 3 and abs(x1 - 300) <= 3 and abs(y1 - 700) <= 3


def test_nothing_found_is_explained_and_nothing_selected(tmp_path):
    from genfill.pipeline import select_things
    pc = SegPC(_doc(), exchange=tmp_path)
    with pytest.raises(UserFacingError, match="Couldn't find the unicorn"):
        select_things("the unicorn", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                      pc=pc, comfy=SegComfy(found=None))
    assert pc.selected == []


def test_missing_sam_model_says_where_to_get_it(tmp_path):
    from genfill.pipeline import select_things
    with pytest.raises(UserFacingError, match="SAM 3 model"):
        select_things("sky", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                      pc=SegPC(_doc(), exchange=tmp_path), comfy=SegComfy(missing_model=True))


def test_empty_text_asks_what_to_select(tmp_path):
    from genfill.pipeline import select_things
    comfy = SegComfy()
    with pytest.raises(UserFacingError, match="Type what to select"):
        select_things("  ", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                      pc=SegPC(_doc(), exchange=tmp_path), comfy=comfy)
    assert comfy.runs == 0


def test_remove_things_redraws_only_around_the_found_shape(tmp_path):
    from genfill.pipeline import remove_things
    pc, comfy = SegPC(_doc(), exchange=tmp_path), SegComfy(found=(100, 400, 300, 700))
    r = remove_things("person", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                      pc=pc, comfy=comfy, label="people", count=1)
    assert r.layer == "Remove – people" and r.masked
    assert "Remove the people completely" in r.job.prompt
    fm = np.asarray(r.job.mask)
    assert fm[550, 200] == 255          # inside the found shape: redrawn
    assert fm[1500, 900] == 0           # far away: untouched
    assert comfy.fill_masks  # (sent to the model as a crop around the shape)


def test_small_selection_in_big_photo_is_filled_from_a_crop(tmp_path, monkeypatch):
    from genfill import imaging
    monkeypatch.setattr(imaging, "match_colors", lambda orig, gen, mask: gen)  # see the fill's own pixels
    big = Image.new("RGBA", (6000, 4000), (90, 120, 160, 255))
    sel = (3000, 2000, 3200, 2200)
    pc, comfy = FakePC(big, sel), FakeComfy()
    r = generate("a red ball", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=pc, comfy=comfy, count=1)
    crop = imaging.fill_crop(big.size, r.job.mask)
    assert crop and crop[0] <= sel[0] and crop[2] >= sel[2] and (crop[2] - crop[0]) < 1000
    zoom = comfy.uploads[0][0] / (crop[2] - crop[0])
    assert zoom > 1.5                                  # more pixels than the photo itself has there
    out = np.asarray(r.images[0])
    assert tuple(out[2100, 3100, :3]) == (0, 255, 0)   # the fill landed in the selection (row y, column x)
    assert tuple(out[200, 200, :3]) == (90, 120, 160)  # far away stays the photo
    assert r.images[0].size == big.size


def test_fill_crop_stays_inside_and_skips_when_no_gain():
    from genfill import imaging
    m = Image.new("L", (4000, 3000), 0)
    m.paste(255, (3950, 10, 4000, 60))                 # corner: crop slides back inside
    x0, y0, x1, y1 = imaging.fill_crop(m.size, m)
    assert x0 >= 0 and y0 == 0 and x1 == 4000 and y1 - y0 >= 512 and x1 - x0 >= 512
    big = Image.new("L", (4000, 3000), 0)
    big.paste(255, (200, 200, 3800, 2800))             # most of the picture: whole picture as before
    assert imaging.fill_crop(big.size, big) is None
    small_doc = Image.new("L", (900, 900), 0)
    small_doc.paste(255, (100, 100, 200, 200))         # picture within the working size: as before
    assert imaging.fill_crop(small_doc.size, small_doc) is None


def test_remove_background_masks_the_active_layer(tmp_path):
    from genfill.pipeline import remove_background
    pc = SegPC(_doc(), exchange=tmp_path)
    msg = remove_background("the cup", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                            pc=pc, comfy=SegComfy())
    assert pc.masked_layer and msg == "Background removed, kept the cup"


def test_small_holes_are_closed_big_gaps_stay_open():
    from genfill.imaging import fill_small_holes
    m = Image.new("L", (400, 400), 0)
    m.paste(255, (50, 50, 350, 350))
    m.paste(0, (100, 100, 110, 110))     # a speck the detector missed: closed
    m.paste(0, (150, 150, 300, 300))     # a big gap (like between an arm and a body): stays open
    out = np.asarray(fill_small_holes(m))
    assert out[105, 105] == 255
    assert out[220, 220] == 0
    assert out[10, 10] == 0


# -- edit by naming ----------------------------------------------------------------------------------------
def test_named_target_reads_the_thing_an_edit_names():
    from genfill.pipeline import named_target
    assert named_target("Make the car red.") == ["car red", "car"]
    assert named_target("change the sofa to green leather") == ["sofa"]
    assert named_target("give the man a hat") == ["man"]
    assert named_target("turn her dress into a ball gown") == ["dress"]
    assert named_target("make it night") == []
    assert named_target("make the whole picture warmer") == []
    assert named_target("a cat on the sofa") == []


def test_naming_a_thing_with_nothing_selected_edits_only_that_thing(tmp_path):
    pc, comfy = SegPC(_doc(), exchange=tmp_path), SegComfy(found=(100, 400, 300, 700))
    r = generate("make the car red", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=pc, comfy=comfy, count=1)
    assert pc.selected                      # the car is selected in PhotoCraft
    assert r.masked and r.layer == "Edit – make the car red"
    fm = np.asarray(r.job.mask)
    assert fm[550, 200] == 255 and fm[1500, 900] == 0
    assert comfy.queries[0].startswith("car red")


def test_naming_a_thing_that_isnt_there_edits_the_whole_picture(tmp_path):
    pc, comfy = SegPC(_doc(), exchange=tmp_path), SegComfy(found=None)
    r = generate("make the car red", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=pc, comfy=comfy, count=1)
    assert not pc.selected and r.job.mask is None and not r.masked
    assert len(comfy.queries) == 2          # tried 'car red', then 'car'


def test_naming_without_sam_edits_the_whole_picture(tmp_path):
    pc, comfy = SegPC(_doc(), exchange=tmp_path), SegComfy(missing_model=True)
    r = generate("make the car red", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=pc, comfy=comfy, count=1)
    assert r.job.mask is None and not pc.selected


def test_naming_a_thing_wins_over_a_style_left_on(tmp_path):
    from dataclasses import replace
    pc, comfy = SegPC(_doc(), exchange=tmp_path), SegComfy(found=(100, 400, 300, 700))
    s = replace(_settings(tmp_path), style="Cinematic", angle="Low angle")
    r = generate("make the car red", s, lambda s: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy,
                 count=1)
    assert r.masked and r.job.prompt == "make the car red"


def test_reflection_presets_run_without_the_users_loras(tmp_path):
    from dataclasses import replace as _r
    pc = FakePC(_doc(), None)
    comfy = FakeComfy()
    settings = _r(_settings(tmp_path), loras=[{"name": "mine.safetensors", "strength": 1.0}])
    generate("x", settings, lambda s: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy, count=1,
             name="Remove reflections: windows")
    generate("x", settings, lambda s: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy, count=1,
             name="Relight: night")
    lora_nodes = [[n for n in wf.values() if "lora" in str(n.get("class_type", "")).lower()] for wf in comfy.wfs]
    assert lora_nodes[0] == [] and lora_nodes[1] != []


# -- sketch to image ---------------------------------------------------------------------------------------
class SketchPC(SegPC):
    """The active layer is a sketch with `bounds` (None: an empty layer)."""
    def __init__(self, doc, bounds, exchange, name="Layer 1", visible=True):
        super().__init__(doc, None, exchange)
        self.row = {"id": 7, "name": name, "kind": "Pixel", "visible": visible, "bounds": bounds}

    def active_layer_info(self): return self.row


def _sketch(pc, tmp_path, prompt="a red vintage car"):
    from genfill.pipeline import sketch_to_image
    return sketch_to_image(prompt, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                           pc=pc, comfy=FakeComfy(), count=1)


def test_sketch_area_adds_a_margin_inside_the_picture():
    from genfill.pipeline import sketch_area
    assert sketch_area((100, 400, 600, 700), (1080, 1920)) == (40, 340, 660, 760)
    assert sketch_area((0, 0, 50, 50), (1080, 1920)) == (0, 0, 74, 74)


def test_a_sketch_is_redrawn_in_the_area_around_it(tmp_path):
    pc = SketchPC(_doc(), (100, 400, 600, 700), tmp_path)
    r = _sketch(pc, tmp_path)
    assert pc.selected and pc.selection == (40, 340, 660, 760)
    assert r.masked and r.layer == "Generative Fill – Sketch – a red vintage car"
    assert "sketch" in r.job.prompt and "a red vintage car" in r.job.prompt


@pytest.mark.parametrize("bounds, name, visible, says", [
    (None, "Layer 1", True, "The active layer is empty"),
    ((0, 0, 1080, 1920), "Layer 1", True, "looks like the photo"),
    ((100, 400, 600, 700), "Background", True, "looks like the photo"),
    ((100, 400, 600, 700), "Layer 1", False, "is hidden"),
])
def test_no_sketch_says_how_to_make_one(tmp_path, bounds, name, visible, says):
    pc = SketchPC(_doc(), bounds, tmp_path, name, visible)
    with pytest.raises(UserFacingError, match=says):
        _sketch(pc, tmp_path)
    assert not pc.selected


def test_a_sketch_is_redrawn_without_the_users_loras(tmp_path):
    from dataclasses import replace as _r
    from genfill.pipeline import sketch_to_image
    pc, comfy = SketchPC(_doc(), (100, 400, 600, 700), tmp_path), FakeComfy()
    s = _r(_settings(tmp_path), loras=[{"name": "mine.safetensors", "strength": 1.0}])
    sketch_to_image("a red car", s, lambda m: None, lambda f: None, threading.Event(), pc=pc, comfy=comfy, count=1)
    assert "mine.safetensors" not in str(comfy.wfs[-1])
