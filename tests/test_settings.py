import json

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
