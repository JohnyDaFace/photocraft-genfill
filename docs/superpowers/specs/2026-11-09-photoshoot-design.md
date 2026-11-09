# Photoshoot: design

Date: 2026-11-09
Status: draft for approval

## Intent

Give GenFill a **Photoshoot** feature: from one reference photo of a product or of a person, make a set of
pictures that look like they came from **one professional shoot** of that same product or person. The user
picks a ready-made shoot and a photo; nothing else to type or set up. The feature is about getting many
usable, varied, on-brand pictures out of one phone snapshot (shop listings, Instagram, profile shots).

What makes it a *shoot* and not "a pile of AI pictures": every shot shares the same set, mood, light and colour
grade, as if taken on one day in one place; what varies is camera angle, distance, framing and, for people,
pose and expression.

## Goals

- One entry point on the bar, **Photoshoot**, that works with or without a document open.
- The user picks a **ready-made shoot** from a list and then a **reference photo** from disk. No prompt needed.
- A shoot makes **8 shots** by default; 4 / 8 / 12 can be chosen in the Photoshoot menu itself ("Shots: 4 · 8 ·
  12", remembered as a new setting `shoot_count`, default 8). The bar's existing results count (1–4, "Results to
  choose from") is not used for the shot count; it only decides how many versions a Reshoot offers. *(Builder's
  call recorded here: the existing control only goes to 4 and means something else.)*
- The shots arrive as **layers in one new PhotoCraft document**, each named after the shot
  ("Shot 4 – label close-up"). The reference photo and any open documents are never touched.
- **Shape**: 1:1 by default, changeable with the same shape menu Create uses (1:1, 4:5, 3:2, 16:9, 9:16, 2:3),
  at least Full HD pixel count (square ≈ 1440×1440), via `pipeline.create_size`.
- **Reshoot**: select any shot layer in PhotoCraft and a **Reshoot** button appears on the bar. It remakes that
  exact shot (same shoot, same subject, same set) with a new seed and replaces the layer in place, same name
  and stacking order. Repeatable as often as wanted, and it works days later (after restarts) like lasting
  results do today.
- Shoots are data: built-in shoots ship with the app; users can add or change their own in a JSON file, the same
  way `styles.json` works.

## Non-goals

- No free-text shoot description in v1 (the user explicitly preferred ready-made shoots). A user shoot in
  `shoots.json` is the way to describe a custom one.
- No mixing a product and a person in one run (for example "this model holding this product"). The existing
  model product styles cover that today.
- No per-shot size variants (story/banner) inside one shoot. Expand already turns any shot into another shape.
- No video, no batch over several reference photos.
- No face swap-back pass after each shot (tested: it barely helps and doubles the time).

## Anti-goals (failure even if it "works")

- **Product: the label changes** (wrong letters, missing logo, different brand). This is the thing users wince at.
- **Person: the face changes** to someone else, or features drift (beard, eye colour, face shape).
- Shots that are near-duplicates of each other (the set is supposed to give 8 *different* pictures).
- Broken shots like a semi-transparent body (seen once with a very wide person shot).
- The bar freezing during a 6-minute run, or no way to cancel.

## What the spike showed (tools/try_photoshoot.py, throwaway)

Run on the user's ComfyUI (FLUX.2 klein 9B fp8, 4 steps) with two products (a perfume with a one-word label; a
night-cream jar with brand, product line, "50 ml / 1.7 fl oz" and a logo) and one man's selfie:

