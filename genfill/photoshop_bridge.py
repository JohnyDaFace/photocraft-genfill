"""Local HTTP bridge between the Photoshop UXP panel and GenFill's ComfyUI pipeline.

The panel owns Photoshop document operations. This server only accepts pixels from
localhost, runs the selected image workflow, and returns the generated PNGs.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from . import __version__, styles
from .comfy import ComfyClient, ComfyError
from .pipeline import Job, UserFacingError, _candidates, create_size, load_reference
from .settings import Settings

BRIDGE_PORT = 47862
MAX_BODY = 110 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 20_000_000


class EditorSession:
    """Commands from the bar are collected by the paired UXP panel's short poll."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = deque()
        self._waiting = {}
        self._sequence = 0
        self._seen = 0.0
        self._document = None
        self._width = self._height = 0
        self._selection = False

    def state(self) -> dict:
        with self._lock:
            return {"connected": time.monotonic() - self._seen < 3 or bool(self._waiting),
                    "document": self._document, "width": self._width, "height": self._height,
                    "selection": self._selection,
                    "stage": next((wait[2] for wait in self._waiting.values() if wait[2]), "")}

    def next(self, document=None, width=0, height=0, selection=False) -> dict | None:
        with self._lock:
            self._seen = time.monotonic()
            self._document, self._width, self._height = document, width, height
            self._selection = bool(selection)
            return self._pending.popleft() if self._pending else None

    def request(self, command: dict, timeout: float = 30, cancel=None) -> dict:
        event = threading.Event()
        with self._lock:
            if time.monotonic() - self._seen >= 3:
                raise UserFacingError("Open the GenFill panel in Photoshop and pair it with the companion.")
            self._sequence += 1
            command = {**command, "id": self._sequence}
            self._waiting[self._sequence] = [event, None, "waiting for Photoshop"]
            self._pending.append(command)
        deadline = time.monotonic() + timeout
        while not event.wait(0.1):
            if cancel is not None and cancel.is_set():
                break
            if time.monotonic() >= deadline:
                break
        with self._lock:
            _, reply, stage = self._waiting.pop(command["id"], (None, None, ""))
            if reply is None:
                self._pending = deque(c for c in self._pending if c["id"] != command["id"])
        if reply is None:
            raise UserFacingError(f"Photoshop did not respond ({stage or 'no reply'}). "
                                  "Check its GenFill panel for an error.")
        if reply.get("error"):
            raise UserFacingError(str(reply["error"]))
        return reply.get("result") or {}

    def reply(self, message: dict) -> None:
        with self._lock:
            waiting = self._waiting.get(message.get("id"))
            if waiting is not None:
                waiting[1] = message
                waiting[0].set()

    def progress(self, message: dict) -> None:
        with self._lock:
            waiting = self._waiting.get(message.get("id"))
            if waiting is not None and isinstance(message.get("stage"), str):
                waiting[2] = message["stage"][:80]


def decode_source(request: dict) -> Image.Image:
    """Read an 8-bit Photoshop buffer or a PNG/JPEG sent by the panel."""
    encoded = request.get("image")
    if not isinstance(encoded, str):
        raise ValueError("Send an image from an open Photoshop document.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The image data is not valid base64.") from exc
    if request.get("image_format") == "raw":
        width, height, components = (request.get(k) for k in ("image_width", "image_height", "image_components"))
        if not all(isinstance(n, int) for n in (width, height, components)) or not (1 <= width <= MAX_SIDE and
                                                                                  1 <= height <= MAX_SIDE and
                                                                                  width * height <= MAX_PIXELS):
            raise ValueError("The image dimensions are invalid or too large.")
        if components not in (3, 4) or len(data) != width * height * components:
            raise ValueError("The image pixel buffer has the wrong length.")
        return Image.frombytes("RGB" if components == 3 else "RGBA", (width, height), data).convert("RGBA")
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
        image = Image.open(io.BytesIO(data))
        expected = "JPEG" if request.get("image_format") == "jpeg" else "PNG"
        if image.format != expected or max(image.size) > MAX_SIDE or image.width * image.height > MAX_PIXELS:
            raise ValueError("Send a supported image no larger than 8192 pixels per side and 20 megapixels.")
        return image.convert("RGBA")
    except OSError as exc:
        raise ValueError("The image is not a valid PNG or JPEG.") from exc


