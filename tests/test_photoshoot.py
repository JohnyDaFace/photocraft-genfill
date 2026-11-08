import threading

import pytest
from PIL import Image

from genfill import shoot_store, shoots
from genfill.comfy import ComfyError
from genfill.photoshoot import run_photoshoot
from genfill.pipeline import UserFacingError, create_size
from genfill.settings import Settings
from test_pipeline import FakeComfy, FakePC, _doc


@pytest.fixture(autouse=True)
def _builtin_shoots_only(tmp_path, monkeypatch):
    monkeypatch.setattr(shoots, "SHOOTS_FILE", tmp_path / "no_shoots.json")


class ShootComfy(FakeComfy):
    """SAM 3 finds a box at fractions `box` (None: nothing); creates are recorded and can be made to fail."""
    def __init__(self):
        super().__init__()
        self.box, self.fail_runs = (0.3, 0.2, 0.7, 0.8), set()
        self.prompts, self.ref_counts, self.creates = [], [], 0

    def check_segment(self, model=None): return []

    def run(self, wf, on_progress, cancel):
        if "detect" in wf:
            w, h = self.uploads[-1]
            m = Image.new("RGB", (w, h), (0, 0, 0))
            if self.box:
                x0, y0, x1, y1 = self.box
                m.paste((255, 255, 255), (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
            return m
        self.creates += 1
        self.prompts.append(wf["text"]["inputs"]["text"])
        self.ref_counts.append(sum(1 for k in wf if k.startswith("extra") and k.endswith("_image")))
        if self.creates in self.fail_runs:
            raise ComfyError("boom")
        return super().run(wf, on_progress, cancel)


class ShootPC(FakePC):
    def __init__(self, doc, selection, exchange):
        super().__init__(doc, selection)
        self.exchange, self.renamed, self.active = exchange, [], None

    def open_document(self, rel):
        self.opened.append(rel)
        self.layers = [1]
        self.doc = Image.new("RGBA", Image.open(self.exchange / rel).size, (0, 0, 0, 0))

    def rename_layer(self, name, layer=None): self.renamed.append((name, layer))

    def place_layer(self, rel, name, masked=True):
        assert (self.exchange / rel).exists()
        super().place_layer(rel, name, masked)

    def active_layer(self): return self.active if self.active is not None else self.layers[-1]


def _settings(tmp_path, **kw):
    return Settings(exchange_dir=str(tmp_path), results_dir=str(tmp_path / "results"),
                    shoots_dir=str(tmp_path / "shoots"), **kw)


def _shoot(tmp_path, comfy, pc, name="Luxury dark", count=8, cancel_after=None, status=None, **kw):
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (352, 440), (200, 40, 40)).save(photo)
    cancel = threading.Event()

    def say(s):
        if cancel_after is not None and s.startswith(f"Shot {cancel_after + 1} of"):
            cancel.set()
        (status or (lambda _s: None))(s)

    return run_photoshoot(name, photo, _settings(tmp_path, shoot_count=count, **kw), say, lambda f: None, cancel,
                          pc=pc, comfy=comfy)


def test_eight_shots_open_as_one_document_with_named_tagged_layers(tmp_path):
    pc, comfy = ShootPC(_doc(), None, tmp_path), ShootComfy()
    msg = _shoot(tmp_path, comfy, pc)
    assert len(pc.opened) == 1 and len(pc.placed) == 7
    assert pc.renamed[0][0] == "Shot 1 – hero"
    assert [n for _, n, m in pc.placed][0].startswith("Shot 2 – ") and all(m is False for _, _, m in pc.placed)
    assert [shoot_store.tag_of(pc.xmp[l])[1] for l in pc.layers] == list(range(1, 9))
    assert msg.startswith("Photoshoot ready: 8 shots as layers")


def test_the_hero_is_shown_to_every_later_shot(tmp_path):
    comfy = ShootComfy(); _shoot(tmp_path, comfy, ShootPC(_doc(), None, tmp_path))
    assert comfy.ref_counts == [1] + [2] * 7
    assert comfy.prompts[0].startswith("A professional photoshoot photo of the product from image 1. The shoot: ")
    assert all(p.startswith("Another photo from the same professional photoshoot as image 2")
               for p in comfy.prompts[1:])


def test_count_picks_the_first_shots_and_odd_counts_fall_back_to_eight(tmp_path):
    for count, made in ((4, 4), (12, 12), (3, 8)):
        pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc, count=count)
        assert 1 + len(pc.placed) == made


