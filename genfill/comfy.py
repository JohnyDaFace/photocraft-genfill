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

from .families import family

POLL_SECONDS = 0.5
TIMEOUT_SECONDS = 600

DEFAULT_PROMPT = ("Extend this photo: replace the blurry empty areas with a seamless, natural continuation of the "
                  "scene. Same lighting, colours, perspective and photographic style. Sharp, realistic detail.")


class ComfyError(Exception):
    """A problem talking to ComfyUI or running the workflow, worded for the user."""


MODEL_SLOTS = {  # slot: (loader node, its file field, ComfyUI/models folder); the files: families.py
    "unet": ("UNETLoader", "unet_name", "diffusion_models"),
    "clip": ("CLIPLoader", "clip_name", "text_encoders"),
    "vae": ("VAELoader", "vae_name", "vae"),
}
REQUIRED_NODES = ["Flux2Scheduler", "ReferenceLatent", "SetLatentNoiseMask", "SplitSigmasDenoise", "CFGGuider",
                  "SamplerCustomAdvanced", "ConditioningZeroOut", "LoadImageMask"]


def _loaders(models: dict) -> dict:
    """The model, text encoder and VAE. `models["loras"]` (optional): LoRAs chained after the model as
    LoraLoaderModelOnly nodes, each a file name or {"name": …, "strength": …}; the last one is "model"."""
    fam = family(models.get("family"))
    wf = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": models["unet"], "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": models["clip"], "type": fam.clip_type}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": models["vae"]}},
    }
    model = ["unet", 0]
    if fam.encoder == "qwen21":  # keeps the reference images' part of the sequence between steps
        wf["cache"] = {"class_type": "QwenImage21Cache", "inputs": {"model": model, "device": "auto",
                                                                    "dtype": "default"}}
        model = ["cache", 0]
    for i, lora in enumerate(models.get("loras") or []):
        name, strength = (lora, 1.0) if isinstance(lora, str) else (lora.get("name"), lora.get("strength", 1.0))
        if not name:
            continue
        wf[f"lora{i}"] = {"class_type": "LoraLoaderModelOnly",
                          "inputs": {"model": model, "lora_name": name, "strength_model": float(strength)}}
        model = [f"lora{i}", 0]
    wf["_model"] = model  # removed by _finish: where sampling takes the model from
    return wf


def _model_ref(wf: dict) -> list:
    """The model to sample with: after the cache and the LoRAs, or the plain model."""
    return list(wf.get("_model", ["unet", 0]))


def _finish(wf: dict) -> dict:
    wf.pop("_model", None)
    return wf


def _schedule(wf: dict, models: dict, steps: int, width: int, height: int) -> dict:
    """The noise schedule: FLUX.2's size-aware one, a distilled model's own fixed one, or the model's own."""
    fam = family(models.get("family"))
    if fam.scheduler == "flux2":
        return {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": width, "height": height}}
    if fam.sigmas and steps == fam.sigmas.count(","):
        return {"class_type": "ManualSigmas", "inputs": {"sigmas": fam.sigmas}}
    return {"class_type": "BasicScheduler",
            "inputs": {"model": _model_ref(wf), "scheduler": "simple", "steps": steps, "denoise": 1.0}}


def _condition(wf: dict, models: dict, prompt: str, refs: list[dict]) -> list:
    """The prompt and the pictures it refers to ("image 1", "image 2"… in order), as the family takes them.
    Each ref: {"pixels": node output} and, when already encoded, {"latent": node output}."""
    fam = family(models.get("family"))
    if fam.encoder == "qwen21":
        inputs = {"clip": ["clip", 0], "prompt": prompt, "negative_prompt": "", "resolution": 0}
        if refs:
            inputs["vae"] = ["vae", 0]
        for i, r in enumerate(refs, 1):
            inputs[f"images.image_{i}"] = r["pixels"]
        wf["text"] = {"class_type": "TextEncodeQwenImage21", "inputs": inputs}
        return ["text", 0]
    wf["text"] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}}
    conditioning = ["text", 0]
    if fam.encoder != "flux":  # a text-to-image model: it can't be shown pictures
        return conditioning
    for i, r in enumerate(refs, 1):
        latent = r.get("latent")
        if latent is None:
            wf[f"ref{i}_encode"] = {"class_type": "VAEEncode", "inputs": {"pixels": r["pixels"], "vae": ["vae", 0]}}
            latent = [f"ref{i}_encode", 0]
        wf[f"ref{i}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": conditioning, "latent": latent}}
        conditioning = [f"ref{i}", 0]
    return conditioning


def _load(wf: dict, key: str, name: str) -> list:
    wf[key] = {"class_type": "LoadImage", "inputs": {"image": name}}
    return [key, 0]


