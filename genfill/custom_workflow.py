"""Your own ComfyUI workflow for Generate and Create: the same .json file you load in ComfyUI. GenFill finds
where the picture, the selection (mask), a reference, the prompt and the result go, puts its own in, and
runs it. Everything else (Expand, Remove, Select, Fix faces, Upscale, Photoshoot…) keeps GenFill's own
workflows and the image model chosen in Settings.

ComfyUI saves two kinds of .json: the normal save (the whole canvas: node positions, wires, groups, notes)
and Export (API) (only what runs). Both are taken; a normal save is converted here, asking ComfyUI how each
node's settings are laid out (its /object_info)."""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path

SLOTS = ("picture", "mask", "reference", "prompt", "result")
SLOT_LABELS = {"picture": "Picture", "mask": "Selection (mask)", "reference": "Reference picture",
               "prompt": "Prompt", "result": "Result"}

# Canvas-only nodes: they never run (notes) or only pass things along in the editor (reroutes, primitives).
FRONTEND_ONLY = {"Note", "MarkdownNote", "Reroute", "PrimitiveNode", "Label (rgthree)", "Fast Groups Bypasser (rgthree)",
                 "Fast Groups Muter (rgthree)", "Bookmark (rgthree)", "Image Comparer (rgthree)"}
CONTROL_VALUES = {"fixed", "increment", "decrement", "randomize"}  # the "control after generate" a seed carries
WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}
TEXT_FIELDS = ("text", "prompt", "text_g", "positive_prompt", "string")
SEED_FIELDS = ("seed", "noise_seed")
MUTED, BYPASSED = 2, 4  # node modes in a normal save


class WorkflowError(Exception):
    """A workflow GenFill can't use, worded for the user."""


# -- reading ---------------------------------------------------------------------------------------
def is_api(data) -> bool:
    return isinstance(data, dict) and bool(data) and all(
        isinstance(v, dict) and "class_type" in v for v in data.values())


