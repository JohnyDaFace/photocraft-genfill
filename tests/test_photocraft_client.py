import base64
import io
import json
import socket
import threading

import pytest
from PIL import Image

from genfill.photocraft_client import PhotoCraftClient, PhotoCraftError

TOKEN = "ab" * 32


class FakeApp:
    """Minimal JSON-lines control server: records requests, answers from a handler."""

    def __init__(self, handler):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self.handler = handler
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.sock.accept()
        f = conn.makefile("rw", encoding="utf-8", newline="\n")
        for line in f:
            req = json.loads(line)
            self.requests.append(req)
            f.write(json.dumps({"id": req["id"], **self.handler(req)}) + "\n")
            f.flush()


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / "control.token"
    p.write_text(TOKEN + "\n")
    return p


def test_auth_first_then_one_request_per_line(token_file):
    # PhotoCraft reports selectionBounds as [x, y, width, height]; DocInfo holds corners
    app = FakeApp(lambda r: {"ok": True, "result": {"width": 100, "height": 200, "selectionBounds": [10, 20, 30, 40]}})
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        info = pc.document()
    assert app.requests[0]["method"] == "auth" and app.requests[0]["params"] == {"token": TOKEN}
    assert app.requests[1]["params"]["command"] == "document.inspect"
    assert (info.width, info.height, info.selection) == (100, 200, (10, 20, 40, 60))


def test_no_selection_is_none(token_file):
    app = FakeApp(lambda r: {"ok": True, "result": {"width": 10, "height": 20, "selectionBounds": None}})
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        assert pc.document().selection is None


def test_error_reply_raises(token_file):
    app = FakeApp(lambda r: {"ok": True, "result": None} if r["method"] == "auth" else {"ok": False, "error": "no document"})
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        with pytest.raises(PhotoCraftError, match="no document"):
            pc.document()


def test_render_decodes_png(token_file):
    buf = io.BytesIO()
    Image.new("RGBA", (4, 3), (1, 2, 3, 0)).save(buf, "PNG")
    app = FakeApp(lambda r: {"ok": True, "result": {"mime": "image/png", "base64": base64.b64encode(buf.getvalue()).decode()}})
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        im = pc.render()
    assert im.size == (4, 3) and im.mode == "RGBA"


def test_place_layer_sequence(token_file):
    app = FakeApp(lambda r: {"ok": True, "result": {}})
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        pc.place_layer("fill_1.png", "Generative Fill")
    seq = [(r["method"], (r["params"] or {}).get("command")) for r in app.requests[1:]]
    assert seq == [("doc.place", None), ("engine.execute", "layer.rasterize.smartObject"),
                   ("engine.execute", "select.modify.expand"), ("engine.execute", "select.modify.feather"),
                   ("engine.execute", "layer.layerMask.revealSelection"), ("engine.execute", "layer.renameLayer")]
    assert app.requests[1]["params"]["path"] == "fill_1.png"
    assert app.requests[-1]["params"]["params"] == {"name": "Generative Fill"}


def _commands_app(ids):
    return FakeApp(lambda r: {"ok": True, "result": [{"id": i} for i in ids] if r["method"] == "engine.commands" else {}})


def test_compatibility_ok_with_current_names(token_file):
    app = _commands_app(["document.inspect", "select.all", "select.deselect", "image.canvasSize", "select.modify.expand", "select.modify.feather", "layer.rasterize.smartObject", "layer.layerMask.revealSelection", "layer.renameLayer"])
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        assert pc.compatibility() == []


def test_compatibility_uses_alternative_name(token_file):
    app = _commands_app(["document.inspect", "select.all", "select.deselect", "image.canvasSize", "select.modify.expand", "select.modify.feather", "layer.smartObjects.rasterize", "layer.layerMask.revealSelection", "layer.renameLayer"])
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        assert pc.compatibility() == []
        pc.place_layer("x.png", "n")
    assert "layer.smartObjects.rasterize" in [(r["params"] or {}).get("command") for r in app.requests]


def test_compatibility_reports_missing_command(token_file):
    app = _commands_app(["document.inspect", "select.all", "select.deselect", "image.canvasSize", "select.modify.expand", "select.modify.feather", "layer.rasterize.smartObject", "layer.renameLayer"])
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        problems = pc.compatibility()
    assert len(problems) == 1 and "layer.layerMask.revealSelection" in problems[0]


def test_refused_connection_mentions_setup(token_file):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    with pytest.raises(PhotoCraftError, match="Setup"):
        PhotoCraftClient(port, token_file).__enter__()


def test_missing_token_file_mentions_setup(tmp_path):
    with pytest.raises(PhotoCraftError, match="Setup"):
        PhotoCraftClient(1, tmp_path / "nope.token").__enter__()


