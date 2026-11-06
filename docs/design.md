# PhotoCraft Generative Fill companion: design

Date: 2026-11-06

## Intent

PhotoCraft (storytold's open-source Photoshop-style editor, installed at `C:\Program Files\PhotoCraft`, v0.20.2,
started from the ArtCraft Launcher) has no generative AI. The user wants Photoshop's Generative Fill/Expand:
enlarge the canvas (for example 1080×1350 → 1080×1920), select the empty area, press **Generate**, and get the
missing pixels filled so the picture looks complete. Generation runs locally on their ComfyUI with Flux 2.

The user ruled out rebuilding PhotoCraft (too much time). The feature is a **separate companion window** that
talks to the running PhotoCraft over its built-in control channel and to ComfyUI over HTTP.

## Goals

- A small always-on-top companion window: optional prompt box, **Generate** button, status/progress line.
- One click: read the open document and the current selection from PhotoCraft, generate the fill with Flux 2
  Klein in ComfyUI, and put the result back as a **new layer above the active one, masked to the selection**.
- The original pixels are never modified. The result is non-destructive (layer + layer mask; one undo removes it).
- Works when PhotoCraft is started the user's normal way (ArtCraft Launcher). No custom PhotoCraft build.

## Non-goals (for now)

- No floating bar drawn on the PhotoCraft canvas (impossible without rebuilding the app).
- No multiple variations per click, no history browser of past generations.
- No text-to-image from scratch, no remove-object/inpaint tuning UI. (Inpainting a selection inside the image
  works by the same path, but outpainting/canvas expansion is the case that must be good.)
- No changes to PhotoCraft or ComfyUI installs, no new ComfyUI custom nodes or model downloads.

## Anti-goals (failure even if it "works")

- Touching or altering Layer 0 / the user's original pixels.
- A visible seam or colour/lighting mismatch at the border between original and generated area that the mask
  cannot hide. Context from the original image must drive the fill.
- Result placed at the wrong position or scale (must align pixel-exactly with the canvas).
- Hanging or freezing the companion while ComfyUI works; silent failures. Every failure shows a plain message
  (PhotoCraft not reachable, ComfyUI not running, no selection, out of VRAM).

## Environment (verified)

- ComfyUI 0.39 (developer's PC), running on `http://127.0.0.1:8188`, Python 3.10, RTX 3080 10 GB.
- Models present: `diffusion_models/flux-2-klein-9b-fp8.safetensors`, `vae/flux2-vae.safetensors`,
  `text_encoders/qwen_3_8b_fp8mixed.safetensors` (Klein 9B text encoder). CLIPLoader supports type `flux2`.
- Nodes present: `UNETLoader`, `CLIPLoader`, `VAELoader`, `VAEEncode/Decode`, `ReferenceLatent`,
  `SetLatentNoiseMask`, `InpaintModelConditioning`, `Flux2Scheduler`, `CFGGuider`, `SamplerCustomAdvanced`,
  `KSamplerSelect`, `RandomNoise`, `GrowMaskWithBlur`, `ImageCompositeMasked`, `LoadImage`, `LoadImageMask`.
- PhotoCraft source (github.com/storytold/photocraft) used as reference only; protocol documented in `docs/control-protocol.md`).

## How it connects (decided)

### PhotoCraft control channel

PhotoCraft listens on a localhost JSON-lines TCP control server when a control port is configured. The desktop
app reads these **environment variables**, so setting them as persistent Windows *user* variables makes it work
when launched from the ArtCraft Launcher (Preferences › Integrations › Allow Agent Control is on by default):

| Variable | Value |
|---|---|
| `PHOTOCRAFT_CONTROL_PORT` | `7878` |
| `PHOTOCRAFT_CONTROL_TOKEN_FILE` | `%LOCALAPPDATA%\PhotoCraftGenFill\control.token` (PhotoCraft creates it) |
| `PHOTOCRAFT_AUTOMATION_READ_ROOT` | `%LOCALAPPDATA%\PhotoCraftGenFill\exchange` |
| `PHOTOCRAFT_AUTOMATION_WRITE_ROOT` | `%LOCALAPPDATA%\PhotoCraftGenFill\exchange` |