def read(path: str | Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise WorkflowError(f"Couldn't read {Path(path).name}: it isn't a ComfyUI workflow (.json).") from e
    if isinstance(data, dict) and "prompt" in data and is_api(data["prompt"]):
        data = data["prompt"]  # saved with its /prompt wrapper
    if not (is_api(data) or (isinstance(data, dict) and isinstance(data.get("nodes"), list))):
        raise WorkflowError(f"{Path(path).name} isn't a ComfyUI workflow. Save it from ComfyUI "
                            "(Workflow › Save, or Export (API)) and pick that file.")
    return data


def _input_order(spec: dict) -> list[tuple[str, list]]:
    """(name, spec) of a node's inputs in the order ComfyUI shows them (that's the order of widgets_values)."""
    inputs = spec.get("input", {})
    order = spec.get("input_order") or {}
    out = []
    for group in ("required", "optional"):
        names = order.get(group) or list((inputs.get(group) or {}).keys())
        out += [(n, (inputs.get(group) or {}).get(n) or ["*"]) for n in names]
    return out


def _is_widget(spec: list) -> bool:
    kind = spec[0] if spec else None
    opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    if opts.get("forceInput"):
        return False
    return isinstance(kind, list) or kind in WIDGET_TYPES


def _link_rows(links) -> list[tuple]:
    """(id, from node, from slot, to node, to slot) of each wire, from either way a save writes them."""
    out = []
    for ln in links or []:
        if isinstance(ln, dict):
            out.append((ln.get("id"), ln.get("origin_id"), ln.get("origin_slot", 0), ln.get("target_id"),
                        ln.get("target_slot", 0)))
        elif isinstance(ln, list) and len(ln) >= 5:
            out.append(tuple(ln[:5]))
    return out


class _Flat:
    """A normal save with its subgraphs opened up: every node that runs, wherever it sits, under an id like
    ComfyUI's own ("12" at the top, "12:5" for node 5 inside subgraph node 12), and where each wire starts."""
    SUB_IN, SUB_OUT = -10, -20  # a subgraph's own input and output ends

    def __init__(self, data: dict):
        self.defs = {g.get("id"): g for g in ((data.get("definitions") or {}).get("subgraphs") or [])}
        self.nodes: dict[str, dict] = {}       # id -> node (its inputs' "link" made into wire keys)
        self.wires: dict[str, tuple] = {}      # wire key -> ("node", id, slot) | ("in", instance, input name)
        self.outs: dict[tuple, str] = {}       # (instance, output index) -> wire key inside it
        self.ins: dict[str, dict] = {}         # instance -> {input name: wire key outside, or ("value", v)}
        self.overrides: dict[tuple, object] = {}  # (node id, widget name) -> value set on the subgraph node
        self._add(data.get("nodes") or [], data.get("links") or [], "", None, ())

    def _add(self, nodes, links, prefix: str, instance: str | None, inside: tuple) -> None:
        sub = self.defs.get(inside[-1]) if inside else None
        names_in = [i.get("name") for i in (sub or {}).get("inputs") or []]
        for wid, src, sslot, dst, dslot in _link_rows(links):
            key = prefix + str(wid)
            if src == self.SUB_IN:
                self.wires[key] = ("in", instance, names_in[sslot] if sslot < len(names_in) else sslot)
            else:
                self.wires[key] = ("node", prefix + str(src), sslot)
            if dst == self.SUB_OUT:
                self.outs[(instance, dslot)] = key
        for node in nodes:
            if not isinstance(node, dict) or "id" not in node:
                continue
            nid = prefix + str(node["id"])
            n = {**node, "id": nid, "inputs": [{**i, "link": None if i.get("link") is None else prefix + str(i["link"])}
                                              for i in node.get("inputs") or []]}
            kind = node.get("type")
            if kind in self.defs and kind not in inside and node.get("mode") not in (MUTED, BYPASSED):
                ins = {i.get("name"): i["link"] for i in n["inputs"] if i.get("link") is not None}
                values = node.get("widgets_values")
                proxies = (node.get("properties") or {}).get("proxyWidgets")
                if isinstance(values, list) and isinstance(proxies, list):  # widgets shown on the subgraph node
                    for (inner, widget), v in zip(proxies, values):
                        self.overrides[(nid + ":" + str(inner), widget)] = v
                elif isinstance(values, list):
                    unlinked = [i.get("name") for i in n["inputs"] if i.get("widget") and i.get("link") is None]
                    ins.update({name: ("value", v) for name, v in zip(unlinked, values)})
                self.ins[nid] = ins
                g = self.defs[kind]
                self._add(g.get("nodes") or [], g.get("links") or [], nid + ":", nid, inside + (kind,))
            else:
                self.nodes[nid] = n

    def source(self, key, seen=()):
        """Where the wire `key` really starts: [node, slot], ("value", v) for a subgraph setting, or None
        (nothing, a primitive or a muted node) — through subgraph ends, reroutes and bypassed nodes."""
        if key is None or key not in self.wires or key in seen:
            return None
        seen = seen + (key,)
        kind, a, b = self.wires[key]
        if kind == "in":
            outer = self.ins.get(a, {}).get(b)
            return outer if isinstance(outer, tuple) else self.source(outer, seen)
        if a in self.ins:  # a subgraph node's output: the wire inside that reaches it
            return self.source(self.outs.get((a, b)), seen)
        node = self.nodes.get(a)
        if node is None or node.get("type") == "PrimitiveNode" or node.get("mode") == MUTED:
            return None
        if node.get("type") == "Reroute" or node.get("mode") == BYPASSED:
            outs = node.get("outputs") or []
            want = (outs[b] if b < len(outs) else {}).get("type")
            hit = next((i for i in node["inputs"] if i.get("link") is not None and (
                node.get("type") == "Reroute" or want is None or i.get("type") == want)), None)
            return self.source(hit["link"], seen) if hit else None
        return [a, b]


def to_api(data: dict, object_info: dict) -> dict:
    """A normal save as the API format ComfyUI runs. `object_info`: ComfyUI's /object_info."""
    if is_api(data):
        return copy.deepcopy(data)
    flat = _Flat(data)
    nodes = flat.nodes
    missing = sorted({str(n.get("type")) for n in nodes.values() if n.get("mode") not in (MUTED, BYPASSED)
                      and n.get("type") not in object_info and n.get("type") not in FRONTEND_ONLY})
    if missing:
        raise WorkflowError("Your ComfyUI doesn't have these nodes: " + ", ".join(missing) + ". Install them "
                            "(ComfyUI Manager › Install Missing Custom Nodes) and restart ComfyUI.")
    api = {}
    for nid, node in nodes.items():
        kind = node.get("type")
        if kind in FRONTEND_ONLY or node.get("mode") in (MUTED, BYPASSED):
            continue
        inputs = {}
        values = node.get("widgets_values")
        if isinstance(values, dict):
            inputs.update({k: v for k, v in values.items() if not isinstance(v, dict)})
        else:
            values = list(values or [])
            i = 0
            for name, spec in _input_order(object_info[kind]):
                if not _is_widget(spec) or i >= len(values):
                    continue
                inputs[name] = values[i]
                i += 1
                opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if (opts.get("control_after_generate") or name in SEED_FIELDS) and i < len(values) \
                        and values[i] in CONTROL_VALUES:
                    i += 1  # the seed's "control after generate" setting, not an input
                if opts.get("image_upload") and i < len(values) and values[i] == "image":
                    i += 1  # the upload button
        for (oid, widget), v in flat.overrides.items():
            if oid == nid:
                inputs[widget] = v
        for inp in node["inputs"]:
            src = flat.source(inp.get("link"))
            if isinstance(src, tuple):  # a setting made on the subgraph node
                inputs[inp["name"]] = src[1]
            elif src is not None:
                inputs[inp["name"]] = src
        api[nid] = {"class_type": kind, "inputs": inputs,
                    "_meta": {"title": node.get("title") or object_info[kind].get("display_name") or kind}}
    if not api:
        raise WorkflowError("This workflow has no nodes that run.")
    return api


def missing_nodes(wf: dict, object_info: dict) -> list[str]:
    return sorted({n["class_type"] for n in wf.values() if n["class_type"] not in object_info})


# -- finding the slots -----------------------------------------------------------------------------
def _title(wf: dict, nid: str) -> str:
    return str((wf[nid].get("_meta") or {}).get("title") or wf[nid]["class_type"])


def label(wf: dict, nid: str | None) -> str:
    return "(none)" if not nid or nid not in wf else f"{_title(wf, nid)} #{nid}"


def _order(nid: str):
    return tuple(int(p) if p.isdigit() else 1 << 30 for p in str(nid).split(":"))


def _text_field(node: dict) -> str | None:
    """The input holding the node's typed text: a text encoder's "text", a string node's "value"…"""
    fields = TEXT_FIELDS + (("value",) if "String" in node["class_type"] else ())
    return next((k for k in fields if isinstance(node["inputs"].get(k), str)), None)


def picture_carries_mask(wf: dict, slots: dict) -> bool:
    """The picture's Load Image also gives the mask (its second output, painted in ComfyUI's mask editor):
    the selection then goes into the picture's transparency."""
    nid = slots.get("picture")
    return bool(nid) and any(v == [nid, 1] for node in wf.values() for v in node["inputs"].values())


def candidates(wf: dict) -> dict[str, list[str]]:
    """The nodes that could be each slot, in workflow order."""
    ids = sorted(wf, key=_order)
    loads = [i for i in ids if "LoadImage" in wf[i]["class_type"] and isinstance(wf[i]["inputs"].get("image"), str)]
    return {"picture": [i for i in loads if wf[i]["class_type"] != "LoadImageMask"],
            "mask": loads, "reference": [i for i in loads if wf[i]["class_type"] != "LoadImageMask"],
            "prompt": [i for i in ids if _text_field(wf[i])],
            "result": [i for i in ids if any(k in wf[i]["class_type"] for k in ("SaveImage", "PreviewImage",
                                                                                  "Image Save"))]}


def _upstream(wf: dict, start: list) -> list[str]:
    """Nodes feeding `start` (a [node, slot] wire), nearest first; not through a negative prompt or a
    system prompt (an LLM's instructions)."""
    out, todo = [], [start]
    while todo:
        ref = todo.pop(0)
        nid = str(ref[0])
        if nid in out or nid not in wf:
            continue
        out.append(nid)
        todo += [v for k, v in wf[nid]["inputs"].items() if isinstance(v, list) and len(v) == 2
                 and "negative" not in k and "system" not in k]
    return out


def detect(wf: dict) -> dict[str, str | None]:
    """GenFill's best guess for each slot (None: the workflow has none)."""
    c = candidates(wf)

    def titled(ids, *words):
        return [i for i in ids if any(w in _title(wf, i).lower() for w in words)]

    found: dict[str, str | None] = dict.fromkeys(SLOTS)
    masks = [i for i in c["mask"] if wf[i]["class_type"] == "LoadImageMask"] + titled(c["mask"], "mask", "inpaint")
    found["mask"] = masks[0] if masks else None
    refs = [i for i in titled(c["reference"], "ref", "style", "face") if i != found["mask"]]
    pictures = [i for i in c["picture"] if i != found["mask"] and i not in refs]
    found["picture"] = (titled(pictures, "picture", "input", "source", "image") or pictures or [None])[0]
    if refs:
        found["reference"] = refs[0]
    elif len(pictures) > 1:
        found["reference"] = next(i for i in pictures if i != found["picture"])
    # The prompt: the text that reaches a sampler's "positive" (or a guider's conditioning)
    for nid in sorted(wf, key=_order):
        wire = next((wf[nid]["inputs"].get(k) for k in ("positive", "conditioning")
                     if isinstance(wf[nid]["inputs"].get(k), list)), None)
        if wire is None or "Sampler" not in wf[nid]["class_type"] and "Guider" not in wf[nid]["class_type"]:
            continue
        hit = next((i for i in _upstream(wf, wire) if _text_field(wf[i])), None)
        if hit:
            found["prompt"] = hit
            break
    if found["prompt"] is None:
        texts = [i for i in c["prompt"] if "negative" not in _title(wf, i).lower()]
        found["prompt"] = (titled(texts, "positive", "prompt") or texts or [None])[0]
    saves = [i for i in c["result"] if "Save" in wf[i]["class_type"]]
    found["result"] = (saves or c["result"] or [None])[-1]
    return found


def problems(slots: dict) -> list[str]:
    out = []
    if not slots.get("result"):
        out.append("No result: the workflow needs a Save Image or Preview Image node.")
    if not slots.get("prompt"):
        out.append("No prompt box found, so what you type won't reach the workflow.")
    return out


# -- running ---------------------------------------------------------------------------------------
def prepare(wf: dict, slots: dict, prompt: str, seed: int, picture: str | None = None, mask: str | None = None,
            reference: str | None = None, size: tuple[int, int] | None = None) -> dict:
    """A copy of the workflow with GenFill's picture, mask, reference (names ComfyUI gave the uploads), prompt,
    a new seed and the working size put in."""
    wf = copy.deepcopy(wf)
    for slot, name in (("picture", picture), ("mask", mask), ("reference", reference)):
        nid = slots.get(slot)
        if nid in wf and name:
            wf[nid]["inputs"]["image"] = name
    nid = slots.get("prompt")
    if nid in wf:
        field = _text_field(wf[nid]) or "text"
        wf[nid]["inputs"][field] = prompt
    rng = random.Random(seed)
    for node in wf.values():
        for k in SEED_FIELDS:
            if isinstance(node["inputs"].get(k), (int, float)):
                node["inputs"][k] = rng.randint(0, 2**32 - 1)
        if size and "Empty" in node["class_type"] and "Latent" in node["class_type"]:
            for k, v in (("width", size[0]), ("height", size[1])):
                if isinstance(node["inputs"].get(k), (int, float)):
                    node["inputs"][k] = v
    return wf


def mask_upload(mask):
    """The selection as a picture every mask loader reads right: white where to generate in the colours (an
    image's red channel), and in the alpha the way ComfyUI reads it (its masks are 1 - alpha)."""
    from PIL import Image, ImageOps
    m = mask.convert("L")
    out = Image.merge("RGB", (m, m, m)).convert("RGBA")
    out.putalpha(ImageOps.invert(m))
    return out


def file_path(settings_dir: Path) -> Path:
    return settings_dir / "custom_workflow.json"


def load_saved(settings_dir: Path) -> dict | None:
    """The converted workflow kept with the settings (None: there's none, or it can't be read)."""
    try:
        data = json.loads(file_path(settings_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if is_api(data) else None


def save(wf: dict, settings_dir: Path) -> None:
    settings_dir.mkdir(parents=True, exist_ok=True)
    file_path(settings_dir).write_text(json.dumps(wf, indent=1), encoding="utf-8")
