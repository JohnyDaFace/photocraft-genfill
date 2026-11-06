"""Seam check: one generation, composited the old way (hard selection mask, no colour match) and the
new way (colour match + soft expanded layer mask), as PhotoCraft would show them. Usage: photo out_dir"""
import random, sys, threading
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill import imaging
from genfill.comfy import ComfyClient, build_fill_workflow
from genfill.settings import Settings

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
pic = Image.open(src).convert("RGB")
W = pic.width; H = round(W * 16 / 9); top = (H - pic.height) // 2
doc = Image.new("RGBA", (W, H), (0, 0, 0, 0)); doc.paste(pic, (0, top))
sel_bounds = (0, 0, W, H)
s = Settings.load()
mask = imaging.fill_mask(doc, sel_bounds)
ww, wh = imaging.working_size(W, H)
c = ComfyClient(s.comfy_url)
a = c.upload(imaging.prepare_source(doc).resize((ww, wh), Image.LANCZOS), "seam_src.png")
b = c.upload(mask.resize((ww, wh), Image.BILINEAR).convert("RGB"), "seam_mask.png")
gen = c.run(build_fill_workflow(a, b, "", random.randint(0, 2**32), s.steps, ww, wh, s.models),
            on_progress=lambda f: None, cancel=threading.Event())

selection = Image.fromarray(((np.asarray(doc.getchannel("A")) < imaging.OPAQUE) * 255).astype(np.uint8), "L")
base = Image.new("RGB", (W, H), (255, 255, 255)); base.paste(pic, (0, top))

def show(layer, layer_mask):  # what PhotoCraft displays: layer over Layer 0 through the layer mask
    return Image.composite(layer.convert("RGB"), base, layer_mask)

old = show(imaging.merge(doc, gen, mask), selection)
grown = selection.filter(ImageFilter.BoxBlur(imaging.LAYER_MASK_EXPAND)).point(lambda v: 255 if v > 0 else 0)
soft = grown.filter(ImageFilter.GaussianBlur(imaging.LAYER_MASK_FEATHER / 2))
fixed = imaging.match_colors(doc, gen, mask)
new = show(imaging.merge(doc, fixed, imaging.blend_mask(doc, sel_bounds)), soft)

def step(img, y):  # mean brightness jump across a horizontal seam at row y
    g = np.asarray(img.convert("L")).astype(float)
    return abs(g[y - 3:y].mean() - g[y:y + 3].mean())

for name, img in (("old", old), ("new", new)):
    img.save(out / f"{name}.jpg", quality=92)
    print(f"{name}: brightness jump at top seam {step(img, top):.1f}, bottom seam {step(img, top + pic.height):.1f}")
    crop = Image.new("RGB", (W, 300), "red")
    crop.paste(img.crop((0, top - 70, W, top + 70)), (0, 0))
    crop.paste(img.crop((0, top + pic.height - 70, W, top + pic.height + 70)), (0, 160))
    crop.save(out / f"seam_{name}.png")