class FakeDesktop:
    """Stateful stand-in for the desktop app: tracks the active document across open/close."""

    def __init__(self, exchange, switch_after_close=None):
        self.docs = [{"name": "post.jpg", "path": "E:/post.jpg"}]
        self.active = 0
        self.exchange = exchange
        self.switch_after_close = switch_after_close
        self.app = FakeApp(self.handle)

    def handle(self, r):
        m, p = r["method"], r["params"] or {}
        cmd = p.get("command")
        if m == "app.save":
            Image.new("RGBA", (8, 6), (0, 0, 0, 0)).save(self.exchange / p["path"])
            return {"ok": True, "result": {"path": p["path"]}}
        if m == "app.open":
            self.docs.append({"name": p["path"], "path": p["path"]})
            self.active = len(self.docs) - 1
            return {"ok": True, "result": {"name": p["path"]}}
        if cmd == "document.inspect":
            d = self.docs[self.active]
            return {"ok": True, "result": {**d, "width": 8, "height": 6, "selectionBounds": [0, 0, 8, 2]}}
        if cmd == "file.close":
            self.docs.pop(self.active)
            self.active = self.switch_after_close if self.switch_after_close is not None else 0
            if self.switch_after_close is not None:
                self.docs.append({"name": "other.psd", "path": "E:/other.psd"})
                self.active = len(self.docs) - 1
            return {"ok": True, "result": None}
        return {"ok": True, "result": {}}

    def commands(self):
        return [(r["params"] or {}).get("command") or r["method"] for r in self.app.requests
                if (r["params"] or {}).get("command") != "document.inspect"]


def test_desktop_detected_and_render_reads_exported_png(token_file, tmp_path):
    fake = FakeDesktop(tmp_path)
    with PhotoCraftClient(fake.app.port, token_file, exchange_dir=tmp_path) as pc:
        assert pc.mode == "desktop"
        im = pc.render()
    assert im.size == (8, 6) and im.getpixel((0, 0))[3] == 0
    assert list(tmp_path.glob("render_*.png")) == []  # temp export cleaned up


def test_desktop_place_layer_pastes_into_original_and_restores_selection(token_file, tmp_path):
    fake = FakeDesktop(tmp_path)
    with PhotoCraftClient(fake.app.port, token_file, exchange_dir=tmp_path) as pc:
        pc.place_layer("fill_1.png", "Generative Fill")
    assert fake.commands() == ["auth", "ui.inspect", "select.saveSelection", "app.open", "select.all", "edit.copy",
                               "file.close", "edit.pasteSpecial.pasteInPlace", "select.loadSelection",
                               "channel.delete", "select.modify.expand", "select.modify.feather",
                               "layer.layerMask.revealSelection", "layer.renameLayer"]
    assert [d["name"] for d in fake.docs] == ["post.jpg"]


def test_desktop_refuses_to_paste_into_another_document(token_file, tmp_path):
    fake = FakeDesktop(tmp_path, switch_after_close=1)
    with PhotoCraftClient(fake.app.port, token_file, exchange_dir=tmp_path) as pc:
        with pytest.raises(PhotoCraftError, match="another document"):
            pc.place_layer("fill_1.png", "Generative Fill")
    assert "edit.pasteSpecial.pasteInPlace" not in fake.commands()


def test_busy_clipboard_is_retried(token_file, tmp_path):
    fake = FakeDesktop(tmp_path)
    base, busy = fake.handle, {"left": 3}

    def flaky(r):
        if (r["params"] or {}).get("command") == "edit.pasteSpecial.pasteInPlace" and busy["left"]:
            busy["left"] -= 1
            return {"ok": False, "error": "The native clipboard is not accessible due to being held by another party."}
        return base(r)

    fake.app.handler = flaky
    with PhotoCraftClient(fake.app.port, token_file, exchange_dir=tmp_path) as pc:
        pc.place_layer("fill_1.png", "Generative Fill")
    assert fake.commands().count("edit.pasteSpecial.pasteInPlace") == 4


def test_unmasked_place_selects_all_then_deselects(token_file):
    app = FakeApp(lambda r: {"ok": True, "result": {}})
    with PhotoCraftClient(app.port, token_file, mode="headless") as pc:
        pc.place_layer("edit_1.png", "Edit", masked=False)
    seq = [(r["params"] or {}).get("command") or r["method"] for r in app.requests[1:]]
    assert seq == ["select.all", "doc.place", "layer.rasterize.smartObject", "select.deselect", "layer.renameLayer"]


def test_desktop_unmasked_place_has_no_mask(token_file, tmp_path):
    fake = FakeDesktop(tmp_path)
    with PhotoCraftClient(fake.app.port, token_file, exchange_dir=tmp_path) as pc:
        pc.place_layer("edit_1.png", "Edit", masked=False)
    cmds = fake.commands()
    assert cmds[2] == "select.all" and "layer.layerMask.revealSelection" not in cmds
    assert cmds[-2:] == ["select.deselect", "layer.renameLayer"]
