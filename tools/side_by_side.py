"""Save a side-by-side JPEG of several images (each scaled to the same height). Usage: out.jpg img1 img2 ..."""
import sys
from PIL import Image

out, paths = sys.argv[1], sys.argv[2:]
h = 800
ims = [Image.open(p).convert("RGB") for p in paths]
ims = [im.resize((round(im.width * h / im.height), h)) for im in ims]
canvas = Image.new("RGB", (sum(i.width for i in ims) + 10 * (len(ims) - 1), h), "white")
x = 0
for im in ims:
    canvas.paste(im, (x, 0))
    x += im.width + 10
canvas.save(out, quality=88)
