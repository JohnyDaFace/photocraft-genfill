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
    def place_layer(self, rel, name, masked=True): self.placed.append((rel, name, masked))
    def open_document(self, rel): self.opened.append(rel)


class FakeComfy:
    def __init__(self): self.runs, self.uploads, self.seeds, self.kinds = 0, [], [], []
    def check(self): return {}
    def upload(self, img, name): self.uploads.append(img.size); return name
    def run(self, wf, on_progress, cancel):
        self.runs += 1
        self.seeds.append(wf["noise"]["inputs"]["noise_seed"])
        self.kinds.append("fill" if "mask" in wf else "edit" if "sigmas" in wf else "upscale")
        w = wf["sched" if "sched" in wf else "sigmas"]["inputs"]
        return Image.new("RGB", (w["width"], w["height"]), (0, 255, 0))


def _doc():
    d = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    d.paste(Image.new("RGB", (1080, 1350), (200, 150, 100)), (0, 285))
    return d


def _settings(tmp_path):
    return Settings(exchange_dir=str(tmp_path))


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


def test_upscale_caps_size(tmp_path):
    pc = FakePC(Image.new("RGBA", (6000, 3000), (9, 9, 9, 255)), None)
    with pytest.raises(UserFacingError, match="too large"):
        _go(run_upscale, pc, FakeComfy(), tmp_path)


def _gen(pc, comfy, tmp_path, prompt="", count=None):
    return generate(prompt, _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                    pc=pc, comfy=comfy, count=count)


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
    assert w % 16 == 0 and h % 16 == 0 and w * h <= 1_100_000
    assert "extra2_ref" in seen[0]


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
