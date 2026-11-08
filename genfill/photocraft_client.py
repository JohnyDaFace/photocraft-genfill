"""Client for PhotoCraft's localhost JSON-lines control channel (docs/control-protocol.md)."""
from __future__ import annotations

import base64
import io
import json
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .imaging import LAYER_MASK_EXPAND, LAYER_MASK_FEATHER

NOT_CONNECTED = ("Can't reach PhotoCraft on port {port}. Run 'Setup PhotoCraft connection' once, then fully quit "
                 "and restart the ArtCraft Launcher and PhotoCraft.")


# Engine commands we rely on, per connection kind: step -> accepted ids (first = current name,
# rest = known alternatives). "desktop" = the PhotoCraft app; "headless" = photocraft-cli serve.
_COMMON = {
    "read document": ["document.inspect"],
    "mask layer to selection": ["layer.layerMask.revealSelection"],
    "rename layer": ["layer.renameLayer"],
    "expand selection": ["select.modify.expand"],
    "feather selection": ["select.modify.feather"],
    "select all": ["select.all"],
    "deselect": ["select.deselect"],
    "canvas size": ["image.canvasSize"],
    "load selection": ["select.loadSelection"],
    "delete layer": ["layer.delete"],
}
# Only needed to swap a placed result for another one (an older PhotoCraft without them just can't swap).
SWAP_COMMANDS = {"choose layer": "layer.select", "list layers": "layer.list", "layer metadata": "layer.xmpMetadata"}
REQUIRED_COMMANDS = {
    "headless": {**_COMMON, "rasterize placed layer": ["layer.rasterize.smartObject", "layer.smartObjects.rasterize"]},
    "desktop": {**_COMMON,
                "save selection": ["select.saveSelection"], "load selection": ["select.loadSelection"],
                "delete channel": ["channel.delete"], "copy": ["edit.copy"],
                "paste in place": ["edit.pasteSpecial.pasteInPlace"], "close document": ["file.close"]},
}
SELECTION_CHANNEL = "genfill-selection"
SWAP_CHANNEL = "genfill-before-swap"


class PhotoCraftError(Exception):
    """A problem talking to PhotoCraft, worded for the user."""


@dataclass
class DocInfo:
    width: int
    height: int
    selection: tuple[int, int, int, int] | None


