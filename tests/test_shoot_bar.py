"""The Photoshoot menu and the Reshoot button on the bar, with the bar faked."""
from types import SimpleNamespace

from genfill import shoots
from genfill.app import App
from genfill.photoshoot import SHOT_COUNTS
from genfill.settings import Settings


def _bar(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(shoots, "SHOOTS_FILE", tmp_path / "shoots.json")
    menus = []
    bar = SimpleNamespace(settings=Settings(), shoot_tagged=False, _edit_shoots=print, _pick_shoot=print,
                          _set_shoot_count=print, _set_create_choice=print, arranged=0, **kw)
    bar._menu = lambda anchor, items, right=False: menus.append(items)
    bar._arrange = lambda: setattr(bar, "arranged", bar.arranged + 1)
    bar.menus = menus
    return bar


def test_menu_lists_groups_counts_shapes_and_edit(tmp_path, monkeypatch):
    bar = _bar(tmp_path, monkeypatch)
    items = App.shoot_items(bar)
    subs = {i[1]: i[2] for i in items if i[0] == "sub"}
    assert [i[1] for i in subs["Product"]][0] == "Clean studio white"
    assert "Studio headshots" in [i[1] for i in subs["People"]]
    checks = [i for i in items if i[0] == "check"]
    assert [i[1] for i in checks] == [f"{n} shots" for n in SHOT_COUNTS]
    assert [i[3] for i in checks] == [n == bar.settings.shoot_count for n in SHOT_COUNTS]
    assert items[-1][1] == "Edit shoots…"


def test_picking_a_shoot_starts_the_job_with_the_photo(tmp_path, monkeypatch):
    started = []
    bar = _bar(tmp_path, monkeypatch, reference=None, root=None,
               start=lambda job, **kw: started.append((job, kw)))
    monkeypatch.setattr("genfill.app.filedialog.askopenfilename", lambda **kw: "C:/p/photo.png")
    App._pick_shoot(bar, "Luxury dark")
    assert started == [("photoshoot", {"choice": "Luxury dark", "what": "C:/p/photo.png"})]
    started.clear()
    monkeypatch.setattr("genfill.app.filedialog.askopenfilename", lambda **kw: "")
    App._pick_shoot(bar, "Luxury dark")
    assert started == []


def test_reshoot_button_starts_the_reshoot_job():
    started = []
    App.reshoot_shot(SimpleNamespace(start=started.append))
    assert started == ["reshoot"]
