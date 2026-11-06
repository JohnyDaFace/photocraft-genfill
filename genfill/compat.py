"""After a PhotoCraft update: run every PhotoCraft step the companion uses and report OK/FAIL per step.

1. Against PhotoCraft's background engine (photocraft-cli serve), with test images in a temp folder.
2. If the PhotoCraft app is running: the real app route on a separate test tab, which is closed again
   only after confirming by name that it is that tab. Open documents are never touched.
"""
from __future__ import annotations

import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from .photocraft_client import PhotoCraftClient, PhotoCraftError
from .settings import Settings

Report = Callable[[str], None]
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _expect(value, cond):
    if not cond(value):
        raise RuntimeError(f"unexpected result: {str(value)[:200]}")


def _no_problems(problems):
    if problems:
        raise RuntimeError("; ".join(problems))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Checker:
    def __init__(self, out: Report):
        self.out, self.ok = out, True

    def __call__(self, label: str, fn) -> None:
        try:
            fn()
            self.out(f"  OK    {label}")
        except Exception as e:  # report every failure, keep going
            self.ok = False
            self.out(f"  FAIL  {label}: {e}")


def run_checks(settings: Settings | None = None, out: Report = print) -> bool:
    s = settings or Settings.load()
    check = _Checker(out)
    engine_checks(s.cli_path, check, out)
    app_checks(s, check, out)
    out("\nAll good: the companion works with this PhotoCraft version." if check.ok else
        "\nSomething changed in PhotoCraft; the FAIL lines show what. The companion refuses to run an "
        "affected step rather than damaging your document.")
    return check.ok


def engine_checks(cli: str, check: _Checker, out: Report) -> None:
    if not Path(cli).is_file():
        check("find PhotoCraft", lambda: (_ for _ in ()).throw(RuntimeError(
            f"photocraft-cli not found at {cli}; set its path in Settings")))
        return
    root = Path(tempfile.mkdtemp(prefix="genfill-compat-"))
    doc = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    doc.paste(Image.new("RGB", (1080, 1350), (200, 150, 100)), (0, 285))
    doc.save(root / "doc.png")
    Image.new("RGBA", (1080, 1920), (0, 255, 0, 255)).save(root / "result.png")
    tok = root / "ctl.token"
    version = subprocess.run([cli, "--version"], capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout.strip()
    out(f"Checking {version or 'PhotoCraft'} (background engine):")
    port = _free_port()
    p = subprocess.Popen([cli, "serve", "--port", str(port), "--control-token-file", str(tok),
                          "--automation-read-root", str(root)], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    try:
        for _ in range(100):
            if tok.exists():
                break
            time.sleep(0.1)
        time.sleep(0.5)
        with PhotoCraftClient(port, tok, exchange_dir=root) as pc:
            check("required commands", lambda: _no_problems(pc.compatibility()))
            check("open document", lambda: pc.open_document("doc.png"))
            # off the top-left corner on purpose: tells [x, y, w, h] apart from [x0, y0, x1, y1]
            check("make selection", lambda: pc.execute("select.rect", {"x": 40, "y": 1635, "width": 1000, "height": 285}))
            check("read size + selection", lambda: _expect(pc.document(), lambda d: d.selection == (40, 1635, 1040, 1920)))
            check("render image with transparency", lambda: _expect(
                pc.render(), lambda im: im.size == (1080, 1920) and im.getpixel((5, 5))[3] == 0))
            check("place masked layer", lambda: pc.place_layer("result.png", "Generative Fill"))
            check("new layer is masked and named", lambda: _expect(
                pc.execute("document.inspect")["layers"],
                lambda ls: ls[0]["name"] == "Generative Fill" and ls[0]["hasMask"]))
            check("open result as new document (Upscale)", lambda: pc.open_document("result.png"))
    except PhotoCraftError as e:
        check("start background engine", lambda: (_ for _ in ()).throw(e))
    finally:
        p.terminate()


def app_checks(s: Settings, check: _Checker, out: Report) -> None:
    ex = Path(s.exchange_dir)
    out("\nPhotoCraft app (open documents are not touched):")
    try:
        pc = PhotoCraftClient(s.port, Path(s.token_file), exchange_dir=ex).__enter__()
    except PhotoCraftError as e:
        out(f"  SKIP  app not reachable ({e})")
        return
    if pc.mode != "desktop":
        pc.__exit__(None, None, None)
        out("  SKIP  the port answered, but not the PhotoCraft app")
        return
    ex.mkdir(parents=True, exist_ok=True)
    doc = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    doc.paste(Image.new("RGB", (400, 400), (200, 150, 100)), (0, 100))
    doc.save(ex / "genfill-compat-test.png")
    Image.new("RGBA", (400, 600), (0, 255, 0, 255)).save(ex / "genfill-compat-result.png")
    with pc:
        check("required commands", lambda: _no_problems(pc.compatibility()))
        check("open test document", lambda: pc.open_document("genfill-compat-test.png"))
        check("make selection", lambda: pc.execute("select.rect", {"x": 40, "y": 20, "width": 300, "height": 60}))
        check("read size + selection", lambda: _expect(pc.document(), lambda d: d.selection == (40, 20, 340, 80)))
        check("export image with transparency", lambda: _expect(
            pc.render(), lambda im: im.size == (400, 600) and im.getpixel((5, 5))[3] == 0))
        check("paste result as masked layer", lambda: pc.place_layer("genfill-compat-result.png", "Generative Fill"))
        check("new layer is masked and named", lambda: _expect(
            pc.execute("document.inspect")["layers"],
            lambda ls: ls[0]["name"] == "Generative Fill" and ls[0]["hasMask"]))
        try:
            if pc.execute("document.inspect").get("name") == "genfill-compat-test.png":
                pc.execute("file.close", {"save": "discard"})
        except PhotoCraftError:
            pass
