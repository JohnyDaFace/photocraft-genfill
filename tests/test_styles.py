"""Styles: a sentence added to Create and whole-image edit prompts while one is on."""
import json
import threading
from dataclasses import replace

import pytest

from genfill import styles
from genfill.pipeline import UserFacingError, create, generate
from test_pipeline import FakeComfy, FakePC, _doc, _settings


@pytest.fixture(autouse=True)
def own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(styles, "STYLES_FILE", tmp_path / "styles.json")


def test_a_blank_file_is_written_and_the_built_ins_come_from_the_app(tmp_path):
    assert styles.load() == styles.DEFAULT_STYLES
    data = json.loads((tmp_path / "styles.json").read_text(encoding="utf-8"))
    assert data["styles"] == [] and data["hidden"] == [] and "help" in data


def test_your_styles_changes_and_hidden_ones(tmp_path):
    f = tmp_path / "styles.json"
    f.write_text(json.dumps({"styles": [{"name": "Mine", "text": "Pastel colours."}, {"name": "", "text": "x"},
                                        {"name": "Anime", "text": "My anime."}], "hidden": ["Surreal"]}))
    got = styles.load()
    assert got[-1] == ("Mine", "Pastel colours.") and dict(got)["Anime"] == "My anime."
    assert "Surreal" not in dict(got) and len(got) == len(styles.DEFAULT_STYLES)  # -1 hidden, +1 yours


def test_the_first_file_format_and_broken_files(tmp_path):
    f = tmp_path / "styles.json"
    f.write_text(json.dumps([{"name": "Mine", "text": "Pastel colours."}]))  # a plain list
    assert styles.load()[-1] == ("Mine", "Pastel colours.")
    f.write_text("not json")
    assert styles.load() == styles.DEFAULT_STYLES


def test_styled_adds_the_sentence():
    text = dict(styles.DEFAULT_STYLES)["Cinematic"]
    assert styles.styled("a fox in snow", "Cinematic") == text + " a fox in snow"
    assert styles.styled("a fox", "") == "a fox" and styles.styled("", "Cinematic") == ""
    assert styles.styled("", "Cinematic", restyle=True) == text + " " + styles.KEEP
    assert styles.styled("a fox", "Gone") == "a fox"  # a style deleted from the file: nothing added


def _on(tmp_path, name="Movie poster"):
    return replace(_settings(tmp_path), style=name)


def test_create_and_whole_image_edits_get_the_style(tmp_path):
    text = dict(styles.DEFAULT_STYLES)["Movie poster"]
    r = create("16:9", "a lighthouse", _on(tmp_path), lambda s: None, lambda f: None, threading.Event(),
               comfy=FakeComfy(), count=1)
    assert r.job.prompt.startswith(text) and r.layer == "Create – a lighthouse"  # the name stays yours
    r = generate("make it night", _on(tmp_path), lambda s: None, lambda f: None, threading.Event(),
                 pc=FakePC(_doc(), None), comfy=FakeComfy(), count=1)
    assert r.job.prompt.startswith(text) and r.layer == "Edit – make it night"


def test_fills_presets_and_remove_dont(tmp_path):
    def run(**kw):
        sel = kw.pop("selection", None)
        return generate(kw.pop("prompt", "a boat"), _on(tmp_path), lambda s: None, lambda f: None,
                        threading.Event(), pc=FakePC(_doc(), sel), comfy=FakeComfy(), count=1, **kw).job.prompt
    assert run(selection=(0, 300, 500, 600)) == "a boat"                   # a fill must match the photo
    assert run(prompt="Restore this old photo.", name="Restore old photo") == "Restore this old photo."
    assert "poster" not in run(selection=(0, 300, 500, 600), remove=True, prompt="the boat")


def test_every_built_in_style_ships_a_thumbnail():
    from genfill import style_thumbs
    for name, text in [(style_thumbs.OFF, "")] + styles.DEFAULT_STYLES:
        assert style_thumbs.find(name, text, (style_thumbs.BUILTIN_DIR,)) is not None, name


