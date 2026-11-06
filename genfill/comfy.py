"""ComfyUI HTTP client and the Flux 2 Klein workflows (API format)."""
from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable

from PIL import Image

POLL_SECONDS = 0.5
TIMEOUT_SECONDS = 600

DEFAULT_PROMPT = ("Extend this photo: replace the blurry empty areas with a seamless, natural continuation of the "
                  "scene. Same lighting, colours, perspective and photographic style. Sharp, realistic detail.")


class ComfyError(Exception):
    """A problem talking to ComfyUI or running the workflow, worded for the user."""


# What each model slot needs: the ComfyUI loader node + its file input, the models subfolder and
# where to get the default file (docs.comfy.org/tutorials/flux/flux-2-klein).
MODEL_SLOTS = {
    "unet": ("UNETLoader", "unet_name", "diffusion_models",
             "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8 (accept the agreement first)"),
    "clip": ("CLIPLoader", "clip_name", "text_encoders",
             "https://huggingface.co/Comfy-Org/flux2-klein-9B/blob/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors"),
    "vae": ("VAELoader", "vae_name", "vae",
            "https://huggingface.co/Comfy-Org/flux2-dev/blob/main/split_files/vae/flux2-vae.safetensors"),
}
REQUIRED_NODES = ["Flux2Scheduler", "ReferenceLatent", "SetLatentNoiseMask", "SplitSigmasDenoise", "CFGGuider",
                  "SamplerCustomAdvanced", "ConditioningZeroOut", "LoadImageMask"]


def _loaders(models: dict) -> dict:
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["clip"], "type": "flux2"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }


def _sampling(latent: list, positive: list, seed: int, sigmas: list, out_prefix: str) -> dict:
    return {
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": positive}},
        "guider": {"class_type": "CFGGuider",
                   "inputs": {"model": ["unet", 0], "positive": positive, "negative": ["neg", 0], "cfg": 1.0}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
                              "sigmas": sigmas, "latent_image": latent}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}},
    }


def build_fill_workflow(image_name: str, mask_name: str, prompt: str, seed: int, steps: int,
                        width: int, height: int, models: dict, ref_name: str | None = None) -> dict:
    """Generate only inside the mask. The reference shown to Klein is `ref_name` (the real picture
    only) when given: Klein is an edit model and copies its reference, so a reference that contains
    the blurry pre-fill gets the blur copied into the result."""
    wf = _loaders(models)
    wf.update({
        "image": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "mask": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
        "encode": {"class_type": "VAEEncode", "inputs": {"pixels": ["image", 0], "vae": ["vae", 0]}},
        "text": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt or DEFAULT_PROMPT, "clip": ["clip", 0]}},
        "ref": {"class_type": "ReferenceLatent",
                "inputs": {"conditioning": ["text", 0], "latent": ["ref_encode" if ref_name else "encode", 0]}},
        "masked": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["encode", 0], "mask": ["mask", 0]}},
        "sigmas": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
    })
    if ref_name:
        wf["ref_image"] = {"class_type": "LoadImage", "inputs": {"image": ref_name}}
        wf["ref_encode"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["ref_image", 0], "vae": ["vae", 0]}}
    wf.update(_sampling(["masked", 0], ["ref", 0], seed, ["sigmas", 0], "genfill"))
    return wf


UPSCALE_PROMPT = ("Same photo, same composition and colours, crisp high-resolution detail, sharp textures, "
                  "clean edges, no artifacts.")


def build_refine_workflow(image_name: str, prompt: str, seed: int, steps: int, denoise: float,
                          width: int, height: int, models: dict) -> dict:
    """Low-strength re-draw of one (already enlarged) tile: adds real detail, keeps content."""
    wf = _loaders(models)
    wf.update({
        "image": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "encode": {"class_type": "VAEEncode", "inputs": {"pixels": ["image", 0], "vae": ["vae", 0]}},
        "text": {"class_type": "CLIPTextEncode",
                 "inputs": {"text": f"{UPSCALE_PROMPT} {prompt}".strip(), "clip": ["clip", 0]}},
        "ref": {"class_type": "ReferenceLatent", "inputs": {"conditioning": ["text", 0], "latent": ["encode", 0]}},
        "sched": {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}},
        "split": {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": ["sched", 0], "denoise": denoise}},
    })
    wf.update(_sampling(["encode", 0], ["ref", 0], seed, ["split", 1], "genfill_up"))
    return wf


