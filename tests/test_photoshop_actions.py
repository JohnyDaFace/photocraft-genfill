"""Photoshop image jobs should use the same image pipeline without a PhotoCraft socket."""
from pathlib import Path

from PIL import Image

from genfill.photoshop_actions import CapturedDocument, decode_selection, run_job
from genfill.photocraft_client import DocInfo
from genfill.settings import Settings


def test_captured_document_exposes_pixels_and_selection(tmp_path: Path):
    image = Image.new("RGBA", (8, 6), "red")
    canvas = CapturedDocument(image, (1, 2, 5, 5), tmp_path)
    with canvas:
        assert canvas.compatibility() == []
        assert canvas.document() == DocInfo(8, 6, (1, 2, 5, 5))
        assert canvas.render().getpixel((0, 0)) == (255, 0, 0, 255)


def test_select_mask_keeps_mask_for_photoshop(tmp_path: Path):
    mask = Image.new("RGBA", (8, 6), (255, 255, 255, 0))
    mask.putpixel((3, 2), (255, 255, 255, 255))
    mask.save(tmp_path / "mask.png")
    canvas = CapturedDocument(Image.new("RGBA", (8, 6)), None, tmp_path)
    canvas.select_mask("mask.png")
    assert canvas.document().selection == (3, 2, 4, 3)
    assert canvas.mask.getpixel((3, 2)) == 255


def test_decode_selection_places_pixels_at_their_origin():
    import base64
    captured = {"selection_bounds": [2, 1, 4, 3], "selection_mask": base64.b64encode(bytes([0, 255, 255, 0])).decode(),
                "selection_width": 2, "selection_height": 2, "selection_left": 2, "selection_top": 1}
    mask = decode_selection(captured, (6, 4))
    assert mask.getpixel((3, 1)) == 255
    assert mask.getpixel((2, 1)) == 0
    assert mask.size == (6, 4)


def test_photoshop_select_sends_the_generated_mask(monkeypatch, tmp_path):
    from genfill import photoshop_actions
    selected = []
    def fake_select(what, settings, status, progress, cancel, pc, **kwargs):
        pc.mask = Image.new("L", (8, 6), 255)
        return "Selected sky"
    monkeypatch.setattr(photoshop_actions.pipeline, "select_things", fake_select)
    canvas = CapturedDocument(Image.new("RGBA", (8, 6)), None, tmp_path)
    result = run_job("select", canvas, Settings(), lambda _: None, lambda _: None, None,
                     on_selection=selected.append, what="sky")
    assert result == "Selected sky"
    assert selected[0].size == (8, 6)
