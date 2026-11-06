"""End-to-end Generate in the running PhotoCraft on a separate test document (left open to look at)."""
import sys, threading, time
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill.pipeline import run_fill
from genfill.photocraft_client import PhotoCraftClient
from genfill.settings import Settings

s = Settings.load()
pic = Image.open(sys.argv[1]).convert("RGB")
W = pic.width; H = round(W * 16 / 9); top = (H - pic.height) // 2
doc = Image.new("RGBA", (W, H), (0, 0, 0, 0)); doc.paste(pic, (0, top))
doc.save(Path(s.exchange_dir) / "genfill-test.png")

with PhotoCraftClient(s.port, Path(s.token_file), exchange_dir=Path(s.exchange_dir)) as pc:
    print("open:", pc.call("app.open", {"path": "genfill-test.png"}))
    pc.execute("select.rect", {"x": 0, "y": 0, "width": W, "height": top})
    pc.execute("select.rect", {"x": 0, "y": top + pic.height, "width": W, "height": H - top - pic.height, "mode": "add"})
    print("doc:", pc.document())

t = time.time()
print(run_fill("", s, lambda m: print(" ", m), lambda f: None, threading.Event()))
print(f"{time.time() - t:.0f}s")
with PhotoCraftClient(s.port, Path(s.token_file)) as pc:
    d = pc.execute("document.inspect")
    print("layers:", [(l["name"], l["kind"], l["hasMask"]) for l in d["layers"]], "active doc:", d["name"])
