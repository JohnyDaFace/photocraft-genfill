# Photoshoot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A **Photoshoot** button on the bar that turns one reference photo of a product or a person into 4/8/12
matching shots of one shoot, opened as layers of a new PhotoCraft document, with **Reshoot** to redo any one shot.

**Architecture:** Shoot definitions are data (`genfill/shoots.py`, like `styles.py`). A run cuts the subject out
with SAM 3, makes a hero shot, then makes every other shot with the hero as a second reference
(`comfy.build_create_workflow`), keeps what Reshoot needs on disk (`genfill/shoot_store.py`) and places the shots
through the existing `PhotoCraftClient` (`genfill/photoshoot.py`). Reshoot returns ordinary `pipeline.Results`
so the existing `swap()` and *Choose a result* window do the placing.

**Tech Stack:** Python 3.12, Pillow, Tkinter, pytest; ComfyUI with FLUX.2 klein + SAM 3 (already used).

**Spec:** `docs/superpowers/specs/2026-11-09-photoshoot-design.md`

## Global Constraints

- No new model downloads and no new ComfyUI custom nodes: only FLUX.2 klein (`build_create_workflow`) and SAM 3.
- No new PhotoCraft step: only `open_document`, `place_layer(masked=False)`, `rename layer`, `layer metadata`,
  `replace_layer`, all already covered by `compat.py`.
- Never change the reference photo or any open document; a run only creates a new document.
- Default shot count **8**; allowed **4 / 8 / 12** (`Settings.shoot_count`).
- Shape: the Create shapes (`pipeline.CREATE_CHOICES`), size from `pipeline.create_size(shape, settings.megapixels)`;
  default `"1:1"`, shared with Create through `Settings.create_choice`.
- Layer names: `f"Shot {n} – {shot.name}"` (en dash, n from 1).
- Styles and angles (`settings.style`, `settings.angle`) are never applied to a photoshoot.
- Keep the last `settings.keep_results` (50) runs on disk.
- Reshoot uses the **current** models from Settings; runs store no model names.
- Hero prompt (exact): `"A professional photoshoot photo of the {noun} from image 1. The shoot: {set}. This shot: {shot}. {keep}"`
- Follow prompt (exact): `"Another photo from the same professional photoshoot as image 2: the same set, lighting and colour grade, of the {noun} from image 1. This shot: {shot}. {keep}"`
- `{noun}` is `product` or `person`. `{keep}` for products (exact): `"Keep the product exactly as in image 1: the same shape, colours, materials and the exact same label text and logo, letter for letter."`; for people (exact): `"Keep the exact same person as in image 1: the same face, facial features, eye colour, skin, hair and beard."`
- Built-in shoots (exact names): Product: `Clean studio white`, `Skincare morning light`, `Luxury dark`,
  `Kitchen / lifestyle table`, `Outdoor summer`; People: `Studio headshots`, `Loft editorial`, `Street / city casual`.

## Review Focus

- **SAM 3 finds no product in the reference photo** → the whole photo is used as the subject and the status says
  so; the run does not fail. (Test in Task 4.)
- **`shoots.json` is broken, empty, or hides every built-in** → the menu still lists what is valid; an unreadable
  file means the built-ins only, never a crash. (Test in Task 1.)
- **The user resized or cropped the shoot document, then clicks Reshoot** → refused with a plain message, nothing
  changed. (Test in Task 5.)
- **ComfyUI errors on one shot mid-run** (it stalled once in the spike) → that shot is retried once, then skipped;
  the other shots still arrive and the status names the missing ones; a failing hero stops the run with a plain
  message. (Test in Task 4.)
- **12 shots vs the exchange folder keeping only 10 PNGs** → all 12 layers are placed from files that still exist.
  (Test in Task 4.)

---

## File Structure

- Create `genfill/shoots.py`: `Shot`, `Shoot`, built-in shoots and the two 12-shot lists, loading `shoots.json`,
  prompt building.
- Create `genfill/shoot_store.py`: `ShootRun`, saving/loading/pruning runs on disk, the layer tag.
- Create `genfill/photoshoot.py`: `run_photoshoot()` (cut-out, shots, placing) and `reshoot()`.
- Modify `genfill/pipeline.py`: `Results.tag`, `_keep()` honours it.
- Modify `genfill/photocraft_client.py`: `rename_layer()`.
- Modify `genfill/settings.py`: `shoot_count`, `shoots_dir`.
- Modify `genfill/widgets.py`: `"camera"` icon.
- Modify `genfill/app.py`: Photoshoot button and menu, the two jobs, Reshoot button, watch for the shoot tag.
- Tests: `tests/test_shoots.py`, `tests/test_shoot_store.py`, `tests/test_photoshoot.py`; additions to
  `tests/test_swap.py`, `tests/test_settings.py`.
