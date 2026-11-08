"""Photoshoots: one subject (a product or a person), one set, a run of different shots of it.

A shoot is a named set (the place, light and colour grade, in a sentence or two) for a kind of subject. The shots
are shared per kind: 12 for products, 12 for people, the first being the hero that fixes the set; a run takes the
first 4, 8 or 12. Every shot says clearly where the camera is and what the subject does, because vague angle
words were ignored, steep high angles broke labels (so none), people keep the same background unless each shot
names a concrete spot in the location, and a very wide person shot came out broken (so knees-up or closer).

The built-in shoots come from here, so a new version can improve them. shoots.json (next to settings.json) holds
only yours: new shoots, a built-in's name with your own words to change it, and names to hide."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import styles

SHOOTS_FILE = styles.CONFIG_DIR / "shoots.json"
KINDS = ("product", "people")


@dataclass(frozen=True)
class Shot:
    name: str
    text: str


@dataclass(frozen=True)
class Shoot:
    name: str
    kind: str  # "product" | "people"
    set: str
    shots: tuple[Shot, ...] = ()  # empty: the shared list for the kind
    subject: str = ""  # the SAM 3 word for the subject; empty: the default for the kind


# products: the label faces the camera in every shot; no steep high angles (they broke labels)
PRODUCT_SHOTS: tuple[Shot, ...] = (
    Shot("hero", "hero shot: the product standing upright facing the camera, eye level, medium shot, "
                 "label fully readable"),
    Shot("label close-up", "close-up of the front label, the label fills much of the frame, perfectly sharp "
                           "and readable"),
    Shot("gentle low angle", "low angle hero shot, the camera just below the product looking slightly up at it, "
                             "label facing the camera"),
    Shot("three-quarter turn", "three-quarter view, the product turned about 30 degrees, the label still "
                               "visible and facing mostly towards the camera"),
    Shot("wide set", "wide shot showing the whole set around the product, the product smaller in the frame, "
                     "label facing the camera"),
    Shot("detail macro", "macro detail of the cap and the top of the product, shallow depth of field, "
                         "label edge in focus"),
    Shot("in hand", "the product held in an elegant hand at chest height, label facing the camera, the face "
                    "out of frame"),
    Shot("lifestyle props", "the product at eye level surrounded by a few props that suit it, placed beside "
                            "and in front of it so none cover the label, label facing the camera"),
    Shot("side light", "eye-level shot with the light coming strongly from one side, a long soft shadow "
                       "falling across the surface, label facing the camera"),
    Shot("off-centre", "eye-level shot with the product on the right third of the frame and calm empty space "
                       "to its left, label facing the camera"),
    Shot("close three-quarter", "close shot at eye level from the left, the product turned about 20 degrees "
                                "towards the camera, filling most of the frame, label readable"),
    Shot("pair", "the product standing next to a second identical one, both upright at eye level, labels "
                 "facing the camera"),
)

# people: knees-up or closer; each shot names a concrete spot in the location and a different pose and expression
PEOPLE_SHOTS: tuple[Shot, ...] = (
    Shot("hero", "head-and-shoulders portrait facing the camera, standing in the middle of the set, eye level, "
                 "calm confident expression"),
    Shot("by the window", "waist-up portrait standing by the window, body turned slightly to the left, soft "
                          "light on the face, looking into the camera"),
    Shot("laughing", "candid moment, laughing naturally, looking off camera, standing against the wall"),
    Shot("close-up", "tight close-up of the face, eyes sharp, in front of the most open part of the background"),
    Shot("arms crossed", "waist-up portrait leaning against the wall with arms crossed, slight smile"),
    Shot("profile", "profile view looking to the right, standing near the window with side light on the face"),
    Shot("knees-up", "three-quarter length portrait from the knees up, standing relaxed in the open middle of "
                     "the set, hands in pockets"),
    Shot("over the shoulder", "looking back over the shoulder towards the camera, shot from beside a doorway "
                              "or the edge of the set"),
    Shot("seated", "seated at a table or on a bench, waist-up, leaning forward on one elbow, thoughtful "
                   "expression looking slightly past the camera"),
    Shot("walking", "walking towards the camera along the length of the set, three-quarter length from the "
                    "knees up, mid-stride, relaxed smile"),
    Shot("hand on chin", "head-and-shoulders portrait sitting on a chair, chin resting on one hand, eyes on "
                         "the camera, serious expression"),
    Shot("leaning on a rail", "waist-up portrait leaning on a railing or the back of a chair, shoulders angled "
                              "to the camera, glancing down with a soft smile"),
)

_BUILT_IN = (
    ("product", "Clean studio white",
     "A bright professional product studio: a seamless pure white backdrop and a white surface, large soft even "
     "light from both sides, a faint natural shadow under the product, true colours."),
    ("product", "Skincare morning light",
     "A calm bathroom shelf or vanity in soft morning window light, pale stone and warm white tones, a sprig of "
     "greenery and a folded linen towel, gentle shadows, fresh and clean."),
    ("product", "Luxury dark",
     "Polished black marble with faint gold veining under a warm spotlight, a deep charcoal backdrop, rich "
     "reflections and velvet shadows, golden highlights on the edges."),
    ("product", "Kitchen / lifestyle table",
     "A sunlit wooden kitchen table in a cosy home, warm window daylight, a ceramic bowl, a cup and some fresh "
     "fruit, a softly blurred kitchen behind, authentic and inviting."),
    ("product", "Outdoor summer",
     "A sunny summer garden table, dappled light through green leaves, weathered wood and a few wild flowers, "
     "bright warm daylight, a softly blurred lawn behind."),
    ("people", "Studio headshots",
     "A professional photo studio: a plain light grey backdrop, a large soft key light in front and a gentle fill, "
     "a stool and a small side table, clean true-to-life colour."),
    ("people", "Loft editorial",
     "A bright industrial loft: tall steel-framed windows, a brick wall, a concrete floor, a worn leather sofa and "
     "a wooden table, soft daylight with warm highlights, a refined editorial colour grade."),
    ("people", "Street / city casual",
     "A city street on a bright day: a brick building with a doorway, a railing and a bench along the pavement, "
     "soft daylight with gentle shadows, a relaxed natural colour grade."),
)

DEFAULT_SHOOTS: tuple[Shoot, ...] = tuple(Shoot(n, k, s) for k, n, s in _BUILT_IN)

HELP = ('Your photoshoots. Add one to "shoots" as {"name": "...", "kind": "product" or "people", "set": "..."}: '
        "describe the place, light and colour grade in a sentence or two. Use a built-in shoot's name to change its "
        'words (leave "kind" out to keep the built-in one). Optional: "shots" as [{"name": "...", "text": "..."}] '
        'for your own shot list (the first is the hero), and "subject" for the word that finds the subject in the '
        'photo. Put names in "hidden" to leave them out of the panel.')

NOUN = {"product": "product", "people": "person"}
KEEP = {
    "product": "Keep the product exactly as in image 1: the same shape, colours, materials and the exact same "
               "label text and logo, letter for letter.",
    "people": "Keep the exact same person as in image 1: the same face, facial features, eye colour, skin, hair "
              "and beard.",
}


def _write_blank(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"help": HELP, "shoots": [], "hidden": []}, indent=2), encoding="utf-8")
    except OSError:
        pass


def _text(d: dict, key: str) -> str:
    v = d.get(key)
    return v.strip() if isinstance(v, str) else ""


def _shots(raw) -> tuple[Shot, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(Shot(_text(d, "name"), _text(d, "text")) for d in raw
                 if isinstance(d, dict) and _text(d, "name") and _text(d, "text"))


def _yours(path: Path) -> tuple[list[Shoot], set[str]]:
    """(your shoots, hidden names) from the file; nothing if it's missing or can't be read. A shoot without a
    kind is kept (kind "") so it can override a built-in of the same name; `load` drops it if there is none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], set()
    if not isinstance(data, dict):
        return [], set()
    raw, hidden = data.get("shoots"), data.get("hidden")
    mine = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict) or not _text(d, "name") or not _text(d, "set"):
            continue
        kind = _text(d, "kind")
        if kind and kind not in KINDS:
            continue
        mine.append(Shoot(_text(d, "name"), kind, _text(d, "set"), _shots(d.get("shots")), _text(d, "subject")))
    return mine, ({n.strip() for n in hidden if isinstance(n, str)} if isinstance(hidden, list) else set())


