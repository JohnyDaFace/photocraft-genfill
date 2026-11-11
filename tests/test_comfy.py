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


def test_two_image_create_keeps_requested_canvas_shape():
    from genfill.comfy import build_create_workflow
    wf = build_create_workflow("person in image 2", 17, 4, 1888, 1056,
                               {**MODELS, "loras": [{"name": "example_lora.safetensors", "strength": 0.7}]},
                               ["scene.png", "person.png"], scale_references=True)
    assert [wf[f"extra{i}_image"]["inputs"]["image"] for i in (1, 2)] == ["scene.png", "person.png"]
    assert all(wf[f"ref{i}_scale"]["inputs"]["upscale_method"] == "lanczos" for i in (1, 2))
    assert wf["latent"]["inputs"]["width"] == 1888
    assert wf["latent"]["inputs"]["height"] == 1056
    assert wf["sigmas"]["inputs"]["width"] == 1888
    assert wf["sigmas"]["inputs"]["height"] == 1056
    assert "ref_size" not in wf
    assert wf["guider"]["inputs"]["positive"] == ["ref2", 0]
    assert wf["guider"]["inputs"]["negative"] == ["neg_ref2", 0]
    assert wf["guider"]["inputs"]["model"] == ["lora0", 0]
    assert wf["lora0"]["inputs"]["strength_model"] == 0.7


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


def test_selected_outputs_are_collected_from_one_queue_run(monkeypatch):
    from PIL import Image

    c = ComfyClient("http://x")
    queued = []

    def fake_json(path, payload=None):
        if path == "/prompt":
            queued.append(payload)
            return {"prompt_id": "p1"}
        return {"p1": {"status": {"completed": True}, "outputs": {
            "raw": {"images": [{"filename": "raw.png"}]},
            "matched": {"images": [{"filename": "matched.png"}]},
            "debug": {"images": [{"filename": "debug.png"}]},
        }}}

    monkeypatch.setattr(c, "_json", fake_json)
    fetched = []
    def fetch(im):
        fetched.append(im["filename"])
        return Image.new("RGB", (1, 1), "white")
    monkeypatch.setattr(c, "_fetch", fetch)
    images = c.run_selected({}, lambda _: None, threading.Event(), ["matched", "raw"])
    assert len(queued) == 1 and len(images) == 2
    assert fetched == ["matched.png", "raw.png"]


def test_missing_selected_output_is_an_error(monkeypatch):
    from PIL import Image
    c = ComfyClient("http://x")
    monkeypatch.setattr(c, "_json", lambda path, payload=None: {"prompt_id": "p1"} if path == "/prompt" else
                        {"p1": {"status": {"completed": True}, "outputs": {
                            "raw": {"images": [{"filename": "raw.png"}]}}}})
    monkeypatch.setattr(c, "_fetch", lambda im: Image.new("RGB", (1, 1), "white"))
    with pytest.raises(ComfyError, match="matched"):
        c.run_selected({}, lambda _: None, threading.Event(), ["raw", "matched"])


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


def test_available_loras_reads_comfyui_loader_choices(monkeypatch):
    c = ComfyClient("http://x")
    monkeypatch.setattr(c, "_json", lambda path, payload=None: {
        "LoraLoaderModelOnly": {"input": {"required": {"lora_name": [["example.safetensors"]]}}}})
    assert c.available_loras() == ["example.safetensors"]


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


def test_reference_images_are_chained_after_the_source():
    from genfill.comfy import build_edit_workflow
    for wf in (build_fill_workflow("i.png", "m.png", "p", 1, 4, 64, 64, MODELS, extra_refs=["r.png"]),
               build_edit_workflow("i.png", "p", 1, 4, 64, 64, MODELS, extra_refs=["r.png"])):
        assert wf["extra1_image"]["inputs"]["image"] == "r.png"
        assert wf["ref2"]["inputs"]["conditioning"] == ["ref1", 0]  # source first = image 1
        assert wf["guider"]["inputs"]["positive"] == ["ref2", 0]
        assert wf["neg"]["inputs"]["conditioning"] == ["text", 0]
        assert wf["neg_ref2"]["inputs"]["conditioning"] == ["neg_ref1", 0]
        assert wf["guider"]["inputs"]["negative"] == ["neg_ref2", 0]


def test_flux_reference_is_present_in_negative_guidance_like_comfy_template():
    from genfill.comfy import build_create_workflow
    wf = build_create_workflow("same person", 1, 4, 1024, 1024, MODELS, ["person.png"])
    assert wf["neg"]["inputs"]["conditioning"] == ["text", 0]
    assert wf["neg_ref1"]["class_type"] == "ReferenceLatent"
    assert wf["neg_ref1"]["inputs"] == {"conditioning": ["neg", 0], "latent": ["ref1_encode", 0]}
    assert wf["guider"]["inputs"]["negative"] == ["neg_ref1", 0]


def test_flux2_create_can_scale_reference_like_comfy_image_edit_subgraph():
    from genfill.comfy import build_create_workflow
    wf = build_create_workflow("same person", 1, 4, 1888, 1056, MODELS, ["person.png"],
                               scale_references=True)
    assert wf["ref1_scale"] == {"class_type": "ImageScaleToTotalPixels", "inputs": {
        "upscale_method": "nearest-exact", "megapixels": 1.0, "resolution_steps": 1,
        "image": ["extra1_image", 0]}}
    assert wf["ref1_encode"]["inputs"]["pixels"] == ["ref1_scale", 0]
    assert wf["latent"]["inputs"]["width"] == 1888  # output size stays independent


def test_flux3_reference_guidance_is_unchanged():
    from genfill.comfy import build_create_workflow
    wf = build_create_workflow("same person", 1, 15, 1024, 1024,
                               {**MODELS, "family": "flux3"}, ["person.png"])
    assert "neg_ref1" not in wf
    assert wf["neg"]["inputs"]["conditioning"] == ["ref1", 0]