def test_twelve_shots_all_place_from_files_that_still_exist(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc, count=12)
    assert len(pc.placed) == 11   # ShootPC.place_layer asserted each file existed


def test_shots_are_the_create_size_of_the_chosen_shape(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc)
    assert Image.open(tmp_path / pc.placed[0][0]).size == create_size("1:1", 2.0)


def test_no_product_found_uses_the_whole_photo_and_says_so(tmp_path):
    said = []
    comfy = ShootComfy(); comfy.box = None
    _shoot(tmp_path, comfy, ShootPC(_doc(), None, tmp_path), status=said.append)
    assert any("using the whole photo" in s for s in said)


def test_a_failing_shot_is_retried_once_then_skipped(tmp_path):
    comfy = ShootComfy(); comfy.fail_runs = {3}          # shot 3 fails once, the retry works
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, comfy, pc)
    assert len(pc.placed) == 7
    comfy = ShootComfy(); comfy.fail_runs = {3, 4}       # shot 3 fails twice: skipped
    pc = ShootPC(_doc(), None, tmp_path); msg = _shoot(tmp_path, comfy, pc)
    assert len(pc.placed) == 6 and "Shots 3 failed" in msg
    assert 3 not in [shoot_store.tag_of(pc.xmp[l])[1] for l in pc.layers]


def test_any_error_on_a_shot_skips_it_and_keeps_the_others(tmp_path):
    class Crashy(ShootComfy):
        def run(self, wf, on_progress, cancel):
            if "detect" not in wf and self.creates in (2, 3):  # shot 3 (create runs 3 and 4) crashes twice
                self.creates += 1
                raise RuntimeError("bug")
            return super().run(wf, on_progress, cancel)
    pc = ShootPC(_doc(), None, tmp_path); msg = _shoot(tmp_path, Crashy(), pc)
    assert len(pc.placed) == 6 and "Shots 3 failed" in msg


def test_a_failing_hero_stops_with_a_plain_message(tmp_path):
    comfy = ShootComfy(); comfy.fail_runs = {1, 2}
    pc = ShootPC(_doc(), None, tmp_path)
    with pytest.raises(UserFacingError, match="The first shot failed"):
        _shoot(tmp_path, comfy, pc)
    assert pc.opened == []


def test_cancel_keeps_the_finished_shots(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path)
    msg = _shoot(tmp_path, ShootComfy(), pc, cancel_after=3)
    assert 1 + len(pc.placed) == 3 and "cancelled" in msg


def test_cancel_before_any_shot_is_a_plain_error(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path)
    with pytest.raises(UserFacingError, match="Cancelled"):
        _shoot(tmp_path, ShootComfy(), pc, cancel_after=0)
    assert pc.opened == []


def test_a_run_is_kept_for_reshoot(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc)
    run_id, _ = shoot_store.tag_of(pc.xmp[pc.layers[0]])
    run, subject, hero = shoot_store.load_run(tmp_path / "shoots", run_id)
    assert run.shoot == "Luxury dark" and len(run.shots) == 8 and subject.size == (1024, 1024)


def test_styles_are_never_added(tmp_path):
    comfy = ShootComfy(); pc = ShootPC(_doc(), None, tmp_path)
    _shoot(tmp_path, comfy, pc, style="Anime", angle="Low angle")
    assert not any("anime" in p.lower() for p in comfy.prompts)


def test_unknown_shoot_and_unreadable_photo_are_plain_errors(tmp_path):
    with pytest.raises(UserFacingError): _shoot(tmp_path, ShootComfy(), ShootPC(_doc(), None, tmp_path), name="Nope")
    with pytest.raises(UserFacingError, match="Can't open"):
        run_photoshoot("Luxury dark", tmp_path / "missing.jpg", _settings(tmp_path), print, print,
                       threading.Event(), pc=ShootPC(_doc(), None, tmp_path), comfy=ShootComfy())
