"""Swapping a placed result for another one from the same Generate (like Photoshop's variations)."""
import threading
import tkinter as tk

import pytest
from PIL import Image

from genfill import theme
from genfill.photocraft_client import PhotoCraftClient, PhotoCraftError
from genfill.pipeline import SWAP_GONE, UserFacingError, create, generate, place, swap
from test_pipeline import FakeComfy, FakePC, _doc, _settings


def _results(tmp_path, selection=(0, 300, 500, 600), count=3):
    pc = FakePC(_doc(), selection)
    r = generate("a boat", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=pc, comfy=FakeComfy(), count=count)
    return pc, r


def test_placing_remembers_the_layer_it_added(tmp_path):
    pc, r = _results(tmp_path)
    place(r, 1, _settings(tmp_path), pc=pc)
    assert r.placed.index == 1 and r.placed.layer == 101 and r.placed.document == pc.name


def test_another_result_replaces_the_same_layer_keeping_its_mask(tmp_path):
    pc, r = _results(tmp_path)
    place(r, 0, _settings(tmp_path), pc=pc)
    pc.selection = None  # the user deselected afterwards: a swap doesn't need the selection
    swap(r, 2, _settings(tmp_path), pc=pc)
    old, _, name, masked = pc.replaced[0]
    assert old == 101 and masked and name == r.layer
    assert len(pc.placed) == 1                 # no second layer
    assert r.placed.index == 2 and r.placed.layer == 201
    swap(r, 0, _settings(tmp_path), pc=pc)     # and back again
    assert pc.replaced[1][0] == 201 and r.placed.index == 0


def test_a_whole_image_edit_swaps_without_a_mask(tmp_path):
    pc, r = _results(tmp_path, selection=None)
    place(r, 0, _settings(tmp_path), pc=pc)
    swap(r, 1, _settings(tmp_path), pc=pc)
    assert pc.replaced[0][3] is False


def test_the_first_pick_just_places(tmp_path):
    pc, r = _results(tmp_path)
    swap(r, 1, _settings(tmp_path), pc=pc)
    assert len(pc.placed) == 1 and not getattr(pc, "replaced", []) and r.placed.index == 1


def test_swap_changes_nothing_when_the_layer_was_deleted(tmp_path):
    pc, r = _results(tmp_path)
    place(r, 0, _settings(tmp_path), pc=pc)
    pc.layers = []
    with pytest.raises(UserFacingError) as e:
        swap(r, 1, _settings(tmp_path), pc=pc)
    assert str(e.value) == SWAP_GONE and r.placed.index == 0


def test_swap_changes_nothing_in_another_document(tmp_path):
    pc, r = _results(tmp_path)
    place(r, 0, _settings(tmp_path), pc=pc)
    pc.name = ("other.psd", "D:/other.psd")
    with pytest.raises(UserFacingError, match="Switch back to photo.jpg"):
        swap(r, 1, _settings(tmp_path), pc=pc)
    assert not getattr(pc, "replaced", [])


def test_created_pictures_open_another_document_instead(tmp_path):
    pc = FakePC(_doc(), None)
    r = create("1:1", "a lighthouse", _settings(tmp_path), lambda s: None, lambda f: None, threading.Event(),
               comfy=FakeComfy(), count=2)
    place(r, 0, _settings(tmp_path), pc=pc)
    swap(r, 1, _settings(tmp_path), pc=pc)
    assert len(pc.opened) == 2 and r.placed.index == 1


class Recorder(PhotoCraftClient):
    """Records the commands replace_layer sends (layer 7 is the one GenFill added; 8 is the new one)."""
    def __init__(self, has_selection=True):
        super().__init__(mode="desktop")
        self.sent, self.has_selection, self.active = [], has_selection, 7

    def _inspect(self):
        return {"hasSelection": self.has_selection, "activeLayer": self.active, "name": "a", "path": "b"}

    def _put(self, rel):
        self.sent.append(("paste", rel))
        self.active = 8

    def execute(self, command, params=None):
        self.sent.append((command, params))
        return {}


def test_replace_layer_reuses_the_old_mask_then_deletes_the_old_layer():
    pc = Recorder()
    assert pc.replace_layer(7, "r.png", "Generative Fill – boat", masked=True) == 8
    cmds = [c for c, _ in pc.sent]
    assert cmds == ["select.saveSelection", "layer.select", "select.loadSelection", "paste",
                    "layer.layerMask.revealSelection", "layer.delete", "layer.select", "layer.renameLayer",
                    "select.loadSelection", "channel.delete"]
    assert pc.sent[2][1] == {"channel": "mask", "layer": 7}       # the old layer's exact mask
    assert pc.sent[5][1] == {"layer": 7}                           # only the old layer is deleted
    assert pc.sent[8][1] == {"channel": "genfill-before-swap"}     # the user's selection comes back


def test_replace_layer_without_a_selection_ends_with_nothing_selected():
    pc = Recorder(has_selection=False)
    pc.replace_layer(7, "r.png", "Edit", masked=False)
    cmds = [c for c, _ in pc.sent]
    assert cmds[:3] == ["layer.select", "select.all", "paste"] and "layer.layerMask.revealSelection" not in cmds
    assert cmds[-1] == "select.deselect"


def test_replace_layer_keeps_the_old_layer_if_nothing_was_added():
    pc = Recorder()
    pc._put = lambda rel: None  # PhotoCraft didn't add a layer
    with pytest.raises(PhotoCraftError):
        pc.replace_layer(7, "r.png", "Edit", masked=True)
    assert "layer.delete" not in [c for c, _ in pc.sent]


# -- the window ------------------------------------------------------------------------------------
@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def test_the_window_stays_open_marks_the_placed_one_and_done_keeps_it(root, tmp_path):
    from genfill.picker import Picker
    from genfill.pipeline import Placed
    _, r = _results(tmp_path)
    picks, events = [], []
    w = Picker(root, r, theme.PALETTES["Pro"], theme.fonts(root), picks.append, lambda: events.append("discard"),
               on_close=lambda: events.append("close"))
    w._pick(1)
    w._pick(2)                       # ignored while the first one is being added
    assert picks == [1] and w.winfo_exists()
    r.placed = Placed(1, ("a", "b"), 101)
    w.set_waiting(False)
    assert "In PhotoCraft" in w.tags[1].cget("text")
    w._pick(1)                       # already in PhotoCraft: nothing to do
    w._pick(2)
    assert picks == [1, 2]
    r.placed = Placed(2, ("a", "b"), 201)
    w.set_waiting(False)
    w.close()
    assert events == ["close"]


def test_closing_before_any_pick_discards(root, tmp_path):
    from genfill.picker import Picker
    _, r = _results(tmp_path)
    events = []
    w = Picker(root, r, theme.PALETTES["Pro"], theme.fonts(root), lambda i: None, lambda: events.append("discard"),
               on_close=lambda: events.append("close"))
    w.close()
    assert events == ["discard"]


def test_a_tagged_result_keeps_its_own_tag_and_saves_no_set(tmp_path):
    pc, r = _results(tmp_path, selection=None, count=2)
    r.tag = '<x:xmpmeta genfill:shoot="0123456789abcdef" genfill:shot="3"/>'
    place(r, 0, _settings(tmp_path), pc=pc)
    assert pc.xmp[r.placed.layer] == r.tag
    swap(r, 1, _settings(tmp_path), pc=pc)
    assert pc.xmp[r.placed.layer] == r.tag and r.saved is None
    assert not (tmp_path / "results").exists()
