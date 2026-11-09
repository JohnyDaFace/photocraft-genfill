"""Draws the bar's icons from Lucide's SVG files (assets/icons, ISC licence): outline icons on a 24 px grid,
2 px round strokes. Only what those files use is understood: path (M L H V C S Q T A Z), line, polyline,
polygon, circle, ellipse and rect. Drawn with Pillow, oversampled, so they stay crisp at any screen scale."""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw

STEPS = 16  # straight pieces per curve


def _nums(s: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?(?:\d+\.\d*|\.\d+|\d+)(?:e-?\d+)?", s or "")]


def _arc(x0, y0, rx, ry, phi, large, sweep, x1, y1) -> list[tuple[float, float]]:
    """Points along an SVG elliptical arc (endpoint form), without the start point."""
    if rx == 0 or ry == 0:
        return [(x1, y1)]
    rx, ry = abs(rx), abs(ry)
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (x0 - x1) / 2, (y0 - y1) / 2
    x1p, y1p = c * dx + s * dy, -s * dx + c * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    k = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        k = -k
    cxp, cyp = k * rx * y1p / ry, -k * ry * x1p / rx
    cx, cy = c * cxp - s * cyp + (x0 + x1) / 2, s * cxp + c * cyp + (y0 + y1) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t0 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(4, int(abs(dt) / (math.pi / 16)))
    pts = []
    for i in range(1, n + 1):
        t = t0 + dt * i / n
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        pts.append((c * ex - s * ey + cx, s * ex + c * ey + cy))
    return pts


def path_lines(d: str) -> list[tuple[list, bool]]:
    """(points, closed) for each subpath of an SVG path."""
    tokens = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.\d*|\.\d+|\d+)(?:e-?\d+)?", d)
    out, pts = [], []
    x = y = sx = sy = 0.0
    last_c = last_q = None
    cmd, i = None, 0

    def nxt(n):
        nonlocal i
        vals = [float(v) for v in tokens[i:i + n]]
        i += n
        return vals

    def bez(p0, p1, p2, p3):
        return [((1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t ** 2 * p2[0] + t ** 3 * p3[0],
                 (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t ** 2 * p2[1] + t ** 3 * p3[1])
                for t in (j / STEPS for j in range(1, STEPS + 1))]

    def quad(p0, p1, p2):
        return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0],
                 (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1])
                for t in (j / STEPS for j in range(1, STEPS + 1))]

    while i < len(tokens):
        if re.match(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if pts:
                    out.append((pts, True))
                pts, (x, y) = [], (sx, sy)
                last_c = last_q = None
                continue
        rel = cmd.islower()
        C = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if C == "M":
            if pts:
                out.append((pts, False))
            x, y = nxt(2)
            x, y = x + ox, y + oy
            sx, sy = x, y
            pts = [(x, y)]
            cmd = "l" if rel else "L"  # more pairs after M are lines
            last_c = last_q = None
            continue
        if not pts:
            pts = [(x, y)]
        if C == "L":
            a, b = nxt(2)
            x, y = a + ox, b + oy
            pts.append((x, y))
            last_c = last_q = None
        elif C == "H":
            (a,) = nxt(1)
            x = a + ox
            pts.append((x, y))
            last_c = last_q = None
        elif C == "V":
            (b,) = nxt(1)
            y = b + oy
            pts.append((x, y))
            last_c = last_q = None
        elif C in "CS":
            if C == "C":
                x1, y1, x2, y2, x3, y3 = nxt(6)
                p1 = (x1 + ox, y1 + oy)
            else:
                x2, y2, x3, y3 = nxt(4)
                p1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
            p2, p3 = (x2 + ox, y2 + oy), (x3 + ox, y3 + oy)
            pts += bez((x, y), p1, p2, p3)
            last_c, last_q = p2, None
            x, y = p3
        elif C in "QT":
            if C == "Q":
                x1, y1, x3, y3 = nxt(4)
                p1 = (x1 + ox, y1 + oy)
            else:
                x3, y3 = nxt(2)
                p1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
            p3 = (x3 + ox, y3 + oy)
            pts += quad((x, y), p1, p3)
            last_q, last_c = p1, None
            x, y = p3
        elif C == "A":
            rx, ry, phi, large, sweep, a, b = nxt(7)
            ex, ey = a + ox, b + oy
            pts += _arc(x, y, rx, ry, phi, int(large), int(sweep), ex, ey)
            x, y = ex, ey
            last_c = last_q = None
        else:
            i += 1  # something not understood: skip it
    if pts:
        out.append((pts, False))
    return out


def _ellipse(cx, cy, rx, ry, n=48):
    return [(cx + rx * math.cos(2 * math.pi * j / n), cy + ry * math.sin(2 * math.pi * j / n)) for j in range(n)]


def shapes(svg_text: str) -> list[tuple[list, bool]]:
    """Every outline in the file as (points, closed) on the 24 px grid."""
    root = ET.fromstring(re.sub(r"<!--.*?-->", "", svg_text, flags=re.S))
    out = []
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        a = el.attrib
        f = lambda k, d=0.0: float(a.get(k, d))  # noqa: E731
        if tag == "path":
            out += path_lines(a.get("d", ""))
        elif tag == "line":
            out.append(([(f("x1"), f("y1")), (f("x2"), f("y2"))], False))
        elif tag in ("polyline", "polygon"):
            v = _nums(a.get("points"))
            out.append((list(zip(v[::2], v[1::2])), tag == "polygon"))
        elif tag == "circle":
            out.append((_ellipse(f("cx"), f("cy"), f("r"), f("r")), True))
        elif tag == "ellipse":
            out.append((_ellipse(f("cx"), f("cy"), f("rx"), f("ry")), True))
        elif tag == "rect":
            x, y, w, h = f("x"), f("y"), f("width"), f("height")
            r = min(f("rx", a.get("ry", 0)), w / 2, h / 2)
            if r <= 0:
                out.append(([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True))
            else:  # rounded corners as quarter circles
                pts = []
                for (cx, cy), start in (((x + w - r, y + r), -90), ((x + w - r, y + h - r), 0),
                                        ((x + r, y + h - r), 90), ((x + r, y + r), 180)):
                    pts += [(cx + r * math.cos(math.radians(start + 90 * j / 8)),
                             cy + r * math.sin(math.radians(start + 90 * j / 8))) for j in range(9)]
                out.append((pts, True))
    return out


def render(path: Path, size: int, color: tuple, stroke: float = 2.0, oversample: int = 4) -> Image.Image:
    """The icon as an RGBA picture `size` px square, its strokes in `color`."""
    S = size * oversample
    k = S / 24.0
    w = max(1.0, stroke * k)
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    for pts, closed in shapes(path.read_text(encoding="utf-8")):
        p = [(x * k, y * k) for x, y in pts]
        if closed and p[0] != p[-1]:
            p.append(p[0])
        if len(p) > 1:
            d.line(p, fill=255, width=round(w))
        for x, y in p:  # round caps and joins
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    im = Image.new("RGBA", (size, size), color[:3] + (0,))
    im.putalpha(mask.point(lambda v: v * (color[3] if len(color) > 3 else 255) // 255))
    return im