def test_an_edited_style_needs_a_new_thumbnail(tmp_path):
    from genfill import style_thumbs
    name, text = styles.DEFAULT_STYLES[0]
    assert style_thumbs.file_name(name, text) != style_thumbs.file_name(name, text + " More.")
    (tmp_path / "styles.json").write_text(json.dumps([{"name": name, "text": text + " More."}]))
    assert style_thumbs.missing((style_thumbs.BUILTIN_DIR,)) == [(name, text + " More.")]


def test_the_panel_has_a_tab_per_kind_each_starting_with_off():
    from genfill.style_panel import groups, tab_of
    every = styles.DEFAULT_STYLES + [("Mine", "Pastel.")]
    got = groups(every)
    assert [t for t, _ in got] == ["Photo", "Portrait", "Product", "Art", "Design", "Eras", "Yours", "Angles"]
    assert all(g[0][0] == "Off" for _, g in got)
    tab = dict(got)
    assert "Rembrandt light" in dict(tab["Portrait"]) and "Studio white" in dict(tab["Product"])
    assert "Anime" in dict(tab["Art"]) and "1970s" in dict(tab["Eras"])
    assert tab["Yours"][1] == ("Mine", "Pastel.")
    assert tab_of("Luxury", every) == "Product" and tab_of("", every) == "Photo" and tab_of("Mine", every) == "Yours"
    assert tab_of("Split light", every) == "Portrait"
    assert tab_of("Bauhaus", every) == "Design" and tab_of("Gouache", every) == "Art"
    assert tab_of("Film noir", every) == "Photo" and tab_of("Retro-futurism", every) == "Eras"
    assert [t for t, _ in groups(styles.DEFAULT_STYLES)] == ["Photo", "Portrait", "Product", "Art", "Design", "Eras",
                                                             "Angles"]
    assert tab_of("Low angle", every) == "Photo"  # the panel opens on the style's tab, never on Angles


def test_restyling_a_product_keeps_the_product_not_the_framing():
    assert styles.styled("", "Studio white", restyle=True).endswith(styles.KEEP_PRODUCT)
    assert styles.styled("", "Anime", restyle=True).endswith(styles.KEEP)


def test_each_kind_of_style_keeps_what_matters_when_restyling():
    assert styles.keep_for("Food overhead") == styles.KEEP_FOOD
    assert styles.keep_for("Interior bright") == styles.KEEP_ROOM
    assert styles.keep_for("Toy figure") == styles.KEEP_FIGURE
    assert styles.keep_for("Fashion editorial") == styles.KEEP_FASHION
    assert styles.keep_for("1920s") == styles.KEEP_ERA      # the clothes may change, the people stay
    assert styles.keep_for("Luxury") == styles.KEEP_PRODUCT and styles.keep_for("Cinematic") == styles.KEEP
    assert styles.keep_for("Mine") == styles.KEEP           # your own styles: the default


def test_every_style_is_short_enough_to_leave_room_for_the_prompt():
    for name, text in styles.DEFAULT_STYLES:
        assert 12 <= len(text.split()) <= 45, name     # BFL: about 40-70 words for the whole prompt
    names = [n for n, _ in styles.DEFAULT_STYLES]
    assert len(names) == len(set(names))


def test_an_era_on_a_photo_changes_only_the_era():
    text = styles.text_for("1970s")
    assert styles.styled("a man in a park", "1970s") == f"{text} a man in a park"       # Create: as written
    edit = styles.styled("make it night", "1970s", edit=True)
    assert edit.startswith("Change only the era of this photo and add nobody") and edit.endswith("make it night")
    assert styles.styled("", "1970s", restyle=True).endswith(styles.KEEP_ERA)
    for name in styles.ERAS:
        assert " dresses" not in styles.text_for(name) and " suits" not in styles.text_for(name)


def test_a_portrait_style_keeps_the_face():
    assert styles.PORTRAIT <= {n for n, _ in styles.DEFAULT_STYLES}
    for name in styles.PORTRAIT:
        assert styles.keep_for(name) == styles.KEEP_FACE
        assert styles.styled("", name, restyle=True).endswith(styles.KEEP_FACE)
    assert "Portrait" not in dict(styles.DEFAULT_STYLES)  # became Editorial, in the Portrait tab


