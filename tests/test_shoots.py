from genfill import shoots
from genfill.shoots import Shot, Shoot


def test_built_ins_are_the_agreed_eight_in_order(tmp_path):
    names = [(s.kind, s.name) for s in shoots.load(tmp_path / "shoots.json")]
    assert names == [("product", "Clean studio white"), ("product", "Skincare morning light"),
                     ("product", "Luxury dark"), ("product", "Kitchen / lifestyle table"),
                     ("product", "Outdoor summer"), ("people", "Studio headshots"),
                     ("people", "Loft editorial"), ("people", "Street / city casual")]


def test_each_kind_has_twelve_distinct_shots_and_the_hero_comes_first():
    for lst in (shoots.PRODUCT_SHOTS, shoots.PEOPLE_SHOTS):
        assert len(lst) == 12 and len({s.name for s in lst}) == 12 and lst[0].name == "hero"


def test_product_shots_avoid_angles_that_break_labels():
    text = " ".join(s.text.lower() for s in shoots.PRODUCT_SHOTS)
    for bad in ("top-down", "overhead", "from behind", "bird", "high angle"):
        assert bad not in text


def test_a_blank_file_is_written_and_yours_override_add_and_hide(tmp_path):
    f = tmp_path / "shoots.json"
    shoots.load(f)
    assert f.exists()
    f.write_text('{"shoots": [{"name": "Luxury dark", "kind": "product", "set": "MINE"},'
                 ' {"name": "Beach", "kind": "people", "set": "sand"}], "hidden": ["Outdoor summer"]}')
    got = {s.name: s for s in shoots.load(f)}
    assert got["Luxury dark"].set == "MINE" and "Beach" in got and "Outdoor summer" not in got


def test_a_broken_file_gives_the_built_ins(tmp_path):
    f = tmp_path / "shoots.json"
    for bad in ("{not json", "[]", '{"shoots": [{"name": "x"}], "hidden": 5}'):
        f.write_text(bad)
        assert [s.name for s in shoots.load(f)][:1] == ["Clean studio white"]


def test_hiding_everything_gives_an_empty_list_not_an_error(tmp_path):
    f = tmp_path / "shoots.json"
    f.write_text('{"hidden": %s}' % str([s.name for s in shoots.DEFAULT_SHOOTS]).replace("'", '"'))
    assert shoots.load(f) == []


def test_own_shot_list_wins_else_the_shared_one():
    own = Shoot("H", "people", "studio", (Shot("hero", "close"),))
    assert shoots.shots_for(own) == own.shots
    assert shoots.shots_for(Shoot("P", "product", "x")) == shoots.PRODUCT_SHOTS


def test_prompts_are_the_spec_wording():
    assert shoots.hero_prompt("product", "dark marble", "eye level") == (
        "A professional photoshoot photo of the product from image 1. The shoot: dark marble. This shot: eye level. "
        "Keep the product exactly as in image 1: the same shape, colours, materials and the exact same label text "
        "and logo, letter for letter.")
    assert shoots.follow_prompt("people", "laughing").startswith(
        "Another photo from the same professional photoshoot as image 2: the same set, lighting and colour grade, "
        "of the person from image 1. This shot: laughing. Keep the exact same person as in image 1:")