- Delete at the end: `tools/try_photoshoot.py`, `tools/try_make_product.py`. Update `README.md`.

---

### Task 1: Shoot definitions and prompts (`genfill/shoots.py`)

**Files:**
- Create: `genfill/shoots.py`
- Test: `tests/test_shoots.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Shot: name: str; text: str`
  - `@dataclass(frozen=True) class Shoot: name: str; kind: str  # "product" | "people"; set: str; shots: tuple[Shot, ...] = (); subject: str = ""`
  - `PRODUCT_SHOTS: tuple[Shot, ...]` and `PEOPLE_SHOTS: tuple[Shot, ...]`, 12 each
  - `DEFAULT_SHOOTS: tuple[Shoot, ...]` (the 8 built-ins, in the Global Constraints order)
  - `SHOOTS_FILE: Path` = `styles.CONFIG_DIR / "shoots.json"`
  - `load(path: Path | None = None) -> list[Shoot]`
  - `by_name(name: str, path: Path | None = None) -> Shoot | None`
  - `shots_for(shoot: Shoot) -> tuple[Shot, ...]` (the shoot's own `shots`, else the shared list for its kind)
  - `hero_prompt(kind: str, set_text: str, shot_text: str) -> str`
  - `follow_prompt(kind: str, shot_text: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_shoots.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'genfill.shoots'`

- [ ] **Step 3: Implement `genfill/shoots.py`**

Mirror `styles.py`: `HELP` text, `_write_blank(path)` writes `{"help": HELP, "shoots": [], "hidden": []}`,
`_yours(path) -> tuple[list[Shoot], set[str]]` ignores entries without a non-empty `name`, a `kind` in
(`product`, `people`) and a non-empty `set` (a user shoot may omit `kind` when it overrides a built-in of the
same name: it keeps the built-in's kind and shots); `load` = built-ins with your changes minus hidden, then yours.
A user entry's optional `shots` is a list of `{"name","text"}`; optional `subject` is the SAM 3 word.

Shot texts: write them from the spike's lists (`tools/try_photoshoot.py`) and the spec's rules — products: label
facing the camera in every shot, include `hero`, `label close-up`, `detail macro`, `in hand`, `wide set`,
`gentle low angle`, `lifestyle props`, `three-quarter turn`; people: knees-up or closer, each shot names a concrete
spot in the location ("by the window", "against the wall", "seated at a table") and a different pose/expression.
`set` texts: one or two sentences each (the spike's two sets are good models). Task 7 tunes them with real runs.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_shoots.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add genfill/shoots.py tests/test_shoots.py
git commit -m "feat(photoshoot): shoot definitions, shoots.json and prompts"
```

---

### Task 2: Keeping runs on disk and the layer tag (`genfill/shoot_store.py`, settings)

**Files:**
- Create: `genfill/shoot_store.py`
- Modify: `genfill/settings.py:56-91` (add two fields)
- Test: `tests/test_shoot_store.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing from Task 1 (a run stores plain strings so a later edit of `shoots.json` can't change it).
- Produces:
  - `@dataclass class ShootRun: shoot: str; kind: str; set_text: str; shots: list[tuple[str, str]]; shape: str; size: tuple[int, int]; created: float = 0.0`
  - `save_run(root: Path, run: ShootRun, subject: Image.Image, hero: Image.Image, keep: int = 50) -> str` (run id, 16 hex chars; writes `shoot.json`, `subject.png`, `hero.png`; prunes)
  - `load_run(root: Path, run_id: str) -> tuple[ShootRun, Image.Image, Image.Image] | None`
  - `tag_packet(run_id: str, index: int) -> str` and `tag_of(packet: str | None) -> tuple[str, int] | None` (index is 1-based)
  - `Settings.shoot_count: int = 8`, `Settings.shoots_dir: str = str(APP_DIR / "shoots")`

- [ ] **Step 1: Write the failing tests**

```python
from PIL import Image
from genfill import results_store, shoot_store
from genfill.shoot_store import ShootRun

def _run(): return ShootRun("Luxury dark", "product", "marble", [("hero", "eye level"), ("macro", "cap")],
                            "1:1", (1440, 1440))

def test_a_run_round_trips(tmp_path):
    rid = shoot_store.save_run(tmp_path, _run(), Image.new("RGB", (64, 64), "red"), Image.new("RGB", (80, 80), "blue"))
    run, subject, hero = shoot_store.load_run(tmp_path, rid)
    assert run.shots == [("hero", "eye level"), ("macro", "cap")] and run.size == (1440, 1440)
    assert subject.size == (64, 64) and hero.getpixel((0, 0)) == (0, 0, 255)

def test_missing_or_broken_runs_load_as_none(tmp_path):
    assert shoot_store.load_run(tmp_path, "0123456789abcdef") is None
    rid = shoot_store.save_run(tmp_path, _run(), Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)))
    (tmp_path / rid / "shoot.json").write_text("{")
    assert shoot_store.load_run(tmp_path, rid) is None

def test_only_the_newest_runs_are_kept(tmp_path):
    ids = [shoot_store.save_run(tmp_path, _run(), Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)), keep=2)
           for _ in range(3)]
    assert shoot_store.load_run(tmp_path, ids[0]) is None and shoot_store.load_run(tmp_path, ids[2]) is not None

def test_the_tag_round_trips_and_is_not_a_results_tag():
    p = shoot_store.tag_packet("0123456789abcdef", 4)
    assert shoot_store.tag_of(p) == ("0123456789abcdef", 4)
    assert results_store.tag_of(p) is None and shoot_store.tag_of(results_store.tag_packet("0123456789abcdef")) is None
    assert shoot_store.tag_of(None) is None
```

In `tests/test_settings.py` add:

```python
def test_shoot_count_defaults_to_eight_and_is_kept(tmp_path):
    f = tmp_path / "s.json"
    s = Settings(); assert s.shoot_count == 8
    s.shoot_count = 12; s.save(f)
    assert Settings.load(f).shoot_count == 12
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_shoot_store.py tests/test_settings.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'genfill.shoot_store'` and `AttributeError: shoot_count`

- [ ] **Step 3: Implement `genfill/shoot_store.py` and the two settings fields**

Follow `results_store.py` (uuid4 hex[:16] ids, `prune` by `shoot.json` mtime). The XMP packet uses the same
`TAG_NS` and wrapper as `results_store.tag_packet`, with attributes `genfill:shoot="<id>" genfill:shot="<n>"`;
`tag_of` uses `re.compile(r'genfill:shoot="([0-9a-f]{8,32})"\s+genfill:shot="(\d+)"')`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_shoot_store.py tests/test_settings.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add genfill/shoot_store.py genfill/settings.py tests/test_shoot_store.py tests/test_settings.py
git commit -m "feat(photoshoot): keep runs on disk and tag shot layers"
```

---

### Task 3: Results can carry their own layer tag; `rename_layer`

**Files:**
- Modify: `genfill/pipeline.py:66-80` (`Results`), `genfill/pipeline.py:783-797` (`_keep`)
- Modify: `genfill/photocraft_client.py` (new method after `place_layer`)
- Test: `tests/test_swap.py`

**Interfaces:**
- Produces:
  - `Results.tag: str | None = None` (last field). When set, `_keep()` writes exactly this packet onto the placed
    layer with `pc.set_layer_metadata(layer, tag)` — for any number of images — and does **not** save a
    lasting-results set.
  - `PhotoCraftClient.rename_layer(name: str, layer: int | None = None) -> None` (best effort: swallows
    `PhotoCraftError`, like the rename inside `place_layer`).

- [ ] **Step 1: Write the failing test** (in `tests/test_swap.py`)

```python
from genfill.pipeline import Placed

def test_a_tagged_result_keeps_its_own_tag_and_saves_no_set(tmp_path):
    pc, r = _results(tmp_path, selection=None, count=2)
    r.tag = '<x:xmpmeta genfill:shoot="0123456789abcdef" genfill:shot="3"/>'
    place(r, 0, _settings(tmp_path), pc=pc)
    assert pc.xmp[r.placed.layer] == r.tag
    swap(r, 1, _settings(tmp_path), pc=pc)
    assert pc.xmp[r.placed.layer] == r.tag and r.saved is None
    assert not (tmp_path / "results").exists()
```

- [ ] **Step 2: Run it to see it fail**

Run: `python -m pytest tests/test_swap.py -q`
Expected: FAIL on `pc.xmp[...] == r.tag`

- [ ] **Step 3: Add `Results.tag`, the `_keep` branch and `rename_layer`**

`rename_layer` executes `self._cmd("rename layer")` with `{"name": name}` plus `{"layer": layer}` when given.

- [ ] **Step 4: Run the whole suite** (this touches `Results`, used everywhere)

Run: `python -m pytest tests -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add genfill/pipeline.py genfill/photocraft_client.py tests/test_swap.py
git commit -m "feat(photoshoot): results with their own layer tag; rename_layer"
```

---

### Task 4: Running a photoshoot (`genfill/photoshoot.py: run_photoshoot`)

**Files:**
- Create: `genfill/photoshoot.py`
- Test: `tests/test_photoshoot.py`

**Interfaces:**
- Consumes: Task 1 (`by_name`, `shots_for`, `hero_prompt`, `follow_prompt`), Task 2 (`ShootRun`, `save_run`,
  `tag_packet`, `Settings.shoot_count`, `Settings.shoots_dir`), Task 3 (`rename_layer`); existing
  `pipeline._clients`, `_exchange`, `_segment`, `_fill_holes`, `_reference_head`, `load_reference`, `create_size`,
  `UserFacingError`; `imaging.working_size`; `comfy.build_create_workflow`, `ComfyError`.
- Produces:
  - `SHOT_COUNTS = (4, 8, 12)`
  - `prepare_subject(photo: Image.Image, shoot: Shoot, settings, status, cancel, comfy) -> Image.Image`
    (1024×1024 RGB cut-out on grey `(128,128,128)`)
  - `run_photoshoot(shoot_name: str, photo_path: str | Path, settings, status, progress, cancel, pc=None, comfy=None) -> str`

Behaviour `run_photoshoot` must have (each line is covered by a test below):
1. Unknown shoot name → `UserFacingError`; unreadable photo → the `UserFacingError` from `load_reference`.
2. Photo enlarged so its long side is ≥ 1024 px before the cut-out. Product: SAM 3 query
   `f"{shoot.subject or 'product'}:1"`; nothing found → the whole (enlarged) photo is the subject and
   `status("Couldn't find the product in the photo; using the whole photo.")`. People: `_reference_head`.
3. `count = settings.shoot_count` if in `SHOT_COUNTS` else 8; never more than `len(shots_for(shoot))`.
4. Size `create_size(settings.create_choice, settings.megapixels)`, generated at `imaging.working_size(W, H,
   settings.megapixels)`, resized back to `(W, H)`.
5. Shot 1: refs `[subject]`, `hero_prompt`. Shots 2..n: refs `[subject, hero]`, `follow_prompt`. New random
   seed each. Status per shot: `f"Shot {i} of {n} – {shot.name}…"`; `progress((i-1)/n)`.
6. A `ComfyError` on a shot is retried once; a second failure: hero → `UserFacingError(f"The first shot failed:
   {e}")`; any other shot → skipped and remembered.
7. `cancel` is checked before each shot. Cancelled with no shot done → `UserFacingError("Cancelled.")`; with
   some done → those are placed.
8. `save_run(Path(settings.shoots_dir), ShootRun(...shots as (name, text) actually planned...), subject, hero,
   settings.keep_results)`.
9. Placing, one shot at a time (exchange folder keeps only 10): shot 1 → `_exchange(settings, "shoot")`, save
   PNG, `pc.compatibility()`, `pc.open_document(rel)`, `pc.rename_layer("Shot 1 – hero")`,
   `pc.set_layer_metadata(pc.active_layer(), tag_packet(run_id, 1))`; shots k ≥ 2 → `_exchange`, save,
   `pc.place_layer(rel, f"Shot {k} – {name}", masked=False)`, then tag `pc.active_layer()` with `k` (k is the
   shot's position in the shot list, so skipped shots leave gaps in the numbering).
10. Returns `f"Photoshoot ready: {done} shots as layers. Select a layer and click Reshoot to redo one."`, with
    `f" Shots {', '.join(missing)} failed."` appended when some were skipped and `" (cancelled)"` when cancelled.
11. `PhotoCraftError`/`ComfyError` elsewhere → `UserFacingError(str(e))`, like `pipeline.generate`.

- [ ] **Step 1: Write the failing tests**

Test fakes at the top of `tests/test_photoshoot.py`:
- `ShootComfy(FakeComfy)` — `check_segment()` returns `[]`; `run(wf)`: if `"detect" in wf` → a mask the size of the
  last upload with a white box at fractions `self.box` (default `(0.3, 0.2, 0.7, 0.8)`; `None` → all black);
  else record `wf["text"]["inputs"]["text"]` in `self.prompts` and the number of `extra*_image` nodes in
  `self.ref_counts`, raise `ComfyError("boom")` while `self.fail_runs` (a set of 1-based create-run numbers)
  contains the current create-run number, else `super().run`.
- `ShootPC(FakePC)` — `__init__(doc, selection, exchange)`; `open_document(rel)` records it, sets
  `self.layers = [1]` and `self.doc` to a blank RGBA of the opened file's size (so `document()` reports the shoot
  document); `rename_layer(name, layer=None)` records `(name, layer)` into `self.renamed`; `place_layer` also
  asserts `(self.exchange / rel).exists()`; `active_layer()` returns `self.active` when set, else
  `self.layers[-1]`.
- `_settings(tmp_path, **kw)` — `Settings(exchange_dir=str(tmp_path), results_dir=str(tmp_path / "results"),
  shoots_dir=str(tmp_path / "shoots"), **kw)`.
- `_shoot(tmp_path, comfy, pc, name="Luxury dark", count=8, cancel_after=None, status=None, **kw)` writes a
  352×440 test photo to `tmp_path / "photo.jpg"` and calls `run_photoshoot(name, photo, _settings(tmp_path,
  shoot_count=count, **kw), status or (lambda s: None), lambda f: None, cancel, pc=pc, comfy=comfy)`.
  `cancel_after=k` sets `cancel` from the status callback when it reports `f"Shot {k + 1} of"`.

```python
def test_eight_shots_open_as_one_document_with_named_tagged_layers(tmp_path):
    pc, comfy = ShootPC(_doc(), None, tmp_path), ShootComfy()
    msg = _shoot(tmp_path, comfy, pc)
    assert len(pc.opened) == 1 and len(pc.placed) == 7
    assert pc.renamed[0][0] == "Shot 1 – hero"
    assert [n for _, n, m in pc.placed][0].startswith("Shot 2 – ") and all(m is False for _, _, m in pc.placed)
    assert [shoot_store.tag_of(pc.xmp[l])[1] for l in pc.layers] == list(range(1, 9))
    assert msg.startswith("Photoshoot ready: 8 shots as layers")

def test_the_hero_is_shown_to_every_later_shot(tmp_path):
    comfy = ShootComfy(); _shoot(tmp_path, comfy, ShootPC(_doc(), None, tmp_path))
    assert comfy.ref_counts == [1] + [2] * 7
    assert comfy.prompts[0].startswith("A professional photoshoot photo of the product from image 1. The shoot: ")
    assert all(p.startswith("Another photo from the same professional photoshoot as image 2") for p in comfy.prompts[1:])

def test_count_picks_the_first_shots_and_odd_counts_fall_back_to_eight(tmp_path):
    for count, made in ((4, 4), (12, 12), (3, 8)):
        pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc, count=count)
        assert 1 + len(pc.placed) == made

def test_twelve_shots_all_place_from_files_that_still_exist(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc, count=12)
    assert len(pc.placed) == 11   # ShootPC.place_layer asserted each file existed

def test_shots_are_the_create_size_of_the_chosen_shape(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc)
    assert Image.open(tmp_path / pc.placed[0][0]).size == create_size("1:1", 2.0)

def test_no_product_found_uses_the_whole_photo_and_says_so(tmp_path):
    said = []
    comfy = ShootComfy(); comfy.box = None
    _shoot(tmp_path, comfy, ShootPC(_doc(), None, tmp_path), status=said.append)
    assert any("using the whole photo" in s for s in said)

def test_a_failing_shot_is_retried_once_then_skipped(tmp_path):
    comfy = ShootComfy(); comfy.fail_runs = {3}          # shot 3 fails once, the retry works
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, comfy, pc)
    assert len(pc.placed) == 7
    comfy = ShootComfy(); comfy.fail_runs = {3, 4}       # shot 3 fails twice: skipped
    pc = ShootPC(_doc(), None, tmp_path); msg = _shoot(tmp_path, comfy, pc)
    assert len(pc.placed) == 6 and "Shots 3 failed" in msg
    assert 3 not in [shoot_store.tag_of(pc.xmp[l])[1] for l in pc.layers]

def test_a_failing_hero_stops_with_a_plain_message(tmp_path):
    comfy = ShootComfy(); comfy.fail_runs = {1, 2}
    pc = ShootPC(_doc(), None, tmp_path)
    with pytest.raises(UserFacingError, match="The first shot failed"):
        _shoot(tmp_path, comfy, pc)
    assert pc.opened == []

def test_cancel_keeps_the_finished_shots(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path)
    msg = _shoot(tmp_path, ShootComfy(), pc, cancel_after=3)
    assert 1 + len(pc.placed) == 3 and "cancelled" in msg

def test_a_run_is_kept_for_reshoot(tmp_path):
    pc = ShootPC(_doc(), None, tmp_path); _shoot(tmp_path, ShootComfy(), pc)
    run_id, _ = shoot_store.tag_of(pc.xmp[pc.layers[0]])
    run, subject, hero = shoot_store.load_run(tmp_path / "shoots", run_id)
    assert run.shoot == "Luxury dark" and len(run.shots) == 8 and subject.size == (1024, 1024)

def test_styles_are_never_added(tmp_path):
    comfy = ShootComfy(); pc = ShootPC(_doc(), None, tmp_path)
    _shoot(tmp_path, comfy, pc, style="Anime", angle="Low angle")
    assert not any("anime" in p.lower() for p in comfy.prompts)

def test_unknown_shoot_and_unreadable_photo_are_plain_errors(tmp_path):
    with pytest.raises(UserFacingError): _shoot(tmp_path, ShootComfy(), ShootPC(_doc(), None, tmp_path), name="Nope")
    with pytest.raises(UserFacingError, match="Can't open"):
        run_photoshoot("Luxury dark", tmp_path / "missing.jpg", _settings(tmp_path), print, print,
                       threading.Event(), pc=ShootPC(_doc(), None, tmp_path), comfy=ShootComfy())
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_photoshoot.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'genfill.photoshoot'`

- [ ] **Step 3: Implement `prepare_subject` and `run_photoshoot` in `genfill/photoshoot.py`**

The product cut-out is the spike's `cutout()` (mask → `_fill_holes` → `MaxFilter(5)` → composite on grey with
`GaussianBlur(1)` → bbox + 1/8 margin → centred square → 1024). One private `_make(comfy, settings, prompt, refs,
size) -> Image.Image` runs `build_create_workflow` and resizes back; one private `_place_all(...)` does item 9.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_photoshoot.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add genfill/photoshoot.py tests/test_photoshoot.py
git commit -m "feat(photoshoot): run a shoot and open it as layers"
```

---

### Task 5: Reshoot (`genfill/photoshoot.py: reshoot`)

**Files:**
- Modify: `genfill/photoshoot.py`
- Test: `tests/test_photoshoot.py`

**Interfaces:**
- Consumes: Task 2 (`tag_of`, `load_run`, `tag_packet`), Task 3 (`Results.tag`), Task 4 (`_make`, prompts);
  `pipeline.Results`, `Placed`, `_preview`, `MAX_VARIATIONS`, `swap`.
- Produces:
  - `NO_SHOT: str` = `"Select a Photoshoot layer (\"Shot 3 – …\") in PhotoCraft's Layers panel to reshoot it."`
  - `reshoot(settings, status, progress, cancel, pc=None, comfy=None) -> pipeline.Results` — images are the run's
    size; `layer` = `f"Shot {k} – {name}"`; `masked=False`; `size=run.size`; `selection=None`;
    `tag=tag_packet(run_id, k)`; `placed=Placed(-1, pc._identity(), active_layer)`. Placing is then
    `pipeline.swap(results, i, settings)` (Task 6 calls it).

Behaviour:
1. Active layer has no shoot tag → `UserFacingError(NO_SHOT)`.
2. Run gone → `UserFacingError(f"This shoot's details are no longer kept; GenFill keeps the last {settings.keep_results}.")`.
3. Document size ≠ `run.size` → `UserFacingError("The document's size changed since the shoot, so a new shot would
   land in the wrong place.")`, nothing generated.
4. `count = max(1, min(MAX_VARIATIONS, settings.variations))` new versions; shot 1 uses `hero_prompt` with refs
   `[subject]`, others `follow_prompt` with refs `[subject, stored hero]`; the stored hero is never replaced.

- [ ] **Step 1: Write the failing tests**

```python
def _shot_then(tmp_path, k=4, variations=1):
    pc, comfy = ShootPC(_doc(), None, tmp_path), ShootComfy()
    _shoot(tmp_path, comfy, pc)
    pc.active = pc.layers[k - 1]          # shot k selected in PhotoCraft
    return pc, comfy, _settings(tmp_path, variations=variations)

def test_reshoot_redraws_that_shot_and_swaps_it_in_place(tmp_path):
    pc, comfy, s = _shot_then(tmp_path, k=4)
    r = reshoot(s, print, print, threading.Event(), pc=pc, comfy=comfy)
    assert len(r.images) == 1 and r.layer.startswith("Shot 4 – ")
    assert comfy.ref_counts[-1] == 2 and comfy.prompts[-1].startswith("Another photo from the same")
    swap(r, 0, s, pc=pc)
    old, _, name, masked = pc.replaced[0]
    assert name == r.layer and masked is False
    assert shoot_store.tag_of(pc.xmp[r.placed.layer]) == (shoot_store.tag_of(pc.xmp[pc.layers[0]])[0], 4)

def test_reshooting_the_hero_uses_the_hero_recipe(tmp_path):
    pc, comfy, s = _shot_then(tmp_path, k=1)
    reshoot(s, print, print, threading.Event(), pc=pc, comfy=comfy)
    assert comfy.ref_counts[-1] == 1 and comfy.prompts[-1].startswith("A professional photoshoot photo")

def test_reshoot_makes_as_many_versions_as_the_results_count(tmp_path):
    pc, comfy, s = _shot_then(tmp_path, variations=3)
    assert len(reshoot(s, print, print, threading.Event(), pc=pc, comfy=comfy).images) == 3

def test_reshoot_refuses_untagged_layers_gone_runs_and_resized_documents(tmp_path):
    pc, comfy, s = _shot_then(tmp_path)
    pc.xmp = {}
    with pytest.raises(UserFacingError, match="Select a Photoshoot layer"): reshoot(s, print, print, threading.Event(), pc=pc, comfy=comfy)
    pc, comfy, s = _shot_then(tmp_path); shutil.rmtree(tmp_path / "shoots")
    with pytest.raises(UserFacingError, match="no longer kept"): reshoot(s, print, print, threading.Event(), pc=pc, comfy=comfy)
    pc, comfy, s = _shot_then(tmp_path); runs = comfy.runs; pc.doc = Image.new("RGBA", (100, 100))
    with pytest.raises(UserFacingError, match="size changed"): reshoot(s, print, print, threading.Event(), pc=pc, comfy=comfy)
    assert comfy.runs == runs
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_photoshoot.py -q -k reshoot`
Expected: FAIL, `ImportError: cannot import name 'reshoot'`

- [ ] **Step 3: Implement `reshoot`**

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest tests -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add genfill/photoshoot.py tests/test_photoshoot.py
git commit -m "feat(photoshoot): reshoot one shot in place"
```

---

### Task 6: The bar — Photoshoot button and menu, Reshoot button

**Files:**
- Modify: `genfill/widgets.py:35-73` (`ICONS["camera"]`)
- Modify: `genfill/app.py` — `build` (≈ line 259-290), `_arrange` (≈ 304-336), new `shoot_menu`, `_pick_shoot`,
  `_set_shoot_count`, `_edit_shoots`, `reshoot_shot`; `watch` (≈ 695-699), `start` (≈ 710-793), `poll`
  (≈ 875-878), `_work` (≈ 840-841)
- Test: `tests/test_widgets.py` covers the new icon automatically (it renders every `ICONS` entry)

**Interfaces:**
- Consumes: `shoots.load`, `shoots.SHOOTS_FILE`, `photoshoot.run_photoshoot`, `photoshoot.reshoot`,
  `photoshoot.SHOT_COUNTS`, `shoot_store.tag_of`, `pipeline.swap`, `pipeline.CREATE_CHOICES`.

- [ ] **Step 1: Add the `"camera"` icon**

Primitives as the other icons (24-unit grid): a rounded body `("rrect", (3, 7, 21, 19.5, 2.5))`, a lens
`("circle", (12, 13.2, 3.6))`, a top bump `("line", [(8.5, 7), (10, 4.5), (14, 4.5), (15.5, 7)])`.

Run: `python -m pytest tests/test_widgets.py -q` → pass.

- [ ] **Step 2: Photoshoot button and menu**

- `self.shoot_btn = button(icon="camera", command=self.shoot_menu, tooltip="Photoshoot: a set of matching shots of a product or a person")`, packed right after `self.ref_btn`, enabled/disabled with the others in `_arrange`.
- `shoot_menu()` items: `("header", "Photoshoot")`; `("sub", "Product", [...])` and `("sub", "People", [...])`
  with one `("item", shoot.name, lambda n=shoot.name: self._pick_shoot(n))` per loaded shoot of that kind (a group
  with none is left out); `("sep",)`; `("header", "Shots")` + `("check", f"{n} shots", lambda n=n:
  self._set_shoot_count(n), n == self.settings.shoot_count)` for `SHOT_COUNTS`; `("sub", "Shape", [...])` with a
  `check` per `CREATE_CHOICES` key bound to `self._set_create_choice`; `("sep",)`; `("item", "Edit shoots…",
  self._edit_shoots, "folder")`.
- `_pick_shoot(name)`: file dialog exactly like `choose_reference` (same filetypes, opens in the reference's
  folder) titled `f"Photoshoot: {name} – choose the photo"`; nothing picked → return; else
  `self.start("photoshoot", choice=name, what=path)`.
- `_edit_shoots()`: like `_edit_styles` with `shoots.load()` and `shoots.SHOOTS_FILE`.
- `start`: `job == "photoshoot"` → `fn` calls `run_photoshoot(choice, what, settings, status, progress, cancel)`;
  `self.say(f"Photoshoot: {choice}…", None)`; nothing remembered in the prompt history. Add `"photoshoot"` and
  `"reshoot"` to the jobs that skip `remember_prompt`. Works in every mode, including `"empty"`.
- `_work`: results starting with `"Photoshoot"` get no "Ctrl+Z" hint (add to the `startswith` tuple).

- [ ] **Step 3: Reshoot button**

- `watch`: from the one `pc.layer_metadata(layer[2])` call, put `("tagged", bool(results_store.tag_of(packet)))`
  and `("shoot_tagged", bool(shoot_store.tag_of(packet)))`.
- `poll`: `"shoot_tagged"` sets `self.shoot_tagged` and calls `_arrange()` when it changes (init `False` in
  `__init__`).
- `build`: `self.reshoot_btn = button(icon="camera", text="Reshoot", kind="outline", command=self.reshoot_shot,
  tooltip="Make this shot again (same shoot, same set)")`; add it to `self.actions`.
- `_arrange`: when `self.shoot_tagged and self.picker is None and not self.busy and self.mode != "empty"`, insert
  `reshoot_btn` before the last button, like `others_btn`.
- `reshoot_shot()`: `self.start("reshoot")`; in `start`, `fn` = `results = reshoot(settings, status, progress,
  cancel)`; one image → `status("Swapping the new shot in…")` and `return swap(results, 0, settings)`; more →
  `self.events.put(("pick", results)); return None` (the existing Picker; `swap` places the pick).

- [ ] **Step 4: Run the suite and try it in the app**

Run: `python -m pytest tests -q` → all pass.
Run: `python GenFill.pyw` with PhotoCraft and ComfyUI running; with no document open, Photoshoot ▸ Product ▸
Luxury dark, pick `docs/showcase` perfume crop (or any product photo). Expected: a new document with 8 layers
"Shot 1 – hero" … ; selecting "Shot 4 – …" shows **Reshoot**; Reshoot swaps that layer in place; Cancel during a
run keeps the finished shots.

- [ ] **Step 5: Commit**

```bash
git add genfill/widgets.py genfill/app.py
git commit -m "feat(photoshoot): Photoshoot menu and Reshoot on the bar"
```

---

### Task 7: Tune the shoots on real runs, document, remove the spike

**Files:**
- Modify: `genfill/shoots.py` (shot and set texts only)
- Modify: `README.md` (new **Photoshoot** section after **Create ▾**, and a bullet in the feature list)
- Delete: `tools/try_photoshoot.py`, `tools/try_make_product.py`

- [ ] **Step 1: Run every built-in shoot for real**

With the app (Task 6) and the spike's inputs — the perfume and night-cream jar for the five product shoots, the
selfie for the three people shoots — run each shoot at 8 shots.
Acceptance per shoot: label/face right in **≥ 7 of 8**, **no two shots near-duplicates**, **no broken shots**
(ghosted bodies, extra labels). Rewrite a shot's or set's text where it fails and run again. Keep the 12-shot
lists' first 4 and first 8 good subsets (hero, label/face close-up, a contrasting angle, a lifestyle shot first).

- [ ] **Step 2: README**

Describe: where the button is, choosing a shoot and a photo, shots 4/8/12 and the shape, that it takes about 6
minutes for 8 shots on a 10 GB card, layers named "Shot N – …", Reshoot, `shoots.json` via *Edit shoots…* (add,
override by name, `hidden`), and the tip that small print on labels can still break: make a few reshoots.

- [ ] **Step 3: Delete the spike scripts and run the suite**

Run: `python -m pytest tests -q` → all pass.

- [ ] **Step 4: Commit**

```bash
git add -A genfill/shoots.py README.md tools/
git commit -m "feat(photoshoot): tuned built-in shoots, README, spike removed"
```
