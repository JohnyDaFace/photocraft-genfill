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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from PIL import Image

from . import __version__
from .comfy import ComfyClient, ComfyError
from .pipeline import Job, UserFacingError, _candidates, create_size
from .settings import Settings

BRIDGE_PORT = 47862
MAX_BODY = 110 * 1024 * 1024
MAX_SIDE = 8192
MAX_PIXELS = 20_000_000


def decode_source(request: dict) -> Image.Image:
    """Read an 8-bit Photoshop RGB/RGBA buffer or a PNG sent by another client."""
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
        if image.format != "PNG" or max(image.size) > MAX_SIDE or image.width * image.height > MAX_PIXELS:
            raise ValueError("Send a PNG no larger than 8192 pixels per side and 20 megapixels.")
        return image.convert("RGBA")
    except OSError as exc:
        raise ValueError("The image is not a valid PNG.") from exc


def generate_images(request: dict, settings: Settings) -> list[Image.Image]:
    """Use the same selected model or custom workflow as the companion bar."""
    mode = request["mode"]
    count = request["count"]
    prompt = request["prompt"].strip()
    if mode == "edit":
        base = decode_source(request)
    else:
        default_width, default_height = create_size(settings.create_choice, settings.megapixels)
        width = request.get("width", default_width)
        height = request.get("height", default_height)
        if not all(isinstance(n, int) for n in (width, height)) or not (16 <= width <= MAX_SIDE and
                                                                         16 <= height <= MAX_SIDE and
                                                                         width * height <= MAX_PIXELS):
            raise ValueError("Create dimensions must fit within 8192 pixels per side and 20 megapixels.")
        base = Image.new("RGBA", (width, height), "white")
    job = Job(base, None, None, prompt, create=mode == "create", custom=True)
    return _candidates(job, settings, lambda _: None, lambda _: None, threading.Event(),
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
            if self.path == "/v1/status":
                secret = settings_provider().photoshop_token
                self._reply(200, {"version": __version__, "connected": True,
                                  "paired": bool(secret and self.headers.get("X-GenFill-Token") == secret)})
            else:
                self._reply(404, {"error": "Unknown endpoint."})

        def do_POST(self):
            if self.path != "/v1/generate":
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

    return Server(("127.0.0.1", port), Handler)
