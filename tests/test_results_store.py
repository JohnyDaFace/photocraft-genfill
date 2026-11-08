"""Lasting results: sets kept on disk, the layer tagged, and the set brought back later."""
import threading
import time

import pytest

from genfill import results_store
from genfill.pipeline import NO_RESULTS, UserFacingError, generate, place, results_for_layer, swap
from test_pipeline import FakeComfy, FakePC, _doc, _settings


def _placed(tmp_path, selection=(0, 300, 500, 600), count=3, index=1):
    pc = FakePC(_doc(), selection)
    s = _settings(tmp_path)
    r = generate("a boat", s, lambda x: None, lambda f: None, threading.Event(), pc=pc, comfy=FakeComfy(),
                 count=count)
    place(r, index, s, pc=pc)
    return pc, s, r


def test_tag_round_trip():
    assert results_store.tag_of(results_store.tag_packet("0123456789abcdef")) == "0123456789abcdef"
    assert results_store.tag_of(None) is None and results_store.tag_of("<x/>") is None


def test_placing_keeps_the_set_and_tags_the_layer(tmp_path):
    pc, s, r = _placed(tmp_path)
    assert r.saved and (tmp_path / "results" / r.saved / "set.json").exists()
    assert results_store.tag_of(pc.layer_metadata(r.placed.layer)) == r.saved


def test_a_single_result_is_not_kept(tmp_path):
    pc, s, r = _placed(tmp_path, count=1, index=0)
    assert r.saved is None and not getattr(pc, "xmp", {})


def test_the_set_comes_back_for_the_selected_layer(tmp_path):
    pc, s, r = _placed(tmp_path)
    back = results_for_layer(s, pc=pc)
    assert back.saved == r.saved and back.job is None and len(back.images) == 3
    assert back.placed.index == 1 and back.placed.layer == r.placed.layer
    assert back.images[2].size == r.images[2].size
    box = results_store._area(r)
    assert back.images[2].crop(box).tobytes() == r.images[2].convert("RGBA").crop(box).tobytes()


def test_a_reloaded_set_swaps_and_tags_the_new_layer(tmp_path):
    pc, s, r = _placed(tmp_path)
    back = results_for_layer(s, pc=pc)
    swap(back, 0, s, pc=pc)
    new = back.placed.layer
    assert pc.replaced[0][0] == r.placed.layer and new != r.placed.layer
    assert results_store.tag_of(pc.layer_metadata(new)) == r.saved
    assert results_for_layer(s, pc=pc).placed.index == 0  # remembered which one is in now


def test_an_untagged_layer_says_how(tmp_path):
    pc = FakePC(_doc(), None)
    pc.layers = [7]  # the photo itself, no tag
    with pytest.raises(UserFacingError) as e:
        results_for_layer(_settings(tmp_path), pc=pc)
    assert str(e.value) == NO_RESULTS


def test_a_pruned_set_says_so(tmp_path):
    pc, s, r = _placed(tmp_path)
    results_store.prune(tmp_path / "results", 0)
    with pytest.raises(UserFacingError, match="no longer kept"):
        results_for_layer(s, pc=pc)


def test_prune_keeps_the_newest(tmp_path):
    root = tmp_path / "results"
    for n in range(4):
        (root / f"{n:016x}").mkdir(parents=True)
        (root / f"{n:016x}" / "set.json").write_text("{}")
        time.sleep(0.02)
    results_store.prune(root, 2)
    assert sorted(p.name for p in root.iterdir()) == [f"{2:016x}", f"{3:016x}"]