class ComfyClient:
    def __init__(self, url: str = "http://127.0.0.1:8188"):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    # -- transport -----------------------------------------------------------------------------
    def _open(self, req: urllib.request.Request | str, timeout: float = 30) -> bytes:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                return json.dumps(json.loads(body)).encode()  # ComfyUI puts details in a JSON body
            except ValueError:
                raise ComfyError(f"ComfyUI answered {e.code}: {body[:300]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
            raise ComfyError(f"ComfyUI is not running at {self.url} (start ComfyUI and try again).") from e

    def _json(self, path: str, payload: Any = None) -> Any:
        if payload is None:
            return json.loads(self._open(self.url + path))
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.loads(self._open(req) or b"{}")

    # -- API -----------------------------------------------------------------------------------
    def check(self) -> dict:
        return self._json("/system_stats")

    def available_models(self) -> dict[str, list[str]]:
        """Model files ComfyUI offers for each slot (from its loader nodes' dropdowns)."""
        out = {}
        for slot, (node, field, _folder, _url) in MODEL_SLOTS.items():
            info = self._json(f"/object_info/{node}").get(node, {})
            spec = info.get("input", {}).get("required", {}).get(field, [[]])
            choices = spec[0] if spec and isinstance(spec[0], list) else (spec[1] or {}).get("options", []) if len(spec) > 1 else []
            out[slot] = list(choices)
        return out

    def check_setup(self, models: dict) -> list[str]:
        """Plain-language problems that would stop Generate/Upscale (empty = ready)."""
        problems = []
        missing_nodes = [n for n in REQUIRED_NODES if not self._json(f"/object_info/{n}").get(n)]
        if missing_nodes:
            problems.append("Your ComfyUI is too old for Flux 2 (missing: " + ", ".join(missing_nodes) +
                            "). Update ComfyUI and restart it.")
        have = self.available_models()
        for slot, (_node, _field, folder, url) in MODEL_SLOTS.items():
            if models.get(slot) not in have.get(slot, []):
                problems.append(f"ComfyUI doesn't have '{models.get(slot)}'. Put it in ComfyUI/models/{folder}/ "
                                f"(download: {url}) or pick another file in Settings.")
        return problems

    def upload(self, img: Image.Image, name: str) -> str:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        boundary = uuid.uuid4().hex
        parts = [
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n".encode(),
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\ngenfill\r\n".encode(),
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
             "Content-Type: image/png\r\n\r\n").encode() + buf.getvalue() + b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        req = urllib.request.Request(self.url + "/upload/image", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        res = json.loads(self._open(req, timeout=120))
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def interrupt(self) -> None:
        try:
            self._json("/interrupt", {})
        except ComfyError:
            pass

    def run(self, workflow: dict, on_progress: Callable[[float | None], None], cancel) -> Image.Image:
        """Queue the workflow, wait for it, and return its first output image."""
        res = self._json("/prompt", {"prompt": workflow, "client_id": self.client_id})
        if "prompt_id" not in res or res.get("node_errors"):
            details = [err.get("details") or err.get("message", "")
                       for node in (res.get("node_errors") or {}).values() for err in node.get("errors", [])]
            msg = (res.get("error") or {}).get("message", "ComfyUI refused the workflow")
            raise ComfyError(f"{msg}: {'; '.join(d for d in details if d)}" if details else msg)
        pid = res["prompt_id"]
        start = time.monotonic()
        while True:
            if cancel.is_set():
                self.interrupt()
                self._json("/queue", {"delete": [pid]})
                raise ComfyError("Cancelled.")
            entry = self._json(f"/history/{pid}").get(pid)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError("ComfyUI failed: " + _error_text(status))
                for out in entry.get("outputs", {}).values():
                    for im in out.get("images", []):
                        return self._fetch(im)
                if status.get("completed"):
                    raise ComfyError("ComfyUI finished without an image.")
            if time.monotonic() - start > TIMEOUT_SECONDS:
                self.interrupt()
                raise ComfyError("ComfyUI took longer than 10 minutes; cancelled.")
            on_progress(None)
            time.sleep(POLL_SECONDS)

    def _fetch(self, im: dict) -> Image.Image:
        q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                    "type": im.get("type", "output")})
        return Image.open(io.BytesIO(self._open(f"{self.url}/view?{q}", timeout=120))).convert("RGB")


def _error_text(status: dict) -> str:
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return str(data.get("exception_message", "unknown error")).strip()
    return "unknown error"
