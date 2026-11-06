"""Screenshot the companion window in each Pro theme (no connections made). Usage: out_dir"""
import sys, time, tkinter as tk
from pathlib import Path
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill import app as app_mod

app_mod.App.check_connections = lambda self: self.events.put(
    ("status", "PhotoCraft: connected.\nComfyUI: connected."))
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
root = tk.Tk()
a = app_mod.App(root)
root.geometry("+200+200")
for name in ("Pro", "ProMedium", "ProLightGray", "ProLight"):
    a.theme_name = None
    a.set_theme(name)
    end = time.time() + 1.0
    while time.time() < end:
        root.update(); time.sleep(0.03)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab((x - 8, y - 32, x + root.winfo_width() + 8, y + root.winfo_height() + 8), all_screens=True).save(out / f"{name}.png")
root.destroy()