def test_a_model_style_keeps_the_product_and_adds_one_woman():
    assert styles.MODEL <= styles.PRODUCT
    for name in styles.MODEL:
        assert styles.keep_for(name) == styles.KEEP_MODEL
        assert styles.styled("", name, restyle=True).endswith(styles.KEEP_MODEL)
    assert "exactly the same" in styles.KEEP_MODEL and "one woman" in styles.KEEP_MODEL
    assert styles.keep_for("Studio white") == styles.KEEP_PRODUCT   # the plain product styles add nobody


def test_an_angle_goes_first_and_stacks_on_the_style():
    low, cine = styles.angle_text("Low angle"), styles.text_for("Cinematic")
    assert styles.styled("a red car", "Cinematic", angle="Low angle") == f"{low} {cine} a red car"
    assert styles.styled("a red car", "", angle="Low angle") == f"{low} a red car"       # an angle on its own
    assert styles.styled("a red car", "Cinematic", angle="") == f"{cine} a red car"      # no angle: as before
    assert styles.styled("", "Cinematic", angle="Low angle") == ""                     # Create needs a prompt


def test_restyling_with_an_angle_lets_the_framing_change():
    assert styles.styled("", "", restyle=True, angle="Wide shot").endswith(styles.KEEP_ANGLE)
    assert styles.styled("", "Cinematic", restyle=True, angle="Wide shot").endswith(styles.KEEP_ANGLE)
    assert styles.styled("", "1970s", restyle=True, edit=True, angle="Wide shot").endswith(styles.KEEP_ANGLE)
    # these already let the angle change, and keep what matters more
    assert styles.styled("", "Studio white", restyle=True, angle="Low angle").endswith(styles.KEEP_PRODUCT)
    assert styles.styled("", "Editorial", restyle=True, angle="Low angle").endswith(styles.KEEP_FACE)


def test_an_angle_alone_turns_on_an_edit_with_nothing_typed(tmp_path):
    from dataclasses import replace
    from genfill.settings import Settings
    s = replace(Settings(), style="", angle="High angle")
    assert bool(styles.text_for(s.style) or styles.angle_text(s.angle))
    assert styles.angle_text("Nope") == "" and styles.angle_text("") == ""


def test_every_angle_has_a_thumbnail_and_a_short_sentence():
    from genfill import style_thumbs
    names = {n for n, _ in styles.DEFAULT_STYLES}
    for name, text in styles.ANGLES:
        assert name not in names
        assert style_thumbs.find(name, text, (style_thumbs.BUILTIN_DIR,)) is not None, name
        assert 8 <= len(text.split()) <= 25, name


def test_clicking_the_style_that_is_on_turns_it_off():
    from genfill.style_panel import toggled
    assert toggled("Cinematic", "") == "Cinematic"
    assert toggled("Cinematic", "Film photo") == "Cinematic"   # another style: switches to this one
    assert toggled("Cinematic", "Cinematic") == ""
    assert toggled("", "Cinematic") == "" and toggled("", "") == ""   # Off stays Off


def test_style_tiles_fit_the_screen():
    from genfill.style_panel import CHROME_H, COLS, GAP, LABEL_H, MAX_ROWS, TILE_MAX, TILE_MIN, grid_size
    row = TILE_MAX + LABEL_H + GAP
    assert grid_size(1920, 1000, 27) == (TILE_MAX, COLS, MAX_ROWS * row)  # full size, 6 to a row; the rest scrolls
    assert grid_size(1920, 1000, 10)[2] == 2 * row                        # a short tab: no empty rows
    assert grid_size(1920, 600, 27)[2] == 600 - CHROME_H                  # a short screen: what fits (a part row)
    assert grid_size(1920, 300, 27)[2] == 2 * row                         # but always 2 rows
    assert grid_size(600, 1000, 27)[0] < TILE_MAX                   # a narrow monitor: smaller tiles…
    assert grid_size(300, 1000, 27)[0] == TILE_MIN                  # …but never smaller than TILE_MIN


def test_style_tiles_grow_with_display_scaling(monkeypatch):
    from genfill import dpi, style_panel
    at_100 = style_panel.grid_size(1920, 1000, 40)
    monkeypatch.setattr(dpi, "SCALE", 1.5)
    side, cols, view = style_panel.grid_size(round(1920 * 1.5), 1500, 40)
    assert side == round(at_100[0] * 1.5) and cols == at_100[1] and view == round(at_100[2] * 1.5)  # just sharper
