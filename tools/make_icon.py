"""Draws the app icon (a sparkle over a dark rounded square) into assets/icon.png and assets/icon.ico."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "assets"


def sparkle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    w = r * 0.28
    pts = [(cx, cy - r), (cx + w, cy - w), (cx + r, cy), (cx + w, cy + w),
           (cx, cy + r), (cx - w, cy + w), (cx - r, cy), (cx - w, cy - w)]
    draw.polygon(pts, fill=fill)


def make(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = size * 0.06
    d.rounded_rectangle((m, m, size - m, size - m), radius=size * 0.2, fill=(38, 38, 42, 255))
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sparkle(ImageDraw.Draw(glow), size * 0.46, size * 0.54, size * 0.32, (64, 140, 255, 200))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(size * 0.04)))
    sparkle(d, size * 0.46, size * 0.54, size * 0.30, (84, 160, 255, 255))
    sparkle(d, size * 0.72, size * 0.28, size * 0.12, (230, 236, 245, 255))
    return img


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    icon = make()
    icon.save(OUT / "icon.png")
    icon.save(OUT / "icon.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", OUT / "icon.png", OUT / "icon.ico")
