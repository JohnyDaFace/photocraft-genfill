"""Spike: probe the installed PhotoCraft engine over `photocraft-cli serve` (stdio JSON lines)."""
import base64, io, json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw

CLI = r"C:\Program Files\PhotoCraft\photocraft-cli.exe"
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)

# test doc: 1080x1350 picture centred on a transparent 1080x1920 canvas
pic = Image.new("RGB", (1080, 1350), (200, 150, 100))
ImageDraw.Draw(pic).ellipse((300, 400, 780, 900), fill=(60, 120, 200))
doc = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
doc.paste(pic, (0, 285))
doc.save(root / "doc.png")
Image.new("RGBA", (1080, 1920), (0, 255, 0, 255)).save(root / "result.png")

p = subprocess.Popen([CLI, "serve", "--automation-read-root", str(root), "--automation-write-root", str(root)],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
n = 0
def call(method, params=None, show=600):
    global n
    n += 1
    p.stdin.write(json.dumps({"id": n, "method": method, "params": params or {}}) + "\n"); p.stdin.flush()
    line = p.stdout.readline()
    r = json.loads(line)
    s = json.dumps(r)
    print(f"--- {method} {json.dumps(params)[:200]}\n{s[:show]}{' …' if len(s) > show else ''}\n")
    return r

call("doc.open", {"path": str(root / "doc.png")})
call("doc.inspect", {}, 1500)
r = call("doc.render", {"maxSide": 0}, 200)
res = r.get("result") or {}
if "base64" in res:
    im = Image.open(io.BytesIO(base64.b64decode(res["base64"])))
    print("render:", im.size, im.mode, "corner px", im.getpixel((5, 5)), "mid px", im.getpixel((540, 960)))
call("engine.execute", {"command": "select.rect", "params": {"rect": [0, 0, 1080, 285]}})
call("engine.execute", {"command": "select.rect", "params": {"x": 0, "y": 0, "width": 1080, "height": 285}})
call("doc.inspect", {}, 1500)
call("doc.place", {"path": "result.png"})
call("doc.inspect", {}, 2500)
call("methods", {}, 3000)
p.stdin.close(); p.wait(10)