- **Approach that works ("hero + follow")**: make shot 1 (the hero) from the subject cut-out; make every other
  shot from the subject cut-out **plus the hero** as a second reference ("another photo from the same shoot as
  image 2"). The set, light and grade then match across all shots. Making shots independently gives
  inconsistent sets and more near-duplicates.
- **Labels**: correct in 16/16 perfume shots and 7/8 jar shots. The one failure was a **steep high angle** (jar
  tilted): text broke into "DI MEA". Steep angles, top-down views and "from behind" must not be in product shot
  lists.
- **Faces**: the same man in all shots with hero + follow; clearly better than independent shots.
- **Weak spots to design around**:
  - Vague shot instructions ("low angle", "turned 30°") were often ignored, giving near-duplicates. Shot
    instructions must be strong and clearly different from each other.
  - For people, the background stayed almost identical to the hero ("a different part of the room" was
    ignored). People shot lists must name concrete spots in the location ("by the window", "against the brick
    wall", "sitting at the long table").
  - A "wide environmental" person shot came out with a ghosted body; a knees-up shot worked. Keep people shots at
    knees-up or closer.
- **Timing** on a ~10 GB card at about 2 MP: cut-out ~10 s, hero ~27 s, each follow shot ~43 s. **8 shots ≈ 6
  minutes.** ComfyUI stalled once while loading a LoRA mid-run; the run must survive transient ComfyUI errors
  on one shot (see Errors).

## How it works

### Flow

1. **Photoshoot** on the bar (a new icon button next to Presets/Reference; available in every bar mode, including
   "no document open") opens a menu: shoots grouped under **Product** and **People**, plus *Edit shoots…*
2. Picking a shoot opens a file dialog for the reference photo (same dialog and remembered folder as Reference
   picture).
3. The run starts at once with the bar's current shape and the remembered shot count. Status pill and progress line as for
   other jobs ("Photoshoot: cutting out the product…", "Shot 3 of 8 – label close-up…"); Cancel/Esc works.
4. When done, one new document opens in PhotoCraft with all shots as layers, shot 1 at the bottom, each named
   "Shot N – <shot name>". Status: "Photoshoot ready: 8 shots as layers. Select a layer and click Reshoot to
   redo one."
5. **Reshoot**: when the active layer in PhotoCraft carries a photoshoot tag, the bar shows **Reshoot**. Clicking
   it remakes that shot and swaps it into the same layer. With results count > 1 it shows the usual *Choose a
   result* window (with the current shot as choice 1? see "Left to the builder").

### Kind of subject

The shoot decides it: shoots are listed under **Product** or **People**, and a shoot is one kind. (During the
conversation automatic detection with SAM 3 was floated; grouping the list by kind makes it unnecessary.
Builder's call recorded here: no detection, the chosen shoot sets the kind.)

### Preparing the reference

- Load with `pipeline.load_reference` (flattens transparency). Enlarge small photos to at least 1024 px on the
  long side before cutting out (the spike did this; labels survive better).
- **Product**: SAM 3 finds the product (a generic noun per shoot kind is not reliable; use the shoot's
  `subject` word if given, else `"product:1"`; if nothing is found, fall back to the whole photo and say so).
  Cut it out on plain grey in a square with a margin (spike: `cutout()`), like `_reference_head` does for heads.
  The kitchen around the product must not reach the model.
- **Person**: `pipeline._reference_head` (head and hair on grey, square). In the spike this was enough to keep
  the face; clothes come from the shoot description.
- Upload the cut-out once per run; it is image 1 for every shot.

### Making the shots

- Size: `create_size(shape, settings.megapixels)`; generate at `imaging.working_size` of it, resize back.
- Workflow: `comfy.build_create_workflow(prompt, seed, steps, w, h, models, extra_refs)` (text-to-image with
  references; Klein calls them image 1, image 2).
- **Hero** (shot 1): refs `[subject]`. Prompt: "A professional photoshoot photo of the {product|person} from
  image 1. The shoot: {shoot.set}. This shot: {shot.text}. {keep}".
- **Follow shots** (2..n): refs `[subject, hero]`. Prompt: "Another photo from the same professional photoshoot
  as image 2: the same set, lighting and colour grade, of the {product|person} from image 1. Take it from a
  clearly different camera position and framing than image 2. This shot: {shot.text}. {keep}". (The camera
  sentence was added after real runs: without it, follow shots copied the hero's framing.)
- `keep` for products: "Keep the product exactly as in image 1: the same shape, colours, materials and the exact
  same label text and logo, letter for letter." For people: "Keep the exact same person as in image 1: the same
  face, facial features, eye colour, skin, hair and beard." (These worked in the spike; `styles.KEEP_PRODUCT`
  and `KEEP_FACE` are close cousins and may be reused if the wording fits.)
- Styles and angles from the Style panel are **not** applied to a photoshoot (the shoot already is the look).
- Count: `shoot_count` selects 4, 8 or 12; shot lists have 12 entries and the first N are used, so 4 is a
  sensible subset (hero, close-up, a contrasting angle, a lifestyle shot).

### Shoot definitions (data)

New module `genfill/shoots.py`, mirroring `styles.py`:

```json
{
  "name": "Skincare morning light",
  "kind": "product",
  "set": "pale travertine stone plinth, soft morning sunlight with gentle leaf shadows, sage green and cream tones, fresh and minimal",
  "shots": [
    {"name": "hero", "text": "..."},
    {"name": "label close-up", "text": "..."}
  ]
}
```

- Built-ins live in the app (so updates can improve them); `shoots.json` next to `styles.json` holds the user's
  own, with the same rules: add by name, same name overrides a built-in, `hidden` hides one. *Edit shoots…* opens
  it (like *Edit styles…*), writing a blank file with a short help text first.
- **Built-in shoots (v1)**, agreed with the user:
  - Product: **Clean studio white** (online shop), **Skincare morning light**, **Luxury dark**,
    **Kitchen / lifestyle table**, **Outdoor summer**.
  - People: **Studio headshots**, **Loft editorial**, **Street / city casual**.
- **Shot lists**: one shared 12-shot list per kind, written so that each shot is clearly different and safe:
  - Product: no steep high angles, no top-down, no from-behind; the label faces the camera in every shot;
    include a label close-up, a detail/texture macro, a hand-held shot, a wider set shot, a low-but-gentle hero
    angle, a lifestyle prop shot.
  - People: knees-up or closer; name concrete spots in the location and vary them; vary expression and pose
    (calm, laughing, looking away, arms crossed, over-the-shoulder, profile).
  - A shoot may override the shared list with its own `shots` (for example headshots: all close).
  The final wording of the 12+12 shots is builder's work, tuned by running them (see Testing).

### Placing in PhotoCraft

- Save each shot to the exchange folder and open shot 1 as a new document (`PhotoCraftClient.open_document`),
  then place shots 2..N on top as whole layers (`place_layer(rel, name, masked=False)`), then rename shot 1's
  layer (the opened picture's own layer) to "Shot 1 – hero". If renaming the base layer is not possible with
  PhotoCraft's commands, place shot 1 as a layer too and leave the base as is; the builder checks which works
  (`compat.py` must cover any new PhotoCraft step, per the app's rule of refusing unsupported steps).
- Tag every shot layer with XMP metadata (as `results_store.tag_packet` does), naming the **shoot run id** and
  the **shot index**: for example `genfill:shoot="<run id>" genfill:shot="4"`. `results_store.TAG_RE` only reads
  `genfill:results`; the shoot tag needs its own reader, and `App.watch` must tell the two apart so a shot layer
  shows **Reshoot**, not *Other results*.
- **Exchange folder pitfall:** `pipeline._exchange` deletes all but the newest 10 PNGs each time it hands out a
  name. A 12-shot run must not write all shots there first: save and place them one at a time (or keep the
  finished shots in the run's store folder and copy each into the exchange folder just before placing it).

### Keeping what Reshoot needs

New store (or an extension of `results_store`, builder's choice), under GenFill's data folder:
`shoots/<run id>/` with `shoot.json` (shoot name, kind, the shoot's set text and shot list *as used*, shape,
size, created; no model names: Reshoot uses the **current** models from Settings, so it always runs even if the
user has since switched files, *builder's call*), `subject.png` (the cut-out) and `hero.png`. Prune like results: keep the last 50 runs
(`keep_results`). If a run is gone, Reshoot says so plainly ("This shoot's details are no longer kept; GenFill
keeps the last 50.").

### Reshoot

- `App.watch` already polls PhotoCraft; extend the check that shows *Other results* so that a selected layer with
  a photoshoot tag shows **Reshoot** (and the tooltip names the shot).
- Reshoot loads the run, uploads subject + hero, rebuilds that shot's prompt (hero shot: hero recipe; others:
  follow recipe) with a new seed, and swaps it into the layer with `PhotoCraftClient.replace_layer`, keeping name
  and position; the new layer gets the same tag. With the bar's results count at 1 the new version goes straight
  in; above 1, the *Choose a result* window offers that many new versions. Refuse with a plain message if the document's size changed or
  the layer is gone (same rules as `pipeline.swap`).
- Reshooting **shot 1** (the hero) changes only that layer; the other shots are not regenerated, and the stored
  hero stays the original one so later reshoots still match the rest of the set.

### Errors and cancelling

- Cancel stops after the current shot. Whatever shots finished are still opened as a document (better than
  losing 4 minutes of work), and the status says how many.
- A ComfyUI error on one follow shot is retried once; if it fails again, that shot is skipped, the run continues,
  and the final status names the missing shots (they can be made later with Reshoot? no: a skipped shot has no
  layer; see "Left to the builder").
- Missing SAM 3 (needed for the cut-out): say what to download, like Select does. No other new models.

## Constraints

- Everything runs locally on the user's ComfyUI with the models GenFill already uses: FLUX.2 klein (create
  workflow with references) and SAM 3. No new model downloads, no new ComfyUI custom nodes.
- PhotoCraft is driven only through `PhotoCraftClient`; any new step goes through `compat.py`'s checks.
- Non-destructive: the reference photo and open documents are never changed.
- Tkinter UI on the existing worker-thread pattern (`App.start`/`_work`/`poll`); the bar must stay responsive.
- Python 3.12, tests with pytest using the existing fakes (`FakePC`, `FakeComfy`).

## Left to the builder

- Exact wording of the 12 product and 12 people shots and the 8 shoots' `set` texts (tune with a real run per
  shoot; acceptance below).
- The Photoshoot icon, and where the button sits in each bar mode.
- Whether the store is a new module or part of `results_store`.
- Reshoot with results count > 1: whether the current shot is shown as choice 1.
- Skipped shots: leave them out, or place a placeholder layer that Reshoot can fill.
- Whether to show a quick "Shot 1 ready" preview while the rest is still running (nice, not required).

## Testing and acceptance

- Unit tests (pytest, fakes): shoot loading and user overrides; prompt building for hero and follow shots;
  count → shot subset; placing N layers with names and tags; Reshoot finds the tag, rebuilds the right prompt,
  replaces the right layer, refuses when the layer is gone or size changed; cancel keeps finished shots; one
  failing shot is retried and then skipped.
- Manual acceptance on the user's machine with the jar and perfume test photos and the selfie (the spike's
  inputs): for each built-in shoot, a full run where the label/face is right in at least 7 of 8 shots, no two
  shots are near-duplicates, and no broken shots. `tools/try_photoshoot.py` and `tools/try_make_product.py` are
  spike code and are deleted when the feature lands.
