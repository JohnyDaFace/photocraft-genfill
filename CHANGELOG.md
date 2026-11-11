# Changelog

## Unreleased

### Added

- The prompt field wraps and grows for longer descriptions, with Shift+Enter for a new line.
- Added Caricature drawing to the Art presets, with a thumbnail generated through FLUX.2.

### Fixed

- Create can apply a selected style to one reference picture without a typed prompt.
- Create explains when the selected model cannot see the reference picture in that flow.
- Create ignores a saved second-reference path when no main reference is attached.
- Film noir no longer asks the model to add venetian blinds to the scene.
- Refreshed the bar menus and Settings dropdown appearance.

### Documentation

- Updated the README for the expanding prompt, new Art preset, and reference-only Create flow.

## 1.3.1 — 2026-11-10

### Added

- Saved ComfyUI workflows can be selected from the Image model dropdown in Settings.
- Model LoRAs can be selected from ComfyUI's available files, with a strength field for each one.
- A button on the bar minimizes it without opening the settings menu.

### Fixed

- Improved PhotoCraft connection recovery and live connection status after a disconnect.
- Corrected how Create prepares reference pictures and output sizes so pictures with different proportions are not stretched.
- Adjusted style prompts for FLUX 3 while preserving the other models' prompts.
- Positioned bar tooltips on the same monitor as the bar and refined the compact icon layout.
- Improved Linux PhotoCraft environment setup and packaged app startup.

### Documentation

- Explained Linux compositor environments, saved workflows, reference pictures, and the LoRA picker in the README.

## 1.3.0

- Introduced the redesigned companion bar and expanded image editing tools.
