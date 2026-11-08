"""Lasting photoshoots: every shoot is kept on disk (the subject, the hero shot and what was asked for), and
each shot's layer carries the run's id and its number (in its XMP metadata, saved with the document).
Selecting such a layer later brings the run back so a shot can be redone ("Reshoot").

A run stores plain strings (the set and the shot descriptions), so editing shoots.json later can't change it.
The oldest runs are deleted once there are more than `keep`."""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .results_store import TAG_NS

TAG_RE = re.compile(r'genfill:shoot="([0-9a-f]{8,32})"\s+genfill:shot="(\d+)"')


@dataclass
class ShootRun:
    shoot: str                        # the shoot's name
    kind: str                         # "product" or "people"
    set_text: str                     # the set / look the shoot was made with
    shots: list[tuple[str, str]]      # (shot name, shot description), in layer order
    shape: str                        # a Create shape, e.g. "1:1"
    size: tuple[int, int]             # pixels
    created: float = 0.0


def tag_packet(run_id: str, index: int) -> str:
    """A minimal XMP packet naming the run and the shot (1-based), kept with the layer like results_store's."""
    return ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            f'<rdf:Description rdf:about="" xmlns:genfill="{TAG_NS}" genfill:shoot="{run_id}" genfill:shot="{index}"/>'
            '</rdf:RDF></x:xmpmeta>')


def tag_of(packet: str | None) -> tuple[str, int] | None:
    m = TAG_RE.search(packet or "")
    return (m.group(1), int(m.group(2))) if m else None


def save_run(root: Path, run: ShootRun, subject: Image.Image, hero: Image.Image, keep: int = 50) -> str:
    """Write the run to disk; returns its id."""
    run_id = uuid.uuid4().hex[:16]
    d = Path(root) / run_id
    d.mkdir(parents=True, exist_ok=True)
    subject.save(d / "subject.png")
    hero.save(d / "hero.png")
    meta = {"shoot": run.shoot, "kind": run.kind, "set_text": run.set_text,
            "shots": [list(s) for s in run.shots], "shape": run.shape, "size": list(run.size),
            "created": run.created or time.time()}
    (d / "shoot.json").write_text(json.dumps(meta), encoding="utf-8")
    prune(root, keep)
    return run_id


def load_run(root: Path, run_id: str):
    """(ShootRun, subject, hero) for a saved run, or None if it is gone or damaged."""
    d = Path(root) / run_id
    try:
        meta = json.loads((d / "shoot.json").read_text(encoding="utf-8"))
        with Image.open(d / "subject.png") as im:
            subject = im.copy()
        with Image.open(d / "hero.png") as im:
            hero = im.copy()
        run = ShootRun(meta["shoot"], meta["kind"], meta["set_text"], [(str(a), str(b)) for a, b in meta["shots"]],
                       meta["shape"], (int(meta["size"][0]), int(meta["size"][1])), float(meta.get("created", 0.0)))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return run, subject, hero


def prune(root: Path, keep: int) -> None:
    runs = sorted((p for p in Path(root).glob("*") if (p / "shoot.json").exists()), key=lambda p: p.stat().st_mtime)
    for old in runs[:max(0, len(runs) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
