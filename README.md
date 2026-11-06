# Generative Fill for PhotoCraft

A small always-on-top bar that adds AI tools to [PhotoCraft](https://github.com/storytold/photocraft),
running entirely on your own computer through [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and
FLUX.2 [klein]:

![The bar: something selected, nothing selected, and while it works](docs/showcase/bar.png)

- **Generate** fills a selected area, like Photoshop's Generative Fill / Generative Expand. Enlarge the canvas
  (say 1080×1350 → 1080×1920), select the empty part, click Generate. The result arrives as a **new masked
  layer**; your original layers are never changed. You get several versions to choose from.
- **Expand** makes the canvas bigger (16:9, 4:5, 1:1… or a margin all round) and fills the new edges to
  match, like Photoshop's Generative Expand.
- **Presets** (like Photoshop's Neural Filters): Harmonize, Colorize, Restore old photo, Sky replacement,
  Background blur, Relight, and portrait tweaks, one click each.
- **More like this** (Generate Similar): new versions close to a result you almost like.
- **Remove** takes away whatever is in the selection and fills in the background behind it.
- **Reference picture**: give Generate a second image to take an object or a style from.
- **Edit** (Generate with nothing selected) changes the whole image as you describe it: "make it night",
  "change the jacket to red", "turn it into a watercolour". Everything you don't mention stays the same.
- **Upscale** (2× or 4×) redraws the whole image larger with new detail and opens it as a new document.

Nothing is uploaded anywhere: the bar talks only to PhotoCraft and ComfyUI on your own machine. The one
exception is a small check at startup that asks GitHub for the latest version number (turn it off in Settings).

Works on **Windows** and **macOS** (Linux builds are provided too). This is an independent project, not made by
the PhotoCraft developers.

## See it in action

All examples below were made with this app's own Generate, Remove, Expand and presets (FLUX.2 [klein] 9B, 4 steps).
The dashed box is the selection. The starting pictures were
made with the same model, so no real photos are involved.

**Generative Fill**: select the water, type *a small red wooden rowing boat on the water*, click Generate.

![Generative Fill: a rowing boat added to a lake](docs/showcase/fill.jpg)

**Remove**: select the cone, type *traffic cone*, click Remove.

![Remove: a traffic cone taken off a park path](docs/showcase/remove.jpg)

**Expand**: nothing selected, Expand ▾ › 16:9. A portrait photo becomes a wide one.

![Expand: a 4:5 photo widened to 16:9](docs/showcase/expand.jpg)

**Presets**: nothing selected, *Colorize black & white* and *Sky: golden sunset*, then Generate.

![Colorize: an old black and white harbour photo in colour](docs/showcase/colorize.jpg)

![Sky replacement: a golden sunset over the lake, reflected in the water](docs/showcase/sky.jpg)

Not every try is a hit: that's why Generate makes several versions (3 by default) to pick from.

![The Presets and Expand menus](docs/showcase/bar-menus.png)

## What you need

| | |
|---|---|
| PhotoCraft | 0.20 or newer (also when started from the ArtCraft Launcher) |
| ComfyUI | A recent version (with FLUX.2 support), running at `http://127.0.0.1:8188` |
| GPU | An NVIDIA card with about 10 GB of memory is comfortable; Apple Silicon Macs work with ComfyUI's Mac setup |

### Model files (download once into ComfyUI)

| File | Put it in `ComfyUI/models/…` | Download |
|---|---|---|
| `flux-2-klein-9b-fp8.safetensors` | `diffusion_models/` | [black-forest-labs/FLUX.2-klein-9b-fp8](https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8) (log in and accept the licence first) |
| `qwen_3_8b_fp8mixed.safetensors` | `text_encoders/` | [Comfy-Org/flux2-klein-9B](https://huggingface.co/Comfy-Org/flux2-klein-9B/blob/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors) |
| `flux2-vae.safetensors` | `vae/` | [Comfy-Org/flux2-dev](https://huggingface.co/Comfy-Org/flux2-dev/blob/main/split_files/vae/flux2-vae.safetensors) |

Restart ComfyUI after adding them. If your files have other names (for example a bf16 version), pick them in
**⋯ › Settings…**; the bar tells you at startup if a file is missing and where it goes.

**Mac note:** fp8 files may not run on Apple Silicon. If ComfyUI reports an fp8 error, use the bf16 versions
of the model and text encoder from the same Hugging Face pages and select them in Settings.

**Licence note:** this app is MIT-licensed, but the FLUX.2 [klein] 9B weights come with Black Forest Labs'
own licence, which is **non-commercial**. Read it on the model page before using results commercially.

## Install

Download the file for your computer from the **Releases** page of this repository:

- **Windows:** `GenFill-for-PhotoCraft-Windows.zip` → unzip → double-click `GenFill-for-PhotoCraft.exe`.
  Windows may show "Windows protected your PC" because the app isn't signed: click **More info › Run anyway**.
- **Mac (M1 and newer):** `GenFill-for-PhotoCraft-macOS-AppleSilicon.zip`;
  **Intel Mac:** `GenFill-for-PhotoCraft-macOS-Intel.zip`. Unzip and drag the app to Applications. The first
  time, **right-click the app › Open › Open** (it isn't signed by Apple). If macOS says it is damaged, run
  `xattr -cr "/Applications/GenFill for PhotoCraft.app"` in Terminal once.
- **Linux:** `GenFill-for-PhotoCraft-Linux.tar.gz` → extract → run `genfill-for-photocraft`.

## First time

1. Open the app, click **⋯** at the right end of the bar, then **PhotoCraft connection…**. This lets PhotoCraft
   accept commands from the bar (it sets a few per-user settings; nothing is installed into PhotoCraft).
2. Save your work, then quit PhotoCraft completely and start it again:
   - Windows: also quit the ArtCraft Launcher, including its tray icon.
   - Mac: PhotoCraft › Quit (Cmd+Q).
   - Linux: log out and back in.
3. Start ComfyUI. A message under the bar should say **Connected to PhotoCraft and ComfyUI**; the **⋯** menu
   shows both connections at any time.

## Use

The app is a small floating bar, like Photoshop's contextual task bar. It follows what you do in PhotoCraft:

- **Something selected:** prompt, **Remove** and **Generate** (fills the selection).
- **Nothing selected:** prompt, **Expand ▾**, **Upscale ▾** and **Generate** (changes the whole image).

Next to the prompt: **Presets** (sliders icon), **Reference picture** (picture icon; a dot means one is set) and
the **results count** (how many versions to choose from). Drag the dots at the left end to move the bar; it
remembers where you put it. Messages appear in a small pill under the bar (click it to hide it). While it works,
a thin line along the bottom shows progress and **Generate** turns into **Cancel** (or press Esc). The **⋯** menu
has Settings, the PhotoCraft connection, the version and Quit.

**Generate (fill a selection)**
1. In PhotoCraft: select what to fill: the empty area after Image › Canvas Size, or something to replace or
   remove (hold Shift to add more areas).
2. In the bar: optionally type what should appear ("remove it" works for taking things away), choose how many
   results you want (1–4, the stacked-squares button), then click **Generate** (about 15–30 seconds per result).
3. With more than one result, a **Choose a result** window shows them side by side: click the one you like (or
   press 1–4). **Discard all** adds nothing. With 1 result it goes straight in.
4. A new layer "Generative Fill" appears with a soft mask. Paint on the mask to adjust, or Undo to remove it.

The prompt box remembers your last 15 prompts: click the clock icon inside it to pick one again.

**Remove**: select the object (generously, including its shadow), optionally type what it is ("basket",
"the person on the left"), and click **Remove**. Naming it helps when the selection holds more than one thing.

**Reference picture**: click the picture icon and pick an image file, then mention it as
"image 2" in the prompt: "the logo from image 2 as a poster on the wall", "a chair like the one in image 2",
"in the style of image 2" (with nothing selected, that restyles the whole picture). Click the icon again to choose another or stop using it. Remove
ignores the reference. Like any fill, not every try picks it up, so ask for 3 results.

**More like this**: in the *Choose a result* window, click *More like this* under a result. You get new
versions that keep its layout and vary the details; the one you picked stays as choice 1.

**Presets**: the sliders icon next to the prompt opens a menu that fills in a ready-made instruction, which you can still
edit before clicking Generate. With nothing selected they work on the whole image; with a selection, only there.

| Preset | Use it for |
|---|---|
| Harmonize | Select an object you pasted in: its light, colour and shadows are matched to the photo |
| Colorize black & white, Restore old photo | Old family photos |
| Sky: golden sunset / clear blue / dramatic storm | Replaces the sky and adjusts the light to match |
| Background blur | A portrait-lens look; the subject stays sharp |
| Relight: golden hour / soft studio / night | Changes the lighting of the whole scene |
| Portrait: smile, look at the camera, retouch skin, younger, older | One person in the photo; select the face for the most control |

Keep the selection as it is while choosing; if it changes, the window asks you to put it back or Generate again
so the result can't land in the wrong place.

**Edit (whole image)**: deselect everything (Select › Deselect), type the change ("make it night, moonlight",
"change the car to red"), click **Generate**. The edited image arrives as a new layer named "Edit" above your
original, so you can lower its opacity, mask parts of it, or delete it. Edits are made at about 1 megapixel and
scaled to your image's size, so on large photos they are softer than the original; use **Upscale** afterwards or
mask the edit to where it matters. To change just one part, select it and use Generate with a prompt instead.

**Expand**: with nothing selected, click **Expand ▾** and pick a shape (4:5, 1:1, 3:2, 16:9, 9:16, 2:3, or
+10% / +25% on each side); it starts right away. It only ever grows the canvas, centred, and fills the new area to match (using the results count
from Generate). Your document is enlarged only when you pick a result; the filled edges come in as a layer
"Generative Expand" above your picture. To expand in one direction only, use Image › Canvas Size yourself,
select the empty part and use Generate.

**Upscale**: with nothing selected, click **Upscale ▾** and pick 2× or 4×. The bigger image opens as a new document; the original is
untouched.

The bar follows PhotoCraft's theme automatically.

## Settings

**⋯ › Settings…** has the ComfyUI address (with a Test button), the three model files (dropdowns list what your
ComfyUI has), fill steps (4 = fast, 8 = finer), upscale strength (0.2 subtle – 0.4 strong), PhotoCraft's control
port, and:

- **Check compatibility**: after a PhotoCraft update, runs every PhotoCraft step this window uses and shows
  OK/FAIL for each. It may open and close a test tab; your open documents are not touched. The window also checks
  at startup and refuses to run a step PhotoCraft no longer supports rather than do something unexpected.
- **Undo PhotoCraft setup**: removes the connection settings again.

The number of results per Generate is chosen on the bar and remembered.

**Updates**: at startup the bar asks GitHub whether a newer version exists; if so, a blue dot appears on
**⋯**, and the menu has a link to the download page. Turn this off under Settings › Updates. The **⋯** menu
shows the version you have.

## Troubleshooting

| Status says | Do this |
|---|---|
| PhotoCraft connection isn't set up | Click **⋯ › PhotoCraft connection…**, then restart PhotoCraft as above. |
| Can't reach PhotoCraft | PhotoCraft isn't running, wasn't fully restarted after Setup, or Preferences › Integrations › Allow Agent Control is off. |
| ComfyUI is not running | Start ComfyUI, or fix the address in Settings. |
| Model file missing | Download it into the folder shown, restart ComfyUI. |
| Out of memory | Lower *megapixels* in the settings file, close other GPU programs. |

Settings are stored in `%APPDATA%\PhotoCraftGenFill` (Windows), `~/Library/Application Support/PhotoCraftGenFill`
(Mac) or `~/.config/photocraft-genfill` (Linux).

## How it works

The window uses PhotoCraft's built-in local control channel. For Generate it exports a PNG copy of your image
(your document keeps its name), sends it to ComfyUI with a mask of the selected area, colour-matches the result to
the original so there is no visible seam, and brings it back as a new layer, pasted in place, with a layer mask from
your selection. It briefly uses the clipboard for that and waits if another program holds it. Upscale works in
overlapping tiles so large images fit in GPU memory.

PhotoCraft's own *Filter › Plug-ins* can't host this: those plug-ins are sandboxed WebAssembly filters without
network access, prompts or new layers. That's why this is a separate window.

## Run from source / build

Python 3.12:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # Windows: .venv\Scripts\pip
python -m pytest tests
python GenFill.pyw
python build.py        # standalone app in dist/
```

Pushing a tag like `v1.0.0` makes GitHub Actions build the Windows, Mac and Linux apps and attach them to a
release.
