import tkinter as tk

import pytest

from genfill import theme
from genfill.widgets import ICONS, BarButton, PopupMenu, Tooltip, icon_image, shape_image


def test_every_icon_renders_at_bar_and_menu_sizes():
    for name in ICONS:
        for size in (12, 16, 18):
            im = icon_image(name, size, "#dedede")
            assert im.size == (size, size)
            assert im.getbbox() is not None, name  # something was drawn


def test_bar_uses_filled_symbols_for_its_main_actions(root, monkeypatch):
    from genfill import widgets
    from genfill.widgets import solid_bar_icon_image

    denser = 0
    for name in ("target", "eraser", "wand", "expand", "create", "swap", "camera",
                 "palette", "image", "stack", "spark"):
        solid = solid_bar_icon_image(name, 32, "#dedede")
        outline = icon_image(name, 32, "#dedede")
        solid_pixels = sum(solid.getchannel("A").histogram()[129:])
        outline_pixels = sum(outline.getchannel("A").histogram()[129:])
        denser += solid_pixels > outline_pixels
        assert solid.tobytes() != outline.tobytes(), name
    assert denser >= 8

    rendered = []
    monkeypatch.setattr(widgets, "solid_bar_icon_image",
                        lambda name, size, color: (rendered.append(name), solid_bar_icon_image(name, size, color))[1])
    button = BarButton(root, theme.PALETTES["Pro"], theme.fonts(root)["body"], icon="target")
    assert button._imgs and rendered == ["target"]


def test_shape_is_transparent_outside_the_rounded_corners():
    im = shape_image(40, 20, 10, "#378ef0", None)
    assert im.getpixel((0, 0))[3] == 0
    assert im.getpixel((20, 10))[3] == 255


def test_borderless_bar_has_direct_minimize_button(root, monkeypatch):
    from genfill import app
    from genfill.app import App
    from genfill.settings import Settings

    monkeypatch.setattr(app, "BORDERLESS", True)
    monkeypatch.setattr(app, "StatusPill", lambda *args: None)
    monkeypatch.setattr(App, "_show_reference", lambda self: None)
    monkeypatch.setattr(App, "_show_style", lambda self: None)
    monkeypatch.setattr(App, "_arrange", lambda self: None)
    bar = App.__new__(App)
    bar.root, bar.settings, bar.theme_name = root, Settings(), "Pro"
    bar.fonts, bar.frame, bar.pill = theme.fonts(root), None, None
    bar._prompt_text, bar.update_info = "", None
    bar.build()

    assert bar.minimize_btn.winfo_manager() == "pack"
    assert bar.minimize_btn.icon == "minimize"
    minimized = []
    monkeypatch.setattr(app, "minimize", lambda win: minimized.append(win))
    bar.minimize_btn.command()
    assert minimized == [root]


def test_action_buttons_are_compact_icons_with_named_tooltips(root, monkeypatch):
    from genfill import app
    from genfill.app import App
    from genfill.settings import Settings

    monkeypatch.setattr(app, "StatusPill", lambda *args: None)
    monkeypatch.setattr(App, "_show_reference", lambda self: None)
    monkeypatch.setattr(App, "_show_style", lambda self: None)
    bar = App.__new__(App)
    bar.root, bar.settings, bar.theme_name = root, Settings(), "Pro"
    bar.fonts, bar.frame, bar.pill = theme.fonts(root), None, None
    bar._prompt_text, bar.update_info = "", None
    bar.mode, bar.last_results, bar.tagged = "image", None, False
    bar.picker, bar.busy, bar.shoot_tagged = None, False, False
    bar.build()

    for name, label in (("sel_btn", "Select"), ("rm_btn", "Remove"), ("fix_btn", "Fix"),
                        ("size_btn", "Resize"), ("create_btn", "Create"),
                        ("others_btn", "Other results"), ("reshoot_btn", "Reshoot")):
        button = getattr(bar, name)
        assert button.kind == "icon" and button.text == "" and not button.chevron
        assert button.w == button.h
        assert button.tip.text.startswith(label)

    bar.mode = "empty"
    bar._arrange()
    assert bar.create_btn.kind == "outline"
    assert bar.create_btn.text == "Size"
    assert bar.create_btn.w > bar.create_btn.h
    assert bar.settings.create_choice in bar.create_btn.tip.text
    bar.mode = "image"
    bar._arrange()
    assert bar.create_btn.kind == "icon" and bar.create_btn.text == ""


