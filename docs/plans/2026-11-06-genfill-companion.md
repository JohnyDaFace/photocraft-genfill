# PhotoCraft Generative Fill Companion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A small always-on-top Python window that fills the selected (empty) area of the open PhotoCraft document with Flux 2 Klein via local ComfyUI and places the result as a new masked layer.

**Architecture:** Four focused modules (PhotoCraft control client, image/mask maths, ComfyUI client + workflow builder, pipeline orchestrator) behind a Tkinter window. PhotoCraft is reached over its JSON-lines TCP control channel (enabled by user environment variables); ComfyUI over HTTP.

**Tech Stack:** Python 3.10 (ComfyUI's interpreter), Tkinter, Pillow, numpy, stdlib `socket`/`urllib`/`json`, pytest.

**Spec:** `docs/design.md`

## Global Constraints

- Never modify existing layers; output is always a new layer + layer mask from the selection.
- Defaults: PhotoCraft control port `7878`; token file `%LOCALAPPDATA%\PhotoCraftGenFill\control.token`; exchange dir (read+write root) `%LOCALAPPDATA%\PhotoCraftGenFill\exchange`; ComfyUI `http://127.0.0.1:8188`.
- Models: `flux-2-klein-9b-fp8.safetensors`, `qwen_3_8b_fp8mixed.safetensors` (CLIPLoader type `flux2`), `flux2-vae.safetensors`.
- Working resolution ≈ 1 MP, both sides multiples of 16, aspect kept.
- Blank prompt allowed → default "seamless natural continuation of the scene, same lighting, same style".
- No extra dependencies beyond Pillow + numpy (+ pytest for tests). No changes to PhotoCraft/ComfyUI installs.
- Every failure becomes a plain message in the status line; UI never blocks (worker thread).

## Review Focus

1. No selection active → status "Make a selection first", nothing sent to ComfyUI. (Task 5 test)
2. PhotoCraft not reachable / env vars not active yet → message telling the user to run Setup and restart the launcher. (Task 2 test)
3. ComfyUI not running or OOM error in history → readable error, Generate re-enabled. (Task 4 test)
4. Odd document sizes (e.g. 1081×1921) and selection partly outside canvas → mask clipped, output exactly document-sized. (Task 3 test)
5. Selection covering both original and transparent pixels → original pixels preserved outside the mask in the merged output. (Task 3 test)

---

### Task 1: Live probe of installed PhotoCraft 0.20.2 (spike, no tests)

**Files:** Create `tools/probe.py`

- [ ] Run `photocraft-cli serve --port <p>` headless with read/write roots, open `post.jpg`-like test doc, call `doc.inspect`, `doc.render {maxSide:0}`, `select.rect`, `doc.place`, `layer.layerMask.revealSelection`; record which methods/params exist and their reply shapes in `docs/probe-notes.md`.
- [ ] Adjust Tasks 2/5 names if the installed version differs.

### Task 2: PhotoCraft control client + env setup

**Files:** Create `genfill/photocraft_client.py`, `genfill/setup_env.py`; Test `tests/test_photocraft_client.py`

**Interfaces — Produces:**
- `class PhotoCraftError(Exception)`
- `PhotoCraftClient(port=7878, token_file=Path, timeout=30)` context manager; `.call(method: str, params: dict|None) -> Any` (raises `PhotoCraftError` on `ok:false` or connection refused, message mentions Setup + restart).
- `.document() -> DocInfo` (`width:int, height:int, selection: tuple[int,int,int,int]|None` as x0,y0,x1,y1)
- `.render() -> PIL.Image` (RGBA, full size)
- `.place_layer(png_path: Path, name: str, selection) -> None` (place, rasterize, re-select if needed, reveal-selection mask, rename)
- `setup_env.install() / uninstall() / status() -> dict` (user-scope `HKCU\Environment` via `winreg`, then broadcast `WM_SETTINGCHANGE`).

- [ ] Tests with a fake TCP server: auth line sent first with token from file; one JSON request per line; `ok:false` → `PhotoCraftError`; refused connection → error text contains "Setup".
- [ ] Implement, run `pytest tests/test_photocraft_client.py` → PASS, commit.

### Task 3: Image + mask maths

**Files:** Create `genfill/imaging.py`; Test `tests/test_imaging.py`

**Interfaces — Produces:**
- `working_size(w:int, h:int, megapixels=1.0) -> tuple[int,int]` (multiples of 16)
- `fill_mask(size, selection, grow=24, feather=16) -> Image L` (255 = generate; clipped to canvas)
- `prepare_source(rgba: Image) -> Image RGB` (transparent pixels filled by edge-extension / blur-pull, no black)
- `merge(original: Image RGBA, generated: Image RGB, mask: Image L) -> Image RGBA` (original where mask=0 and alpha>0, generated elsewhere, full size, fully opaque)

- [ ] Tests: `working_size(1080,1920)` both %16==0 and area within 0.9–1.1 MP; mask clipped for selection `(-10,-10,50,50)`; `merge` keeps an original opaque pixel outside mask byte-identical; output size equals original for 1081×1921; `prepare_source` of fully transparent row has no pure-black pixels.
- [ ] Implement with numpy/Pillow, tests PASS, commit.

### Task 4: ComfyUI client + Klein fill workflow

**Files:** Create `genfill/comfy.py`; Test `tests/test_comfy.py`

**Interfaces — Produces:**
- `build_workflow(image_name:str, mask_name:str, prompt:str, seed:int, steps:int, models:dict) -> dict` (API-format graph: UNETLoader, CLIPLoader flux2, VAELoader, CLIPTextEncode, LoadImage, LoadImageMask, VAEEncode, ReferenceLatent, SetLatentNoiseMask, Flux2Scheduler, CFGGuider cfg 1.0, KSamplerSelect euler, RandomNoise, SamplerCustomAdvanced, VAEDecode, SaveImage)
- `class ComfyError(Exception)`; `ComfyClient(url)`: `.upload(img: Image, name) -> str`, `.run(workflow, on_progress: Callable[[float],None], cancel: threading.Event) -> Image` (raises `ComfyError` with node error text; `/interrupt` on cancel).

- [ ] Tests: workflow is JSON-serialisable, every input reference points at an existing node id, CLIPLoader type is `flux2`, model names come from `models`; `run` maps a history entry with `status.status_str=="error"` to `ComfyError` (fake HTTP via monkeypatched opener); unreachable URL → message "ComfyUI is not running".
- [ ] Implement; also validate graph live once against ComfyUI `/prompt` (expect no `node_errors`). Commit.

### Task 5: Pipeline orchestrator

**Files:** Create `genfill/pipeline.py`, `genfill/settings.py`; Test `tests/test_pipeline.py`

**Interfaces — Produces:**
- `Settings` dataclass (port, token_file, exchange_dir, comfy_url, steps=6, megapixels=1.0, models) with `load()/save()` to `settings.json`.
- `run_fill(prompt:str, settings:Settings, status: Callable[[str],None], progress: Callable[[float],None], cancel: Event, pc=None, comfy=None) -> str` (layer name). Steps: document → no selection → raise `UserFacingError("Make a selection first")`; render; mask; resize to working size; upload; run; upscale; merge; save PNG in exchange dir; place_layer.

- [ ] Tests with fakes for both clients: no selection → error, comfy never called; happy path → placed PNG equals document size and name starts with "Generative Fill".
- [ ] Implement, tests PASS, commit.

### Task 6: Companion window + launcher

**Files:** Create `genfill/app.py`, `GenFill.pyw`, `make_shortcut.ps1`, `README.md`

- [ ] Tkinter always-on-top window: prompt entry, Generate/Cancel, progress bar, status label, Settings dialog, "Setup PhotoCraft connection" button (calls `setup_env.install`, shows restart note). Worker thread + `after()` polling queue.
- [ ] `make_shortcut.ps1` creates desktop shortcut running ComfyUI's `pythonw.exe GenFill.pyw`.
- [ ] Manual check: window opens, Setup status reads correctly. Commit.

### Task 7: End-to-end check

- [ ] With the user: Setup → restart launcher/PhotoCraft → open post.jpg at 1080×1920 → select empty bands → Generate → new layer "Generative Fill" with mask appears, Layer 0 unchanged. Tune steps/feather for seams.
