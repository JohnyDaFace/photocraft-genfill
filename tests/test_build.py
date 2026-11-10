import build


def test_linux_build_includes_pillow_tk_finder(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build.sys, "platform", "linux")
    monkeypatch.setattr(build.subprocess, "run", lambda args, **kwargs: calls.append(args))
    (tmp_path / "dist").mkdir()
    build.main()
    pyinstaller = calls[1]
    assert ["--hidden-import", "PIL._tkinter_finder"] == pyinstaller[
        pyinstaller.index("--hidden-import"):pyinstaller.index("--hidden-import") + 2]
