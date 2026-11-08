"""Pasting a result back in place on the desktop app, with several documents open."""
import pytest

from genfill.photocraft_client import PhotoCraftClient, PhotoCraftError


class Tabs(PhotoCraftClient):
    """A pretend PhotoCraft: a row of tabs, one active. Closing a tab activates `after_close` (PhotoCraft
    may pick the neighbour, not the tab you came from); `activate` says whether document.activate exists."""
    def __init__(self, names, active, after_close, activate=True):
        super().__init__(mode="desktop")
        self.tabs = [(n, f"D:/pics/{n}") for n in names]
        self.active, self.after_close, self.can_activate = active, after_close, activate
        self.pasted_into = []

    def _request(self, method, params, rid=None):
        if method == "app.open":
            name = params["path"].split("/")[-1]
            self.tabs.append((name, None))
            self.active = len(self.tabs) - 1
            return {"name": name}
        assert method == "engine.execute"
        cmd, p = params["command"], params["params"] or {}
        if cmd == "document.inspect":
            i = p.get("document", self.active)
            if i >= len(self.tabs):
                raise PhotoCraftError("PhotoCraft: no document at index")
            return {"name": self.tabs[i][0], "path": self.tabs[i][1]}
        if cmd == "file.close":
            del self.tabs[self.active]
            self.active = self.after_close
        elif cmd == "document.activate":
            if not self.can_activate:
                raise PhotoCraftError("PhotoCraft: unknown command")
            self.active = p["document"]
        elif cmd == "edit.pasteSpecial.pasteInPlace":
            self.pasted_into.append(self.tabs[self.active][0])
        return {}


def test_goes_back_to_the_photo_when_closing_lands_on_another_tab():
    pc = Tabs(["a.jpg", "photo.jpg", "c.psd"], active=1, after_close=2)  # closing lands on c.psd
    pc._paste_in_place("result.png")
    assert pc.pasted_into == ["photo.jpg"]
    assert [n for n, _ in pc.tabs] == ["a.jpg", "photo.jpg", "c.psd"]  # the temporary tab is gone


def test_one_document_open_works_as_before():
    pc = Tabs(["photo.jpg"], active=0, after_close=0)
    pc._paste_in_place("result.png")
    assert pc.pasted_into == ["photo.jpg"]


def test_never_pastes_into_the_wrong_document():
    pc = Tabs(["a.jpg", "photo.jpg", "c.psd"], active=1, after_close=2, activate=False)
    with pytest.raises(PhotoCraftError, match="switched to another document"):
        pc._paste_in_place("result.png")
    assert pc.pasted_into == []
