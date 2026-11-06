"""Manual quality check of the upscaler on a photo file, with real ComfyUI and a stand-in PhotoCraft."""
import sys, threading, time
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill.pipeline import run_upscale
from genfill.settings import Settings


class FilePC:
    def __init__(self, img): self.img, self.opened = img, []
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def render(self): return self.img
    def open_document(self, rel): self.opened.append(rel)


src, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
pc = FilePC(Image.open(src).convert("RGBA"))
s = Settings(exchange_dir=str(out_dir))
t = time.time()
print(run_upscale("", s, print, lambda f: None, threading.Event(), pc=pc))
print(f"{time.time() - t:.0f}s ->", out_dir / pc.opened[0])