class PhotoCraftClient:
    def __init__(self, port: int = 7878, token_file: Path | None = None, timeout: float = 120,
                 exchange_dir: Path | None = None, mode: str | None = None):
        """`mode` None = detect: the desktop app answers `ui.inspect`, the headless engine doesn't."""
        self.port, self.token_file, self.timeout = port, token_file, timeout
        self.exchange_dir, self.mode = exchange_dir, mode
        self._sock: socket.socket | None = None
        self._n = 0

    def __enter__(self) -> "PhotoCraftClient":
        try:
            token = Path(self.token_file).read_text(encoding="utf-8").strip()
        except (OSError, TypeError) as e:
            raise PhotoCraftError(NOT_CONNECTED.format(port=self.port) + f" (no token file at {self.token_file})") from e
        try:
            self._sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        except OSError as e:
            raise PhotoCraftError(NOT_CONNECTED.format(port=self.port)) from e
        self._sock.settimeout(self.timeout)
        self._file = self._sock.makefile("rw", encoding="utf-8", newline="\n")
        self._request("auth", {"token": token}, rid="auth")
        if self.mode is None:
            try:
                self._request("ui.inspect", {})
                self.mode = "desktop"
            except PhotoCraftError:
                self.mode = "headless"
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._sock:
                self._file.close()
                self._sock.close()
        finally:
            self._sock = None

    def _request(self, method: str, params: dict | None, rid: Any = None) -> Any:
        if self._sock is None:
            raise PhotoCraftError("Not connected to PhotoCraft.")
        self._n += 1
        rid = self._n if rid is None else rid
        try:
            self._file.write(json.dumps({"id": rid, "method": method, "params": params}) + "\n")
            self._file.flush()
            while True:
                line = self._file.readline()
                if not line:
                    raise PhotoCraftError("PhotoCraft closed the connection.")
                reply = json.loads(line)
                if reply.get("id") == rid:
                    break
        except (OSError, ValueError) as e:
            raise PhotoCraftError(f"Lost the connection to PhotoCraft ({e}).") from e
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft: {reply.get('error', 'request failed')}")
        return reply.get("result")

    def call(self, method: str, params: dict | None = None) -> Any:
        return self._request(method, params)

    def execute(self, command: str, params: dict | None = None) -> Any:
        return self._request("engine.execute", {"command": command, "params": params})

    # -- compatibility -------------------------------------------------------------------------
    def available_commands(self) -> set[str]:
        return {c.get("id") for c in (self.call("engine.commands", {}) or []) if isinstance(c, dict)}

    def compatibility(self) -> list[str]:
        """Problems that would stop Generate/Upscale with this PhotoCraft version (empty = all good).
        Each required step lists accepted command ids, newest name first."""
        have = self.available_commands()
        self._resolved = {}
        problems = []
        for step, options in REQUIRED_COMMANDS[self.mode].items():
            found = next((c for c in options if c in have), None)
            if found:
                self._resolved[step] = found
            else:
                problems.append(f"PhotoCraft no longer has '{options[0]}' ({step}).")
        return problems

    def _cmd(self, step: str) -> str:
        if step in SWAP_COMMANDS:
            return SWAP_COMMANDS[step]
        return getattr(self, "_resolved", {}).get(step) or REQUIRED_COMMANDS[self.mode][step][0]

    # -- high level ----------------------------------------------------------------------------
    def _inspect(self) -> dict:
        return self.execute(self._cmd("read document")) or {}

    def document(self) -> DocInfo:
        r = self._inspect()
        sel = r.get("selectionBounds")  # PhotoCraft reports [x, y, width, height]; DocInfo holds corners
        if not sel:
            return DocInfo(int(r["width"]), int(r["height"]), None)
        x, y, w, h = (int(v) for v in sel)
        return DocInfo(int(r["width"]), int(r["height"]), (x, y, x + w, y + h))

    def render(self) -> Image.Image:
        """The full-size composite as RGBA (transparent canvas stays transparent)."""
        if self.mode == "headless":
            r = self.call("doc.render", {"maxSide": 0}) or {}
            if "base64" not in r:
                raise PhotoCraftError("PhotoCraft did not return the image.")
            return Image.open(io.BytesIO(base64.b64decode(r["base64"]))).convert("RGBA")
        # desktop: export a PNG copy into the exchange folder (does not rename the document)
        rel = f"render_{uuid.uuid4().hex[:8]}.png"
        self.call("app.save", {"path": rel})
        path = Path(self.exchange_dir) / rel
        try:
            with Image.open(path) as im:
                return im.convert("RGBA")
        except OSError as e:
            raise PhotoCraftError(f"PhotoCraft's exported image could not be read ({e}).") from e
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    def place_layer(self, rel_path: str, name: str, masked: bool = True) -> None:
        """Add a document-sized PNG (relative to the exchange folder) as a new pixel layer above the
        active one, masked to the current selection (`masked=False`: the whole layer, no mask, and
        nothing left selected)."""
        if not masked:
            self.execute(self._cmd("select all"))  # the desktop paste route saves and restores a selection
        self._put(rel_path)
        if masked:
            # Reach a little into the original with a soft edge; the layer's own pixels fade from the
            # exact original to the generated ones inside that band, so no hard line shows.
            self.execute(self._cmd("expand selection"), {"radius": LAYER_MASK_EXPAND})
            self.execute(self._cmd("feather selection"), {"radius": LAYER_MASK_FEATHER})
            self.execute(self._cmd("mask layer to selection"))
        else:
            try:
                self.execute(self._cmd("deselect"))
            except PhotoCraftError:
                pass  # PhotoCraft refuses when nothing is selected any more; that's the goal anyway
        try:
            self.execute(self._cmd("rename layer"), {"name": name})
        except PhotoCraftError:
            pass  # a name is nice to have; the fill itself is done

    def rename_layer(self, name: str, layer: int | None = None) -> None:
        """Rename a layer (the active one by default). Best effort, like the rename in `place_layer`."""
        args = {"name": name} if layer is None else {"name": name, "layer": layer}
        try:
            self.execute(self._cmd("rename layer"), args)
        except PhotoCraftError:
            pass

    def _put(self, rel_path: str) -> None:
        """A document-sized PNG as a new pixel layer above the active one."""
        if self.mode == "headless":
            self.call("doc.place", {"path": rel_path})
            self.execute(self._cmd("rasterize placed layer"))
        else:
            self._paste_in_place(rel_path)

    def active_layer(self) -> int | None:
        return self._inspect().get("activeLayer")

    def layer_ids(self) -> set:
        rows = (self.execute(self._cmd("list layers"), {"flat": True}) or {}).get("layers") or []
        return {r.get("id") for r in rows}

    def layer_metadata(self, layer: int | None = None) -> str | None:
        """The layer's XMP packet (None: none, or this PhotoCraft can't store one)."""
        try:
            r = self.execute(self._cmd("layer metadata"), {} if layer is None else {"layer": layer}) or {}
        except PhotoCraftError:
            return None
        return r.get("rawData")

    def set_layer_metadata(self, layer: int, packet: str) -> bool:
        try:
            self.execute(self._cmd("layer metadata"), {"layer": layer, "rawData": packet})
            return True
        except PhotoCraftError:
            return False

    def replace_layer(self, old: int, rel_path: str, name: str, masked: bool) -> int:
        """Swap a layer GenFill added for another result, in the same place in the layer stack. A fill
        keeps the old layer's mask exactly (its selection is loaded back from it). The user's
        selection is put back afterwards. Returns the new layer's id."""
        had_selection = bool(self._inspect().get("hasSelection")) and "save selection" in REQUIRED_COMMANDS[self.mode]
        if had_selection:
            self.execute(self._cmd("save selection"), {"name": SWAP_CHANNEL})
        try:
            self.execute(self._cmd("choose layer"), {"layer": old})
            if masked:
                self.execute(self._cmd("load selection"), {"channel": "mask", "layer": old})
            else:
                self.execute(self._cmd("select all"))
            self._put(rel_path)
            new = self.active_layer()
            if new is None or new == old:
                raise PhotoCraftError("PhotoCraft didn't add the new result as expected; the old one is still there.")
            if masked:
                self.execute(self._cmd("mask layer to selection"))
            self.execute(self._cmd("delete layer"), {"layer": old})
            self.execute(self._cmd("choose layer"), {"layer": new})
            try:
                self.execute(self._cmd("rename layer"), {"layer": new, "name": name})
            except PhotoCraftError:
                pass
            return new
        finally:
            if had_selection:
                self.execute(self._cmd("load selection"), {"channel": SWAP_CHANNEL})
                try:
                    self.execute(self._cmd("delete channel"), {"channel": SWAP_CHANNEL})
                except PhotoCraftError:
                    pass
            else:
                try:
                    self.execute(self._cmd("deselect"))
                except PhotoCraftError:
                    pass

    def select_mask(self, rel_path: str) -> None:
        """Make a picture's transparency the selection: it comes in as a temporary layer (white,
        alpha = the mask), its transparency is loaded as the selection, and the layer is deleted."""
        self.place_layer(rel_path, "GenFill selection (temporary)", masked=False)
        try:
            self.execute(self._cmd("load selection"), {"channel": "transparency"})
        finally:
            self.execute(self._cmd("delete layer"))

    def mask_active_layer(self) -> None:
        """Hide everything outside the selection on the active layer with a layer mask (Remove
        Background, as in Photoshop: nothing is deleted; a Background layer becomes a normal layer)."""
        self.execute(self._cmd("mask layer to selection"))

    def _paste_in_place(self, rel_path: str) -> None:
        """Desktop route: open the result as a temporary tab, copy it, close that tab (only if it is
        the one we opened), then paste in place into the original document and restore the selection."""
        target = self._identity()
        self.execute(self._cmd("save selection"), {"name": SELECTION_CHANNEL})
        try:
            opened = (self.call("app.open", {"path": rel_path}) or {}).get("name")
            if not opened or self._identity()[0] != opened or self._identity() == target:
                raise PhotoCraftError("PhotoCraft didn't open the result as expected; nothing was changed.")
            self.execute(self._cmd("select all"))
            self._clipboard_retry(lambda: self.execute(self._cmd("copy")))
            self.execute(self._cmd("close document"), {"save": "discard"})
            if self._identity() != target:
                # With several documents open, closing the temporary tab can land on another one: go back.
                self._activate(target)
            if self._identity() != target:
                raise PhotoCraftError("PhotoCraft switched to another document; the result is in the exchange "
                                      "folder but was not pasted.")
            self._clipboard_retry(lambda: self.execute(self._cmd("paste in place")))
            self.execute(self._cmd("load selection"), {"channel": SELECTION_CHANNEL})
        finally:
            if self._identity() == target:
                try:
                    self.execute(self._cmd("delete channel"), {"channel": SELECTION_CHANNEL})
                except PhotoCraftError:
                    pass

    @staticmethod
    def _clipboard_retry(action, attempts: int = 20, delay: float = 0.25):
        """Windows lets one program hold the clipboard at a time (clipboard managers, remote desktop…);
        wait it out for a few seconds instead of failing."""
        for i in range(attempts):
            try:
                return action()
            except PhotoCraftError as e:
                if "clipboard" not in str(e).lower() or i == attempts - 1:
                    if "clipboard" in str(e).lower():
                        raise PhotoCraftError("The Windows clipboard stayed busy (another program is holding "
                                              "it). Try Generate again.") from e
                    raise
                time.sleep(delay)

    def _identity(self) -> tuple:
        r = self._inspect()
        return r.get("name"), r.get("path")

    def _activate(self, identity: tuple, max_tabs: int = 200) -> None:
        """Bring the open document with this (name, path) to the front, if PhotoCraft can (best effort:
        the caller checks the result)."""
        for i in range(max_tabs):
            try:
                r = self.execute(self._cmd("read document"), {"document": i}) or {}
            except PhotoCraftError:
                return  # past the last tab
            if (r.get("name"), r.get("path")) == identity:
                try:
                    self.execute("document.activate", {"document": i})
                except PhotoCraftError:
                    pass
                return

    def open_document(self, rel_path: str) -> None:
        self.call("doc.open" if self.mode == "headless" else "app.open", {"path": rel_path})