def test_bar_has_three_dot_grip_generate_divider_and_vertical_controls(root, monkeypatch):
    from genfill import app
    from genfill.app import App
    from genfill.settings import Settings

    monkeypatch.setattr(app, "BORDERLESS", True)
    monkeypatch.setattr(app, "StatusPill", lambda *args: None)
    monkeypatch.setattr(App, "_show_reference", lambda self: None)
    monkeypatch.setattr(App, "_show_style", lambda self: None)
    bar = App.__new__(App)
    bar.root, bar.settings, bar.theme_name = root, Settings(), "Pro"
    bar.fonts, bar.frame, bar.pill = theme.fonts(root), None, None
    bar._prompt_text, bar.update_info = "", None
    bar.mode, bar.last_results, bar.tagged = "image", None, False
    bar.picker, bar.busy, bar.shoot_tagged = None, False, False
    bar.build()
    root.deiconify()
    root.update()

    assert len(bar.grip.find_all()) == 3
    assert bar.minimize_btn.master == bar.more_btn.master
    assert bar.minimize_btn.winfo_rooty() < bar.more_btn.winfo_rooty()
    row = bar.gen_btn.master
    packed = row.pack_slaves()
    assert packed.index(bar.generate_sep) == packed.index(bar.gen_btn) + 1

    bar.mode = "empty"
    bar._arrange()
    assert bar.generate_sep.winfo_manager() == "pack"


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def test_placeholder_is_never_taken_as_the_prompt(root):
    from genfill.app import PromptField
    f = PromptField(root, theme.PALETTES["Pro"], theme.fonts(root)["body"], 300, lambda: None, lambda: None)
    f.set_placeholder("Describe a change…")
    assert f.get() == ""
    f.set("a red boat")
    assert f.get() == "a red boat"
    f.set_placeholder("Describe what to fill in…")  # changing mode keeps typed text
    assert f.get() == "a red boat"
    f.set("")
    assert f.get() == ""


def test_menu_runs_the_choice_and_closes(root):
    chosen = []
    anchor = tk.Frame(root)
    m = PopupMenu(anchor, [("header", "Presets"), ("item", "Sky", lambda: chosen.append("sky"))],
                  theme.PALETTES["Pro"], theme.fonts(root)["body"])
    m._choose(lambda: chosen.append("sky"))
    assert chosen == ["sky"]
    assert not m.winfo_exists()


def test_menu_button_is_a_wide_pill_that_runs_its_command(root):
    chosen = []
    anchor = tk.Frame(root)
    m = PopupMenu(anchor, [("item", "Settings…", None), ("button", "Support me on Ko-fi",
                                                         lambda: chosen.append("kofi"), "coffee", "#29abe0")],
                  theme.PALETTES["Pro"], theme.fonts(root)["body"])
    root.update()
    button = [w for w in m.winfo_children()[0].winfo_children() if isinstance(w, tk.Canvas)][0]
    assert button.find_all()  # the pill, the cup and the words are drawn
    button.event_generate("<ButtonRelease-1>")
    root.update()
    assert chosen == ["kofi"]
    assert not m.winfo_exists()


def test_popup_is_positioned_before_it_is_mapped(root, monkeypatch):
    states = []
    original = PopupMenu.geometry

    def track(self, value=None):
        if value is not None:
            states.append(self.state())
        return original(self, value)

    monkeypatch.setattr(PopupMenu, "geometry", track)
    anchor = tk.Frame(root)
    m = PopupMenu(anchor, [("item", "One", None)], theme.PALETTES["Pro"], theme.fonts(root)["body"])
    assert states == ["withdrawn"]
    assert m.state() == "normal"
    m.close_all()


def test_tooltip_is_positioned_before_it_is_mapped(root, monkeypatch):
    root.deiconify()
    button = tk.Button(root, text="Help")
    button.pack()
    root.update()
    states = []
    original = tk.Toplevel.geometry

    def track(self, value=None):
        if value is not None:
            states.append(self.state())
        return original(self, value)

    monkeypatch.setattr(tk.Toplevel, "geometry", track)
    tip = Tooltip(button, "Useful tip")
    tip._show()
    assert states == ["withdrawn"]
    assert tip._win.state() == "normal"
    tip._hide()


def test_tooltip_stays_on_monitor_left_of_primary(root):
    root.geometry("240x60+-1000+550")
    root.deiconify()
    button = tk.Button(root, text="Help")
    button.pack()
    root.update()
    if button.winfo_rootx() >= 0:
        pytest.skip("The display has no monitor left of the primary, or constrains windows to the primary")
    from genfill.widgets import monitor_area
    if monitor_area(button)[0] >= 0:
        pytest.skip("The button is outside any monitor left of the primary")

    tip = Tooltip(button, "Useful tip")
    tip._show()
    assert tip._win.winfo_rootx() < 0
    tip._hide()


def test_anchor_click_closes_menu_without_reopening_it(root, monkeypatch):
    from types import SimpleNamespace
    from genfill import app

    anchor = tk.Frame(root, width=90, height=25)
    anchor.pack()
    root.deiconify()
    root.update()
    m = PopupMenu(anchor, [("item", "One", None)], theme.PALETTES["Pro"], theme.fonts(root)["body"])
    m._click(SimpleNamespace(x_root=anchor.winfo_rootx() + 1, y_root=anchor.winfo_rooty() + 1))
    assert not m.winfo_exists()
    assert anchor._skip_open is True

    opened = []
    monkeypatch.setattr(app, "PopupMenu", lambda *args, **kwargs: opened.append(args))
    fake_app = SimpleNamespace(p=theme.PALETTES["Pro"], fonts={"body": theme.fonts(root)["body"]})
    app.App._menu(fake_app, anchor, [("item", "One", None)])
    assert not opened and anchor._skip_open is False
    app.App._menu(fake_app, anchor, [("item", "One", None)])
    assert len(opened) == 1
