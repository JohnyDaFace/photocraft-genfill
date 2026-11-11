# Changelog

## Unreleased

### Added

- Workflow node mapping, seeds, output size and additional outputs now open in a separate Workflow settings window; the main Settings window scrolls with Save fixed at the bottom.
- Workflow settings explain Image 1 and use plain result names (such as Generated image and Color matched image); they show a dropdown only when a choice is needed.
- Custom workflows can keep their saved seeds and generated image dimensions.
- Custom workflows can return selected additional image outputs from one run, including paired previews.
- A native-size custom result opens as a new PhotoCraft document when it cannot fit the current canvas unchanged.
- Added a Photoshop UXP panel that uses the same companion, ComfyUI models and custom workflows.
- New installations can choose PhotoCraft, Photoshop or both during first-run setup; Settings can install the Photoshop panel later.
- The companion bar follows the last clicked editor and controls Photoshop's prompt, styles, reference, result count, Create, Generate, and result picker; the Photoshop panel provides the document connection.
- Photoshop now shows the bar's Select, Remove, Fix, and Resize tools for an open document, with selection masks exchanged through the paired panel.

### Fixed

- Hyprland/XWayland menus stay open when focus moves between the bar and its own popup, and close when another app takes focus.
- Create now sends the bar's reference image to a custom workflow's first image input; a second image input is needed only when two images are attached.
- Photoshop keeps its document status while the panel captures pixels, reports capture progress, and transfers full-frame pixel buffers without the encoder that stalled on layered documents.

### Documentation

- The README now lists Photoshop as a supported editor and gives it a separate setup path.

## 1.3.2 — 2026-11-11

### Added

- The prompt field wraps and grows for longer descriptions, with Shift+Enter for a new line.
- Added Caricature drawing to the Art presets, with a thumbnail generated through FLUX.2.

### Fixed

- Create can apply a selected style to one reference picture without a typed prompt.
- Create explains when the selected model cannot see the reference picture in that flow.
- Create ignores a saved second-reference path when no main reference is attached.
- Film noir no longer asks the model to add venetian blinds to the scene.
- On Hyprland/XWayland, open menus close when another window gets focus.
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