def generate_images(request: dict, settings: Settings, status=None, progress=None, cancel=None) -> list[Image.Image]:
    """Use the same selected model or custom workflow as the companion bar."""
    mode = request["mode"]
    count = request["count"]
    prompt = request["prompt"].strip()
    if mode == "edit":
        base = decode_source(request)
        if not prompt and not (settings.style or settings.angle):
            raise UserFacingError("Describe a change or choose a style on the bar.")
        prompt = styles.styled(prompt, settings.style, restyle=True, edit=True, angle=settings.angle,
                               model_family=settings.model_family) if settings.style or settings.angle else prompt
        reference = load_reference(request["reference"]) if request.get("reference") else None
    else:
        reference = None
        default_width, default_height = create_size(settings.create_choice, settings.megapixels)
        width = request.get("width", default_width)
        height = request.get("height", default_height)
        if not all(isinstance(n, int) for n in (width, height)) or not (16 <= width <= MAX_SIDE and
                                                                         16 <= height <= MAX_SIDE and
                                                                         width * height <= MAX_PIXELS):
            raise ValueError("Create dimensions must fit within 8192 pixels per side and 20 megapixels.")
        base = Image.new("RGBA", (width, height), "white")
    job = Job(base, None, None, prompt, reference, create=mode == "create", custom=True)
    return _candidates(job, settings, status or (lambda _: None), progress or (lambda _: None), cancel or threading.Event(),
                       ComfyClient(settings.comfy_url), count)


def create_server(settings_provider: Callable[[], Settings], generator=generate_images,
                  port: int = BRIDGE_PORT) -> ThreadingHTTPServer:
    """Bind only loopback. A per-user token is required for every image request."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # image requests and tokens must never enter console logs

        def _reply(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/v1/status":
                secret = settings_provider().photoshop_token
                self._reply(200, {"version": __version__, "connected": True,
                                  "paired": bool(secret and self.headers.get("X-GenFill-Token") == secret)})
            elif url.path == "/v1/editor/next":
                secret = settings_provider().photoshop_token
                if not secret or self.headers.get("X-GenFill-Token") != secret:
                    self._reply(401, {"error": "Pair Photoshop with the companion."})
                    return
                query = parse_qs(url.query)
                def number(key):
                    try:
                        return int(query.get(key, ["0"])[0])
                    except ValueError:
                        return 0
                document = number("document") or None
                self._reply(200, {"command": self.server.editor.next(document, number("width"), number("height"),
                                                                      bool(number("selection")))})
            else:
                self._reply(404, {"error": "Unknown endpoint."})

        def do_POST(self):
            if self.path not in ("/v1/generate", "/v1/editor/reply", "/v1/editor/progress"):
                self._reply(404, {"error": "Unknown endpoint."})
                return
            settings = settings_provider()
            if not settings.photoshop_token or self.headers.get("X-GenFill-Token") != settings.photoshop_token:
                self._reply(401, {"error": "Pair Photoshop in the companion's Settings and enter its token here."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("The request is empty or too large.")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("The request must be an object.")
                if self.path == "/v1/editor/reply":
                    if type(request.get("id")) is not int:
                        raise ValueError("Missing command ID.")
                    self.server.editor.reply(request)
                    self._reply(200, {"ok": True})
                    return
                if self.path == "/v1/editor/progress":
                    if type(request.get("id")) is not int:
                        raise ValueError("Missing command ID.")
                    self.server.editor.progress(request)
                    self._reply(200, {"ok": True})
                    return
                if request.get("mode") not in ("edit", "create"):
                    raise ValueError("Choose Edit or Create.")
                if not isinstance(request.get("prompt"), str) or not request["prompt"].strip():
                    raise ValueError("Describe the image or change to generate.")
                if type(request.get("count")) is not int or not 1 <= request["count"] <= 4:
                    raise ValueError("Results count must be between 1 and 4.")
                images = generator(request, settings)
                results = []
                for image in images:
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                    results.append({"image": base64.b64encode(buffer.getvalue()).decode("ascii"),
                                    "width": image.width, "height": image.height})
                self._reply(200, {"results": results})
            except (ValueError, UserFacingError, ComfyError, json.JSONDecodeError) as exc:
                self._reply(400, {"error": str(exc)})

    class Server(ThreadingHTTPServer):
        daemon_threads = True

    server = Server(("127.0.0.1", port), Handler)
    server.editor = EditorSession()
    return server
