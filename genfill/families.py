"""The image models GenFill can draw with. Each family names its three files (model, text encoder, VAE),
where to get them, and the few things its ComfyUI workflow does differently."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Family:
    label: str          # shown in Settings
    clip_type: str      # CLIPLoader's "type"
    models: dict        # default files: unet, clip, vae
    urls: dict          # where to download each default file
    steps: int          # default sampling steps
    scheduler: str      # "flux2": Flux2Scheduler (size-aware); "simple": the model's own schedule
    steps_hint: str     # shown next to Steps in Settings
    head_swap: bool     # Replace face's head-swap LoRA is trained for this model
    fill_instruction: bool = False  # fills need an instruction ("Add a dog"); a bare "a dog" repaints what was there


FLUX3_URL = "https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy/resolve/main/"

FAMILIES: dict[str, Family] = {
    "flux2": Family(
        label="FLUX.2 Klein 9B",
        clip_type="flux2",
        models={"unet": "flux-2-klein-9b-fp8.safetensors", "clip": "qwen_3_8b_fp8mixed.safetensors",
                "vae": "flux2-vae.safetensors"},
        urls={"unet": "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8 (accept the agreement first)",
              "clip": "https://huggingface.co/Comfy-Org/flux2-klein-9B/blob/main/split_files/text_encoders/"
                      "qwen_3_8b_fp8mixed.safetensors",
              "vae": "https://huggingface.co/Comfy-Org/flux2-dev/blob/main/split_files/vae/flux2-vae.safetensors"},
        steps=4, scheduler="flux2", steps_hint="4 = fast, 8 = finer", head_swap=True),
    "flux3": Family(
        label="FLUX 3 Klein",
        clip_type="flux3",
        models={"unet": "flux3-image-klein-int8-convrot.safetensors", "clip": "qwen3vl_4b_int8_convrot.safetensors",
                "vae": "flux2-vae.safetensors"},
        urls={"unet": FLUX3_URL + "diffusion_models/flux3-image-klein-int8-convrot.safetensors",
              "clip": FLUX3_URL + "text_encoders/qwen3vl_4b_int8_convrot.safetensors",
              "vae": FLUX3_URL + "vae/flux2-vae.safetensors"},
        steps=8, scheduler="simple", steps_hint="8 = fast, 16 = finer", head_swap=False, fill_instruction=True),
}
DEFAULT_FAMILY = "flux2"


def family(name: str | None) -> Family:
    return FAMILIES.get(name or DEFAULT_FAMILY, FAMILIES[DEFAULT_FAMILY])
