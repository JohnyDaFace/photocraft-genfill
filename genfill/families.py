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
    encoder: str = "flux"  # "flux": text + ReferenceLatent per picture; "qwen21": one node sees the text and
    #                        the pictures; "text": a text-to-image model that can't be shown pictures
    sigmas: str = ""    # a distilled model's own schedule, used when Steps matches it
    redraw: float = 1.0  # "text" models: how much of the picture a whole-image edit redraws (they can't follow it)
    sampler: str = "euler"  # ComfyUI sampler; FLUX 3's official workflow uses er_sde


FLUX3_URL = "https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy/resolve/main/"
QWEN21_URL = "https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/"
KREA2_URL = "https://huggingface.co/Comfy-Org/Krea-2/tree/main/"

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
        steps=15, scheduler="simple", steps_hint="15 = best, 8 = faster", head_swap=False, fill_instruction=True,
        sampler="er_sde"),
    "qwen21": Family(
        label="Qwen Image 2.1 Turbo",
        clip_type="qwen_image",
        models={"unet": "qwen_image_2.1_turbo_int8_convrot.safetensors",
                "clip": "qwen3vl_8b_int8_convrot.safetensors", "vae": "qwen_image_2.1_vae_bf16.safetensors"},
        urls={"unet": QWEN21_URL + "diffusion_models/qwen_image_2.1_turbo_int8_convrot.safetensors",
              "clip": QWEN21_URL + "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
              "vae": QWEN21_URL + "vae/qwen_image_2.1_vae_bf16.safetensors"},
        steps=8, scheduler="simple", steps_hint="8 = its own fast schedule", head_swap=False,
        encoder="qwen21", sigmas="1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"),
    "krea2": Family(
        label="Krea 2 Turbo (create only)",
        clip_type="krea2",
        models={"unet": "krea2_turbo_fp8_scaled.safetensors", "clip": "qwen3vl_4b_fp8_scaled.safetensors",
                "vae": "qwen_image_vae.safetensors"},
        urls={"unet": KREA2_URL + "diffusion_models", "clip": KREA2_URL + "text_encoders",
              "vae": KREA2_URL + "vae"},
        steps=8, scheduler="simple", steps_hint="8 = fast", head_swap=False, encoder="text", redraw=0.6),
}
DEFAULT_FAMILY = "flux2"


def family(name: str | None) -> Family:
    return FAMILIES.get(name or DEFAULT_FAMILY, FAMILIES[DEFAULT_FAMILY])
