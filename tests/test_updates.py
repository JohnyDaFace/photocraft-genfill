import io
import json

from genfill import updates


def test_is_newer_compares_numbers_not_text():
    assert updates.is_newer("v1.10.0", "1.9.3")
    assert updates.is_newer("v1.1.1", "1.1")
    assert not updates.is_newer("v1.1.0", "1.1.0")
    assert not updates.is_newer("v1.0.9", "1.1.0")
    assert not updates.is_newer("nightly", "1.1.0")


def _fake_urlopen(payload):
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
    return lambda req, timeout=None: R(json.dumps(payload).encode())


def test_newer_release_found(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen",
                        _fake_urlopen({"tag_name": "v9.0.0", "html_url": "https://example/releases/v9.0.0"}))
    assert updates.newer_release("1.1.0") == ("9.0.0", "https://example/releases/v9.0.0")


def test_same_version_or_prerelease_is_ignored(monkeypatch):
    monkeypatch.setattr(updates.urllib.request, "urlopen", _fake_urlopen({"tag_name": "v1.1.0"}))
    assert updates.newer_release("1.1.0") is None
    monkeypatch.setattr(updates.urllib.request, "urlopen", _fake_urlopen({"tag_name": "v2.0.0", "prerelease": True}))
    assert updates.newer_release("1.1.0") is None


def test_offline_is_quiet(monkeypatch):
    def boom(*a, **k): raise OSError("no network")
    monkeypatch.setattr(updates.urllib.request, "urlopen", boom)
    assert updates.newer_release("1.1.0") is None
