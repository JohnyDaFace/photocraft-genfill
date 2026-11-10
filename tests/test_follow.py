"""The bar follows PhotoCraft: shown when it opens, hidden when it closes; started with Windows hidden."""
from types import SimpleNamespace
import queue

import pytest

from genfill import autostart
from genfill.app import App
from genfill.photocraft_client import PhotoCraftError
from genfill.settings import Settings


def _bar(visible=True, background=False, busy=False, follow=True):
    calls = []
    bar = SimpleNamespace(visible=visible, background=background, busy=busy, photocraft_seen=False,
                          _photocraft_down=0, settings=Settings(follow_photocraft=follow), calls=calls)
    bar.show = lambda: (calls.append("show"), setattr(bar, "visible", True))
    bar.hide = lambda: (calls.append("hide"), setattr(bar, "visible", False))
    bar.check_connections = lambda: None
    return bar


def follow(bar, *states):
    for s in states:
        App.follow_photocraft(bar, s)


def test_closing_photocraft_hides_after_two_failed_checks():
    bar = _bar()
    follow(bar, True, False)
    assert bar.calls == []           # one failed check can be a busy moment
    follow(bar, False)
    assert bar.calls == ["hide"]


def test_opening_photocraft_shows_the_hidden_bar():
    bar = _bar(visible=False, background=True)
    follow(bar, False, False, True)
    assert bar.calls == ["show"]


def test_started_by_hand_without_photocraft_stays_visible():
    bar = _bar()                     # never saw PhotoCraft: the user just opened the bar
    follow(bar, False, False, False)
    assert bar.calls == []


def test_never_hides_while_working_or_when_turned_off():
    busy = _bar(busy=True)
    follow(busy, True, False, False)
    off = _bar(follow=False)
    follow(off, True, False, False)
    assert busy.calls == [] and off.calls == []


def test_startup_shortcut_starts_the_bar_hidden():
    program, args = autostart.command()
    assert args.endswith(autostart.BACKGROUND)
    assert program.lower().endswith((".exe", "python", "pythonw"))
    assert autostart.shortcut_path().name == autostart.NAME


def test_watcher_connection_events_update_status():
    events = queue.Queue()
    calls = []
    bar = SimpleNamespace(events=events, conn={"PhotoCraft": (True, "connected")},
                          root=SimpleNamespace(after=lambda *args: None),
                          follow_photocraft=lambda running: calls.append(running), poll=lambda: None)
    events.put(("photocraft_connection", (False, "reconnecting")))
    events.put(("photocraft", False))
    App.poll(bar)
    assert bar.conn["PhotoCraft"] == (False, "reconnecting")
    events.put(("photocraft_connection", (True, "connected")))
    events.put(("photocraft", True))
    App.poll(bar)
    assert bar.conn["PhotoCraft"] == (True, "connected")
    assert calls == [False, True]


def test_watcher_reconnects_on_document_error_other_than_no_document(monkeypatch):
    from genfill import app

    class Client:
        closed = False
        mode = "desktop"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def _inspect(self):
            raise PhotoCraftError("PhotoCraft: temporary request failure")

    client = Client()
    monkeypatch.setattr(app, "PhotoCraftClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(app.setup_env, "status", lambda port: {"ready": True})
    ticks = iter(range(3))

    def sleep(seconds):
        try:
            next(ticks)
        except StopIteration:
            raise RuntimeError("stop watcher")

    monkeypatch.setattr(app.time, "sleep", sleep)
    bar = SimpleNamespace(busy=False, settings=Settings(), events=queue.Queue())
    with pytest.raises(RuntimeError, match="stop watcher"):
        App.watch(bar)
    assert client.closed