def _extras(wf: dict, names) -> list[dict]:
    return [{"pixels": _load(wf, f"extra{i}_image", n)} for i, n in enumerate(names, 1)]


def _sampling(latent: list, positive: list, seed: int, sigmas: list, out_prefix: str,
              model: list = ("unet", 0)) -> dict:
    return {
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": positive}},
        "guider": {"class_type": "CFGGuider",
                   "inputs": {"model": list(model), "positive": positive, "negative": ["neg", 0], "cfg": 1.0}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sample": {"class_type": "SamplerCustomAdvanced",
                   "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
                              "sigmas": sigmas, "latent_image": latent}},
        "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}},
    }


def _sigmas(wf: dict, denoise: float) -> list:
    """Full schedule, or only its low-noise end for a partial redraw (`denoise` < 1: keeps the
    composition of the input and varies the details: Generate Similar, Harmonize)."""
    if denoise >= 1.0:
        return ["sigmas", 0]
    wf["split"] = {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": ["sigmas", 0], "denoise": denoise}}
    return ["split", 1]


def build_fill_workflow(image_name: str, mask_name: str, prompt: str, seed: int, steps: int,
                        width: int, height: int, models: dict, ref_name: str | None = None,
                        extra_refs: list[str] = (), denoise: float = 1.0) -> dict:
    """Generate only inside the mask. The reference shown to the model is `ref_name` (the real picture
    only) when given: an edit model copies its reference, so a reference that contains the blurry
    pre-fill gets the blur copied into the result."""
    wf = _loaders(models)
    image = _load(wf, "image", image_name)
    wf.update({
        "mask": {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}},
        "encode": {"class_type": "VAEEncode", "inputs": {"pixels": image, "vae": ["vae", 0]}},
        "masked": {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["encode", 0], "mask": ["mask", 0]}},
        "sigmas": _schedule(wf, models, steps, width, height),
    })
    first = {"pixels": _load(wf, "ref_image", ref_name)} if ref_name else {"pixels": image, "latent": ["encode", 0]}
    conditioning = _condition(wf, models, prompt or DEFAULT_PROMPT, [first] + _extras(wf, extra_refs))
    wf.update(_sampling(["masked", 0], conditioning, seed, _sigmas(wf, denoise), "genfill", _model_ref(wf)))
    return _finish(wf)


def build_edit_workflow(image_name: str, prompt: str, seed: int, steps: int,
                        width: int, height: int, models: dict, extra_refs: list[str] = (),
                        denoise: float = 1.0, self_reference: bool = True) -> dict:
    """Instruction edit of the whole picture ("make it night", "change the jacket to red"): an edit model
    redraws the image from full noise while following its reference, so what the prompt doesn't
    mention stays the same. `self_reference=False` (with denoise < 1): a plain partial redraw that does
    not show the picture as a reference, so it doesn't copy its blur (Fix faces); `extra_refs` are
    then image 1, 2…. A text-to-image model can't follow a reference: it redraws partly instead."""
    fam = family(models.get("family"))
    wf = _loaders(models)
    image = _load(wf, "image", image_name)
    wf.update({
        "encode": {"class_type": "VAEEncode", "inputs": {"pixels": image, "vae": ["vae", 0]}},
        "sigmas": _schedule(wf, models, steps, width, height),
    })
    refs = ([{"pixels": image, "latent": ["encode", 0]}] if self_reference else []) + _extras(wf, extra_refs)
    if fam.encoder == "text":
        denoise = min(denoise, fam.redraw)
    wf.update(_sampling(["encode", 0], _condition(wf, models, prompt, refs), seed, _sigmas(wf, denoise),
                        "genfill_edit", _model_ref(wf)))
    return _finish(wf)


def build_create_workflow(prompt: str, seed: int, steps: int, width: int, height: int, models: dict,
                          extra_refs: list[str] = ()) -> dict:
    """A new picture from the prompt alone (text to image); `extra_refs` are pictures it may take an
    object or a style from (the first one is "image 1" here)."""
    fam = family(models.get("family"))
    wf = _loaders(models)
    wf.update({
        "latent": {"class_type": "EmptyFlux2LatentImage" if fam.encoder == "flux" else "EmptyLatentImage",
                   "inputs": {"width": width, "height": height, "batch_size": 1}},
        "sigmas": _schedule(wf, models, steps, width, height),
    })
    conditioning = _condition(wf, models, prompt, _extras(wf, extra_refs))
    wf.update(_sampling(["latent", 0], conditioning, seed, ["sigmas", 0], "genfill_create", _model_ref(wf)))
    return _finish(wf)


