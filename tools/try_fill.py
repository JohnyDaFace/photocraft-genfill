"""Quality comparison: pad a photo to 9:16 transparent and fill it with each variant, same seed.
Usage: try_fill.py photo out_dir [variants...]   variants: old (blurry reference), ref (photo-only reference)"""
import random, sys, threading, time
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill.comfy import ComfyClient, build_fill_workflow
from genfill.imaging import fill_mask, merge, prepare_source, working_size
from genfill.settings import Settings

src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
variants = sys.argv[3:] or ["old", "ref"]
out_dir.mkdir(parents=True, exist_ok=True)
pic = Image.open(src).convert("RGB")
W = pic.width; H = round(W * 16 / 9); top = (H - pic.height) // 2
doc = Image.new("RGBA", (W, H), (0, 0, 0, 0)); doc.paste(pic, (0, top))
s = Settings.load()
mask = fill_mask(doc, (0, 0, W, H))
ww, wh = working_size(W, H)
c = ComfyClient(s.comfy_url)
img_name = c.upload(prepare_source(doc).resize((ww, wh), Image.LANCZOS), "try_src.png")
mask_name = c.upload(mask.resize((ww, wh), Image.BILINEAR).convert("RGB"), "try_mask.png")
rw, rh = working_size(pic.width, pic.height)
ref_name = c.upload(pic.resize((rw, rh), Image.LANCZOS), "try_ref.png")
seed = random.randint(0, 2**32)
for v in variants:
    t = time.time()
    wf = build_fill_workflow(img_name, mask_name, "", seed, s.steps, ww, wh, s.models,
                             ref_name=ref_name if v == "ref" else None)
    gen = c.run(wf, on_progress=lambda f: None, cancel=threading.Event())
    merge(doc, gen, mask).convert("RGB").save(out_dir / f"{v}.jpg", quality=92)
    print(f"{v}: {time.time() - t:.0f}s")
