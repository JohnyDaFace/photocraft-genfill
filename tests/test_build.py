import build
from zipfile import ZipFile

from genfill.photoshop_install import build_package
from genfill import photoshop_install


def test_linux_build_includes_pillow_tk_finder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build.subprocess, "run", lambda args, **kwargs: calls.append(args))
    (tmp_path / "dist").mkdir()
    (tmp_path / "photoshop").mkdir()
    (tmp_path / "photoshop" / "manifest.json").write_text("{}")
    build.main()
    pyinstaller = calls[1]
    assert any("GenFill-Photoshop.ccx" in arg for arg in pyinstaller)
    assert ["--hidden-import", "PIL._tkinter_finder"] == pyinstaller[
        pyinstaller.index("--hidden-import"):pyinstaller.index("--hidden-import") + 2]


def test_photoshop_package_has_manifest_at_archive_root(tmp_path):
    source = tmp_path / "photoshop"
    source.mkdir()
    (source / "manifest.json").write_text('{"manifestVersion":5}')
    (source / "index.js").write_text("// panel")
    package = build_package(tmp_path, tmp_path / "build" / "panel.ccx")
    with ZipFile(package) as archive:
        assert set(archive.namelist()) == {"manifest.json", "index.js"}


def test_photoshop_installer_is_copied_out_of_exe_before_opening(tmp_path, monkeypatch):
    package = tmp_path / "unpacked" / "GenFill-Photoshop.ccx"
    package.parent.mkdir()
    package.write_bytes(b"package")
    opened = []
    monkeypatch.setattr(photoshop_install, "package_path", lambda: package)
    monkeypatch.setattr(photoshop_install, "APP_DIR", tmp_path / "persistent")
    monkeypatch.setattr(photoshop_install.sys, "platform", "win32")
    monkeypatch.setattr(photoshop_install.os, "startfile", lambda path: opened.append(path))
    photoshop_install.open_installer()
    installed = tmp_path / "persistent" / package.name
    assert installed.read_bytes() == b"package"
    assert opened == [str(installed)]