UPSCALE_PROMPT = ("Same photo, same composition and colours, crisp high-resolution detail, sharp textures, "
                  "clean edges, no artifacts.")


UPSCALE_MODEL = "4x-UltraSharp.pth"
UPSCALE_MODEL_URL = "https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth"


def build_refine_workflow(image_name: str, prompt: str, seed: int, steps: int, denoise: float,
                          width: int, height: int, models: dict, upscale_model: str | None = None) -> dict:
    """Low-strength re-draw of one tile: adds natural texture, keeps content. With `upscale_model` the
    image is the small original tile, enlarged here by that model (real, crisp edges) to width×height
    first; without it, the image is already enlarged (soft, and an edit model copies what it is shown)."""
    wf = _loaders(models)
    pixels = _load(wf, "image", image_name)
    if upscale_model:
        wf["up_model"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}}
        wf["up"] = {"class_type": "ImageUpscaleWithModel",
                    "inputs": {"upscale_model": ["up_model", 0], "image": pixels}}
        wf["up_size"] = {"class_type": "ImageScale", "inputs": {"image": ["up", 0], "upscale_method": "lanczos",
                                                                "width": width, "height": height,
                                                                "crop": "disabled"}}
        pixels = ["up_size", 0]
    wf.update({
        "encode": {"class_type": "VAEEncode", "inputs": {"pixels": pixels, "vae": ["vae", 0]}},
        "sched": _schedule(wf, models, steps, width, height),
        "split": {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": ["sched", 0], "denoise": denoise}},
    })
    conditioning = _condition(wf, models, f"{UPSCALE_PROMPT} {prompt}".strip(),
                              [{"pixels": pixels, "latent": ["encode", 0]}])
    wf.update(_sampling(["encode", 0], conditioning, seed, ["split", 1], "genfill_up", _model_ref(wf)))
    return _finish(wf)


SEEDVR_MODEL = "seedvr2_7b_int8_convrot.safetensors"
SEEDVR_VAE = "seedvr2_ema_vae_fp16.safetensors"
SEEDVR_URL = "https://huggingface.co/Comfy-Org/SeedVR2/resolve/main/"
SEEDVR_MODEL_URL = SEEDVR_URL + "diffusion_models/" + SEEDVR_MODEL
SEEDVR_VAE_URL = SEEDVR_URL + "vae/" + SEEDVR_VAE


def build_seedvr_workflow(image_name: str, seed: int, model: str = SEEDVR_MODEL, vae: str = SEEDVR_VAE,
                          colour: str = "lab") -> dict:
    """SeedVR2 (one-step restoration model): the image, already resized to the target size, comes back
    the same size with real detail; colours are matched back to the input. Same graph as ComfyUI's
    own Image Upscale (SeedVR2) template. It takes no prompt."""
    tiled = {"tile_size": 512, "overlap": 128, "temporal_size": 4096, "temporal_overlap": 8}
    return {
        "image": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "svr_vae": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "svr_unet": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "pre": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["image", 0]}},
        "encode": {"class_type": "VAEEncodeTiled", "inputs": {"pixels": ["pre", 0], "vae": ["svr_vae", 0], **tiled}},
        "cond": {"class_type": "SeedVR2Conditioning",
                 "inputs": {"model": ["svr_unet", 0], "vae_conditioning": ["encode", 0]}},
        "sample": {"class_type": "KSampler",
                   "inputs": {"model": ["svr_unet", 0], "positive": ["cond", 0], "negative": ["cond", 1],
                              "latent_image": ["encode", 0], "seed": seed, "steps": 1, "cfg": 1.0,
                              "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "decode": {"class_type": "VAEDecodeTiled", "inputs": {"samples": ["sample", 0], "vae": ["svr_vae", 0], **tiled}},
        "post": {"class_type": "SeedVR2PostProcessing",
                 "inputs": {"images": ["decode", 0], "original_resized_images": ["image", 0],
                            "color_correction_method": colour}},
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["post", 0]}},
    }


SEGMENT_MODEL = "sam3.1_multiplex_fp16.safetensors"
SEGMENT_MODEL_URL = ("https://huggingface.co/Comfy-Org/sam3.1/resolve/main/checkpoints/"
                     "sam3.1_multiplex_fp16.safetensors")


