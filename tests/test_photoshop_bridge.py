"""The Photoshop panel's localhost contract, without Photoshop or a live model."""
import base64
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from genfill import photoshop_bridge
from genfill.photoshop_bridge import create_server, decode_source
from genfill.settings import Settings


def _request(port, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-GenFill-Token"] = token
    req = Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers)
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def test_decode_photoshop_raw_pixels():
    pixels = bytes((255, 0, 0, 255, 0, 0, 255, 128))
    source = decode_source({"image_format": "raw", "image_width": 2, "image_height": 1,
                            "image_components": 4, "image": base64.b64encode(pixels).decode()})
    assert source.size == (2, 1) and source.getpixel((1, 0)) == (0, 0, 255, 128)


def test_generate_uses_selected_companion_workflow(monkeypatch):
    captured = []
    def candidates(job, settings, *args):
        captured.append((job.base.size, job.custom, job.create, job.prompt, settings.custom_workflow))
        return [Image.new("RGB", (512, 768), "green")]
    monkeypatch.setattr(photoshop_bridge, "_candidates", candidates)
    settings = Settings(custom_workflow="saved workflow.json")
    result = photoshop_bridge.generate_images({"mode": "create", "prompt": "a forest", "count": 1}, settings)
    assert captured[0][1:] == (True, True, "a forest", "saved workflow.json")
    assert result[0].size == (512, 768)


def test_bar_edit_applies_shared_style_and_reference(monkeypatch):
    captured = []
    monkeypatch.setattr(photoshop_bridge, "_candidates", lambda job, settings, *args:
                        captured.append(job) or [Image.new("RGB", (2, 1), "green")])
    monkeypatch.setattr(photoshop_bridge, "load_reference", lambda path: Image.new("RGB", (2, 1), "blue"))
    settings = Settings(style="Cinematic")
    pixels = base64.b64encode(bytes((1, 2, 3, 255, 4, 5, 6, 255))).decode()
    photoshop_bridge.generate_images({"mode": "edit", "prompt": "red coat", "count": 1,
        "image_format": "raw", "image_width": 2, "image_height": 1, "image_components": 4,
        "image": pixels, "reference": "person.jpg"}, settings)
    assert captured[0].reference is not None
    assert "red coat" in captured[0].prompt
    assert captured[0].prompt != "red coat"


def test_bridge_requires_token_and_returns_all_images():
    calls = []

    def generate(request, settings):
        calls.append((request["mode"], request["count"], settings.photoshop_token))
        return [Image.new("RGB", (120, 80), "red"), Image.new("RGB", (120, 80), "blue")]

    settings = Settings(photoshop_token="test-secret")
    server = create_server(lambda: settings, generator=generate, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        assert _request(port, "/v1/status")["connected"] is True
        assert _request(port, "/v1/status")["paired"] is False
        assert _request(port, "/v1/status", token="test-secret")["paired"] is True
        try:
            _request(port, "/v1/generate", {"mode": "create", "prompt": "x", "count": 1})
            assert False, "request without token must fail"
        except HTTPError as error:
            assert error.code == 401
        result = _request(port, "/v1/generate", {"mode": "create", "prompt": "x", "count": 1}, "test-secret")
        assert calls == [("create", 1, "test-secret")]
        assert [(r["width"], r["height"]) for r in result["results"]] == [(120, 80), (120, 80)]
        assert base64.b64decode(result["results"][0]["image"]).startswith(b"\x89PNG")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_companion_can_request_photoshop_capture_and_receive_pixels():
    settings = Settings(photoshop_token="test-secret")
    server = create_server(lambda: settings, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        assert _request(port, "/v1/editor/next", token="test-secret")["command"] is None
        returned = []
        waiter = threading.Thread(target=lambda: returned.append(server.editor.request({"type": "capture"}, timeout=3)))
        waiter.start()
        command = _request(port, "/v1/editor/next?document=42&width=2&height=1", token="test-secret")["command"]
        assert command["type"] == "capture"
        pixels = base64.b64encode(bytes((1, 2, 3, 255, 4, 5, 6, 255))).decode()
        _request(port, "/v1/editor/reply", {"id": command["id"], "result": {"document": 42,
                 "image_format": "raw", "image_width": 2, "image_height": 1,
                 "image_components": 4, "image": pixels}}, token="test-secret")
        waiter.join(3)
        assert decode_source(returned[0]).getpixel((1, 0)) == (4, 5, 6, 255)
        assert server.editor.state()["document"] == 42
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
