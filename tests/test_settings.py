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
