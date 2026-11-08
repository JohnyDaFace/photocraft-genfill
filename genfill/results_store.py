"""Lasting results: every set of results with one placed in PhotoCraft is kept on disk, and the layer it
went into carries the set's id (in its XMP metadata, which is saved with the document). Selecting that
layer later, even after restarting everything, brings the set back so another result can be swapped in.

Only what a swap needs is kept: for a fill, the area around the selection (the layer keeps its own mask);
for an edit, the whole picture. The oldest sets are deleted once there are more than `keep`."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from PIL import Image

from .imaging import LAYER_MASK_EXPAND, LAYER_MASK_FEATHER

TAG_NS = "https://github.com/JohnyDaFace/photocraft-genfill/ns/1.0/"
TAG_RE = re.compile(r'genfill:results="([0-9a-f]{8,32})"')
MARGIN = LAYER_MASK_EXPAND + 2 * LAYER_MASK_FEATHER + 16  # the layer mask reaches this far past the selection


def tag_packet(set_id: str) -> str:
    """A minimal XMP packet naming the set (PhotoCraft keeps it with the layer, like Photoshop)."""
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about="" xmlns:genfill="{TAG_NS}" genfill:results="{set_id}"/>'
            '</rdf:RDF></x:xmpmeta>')


def tag_of(packet: str | None) -> str | None:
    m = TAG_RE.search(packet or "")
    return m.group(1) if m else None


def _area(results) -> tuple[int, int, int, int]:
    """The part of the pictures a swap uses."""
    w, h = results.images[0].size
    if results.masked and results.job is not None and results.job.mask is not None:
        box = results.job.mask.getbbox()
        if box:
            return (max(0, box[0] - MARGIN), max(0, box[1] - MARGIN), min(w, box[2] + MARGIN),
                    min(h, box[3] + MARGIN))
    boxes = [im.getchannel("A").getbbox() for im in results.images if im.mode == "RGBA"]
    boxes = [b for b in boxes if b]
    if boxes:
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes))
    return (0, 0, w, h)


def save(results, root: Path, keep: int = 50) -> str:
    """Write the set to disk; returns its id."""
    set_id = uuid.uuid4().hex[:16]
    d = Path(root) / set_id
    d.mkdir(parents=True, exist_ok=True)
    area = _area(results)
    for i, (im, pv) in enumerate(zip(results.images, results.previews)):
        im.crop(area).save(d / f"{i}.png")
        pv.convert("RGB").save(d / f"{i}_preview.jpg", quality=90)
    p = results.placed
    meta = {"layer": results.layer, "masked": results.masked, "size": list(results.size),
            "selection": list(results.selection) if results.selection else None,
            "expand_to": list(results.expand_to) if results.expand_to else None,
            "full": list(results.images[0].size), "area": list(area), "count": len(results.images),
            "index": p.index if p else 0, "shift": list(p.shift) if p else [0, 0], "created": time.time()}
    (d / "set.json").write_text(json.dumps(meta), encoding="utf-8")
    prune(root, keep)
    return set_id


def set_index(root: Path, set_id: str, index: int) -> None:
    """Remember which result is in PhotoCraft now."""
    f = Path(root) / set_id / "set.json"
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
        meta["index"] = index
        f.write_text(json.dumps(meta), encoding="utf-8")
    except (OSError, ValueError):
        pass


def load(root: Path, set_id: str):
    """(Results, index, shift) for a saved set, or None if it is gone. The pictures come back at full size,
    transparent outside the kept area; there's no job, so no "More like this"."""
    from .pipeline import Results  # here: pipeline imports this module
    d = Path(root) / set_id
    try:
        meta = json.loads((d / "set.json").read_text(encoding="utf-8"))
        images, previews = [], []
        for i in range(meta["count"]):
            full = Image.new("RGBA", tuple(meta["full"]), (0, 0, 0, 0))
            with Image.open(d / f"{i}.png") as piece:
                full.paste(piece.convert("RGBA"), tuple(meta["area"][:2]))
            images.append(full)
            with Image.open(d / f"{i}_preview.jpg") as pv:
                previews.append(pv.convert("RGB"))
    except (OSError, ValueError, KeyError):
        return None
    sel = tuple(meta["selection"]) if meta.get("selection") else None
    r = Results(images, previews, meta["layer"], meta["masked"], tuple(meta["size"]), sel,
                expand_to=tuple(meta["expand_to"]) if meta.get("expand_to") else None)
    r.saved = set_id
    return r, int(meta.get("index", 0)), tuple(meta.get("shift", (0, 0)))


def prune(root: Path, keep: int) -> None:
    sets = sorted((p for p in Path(root).glob("*") if (p / "set.json").exists()), key=lambda p: p.stat().st_mtime)
    for old in sets[:max(0, len(sets) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
