"""Open the bundled Photoshop UXP installer through Adobe Creative Cloud."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .setup_env import APP_DIR


def build_package(root: Path, destination: Path) -> Path:
    """Bundle the UXP panel with its manifest at the root of the CCX archive."""
    source = root / "photoshop"
    if not (source / "manifest.json").is_file():
        raise FileNotFoundError("The Photoshop plugin source is missing its manifest.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as package:
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix != ".ccx" and not path.name.startswith("."):
                package.write(path, path.relative_to(source).as_posix())
    return destination


def package_path() -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "photoshop" / "GenFill-Photoshop.ccx"
    root = Path(__file__).resolve().parents[1]
    return build_package(root, root / "build" / "GenFill-Photoshop.ccx")


def open_installer() -> None:
    package = package_path()
    if not package.is_file():
        raise FileNotFoundError("The Photoshop plugin package is missing from this GenFill build.")
    APP_DIR.mkdir(parents=True, exist_ok=True)
    installer = APP_DIR / package.name
    if package.resolve() != installer.resolve():
        shutil.copy2(package, installer)  # persists after a one-file EXE clears its extraction folder
    if sys.platform == "win32":
        os.startfile(str(installer))  # Creative Cloud shows its own install and permission confirmation
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(installer)], close_fds=True)
    else:
        raise OSError("Photoshop plugin installation is available on Windows and macOS.")
