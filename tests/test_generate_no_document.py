import queue
from types import SimpleNamespace

import pytest

from genfill import app as app_mod
from genfill.pipeline import UserFacingError
from genfill.settings import Settings

NO_DOC = "PhotoCraft: command `document.inspect` is not available right now: no document open"


def _fake_app(offered):
    return SimpleNamespace(events=queue.Queue(), _offer=lambda r, s, st: offered.append(r) or "ok")


def _no_doc(*a, **k):
    raise UserFacingError(NO_DOC)


def test_generate_with_no_picture_open_creates_one_instead(monkeypatch):
    made, offered = [], []
    monkeypatch.setattr(app_mod, "generate", _no_doc)
    monkeypatch.setattr(app_mod, "create", lambda choice, prompt, *a, **k: made.append((choice, prompt)) or "R")
    fake = _fake_app(offered)
    s = Settings(create_choice="4:5")
    assert app_mod.App._generate(fake, "a red lighthouse", s, lambda m: None, lambda f: None, None) == "ok"
    assert made == [("4:5", "a red lighthouse")] and offered == ["R"]
    assert fake.events.get_nowait() == ("mode", "empty")


@pytest.mark.parametrize("kw", [{"remove": True}, {"name": "Sky: dramatic storm"}])
def test_remove_and_presets_still_report_no_picture(monkeypatch, kw):
    monkeypatch.setattr(app_mod, "generate", _no_doc)
    with pytest.raises(UserFacingError, match="no document open"):
        app_mod.App._generate(_fake_app([]), "x", Settings(), lambda m: None, lambda f: None, None, **kw)
