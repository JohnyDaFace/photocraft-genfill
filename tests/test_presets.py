"""Clicking a preset runs it straight away instead of filling the prompt box."""
from types import SimpleNamespace

from genfill.app import App


def _bar(mode="edit"):
    bar = SimpleNamespace(mode=mode, started=[], said=[])
    bar.start = lambda job, **kw: bar.started.append((job, kw))
    bar.say = lambda text, *a, **kw: bar.said.append(text)
    return bar


def test_a_preset_runs_generate_with_its_own_instruction():
    bar = _bar()
    App.use_preset(bar, "Restore old photo")
    assert bar.started == [("fill", {"preset": "Restore old photo"})]


def test_a_preset_with_nothing_open_explains_instead_of_creating_a_picture():
    bar = _bar(mode="empty")
    App.use_preset(bar, "Restore old photo")
    assert bar.started == [] and "Open a photo" in bar.said[0]


def test_an_unknown_preset_does_nothing():
    bar = _bar()
    App.use_preset(bar, "Presets…")
    assert bar.started == [] and bar.said == []


def test_reflection_presets_for_windows_and_glasses():
    from genfill.presets import prompt_for
    assert "Keep the room behind the glass" in prompt_for("Remove reflections: windows")
    assert "glasses" in prompt_for("Remove reflections: glasses")


def test_reflection_presets_share_one_submenu():
    bar = SimpleNamespace(use_preset=lambda n: None)
    shown = App.preset_items(bar)
    sub = next(i for i in shown if i[0] == "sub" and i[1] == "Remove reflections")
    assert [i[1] for i in sub[2]] == ["Windows", "Glasses"]
