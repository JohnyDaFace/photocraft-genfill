import threading

import numpy as np
import pytest
from PIL import Image

from genfill.photocraft_client import DocInfo
from genfill.pipeline import UserFacingError, run_fill, run_upscale
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
    def place_layer(self, rel, name): self.placed.append((rel, name))
    def open_document(self, rel): self.opened.append(rel)


class FakeComfy:
    def __init__(self): self.runs, self.uploads = 0, []
    def check(self): return {}
    def upload(self, img, name): self.uploads.append(img.size); return name
    def run(self, wf, on_progress, cancel):
        self.runs += 1
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
    rel, layer_name = pc.placed[0]
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
