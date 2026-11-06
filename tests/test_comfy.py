import json
import threading

import pytest

from genfill import comfy
from genfill.comfy import ComfyClient, ComfyError, build_fill_workflow

MODELS = {"unet": "u.safetensors", "clip": "c.safetensors", "vae": "v.safetensors"}


def _wf():
    return build_fill_workflow("img.png", "mask.png", "a prompt", seed=7, steps=4, width=768, height=1360, models=MODELS)


def test_workflow_is_json_and_links_resolve():
    wf = _wf()
    json.dumps(wf)
    for node in wf.values():
        for v in node["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                assert v[0] in wf, f"dangling link {v}"


def test_workflow_uses_flux2_models_and_settings():
    wf = _wf()
    by_type = {n["class_type"]: n["inputs"] for n in wf.values()}
    assert by_type["CLIPLoader"] == {"clip_name": "c.safetensors", "type": "flux2"}
    assert by_type["UNETLoader"]["unet_name"] == "u.safetensors"
    assert by_type["VAELoader"]["vae_name"] == "v.safetensors"
    assert by_type["Flux2Scheduler"] == {"steps": 4, "width": 768, "height": 1360}
    assert by_type["RandomNoise"]["noise_seed"] == 7
    assert by_type["CFGGuider"]["cfg"] == 1.0
    assert "a prompt" in json.dumps(wf)


def test_refine_workflow_denoise_uses_low_sigmas():
    from genfill.comfy import build_refine_workflow
    wf = build_refine_workflow("t.png", "", seed=1, steps=8, denoise=0.3, width=1024, height=1024, models=MODELS)
    by_type = {n["class_type"]: n["inputs"] for n in wf.values()}
    assert by_type["SplitSigmasDenoise"]["denoise"] == 0.3
    assert by_type["SamplerCustomAdvanced"]["sigmas"] == ["split", 1]
    assert by_type["SamplerCustomAdvanced"]["latent_image"] == ["encode", 0]


def test_unreachable_comfy_says_not_running():
    c = ComfyClient("http://127.0.0.1:1")
    with pytest.raises(ComfyError, match="ComfyUI is not running"):
        c.check()


def test_history_error_becomes_comfy_error(monkeypatch):
    c = ComfyClient("http://x")

    def fake_json(path, payload=None):
        if path == "/prompt":
            return {"prompt_id": "p1", "node_errors": {}}
        return {"p1": {"status": {"status_str": "error", "completed": False,
                                  "messages": [["execution_error", {"exception_message": "CUDA out of memory"}]]},
                       "outputs": {}}}

    monkeypatch.setattr(c, "_json", fake_json)
    monkeypatch.setattr(comfy, "POLL_SECONDS", 0)
    with pytest.raises(ComfyError, match="out of memory"):
        c.run(_wf(), on_progress=lambda f: None, cancel=threading.Event())


def test_prompt_node_errors_become_comfy_error(monkeypatch):
    c = ComfyClient("http://x")
    monkeypatch.setattr(c, "_json", lambda path, payload=None: {
        "error": {"message": "Prompt outputs failed validation"},
        "node_errors": {"1": {"errors": [{"details": "unet_name not in list"}]}}})
    with pytest.raises(ComfyError, match="unet_name not in list"):
        c.run(_wf(), on_progress=lambda f: None, cancel=threading.Event())


def test_check_setup_reports_missing_model_with_folder_and_link(monkeypatch):
    c = ComfyClient("http://x")
    loaders = {"UNETLoader": ("unet_name", ["other.safetensors"]), "CLIPLoader": ("clip_name", ["c.safetensors"]),
               "VAELoader": ("vae_name", ["v.safetensors"])}

    def fake_json(path, payload=None):
        node = path.rsplit("/", 1)[-1]
        if node in loaders:
            field, files = loaders[node]
            return {node: {"input": {"required": {field: [files]}}}}
        return {node: {"input": {}}}

    monkeypatch.setattr(c, "_json", fake_json)
    problems = c.check_setup(MODELS)
    assert len(problems) == 1
    assert "u.safetensors" in problems[0] and "diffusion_models" in problems[0] and "huggingface.co" in problems[0]


def test_check_setup_reports_old_comfyui(monkeypatch):
    c = ComfyClient("http://x")
    monkeypatch.setattr(c, "_json", lambda path, payload=None: {})
    assert any("too old" in p for p in c.check_setup(MODELS))


def test_available_models_reads_new_combo_format(monkeypatch):
    c = ComfyClient("http://x")
    monkeypatch.setattr(c, "_json", lambda path, payload=None: {
        path.rsplit("/", 1)[-1]: {"input": {"required": {"unet_name": ["COMBO", {"options": ["a", "b"]}],
                                                          "clip_name": ["COMBO", {"options": ["c"]}],
                                                          "vae_name": [["v"]]}}}})
    assert c.available_models() == {"unet": ["a", "b"], "clip": ["c"], "vae": ["v"]}


def test_edit_workflow_redraws_whole_image_from_reference():
    from genfill.comfy import build_edit_workflow
    wf = build_edit_workflow("img.png", "make it night", seed=3, steps=4, width=1024, height=768, models=MODELS)
    json.dumps(wf)
    by_type = {n["class_type"]: n["inputs"] for n in wf.values()}
    assert "SetLatentNoiseMask" not in by_type and "LoadImageMask" not in by_type
    assert by_type["ReferenceLatent"]["latent"] == ["encode", 0]
    assert by_type["SamplerCustomAdvanced"]["sigmas"] == ["sigmas", 0]  # full denoise
    assert by_type["CLIPTextEncode"]["text"] == "make it night"
    for node in wf.values():
        for v in node["inputs"].values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                assert v[0] in wf, f"dangling link {v}"
