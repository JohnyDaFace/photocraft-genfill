import base64
import threading
from types import SimpleNamespace

from PIL import Image

from genfill import photoshop_actions, photoshop_bridge
from genfill.app import App
from genfill.pipeline import Results
from genfill.settings import Settings


def test_bar_edit_reads_photoshop_document_and_uses_bar_prompt(monkeypatch):
    captured = []
    pixels = base64.b64encode(bytes((1, 2, 3, 255, 4, 5, 6, 255))).decode()
    source = {"document": 24, "image_format": "raw", "image_width": 2, "image_height": 1,
              "image_components": 4, "image": pixels}
    editor = SimpleNamespace(request=lambda command, **kwargs: source)
    bar = SimpleNamespace(photoshop_server=SimpleNamespace(editor=editor),
                          _offer=lambda results, settings, status: captured.append(results))
    monkeypatch.setattr(photoshop_bridge, "generate_images", lambda request, *args:
                        captured.append(request) or [Image.new("RGB", (2, 1), "red")])
    App._photoshop_edit(bar, "make it red", Settings(variations=1), lambda _: None,
                        lambda _: None, threading.Event(), "reference.jpg")
    assert captured[0]["prompt"] == "make it red"
    assert captured[0]["reference"] == "reference.jpg"
    assert captured[1].editor == "photoshop"
    assert captured[1].photoshop_document == 24


def test_bar_places_chosen_result_back_in_same_photoshop_document():
    commands = []
    editor = SimpleNamespace(request=lambda command, **kwargs: commands.append(command) or
                             {"message": "Placed result as a new layer."})
    bar = SimpleNamespace(photoshop_server=SimpleNamespace(editor=editor))
    image = Image.new("RGB", (2, 1), "red")
    results = Results([image], [image], "edit", False, image.size, None)
    results.photoshop_document = 24
    message = App._place_photoshop(bar, results, 0, lambda _: None, threading.Event())
    assert message == "Placed result as a new layer."
    assert commands[0]["type"] == "place" and commands[0]["document"] == 24
    assert base64.b64decode(commands[0]["image"]["image"]).startswith(b"\x89PNG")


def test_photoshop_select_action_sends_selection_to_the_active_document(monkeypatch):
    commands = []
    source = {"document": 24, "image_format": "raw", "image_width": 2, "image_height": 1,
              "image_components": 4, "image": base64.b64encode(bytes((1, 2, 3, 255) * 2)).decode()}
    def request(command, **_kwargs):
        commands.append(command)
        return source if command["type"] == "capture" else {"message": "Selected"}
    editor = SimpleNamespace(request=request)
    bar = SimpleNamespace(photoshop_server=SimpleNamespace(editor=editor))
    def fake_run_job(job, canvas, settings, status, progress, cancel, on_selection, **kwargs):
        on_selection(Image.new("L", (2, 1), 255))
        return "Selected sky"
    monkeypatch.setattr(photoshop_actions, "run_job", fake_run_job)
    result = App._photoshop_action(bar, "select", "", Settings(), lambda _: None,
                                   lambda _: None, threading.Event(), what="sky")
    assert result == "Selected sky"
    assert [command["type"] for command in commands] == ["capture", "selection"]
    assert commands[1]["document"] == 24
