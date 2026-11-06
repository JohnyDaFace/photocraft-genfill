# Generative Fill for PhotoCraft

A small always-on-top window that adds two AI tools to [PhotoCraft](https://github.com/storytold/photocraft),
running entirely on your own computer through [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and
FLUX.2 [klein]:

- **Generate** fills a selected area, like Photoshop's Generative Fill / Generative Expand. Enlarge the canvas
  (say 1080×1350 → 1080×1920), select the empty part, click Generate. The result arrives as a **new masked
  layer**; your original layers are never changed.
- **Upscale** (2× or 4×) redraws the whole image larger with new detail and opens it as a new document.

Nothing is uploaded anywhere: the window talks only to PhotoCraft and ComfyUI on your own machine.

Works on **Windows** and **macOS** (Linux builds are provided too). This is an independent project, not made by
the PhotoCraft developers.

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
**Settings…**; the window tells you at startup if a file is missing and where it goes.

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

1. Open the app and click **Setup PhotoCraft connection**. This lets PhotoCraft accept commands from this
   window (it sets a few per-user settings; nothing is installed into PhotoCraft).
2. Save your work, then quit PhotoCraft completely and start it again:
   - Windows: also quit the ArtCraft Launcher, including its tray icon.
   - Mac: PhotoCraft › Quit (Cmd+Q).
   - Linux: log out and back in.
3. Start ComfyUI. The window's status line should say **PhotoCraft: connected** and **ComfyUI: connected**.

## Use

**Generate**
1. In PhotoCraft: Image › Canvas Size to enlarge, then select the empty area (hold Shift to add more areas).
2. In the window: optionally type what should appear, then click **Generate** (about 20–60 seconds).
3. A new layer "Generative Fill" appears with a soft mask. Paint on the mask to adjust, or Undo to remove it.

**Upscale**: choose 2× or 4× and click **Upscale**. The bigger image opens as a new document; the original is
untouched.

The window follows PhotoCraft's theme automatically.

## Settings

**Settings…** has the ComfyUI address (with a Test button), the three model files (dropdowns list what your
ComfyUI has), fill steps (4 = fast, 8 = finer), upscale strength (0.2 subtle – 0.4 strong), PhotoCraft's control
port, and:

- **Check compatibility**: after a PhotoCraft update, runs every PhotoCraft step this window uses and shows
  OK/FAIL for each. It may open and close a test tab; your open documents are not touched. The window also checks
  at startup and refuses to run a step PhotoCraft no longer supports rather than do something unexpected.
- **Undo PhotoCraft setup**: removes the connection settings again.

## Troubleshooting

| Status says | Do this |
|---|---|
| PhotoCraft connection isn't set up | Click **Setup PhotoCraft connection**, then restart PhotoCraft as above. |
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
