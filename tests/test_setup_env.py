import sys

import pytest

from genfill import setup_env

TEST_KEY = r"Software\PhotoCraftGenFillTest\Environment"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows registry")
def test_windows_install_status_uninstall_roundtrip(tmp_path, monkeypatch):
    import winreg
    monkeypatch.setattr(setup_env, "_broadcast", lambda: None)
    try:
        setup_env.install(7878, tmp_path, key=TEST_KEY)
        assert all(setup_env.status(7878, tmp_path, key=TEST_KEY).values())
        assert (tmp_path / "exchange").is_dir()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as k:
            assert winreg.QueryValueEx(k, "PHOTOCRAFT_CONTROL_PORT")[0] == "7878"
            assert winreg.QueryValueEx(k, "PHOTOCRAFT_AUTOMATION_READ_ROOT")[0] == str(tmp_path / "exchange")
        setup_env.uninstall(key=TEST_KEY)
        assert not any(setup_env.status(7878, tmp_path, key=TEST_KEY).values())
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, TEST_KEY)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\PhotoCraftGenFillTest")


def test_mac_launchctl_and_login_agent(tmp_path):
    env: dict[str, str] = {}

    def fake_launchctl(args):
        if args[1] == "setenv":
            env[args[2]] = args[3]
        elif args[1] == "unsetenv":
            env.pop(args[2], None)
        return env.get(args[2], "") if args[1] == "getenv" else ""

    agent = tmp_path / "LaunchAgents" / "x.plist"
    app = tmp_path / "Application Support" / "PhotoCraftGenFill"
    assert not any(setup_env._mac_status(7878, app, run=fake_launchctl, agent=agent).values())
    setup_env._mac_install(7878, app, run=fake_launchctl, agent=agent)
    assert all(setup_env._mac_status(7878, app, run=fake_launchctl, agent=agent).values())
    plist = agent.read_text()
    assert "<key>RunAtLoad</key><true/>" in plist
    assert f"launchctl setenv PHOTOCRAFT_CONTROL_TOKEN_FILE '{app / 'control.token'}'" in plist
    setup_env._mac_uninstall(run=fake_launchctl, agent=agent)
    assert not agent.exists() and not env


def test_linux_environment_d_roundtrip(tmp_path):
    f = tmp_path / "environment.d" / "photocraft-genfill.conf"
    setup_env._linux_install(7878, tmp_path, env_file=f)
    assert "PHOTOCRAFT_CONTROL_PORT=7878\n" in f.read_text()
    assert all(setup_env._linux_status(7878, tmp_path, env_file=f).values())
    assert not setup_env._linux_status(9999, tmp_path, env_file=f)["PHOTOCRAFT_CONTROL_PORT"]
    setup_env._linux_uninstall(env_file=f)
    assert not any(setup_env._linux_status(7878, tmp_path, env_file=f).values())
