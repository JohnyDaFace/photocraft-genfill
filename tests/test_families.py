from genfill.comfy import build_create_workflow, build_edit_workflow, build_fill_workflow
from genfill.families import FAMILIES
from genfill.settings import Settings


def test_switching_family_brings_its_files_and_keeps_the_others(tmp_path):
    s = Settings(steps=6, loras=["klein.safetensors"])
    s.models["unet"] = "my-klein.safetensors"
    s.switch_family("flux3")
    assert s.model_family == "flux3" and s.models == FAMILIES["flux3"].models
    assert s.steps == FAMILIES["flux3"].steps and s.loras == []  # FLUX.2 LoRAs don't fit FLUX 3
    s.save(tmp_path / "s.json")
    s = Settings.load(tmp_path / "s.json")
    s.switch_family("flux2")
    assert s.models["unet"] == "my-klein.safetensors" and s.steps == 6 and s.loras == ["klein.safetensors"]


def test_flux3_workflows_use_its_text_encoder_and_own_schedule():
    m = {**FAMILIES["flux3"].models, "family": "flux3"}
    for wf in (build_edit_workflow("a.png", "make it night", 1, 8, 1024, 1024, m),
               build_fill_workflow("a.png", "m.png", "", 1, 8, 1024, 1024, m),
               build_create_workflow("a cat", 1, 8, 1024, 1024, m)):
        assert wf["clip"]["inputs"]["type"] == "flux3"
        assert wf["sigmas"]["class_type"] == "BasicScheduler" and wf["sigmas"]["inputs"]["steps"] == 8
        assert wf["sampler"]["inputs"]["sampler_name"] == "er_sde"  # as BFL's own workflow
    wf = build_edit_workflow("a.png", "x", 1, 4, 1024, 1024, {**FAMILIES["flux2"].models})  # no family: FLUX.2
    assert wf["clip"]["inputs"]["type"] == "flux2" and wf["sigmas"]["class_type"] == "Flux2Scheduler"
    assert wf["sampler"]["inputs"]["sampler_name"] == "euler"


def test_flux3_old_default_steps_move_to_15(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"settings_version": 2, "model_family": "flux3", "steps": 8}), encoding="utf-8")
    assert Settings.load(p).steps == 15
    p.write_text(json.dumps({"settings_version": 2, "steps": 4, "other_families": {"flux3": {"steps": 8}}}),
                 encoding="utf-8")
    s = Settings.load(p)
    s.switch_family("flux3")
    assert s.steps == 15
    p.write_text(json.dumps({"settings_version": 3, "model_family": "flux3", "steps": 8}), encoding="utf-8")
    assert Settings.load(p).steps == 8  # chosen by hand after the update: kept


def test_flux3_fills_get_an_instruction():
    from genfill.pipeline import fill_text
    s = Settings()
    assert fill_text("a golden retriever", s) == "a golden retriever"  # FLUX.2: as typed
    s.switch_family("flux3")
    assert fill_text("a golden retriever", s) == "Add a golden retriever"
    assert fill_text("Make the car red", s) == "Make the car red"
    assert fill_text("", s) == ""
