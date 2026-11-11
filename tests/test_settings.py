import json
import tkinter as tk

import pytest

from genfill import theme
from genfill.settings import DEFAULT_MODELS, Settings


def test_roundtrip_and_creates_folder(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    s = Settings(comfy_url="http://10.0.0.5:8188", steps=6)
    s.save(p)
    back = Settings.load(p)
    assert back.comfy_url == "http://10.0.0.5:8188" and back.steps == 6


def test_unknown_keys_ignored_and_missing_models_filled(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"future_option": 1, "models": {"unet": "my-klein.safetensors"}}))
    s = Settings.load(p)
    assert s.models["unet"] == "my-klein.safetensors"
    assert s.models["vae"] == DEFAULT_MODELS["vae"]


def test_corrupt_file_gives_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json")
    assert Settings.load(p) == Settings()


def test_prompt_history_newest_first_no_duplicates_capped():
    from genfill.settings import MAX_RECENT, Settings
    s = Settings()
    for t in ["a", "b", "a", "  ", *[str(i) for i in range(30)]]:
        s.remember_prompt(t)
    assert s.recent_prompts[0] == "29" and len(s.recent_prompts) == MAX_RECENT
    s.remember_prompt("b")
    assert s.recent_prompts[0] == "b" and s.recent_prompts.count("b") == 1


def test_bad_history_in_file_is_ignored(tmp_path):
    from genfill.settings import Settings
    f = tmp_path / "s.json"
    f.write_text('{"recent_prompts": "oops", "check_updates": false}')
    s = Settings.load(f)
    assert s.recent_prompts == [] and s.check_updates is False


def test_shoot_count_defaults_to_eight_and_is_kept(tmp_path):
    f = tmp_path / "s.json"
    s = Settings()
    assert s.shoot_count == 8
    s.shoot_count = 12
    s.save(f)
    assert Settings.load(f).shoot_count == 12


def test_model_lora_switch_keeps_configuration_and_face_swap(tmp_path):
    s = Settings(loras=[{"name": "mine.safetensors", "strength": 0.7}], use_loras=False)
    assert s.workflow_models["loras"] == []
    assert s.loras == [{"name": "mine.safetensors", "strength": 0.7}]
    face_swap = s.face_swap_lora
    path = tmp_path / "settings.json"
    s.save(path)
    loaded = Settings.load(path)
    assert loaded.use_loras is False and loaded.workflow_models["loras"] == []
    loaded.use_loras = True
    assert loaded.workflow_models["loras"] == s.loras
    assert loaded.face_swap_lora == face_swap


def test_settings_dialog_lora_picker_preserves_paused_configuration(monkeypatch):
    from genfill.settings_dialog import SettingsDialog

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    monkeypatch.setattr(SettingsDialog, "load_models", lambda self: None)
    try:
        dialog = SettingsDialog(root, Settings(loras=["example.safetensors"], use_loras=False),
                                theme.PALETTES["Pro"], theme.fonts(root), lambda _: None)
        assert dialog.lora_names[0].get() == "None"
        saved = dialog._collect()
        assert saved.use_loras is False
        assert saved.loras == [{"name": "example.safetensors", "strength": 1.0}]
        dialog.lora_names[0].set("example.safetensors")
        dialog.lora_strengths[0].set("0.7")
        saved = dialog._collect()
        assert saved.use_loras is True
        assert saved.loras == [{"name": "example.safetensors", "strength": 0.7}]
        dialog.destroy()
    finally:
        root.destroy()


def test_settings_dialog_keeps_lora_strength_when_switching_models(monkeypatch):
    from genfill.settings_dialog import SettingsDialog

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    monkeypatch.setattr(SettingsDialog, "load_models", lambda self: None)
    try:
        dialog = SettingsDialog(root, Settings(loras=[{"name": "example.safetensors", "strength": 1.0}]),
                                theme.PALETTES["Pro"], theme.fonts(root), lambda _: None)
        dialog.lora_strengths[0].set("0.7")
        dialog.family_var.set("FLUX 3 Klein")
        dialog._switch_family()
        assert dialog.lora_names[0].get() == "None"
        dialog.family_var.set("FLUX.2 Klein 9B")
        dialog._switch_family()
        assert dialog.lora_strengths[0].get() == "0.7"
        assert dialog._collect().loras == [{"name": "example.safetensors", "strength": 0.7}]
        dialog.destroy()
    finally:
        root.destroy()


