"""Probe the desktop app's round trip on a throwaway document (never touches the user's documents)."""
import json, sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from genfill.settings import Settings
from genfill.photocraft_client import PhotoCraftClient, PhotoCraftError

s = Settings.load()
ex = Path(s.exchange_dir); ex.mkdir(parents=True, exist_ok=True)
Image.new("RGBA", (400, 600), (0, 255, 0, 255)).save(ex / "probe_result.png")


def step(label, fn):
    try:
        r = fn()
        print(f"OK   {label}: {json.dumps(r)[:300]}")
        return r
    except PhotoCraftError as e:
        print(f"FAIL {label}: {e}")


def close_if(pc, expected_name):
    """Close the active document only if it is one this probe created; never anything else."""
    name = pc.execute("document.inspect")["name"]
    if name != expected_name:
        raise PhotoCraftError(f"refusing to close '{name}' (expected '{expected_name}')")
    return pc.execute("file.close", {"save": "discard"})


def info(pc):
    d = pc.execute("document.inspect")
    return {"name": d["name"], "size": [d["width"], d["height"]], "sel": d["selectionBounds"],
            "layers": [(l["name"], l["kind"], l["hasMask"]) for l in d["layers"]], "path": d.get("path")}


with PhotoCraftClient(s.port, Path(s.token_file), timeout=60) as pc:
    step("ui.inspect documents", lambda: {k: v for k, v in pc.call("ui.inspect", {}).items() if "doc" in k.lower()})
    step("file.new", lambda: pc.execute("file.new", {"width": 400, "height": 600, "background": "transparent", "name": "genfill-probe"}))
    step("info", lambda: info(pc))
    step("select.rect", lambda: pc.execute("select.rect", {"x": 0, "y": 0, "width": 400, "height": 100}))
    step("select.rect add", lambda: pc.execute("select.rect", {"x": 0, "y": 500, "width": 400, "height": 100, "mode": "add"}))
    step("app.save png", lambda: pc.call("app.save", {"path": "probe_render.png"}))
    step("info after save (name/path changed?)", lambda: info(pc))
    step("saveSelection", lambda: pc.execute("select.saveSelection", {"name": "genfill-sel"}))
    step("app.open result", lambda: pc.call("app.open", {"path": "probe_result.png"}))
    step("info opened", lambda: info(pc))
    step("select.all", lambda: pc.execute("select.all"))
    step("edit.copy", lambda: pc.execute("edit.copy"))
    step("close opened result", lambda: close_if(pc, "probe_result.png"))
    step("info active after close", lambda: info(pc))
    step("pasteInPlace", lambda: pc.execute("edit.pasteSpecial.pasteInPlace"))
    step("loadSelection", lambda: pc.execute("select.loadSelection", {"channel": "genfill-sel"}))
    step("revealSelection", lambda: pc.execute("layer.layerMask.revealSelection"))
    step("rename", lambda: pc.execute("layer.renameLayer", {"name": "Generative Fill"}))
    step("channel.delete", lambda: pc.execute("channel.delete", {"channel": "genfill-sel"}))
    step("info final", lambda: info(pc))
    step("close probe doc", lambda: close_if(pc, "genfill-probe"))
    step("info active (should be the user's doc)", lambda: info(pc))
print("render file:", (ex / "probe_render.png").exists() and Image.open(ex / "probe_render.png").size)
