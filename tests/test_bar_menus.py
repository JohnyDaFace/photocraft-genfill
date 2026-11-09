"""The bar's menus (Select, Remove, Fix, Resize, Create), with the bar faked: what each one holds."""
from types import SimpleNamespace

from genfill import shoots
from genfill.app import App
from genfill.settings import Settings


def _bar(tmp_path, monkeypatch, typed="", reference=None, mode="image"):
    monkeypatch.setattr(shoots, "SHOOTS_FILE", tmp_path / "shoots.json")
    bar = SimpleNamespace(settings=Settings(), reference=reference, mode=mode, started=[], menus=[],
                          prompt=SimpleNamespace(get=lambda: typed), sel_btn=1, fix_btn=2, size_btn=3, create_btn=4, rm_btn=5,
                          _edit_shoots=print, _pick_shoot=print, _set_shoot_count=print, _set_create_choice=print,
                          _replace_face_with_photo=print, use_preset=print)
    bar.start = lambda job, **kw: bar.started.append((job, kw))
    bar._menu = lambda anchor, items, right=False: bar.menus.append((anchor, items))
    for name in ("_typed", "preset_items", "shoot_items"):
        setattr(bar, name, getattr(App, name).__get__(bar))
    return bar


def _labels(items):
    return [i[1] for i in items if i[0] in ("item", "sub", "check", "header")]


def test_select_only_selects(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch, typed="the dog")
    App.select_menu(bar)
    labels = _labels(bar.menus[0][1])
    assert labels == ["Select", "“the dog”", "Sky", "People", "By distance", "Foreground (what's near)",
                      "Background (what's far)"]
    bar.menus[0][1][-1][2]()
    assert bar.started[-1] == ("depth_select", {"choice": "background"})


def test_remove_holds_everything_that_takes_things_out(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch, typed="the dog")
    picked = []
    bar.use_preset = picked.append
    App.remove_menu(bar)
    anchor, items = bar.menus[0]
    assert anchor == 5
    assert _labels(items) == ["Remove", "“the dog”", "People", "Cars", "Background", "Remove background, keep “the dog”",
                              "Reflections", "On windows", "On glasses"]
    next(i for i in items if i[1:2] == ("Cars",))[2]()
    assert bar.started[-1][0] == "remove_things"
    next(i for i in items if i[1:2] == ("On glasses",))[2]()
    assert picked == ["Remove reflections: glasses"]
    sel = _bar(tmp_path, monkeypatch, mode="selection")
    App.remove_menu(sel)
    items = sel.menus[0][1]
    assert _labels(items) == ["Remove", "What's selected", "Reflections", "On windows", "On glasses"]
    items[1][2]()
    assert sel.started[-1] == ("remove", {})


def test_fix_improves_and_changes_the_look(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch, typed="the dog", reference="C:/p/face.jpg")
    picked = []
    bar.use_preset = picked.append
    App.fix_menu(bar)
    anchor, items = bar.menus[0]
    assert anchor == 2
    assert _labels(items) == ["Fix", "Enhance: crisper, better light", "Restore old photo", "Colorize black & white",
                              "Harmonize (select the pasted object)", "Faces", "Change the look", "Relight", "Sky",
                              "Blur & haze"]
    subs = {i[1]: i[2] for i in items if i[0] == "sub"}
    assert _labels(subs["Faces"]) == ["Sharpen faces", "As typed: “the dog”", "Natural smile", "Look at the camera",
                                      "Retouch skin", "Younger", "Older", "Replace with face.jpg",
                                      "Replace with another photo…"]
    assert _labels(subs["Sky"]) == ["Golden sunset", "Clear blue", "Dramatic storm"]
    assert _labels(subs["Relight"]) == ["Golden hour", "Soft studio", "Night"]
    subs["Sky"][0][2]()
    assert picked == ["Sky: golden sunset"]
    assert not any("Remove" in label for label in _labels(items))  # taking things out is under Remove


def test_resize_holds_expand_and_upscale(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch)
    App.resize_menu(bar)
    labels = _labels(bar.menus[0][1])
    assert labels[0] == "Expand the canvas to" and "Upscale to a new document" in labels
    assert labels[-2:] == ["2× larger", "4× larger"]


def test_create_holds_sketch_and_photoshoot_and_photoshoot_works_with_nothing_open(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch)
    App.create_menu(bar)
    labels = _labels(bar.menus[0][1])
    assert "From my sketch (the active layer)" in labels and "Photoshoot" in labels
    empty = _bar(tmp_path, monkeypatch, mode="empty")
    App.create_menu(empty)
    labels = _labels(empty.menus[0][1])
    assert "From my sketch (the active layer)" not in labels and "Photoshoot" in labels


def test_fix_blur_focuses_on_the_typed_thing_only_when_picked(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch, typed="the dog")
    App.fix_menu(bar)
    depth = {i[1]: i[2] for i in bar.menus[0][1] if i[0] == "sub"}["Blur & haze"]
    assert _labels(depth) == ["Lens blur: subject sharp", "Lens blur: “the dog” sharp", "Portrait lens (redrawn)",
                              "Haze in the distance", "Fog"]
    depth[0][2]()
    assert bar.started[-1] == ("depth", {"choice": "blur"})
    depth[1][2]()
    assert bar.started[-1] == ("depth", {"choice": "blur", "what": "the dog"})
    bar2 = _bar(tmp_path, monkeypatch)
    App.fix_menu(bar2)
    assert len({i[1]: i[2] for i in bar2.menus[0][1] if i[0] == "sub"}["Blur & haze"]) == 4


def test_restart_frees_the_port_and_starts_a_new_bar(monkeypatch):
    import sys
    from genfill import app as app_mod
    started, events = [], []
    monkeypatch.setattr(app_mod.subprocess, "Popen", lambda args, **kw: started.append(args))
    monkeypatch.setattr(sys, "argv", ["GenFill.pyw", app_mod.autostart.BACKGROUND])
    bar = SimpleNamespace(server=SimpleNamespace(close=lambda: events.append("closed")),
                          quit=lambda: events.append("quit"), say=lambda *a, **k: None)
    App.restart(bar)
    assert events == ["closed", "quit"]
    assert started == [[sys.executable, "GenFill.pyw"]]  # shows itself, not a hidden Windows start