def load(path: Path | None = None) -> list[Shoot]:
    """The built-in shoots (with your changes, minus hidden ones), then yours. Creates a blank file to edit."""
    path = Path(path or SHOOTS_FILE)
    if not path.exists():
        _write_blank(path)
    mine, hidden = _yours(path)
    changed = {s.name: s for s in mine}
    names = {s.name for s in DEFAULT_SHOOTS}
    out = []
    for base in DEFAULT_SHOOTS:
        if base.name in hidden:
            continue
        own = changed.get(base.name)
        out.append(base if own is None else Shoot(base.name, own.kind or base.kind, own.set,
                                                  own.shots or base.shots, own.subject or base.subject))
    return out + [s for s in mine if s.name not in names and s.name not in hidden and s.kind]


def by_name(name: str, path: Path | None = None) -> Shoot | None:
    return next((s for s in load(path) if s.name == name), None)


def shots_for(shoot: Shoot) -> tuple[Shot, ...]:
    """The shoot's own shot list, else the shared one for its kind."""
    return shoot.shots or (PRODUCT_SHOTS if shoot.kind == "product" else PEOPLE_SHOTS)


def _bare(text: str) -> str:
    """Without a final full stop, since the prompt adds its own (your sets may end in one)."""
    return text.strip().rstrip(".")


def hero_prompt(kind: str, set_text: str, shot_text: str) -> str:
    return (f"A professional photoshoot photo of the {NOUN[kind]} from image 1. The shoot: {_bare(set_text)}. "
            f"This shot: {_bare(shot_text)}. {KEEP[kind]}")


def follow_prompt(kind: str, shot_text: str) -> str:
    return ("Another photo from the same professional photoshoot as image 2: the same set, lighting and colour "
            f"grade, of the {NOUN[kind]} from image 1. This shot: {_bare(shot_text)}. {KEEP[kind]}")
