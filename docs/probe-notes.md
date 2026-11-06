# PhotoCraft 0.20.2 control probe (2026-11-06, headless `photocraft-cli serve`)

- `doc.inspect {}` → `width`, `height`, `hasSelection`, `selectionBounds: [x0, y0, x1, y1] | null`, `layers[]`, `activeLayer`.
- `doc.render {maxSide: 0}` → `{base64}` full-size RGBA PNG; transparent canvas stays alpha 0.
- `engine.execute select.rect {x, y, width, height, mode?: "add"}` (no `rect` array).
- `doc.place {path}` (relative to read root) → Smart Object at `bounds [0,0,W,H]`, `scale 100` when the PNG is document-sized; selection kept.
- `layer.rasterize.smartObject` → `{layer, layers}`; `layer.layerMask.revealSelection` adds mask and consumes the selection; `layer.renameLayer {name}`.
- Disjoint selections (top + bottom band) report one bounding box covering both → generation mask must not be the bare bounds.
- History: Place Embedded, Rasterize Smart Object, Add Layer Mask, Rename Layer (4 undo steps).
