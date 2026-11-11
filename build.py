"""Builds the standalone app with PyInstaller (no Python needed on the user's computer).

    python build.py

Windows: dist/GenFill-for-PhotoCraft.exe (one file)
macOS:   dist/GenFill for PhotoCraft.app (zip it for download)
Linux:   dist/genfill-for-photocraft (one file)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = {"win32": "GenFill-for-PhotoCraft", "darwin": "GenFill for PhotoCraft"}.get(sys.platform, "genfill-for-photocraft")


def main() -> None:
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    from genfill.photoshop_install import build_package
    plugin = build_package(ROOT, ROOT / "build" / "GenFill-Photoshop.ccx")
    icon = ROOT / "assets" / ("icon.ico" if sys.platform == "win32" else "icon.png")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
            "--name", NAME, "--icon", str(icon), "--paths", str(ROOT),
            "--osx-bundle-identifier", "com.photocraftgenfill.app",
            "--exclude-module", "pytest",
            "--add-data", f"{ROOT / 'assets' / 'icon.png'}{';' if sys.platform == 'win32' else ':'}assets",
            "--add-data", f"{ROOT / 'assets' / 'styles'}{';' if sys.platform == 'win32' else ':'}assets/styles",
            "--add-data", f"{ROOT / 'assets' / 'icons'}{';' if sys.platform == 'win32' else ':'}assets/icons"]
    args += ["--add-data", f"{plugin}{';' if sys.platform == 'win32' else ':'}photoshop"]
    if sys.platform != "darwin":
        args.append("--onefile")  # a .app bundle is already a single thing to download on macOS
    if sys.platform.startswith("linux"):
        args += ["--hidden-import", "PIL._tkinter_finder"]  # Pillow loads this dynamically for ImageTk
    args.append(str(ROOT / "GenFill.pyw"))
    subprocess.run(args, check=True, cwd=ROOT)
    print("built:", *sorted(p.name for p in (ROOT / "dist").iterdir()))


if __name__ == "__main__":
    main()