def build_segment_workflow(image_name: str, what: str, model: str = SEGMENT_MODEL,
                           threshold: float = 0.5, individual: bool = False) -> dict:
    """SAM 3: a mask of everything in the picture that matches `what` ("sky", "person", "the red car").
    Same graph as ComfyUI's own Image Segmentation (SAM3) template; the mask comes back as a picture
    (`individual`: one picture per thing found, for ComfyClient.run_all)."""
    return {
        "seg_model": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": model}},
        "image": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "seg_text": {"class_type": "CLIPTextEncode", "inputs": {"text": what, "clip": ["seg_model", 1]}},
        "detect": {"class_type": "SAM3_Detect",
                   "inputs": {"model": ["seg_model", 0], "image": ["image", 0], "conditioning": ["seg_text", 0],
                              "threshold": threshold, "refine_iterations": 2, "individual_masks": individual}},
        "mask_image": {"class_type": "MaskToImage", "inputs": {"mask": ["detect", 0]}},
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["mask_image", 0]}},
    }


DEPTH_MODEL = "depth_anything_v2_vitl.pth"
DEPTH_RESOLUTION = 1024


def build_depth_workflow(image_name: str, resolution: int = DEPTH_RESOLUTION, model: str = DEPTH_MODEL) -> dict:
    """Depth Anything V2: a grey picture of how far each part is (brighter = nearer)."""
    return {
        "image": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "depth": {"class_type": "DepthAnythingV2Preprocessor",
                  "inputs": {"image": ["image", 0], "ckpt_name": model, "resolution": resolution}},
        "out": {"class_type": "PreviewImage", "inputs": {"images": ["depth", 0]}},
    }


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
        for slot, (node, field, _folder) in MODEL_SLOTS.items():
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
        fam = family(models.get("family"))
        for slot, (_node, _field, folder) in MODEL_SLOTS.items():
            if models.get(slot) not in have.get(slot, []):
                problems.append(f"ComfyUI doesn't have '{models.get(slot)}'. Put it in ComfyUI/models/{folder}/ "
                                f"(download: {fam.urls[slot]}) or pick another file in Settings.")
        return problems

    def check_segment(self, model: str = SEGMENT_MODEL) -> list[str]:
        """Plain-language problems that would stop the smart selections (empty = ready)."""
        if not self._json("/object_info/SAM3_Detect").get("SAM3_Detect"):
            return ["Your ComfyUI is too old for SAM 3 selections. Update ComfyUI and restart it."]
        info = self._json("/object_info/CheckpointLoaderSimple").get("CheckpointLoaderSimple", {})
        spec = info.get("input", {}).get("required", {}).get("ckpt_name", [[]])
        have = spec[0] if spec and isinstance(spec[0], list) else []
        if model not in have:
            return [f"ComfyUI doesn't have the SAM 3 model '{model}'. Put it in ComfyUI/models/checkpoints/ "
                    f"(download: {SEGMENT_MODEL_URL})."]
        return []

    def _choices(self, node: str, field: str) -> list[str]:
        """The files a loader node offers in one of its dropdowns."""
        info = self._json(f"/object_info/{node}").get(node, {})
        spec = info.get("input", {}).get("required", {}).get(field, [[]])
        if spec and isinstance(spec[0], list):
            return spec[0]
        return (spec[1] or {}).get("options", []) if len(spec) > 1 else []

    def has_depth(self) -> bool:
        try:  # the node downloads its model the first time it runs
            return "DepthAnythingV2Preprocessor" in self._json("/object_info/DepthAnythingV2Preprocessor")
        except ComfyError:
            return False

    def has_upscale_model(self, model: str = UPSCALE_MODEL) -> bool:
        """Whether ComfyUI has this upscale model (ComfyUI/models/upscale_models)."""
        return bool(model) and model in self._choices("UpscaleModelLoader", "model_name")

    def has_lora(self, name: str) -> bool:
        """Whether ComfyUI has this LoRA (ComfyUI/models/loras)."""
        return bool(name) and name in self._choices("LoraLoaderModelOnly", "lora_name")

    def has_seedvr(self, model: str = SEEDVR_MODEL, vae: str = SEEDVR_VAE) -> bool:
        """Whether ComfyUI can run SeedVR2: its nodes, the model and its VAE."""
        if not model or not self._json("/object_info/SeedVR2Conditioning").get("SeedVR2Conditioning"):
            return False
        return model in self._choices("UNETLoader", "unet_name") and vae in self._choices("VAELoader", "vae_name")

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
        return self._fetch(self._outputs(workflow, on_progress, cancel)[0])

    def run_all(self, workflow: dict, on_progress: Callable[[float | None], None], cancel) -> list[Image.Image]:
        """Like `run`, but every output image (e.g. one mask per thing SAM 3 found)."""
        return [self._fetch(im) for im in self._outputs(workflow, on_progress, cancel)]

    def _outputs(self, workflow: dict, on_progress: Callable[[float | None], None], cancel) -> list[dict]:
        """Queue the workflow, wait for it, and return its output images' descriptions (at least one)."""
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
                images = [im for out in entry.get("outputs", {}).values() for im in out.get("images", [])]
                if images:
                    return images
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
