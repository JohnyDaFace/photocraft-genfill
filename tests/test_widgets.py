import tkinter as tk

import pytest

from genfill import theme
from genfill.widgets import ICONS, PopupMenu, icon_image, shape_image


def test_every_icon_renders_at_bar_and_menu_sizes():
    for name in ICONS:
        for size in (12, 16, 18):
            im = icon_image(name, size, "#dedede")
            assert im.size == (size, size)
            assert im.getbbox() is not None, name  # something was drawn


def test_shape_is_transparent_outside_the_rounded_corners():
    im = shape_image(40, 20, 10, "#378ef0", None)
    assert im.getpixel((0, 0))[3] == 0
    assert im.getpixel((20, 10))[3] == 255


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