A one-time `setup` step sets these (user scope, broadcast so new processes see them) and tells the user to fully
quit and restart the ArtCraft Launcher + PhotoCraft once. The setup is reversible (an `uninstall` removes them).

Protocol: first line `{"id":"auth","method":"auth","params":{"token":...}}`, then one JSON request per line.
The app drops idle connections after 30 s, so connect per Generate click.

Methods used (from `docs/control-protocol.md`; must be verified against installed v0.20.2):

- `doc.inspect {}` → canvas size, layer tree, active layer, **selection bounds** (null = no selection).
- `doc.render {maxSide: 0}` → full-resolution composite as base64 PNG **with transparency**.
- `doc.place {path, fit, center}` → places the result PNG (from the read root) as a Smart Object layer above the
  active one.
- `engine.execute` for: rasterizing the placed layer, `layer.layerMask.revealSelection`, renaming the layer
  (e.g. "Generative Fill – <prompt>"), and re-creating the selection with `select.rect` if placing drops it.

### Generation (ComfyUI)

1. Composite PNG + selection → build the **fill mask** = selection area (if the selection is a rectangle bounds
   only, use the bounds), grown/feathered by a few pixels so the generated area overlaps the original edge.
2. Fill fully transparent pixels with a neutral/edge-extended colour so the VAE gets no black holes.
3. Scale working size to about 1 MP (both sides multiples of 16) to fit 10 GB VRAM; keep aspect ratio.
4. Upload image + mask (`/upload/image`), queue a workflow built in code (API JSON), poll `/history/<id>`
   (or the websocket) for progress, download the output with `/view`.
5. Workflow: Klein 9B fp8 (UNETLoader) + qwen_3_8b (CLIPLoader `flux2`) + flux2-vae; text conditioning from
   the prompt (blank prompt → a generic "seamless natural continuation of the scene" prompt);
   `ReferenceLatent` with the encoded source image for context; `VAEEncode` + `SetLatentNoiseMask` so only the
   masked area is denoised; `Flux2Scheduler`, `CFGGuider` (cfg ≈ 1, distilled), ~4–8 steps, random seed.
6. Upscale the result back to the document size, then composite: **original pixels outside the mask, generated
   pixels inside**, so the placed layer matches the original exactly where the mask edge feathers.
7. Save a full-canvas PNG into the exchange folder and place it (step above). Clean up old exchange files.

Prompt: optional. Blank is allowed and is the expected common case (user didn't specify; my call, like
Photoshop).

## Companion app (decided by me, user can override)

- Python, Tkinter UI (bundled with Python; no extra GUI dependency). Runs with the Python already used by
  ComfyUI or any Python 3.10+; only needs `Pillow` and `numpy` (both already in the ComfyUI environment).
- Standalone project folder. Launched by a desktop shortcut/`.pyw` (no console window).
- Window: always on top, small; prompt field, Generate button, status line, "Settings" for ComfyUI URL,
  control port, steps, and model file names (defaults as above). Settings saved to a JSON file next to it.
- Generation runs on a worker thread; the UI never freezes; Generate is disabled while busy, Cancel interrupts
  ComfyUI (`/interrupt`).

## Left to the builder

- Exact Klein outpainting graph (ReferenceLatent vs InpaintModelConditioning, step count, mask feather,
  padding fill method). Pick by testing on the user's example (post.jpg extended to 1080×1920) for seam quality.
- How the installed v0.20.2 behaves for `doc.place` positioning, whether placing keeps the selection, and the
  exact command ids for rasterize/rename. Verify live, fall back to the alternatives above.
- Non-rectangular selections: if only bounds are available over the control channel, the PhotoCraft layer mask
  from the live selection still gives the exact shape; generation uses the bounds.

## Risks

- PhotoCraft 0.20.2 may differ from the cloned docs (repo is newer). Mitigation: first step of the build is a
  live probe script listing the methods that actually answer.
- VRAM: Klein 9B fp8 + 8B text encoder on 10 GB relies on ComfyUI offloading; expect 20–60 s per fill.