def test_settings_dialog_can_pick_two_loras_with_independent_strengths(monkeypatch):
    from genfill.settings_dialog import SettingsDialog

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    monkeypatch.setattr(SettingsDialog, "load_models", lambda self: None)
    try:
        dialog = SettingsDialog(root, Settings(), theme.PALETTES["Pro"], theme.fonts(root), lambda _: None)
        dialog.lora_names[0].set("first.safetensors")
        dialog.lora_strengths[0].set("0.6")
        dialog._add_lora()
        dialog.lora_names[1].set("second.safetensors")
        dialog.lora_strengths[1].set("0.9")
        assert dialog._collect().loras == [
            {"name": "first.safetensors", "strength": 0.6},
            {"name": "second.safetensors", "strength": 0.9},
        ]
        dialog.destroy()
    finally:
        root.destroy()


def test_saved_workflows_appear_in_image_model_list_and_can_be_switched(tmp_path, monkeypatch):
    from genfill import custom_workflow, settings_dialog
    from genfill.settings_dialog import SettingsDialog

    first = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "one", "clip": ["2", 0]}},
             "3": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}}}
    second = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "two", "clip": ["2", 0]}},
              "3": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}}}
    custom_workflow.save(first, tmp_path, "one")
    custom_workflow.save(second, tmp_path, "two")
    monkeypatch.setattr(settings_dialog, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(SettingsDialog, "load_models", lambda self: None)
    settings = Settings(custom_workflow="C:/one.json", active_workflow_id="one",
                        saved_workflows=[
                            {"id": "one", "name": "One", "source": "C:/one.json", "slots": {"result": "3"}},
                            {"id": "two", "name": "Two", "source": "C:/two.json", "slots": {"result": "3"}},
                        ])
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        dialog = SettingsDialog(root, settings, theme.PALETTES["Pro"], theme.fonts(root), lambda _: None)
        assert "Workflow: One" in dialog.family_box["values"]
        assert "Workflow: Two" in dialog.family_box["values"]
        assert dialog.family_var.get() == "Workflow: One"
        dialog.family_var.set("Workflow: Two")
        dialog._switch_family()
        assert dialog.own_wf == second and dialog.own_path == "C:/two.json"
        dialog.family_var.set("FLUX 3 Klein")
        dialog._switch_family()
        assert dialog.own_wf is None
        assert len(dialog.saved_workflows) == 2
        assert dialog._collect().custom_workflow == ""
        dialog.destroy()
    finally:
        root.destroy()


def test_chosen_workflow_is_saved_as_a_model_choice(tmp_path, monkeypatch):
    from genfill import custom_workflow, settings_dialog
    from genfill.settings_dialog import SettingsDialog

    monkeypatch.setattr(settings_dialog, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(SettingsDialog, "load_models", lambda self: None)
    original_save = Settings.save
    monkeypatch.setattr(Settings, "save", lambda self: original_save(self, tmp_path / "settings.json"))
    wf = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["2", 0]}},
          "3": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}}}
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        dialog = SettingsDialog(root, Settings(), theme.PALETTES["Pro"], theme.fonts(root), lambda _: None)
        dialog._workflow_read("C:/my-workflow.json", wf, [])
        assert "Workflow: my-workflow" in dialog.family_box["values"]
        dialog.save()
        loaded = Settings.load(tmp_path / "settings.json")
        assert loaded.custom_workflow == "C:/my-workflow.json"
        assert loaded.active_workflow_id == loaded.saved_workflows[0]["id"]
        assert custom_workflow.load_saved(tmp_path, loaded.active_workflow_id) == wf
        assert custom_workflow.load_saved(tmp_path) == wf
    finally:
        root.destroy()


def test_workflow_options_open_separately_and_save_stays_visible(monkeypatch):
    from genfill.settings_dialog import SettingsDialog

    monkeypatch.setattr(SettingsDialog, "load_models", lambda self: None)
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        dialog = SettingsDialog(root, Settings(), theme.PALETTES["Pro"], theme.fonts(root), lambda _: None)
        dialog._workflow_read("C:/my-workflow.json", {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["2", 0]}},
            "3": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
            "5": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}},
        }, [])
        dialog.open_workflow_settings()
        dialog.update()
        assert dialog.workflow_window.winfo_exists()
        assert dialog.workflow_window.winfo_toplevel() is not dialog
        assert dialog.save_button.winfo_manager() == "pack"
        assert dialog.save_button.winfo_toplevel() is dialog
        assert dialog.winfo_height() <= root.winfo_screenheight() - 80
        dialog.keep_seeds_var.set(True)
        dialog.native_size_var.set(True)
        dialog.extra_results.selection_set(0)
        dialog._close_workflow_settings()
        settings = dialog._collect()
        assert settings.custom_keep_seeds and settings.custom_native_size
        assert len(settings.custom_result_nodes) == 1
        assert "2 outputs" in dialog.workflow_summary.get()
    finally:
        root.destroy()
