import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from fl_mcp import cli, paths
from fl_mcp.bridge import Bridge, FLError
from fl_mcp.install import DEVICE, PYSCRIPT, install, targets
from tests.names import NAMES

pytestmark = pytest.mark.anyio


@pytest.fixture
def fl_home(tmp_path):
    root = tmp_path / "Image-Line" / "FL Studio"
    (root / "Settings").mkdir(parents=True)
    return root


def test_install_copies_both_scripts(fl_home, tmp_path):
    notes = tmp_path / "shared notes"
    written = install(fl_home, notes)
    device, script = targets(fl_home)
    assert written == [device, script] and notes.is_dir()
    assert device.parent == fl_home / "Settings" / "Hardware" / "FLMCP"
    assert script.parent == fl_home / "Settings" / "Piano roll scripts"
    source = os.path.join(os.path.dirname(cli.__file__), "fl")
    assert (
        device.read_text(encoding="utf-8")
        == open(os.path.join(source, DEVICE), encoding="utf-8").read()
    )
    text = script.read_text(encoding="utf-8")
    assert f"DIR = {json.dumps(str(notes))}" in text and '"@DIR@"' not in text
    assert script.name == PYSCRIPT
    compile(text, "FL MCP.pyscript", "exec")


def test_relative_notes_dir_is_stored_as_an_absolute_path(fl_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install(fl_home, "rel/notes")
    text = targets(fl_home)[1].read_text(encoding="utf-8")
    assert f"DIR = {json.dumps(str(tmp_path / 'rel' / 'notes'))}" in text


def test_data_dir_is_absolute_and_overridable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FL_MCP_DIR", "shared")
    assert paths.data_dir() == tmp_path / "shared"
    monkeypatch.delenv("FL_MCP_DIR")
    assert paths.data_dir().name == ".fl-mcp" and paths.data_dir().is_absolute()


def test_install_is_repeatable_and_requires_fl_user_data(fl_home, tmp_path):
    install(fl_home, tmp_path / "n")
    install(fl_home, tmp_path / "n")
    with pytest.raises(SystemExit, match="not found"):
        install(tmp_path / "nowhere", tmp_path / "n")


def test_cli_install(fl_home, tmp_path, capsys):
    cli.main(["install", "--fl-dir", str(fl_home), "--notes-dir", str(tmp_path / "n")])
    out = capsys.readouterr().out
    assert out.count("installed") == 2 and "Restart FL Studio" in out


def test_cli_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert all(word in out for word in ("serve", "install", "doctor"))


def test_doctor_reports_each_layer(monkeypatch, fl_home, capsys):
    from fl_mcp.bridge import SCRIPT_VERSION
    from tests.test_bridge import FakeMido

    monkeypatch.setitem(sys.modules, "mido", FakeMido(["FL MCP 1"], ["FL MCP 1"]))
    monkeypatch.setattr(paths, "fl_user_dir", lambda: fl_home)
    monkeypatch.setattr(
        Bridge,
        "call",
        lambda self, req: {"fl": "Producer v25", "api": 37, "ver": SCRIPT_VERSION, "proto": 1},
    )
    assert cli._doctor() == 0
    out = capsys.readouterr().out
    assert "port 'FL MCP': found" in out and "FL script   : missing" in out
    assert "piano roll  : missing" in out
    assert f"FL Studio   : ok, Producer v25 (API 37, script v{SCRIPT_VERSION})" in out
    install(fl_home, fl_home / "n")
    cli._doctor()
    assert "FL script   : ok" in capsys.readouterr().out

    def silent(self, req):
        raise FLError("No answer from FL Studio")

    monkeypatch.setattr(Bridge, "call", silent)
    assert cli._doctor() == 1
    assert "FL Studio   : No answer" in capsys.readouterr().out


def test_doctor_without_midi_backend(monkeypatch, capsys):
    class Broken:
        def get_output_names(self):
            raise RuntimeError("no ALSA")

    monkeypatch.setitem(sys.modules, "mido", Broken())
    assert cli._doctor() == 1 and "MIDI backend: FAILED (no ALSA)" in capsys.readouterr().out


async def test_real_stdio_server_speaks_mcp():
    env = {**os.environ, "FL_MCP_TIMEOUT": "0.2", "FL_MCP_PORT": "no such port"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "fl_mcp"], env=env)
    async with Client(params) as client:
        assert len((await client.list_tools()).tools) == len(NAMES)
        api = await client.call_tool("fl_api", {"module": "transport", "query": "isplaying"})
        assert api.content[0].text == "isPlaying()->bool"
        status = await client.call_tool("fl_status", {})
    assert (
        status.is_error
        and "MIDI" in status.content[0].text
        or "FL Studio" in status.content[0].text
    )


# ---- selftest, desktop, config, setup


def test_config_prints_ready_text_for_every_client(capsys):
    from fl_mcp import clients

    for name in clients.CLIENTS:
        where, text = clients.snippet(name, "pypi")
        if name == "claude-code":
            assert text.startswith("claude mcp add --scope user fl-studio -- ") and "fl-mcp" in text
        elif name == "codex":
            assert text.startswith("[mcp_servers.fl-studio]") and 'args = ["--from"' in text
            if sys.version_info >= (3, 11):
                import tomllib

                assert tomllib.loads(text)["mcp_servers"]["fl-studio"]["args"][-1] == "fl-mcp"
        elif name == "opencode":
            doc = json.loads(text)
            server = doc["mcp"]["fl-studio"]
            assert server["type"] == "local"
            assert server["command"][1:] == ["--from", "fl-mcp[audio]", "fl-mcp"]
        else:
            doc = json.loads(text)
            section = doc.get("mcpServers") or doc.get("servers") or doc["context_servers"]
            server = section["fl-studio"]
            assert server["args"] == ["--from", "fl-mcp[audio]", "fl-mcp"]
            assert ("type" in server) is (name == "vscode")
    assert json.loads(clients.snippet("cursor", "pypi", audio=False)[1])["mcpServers"]["fl-studio"][
        "args"
    ] == ["fl-mcp"]
    git = json.loads(clients.snippet("cursor", "git")[1])["mcpServers"]["fl-studio"]["args"]
    assert git[1].endswith("@ git+https://github.com/Rtur2003/FL-MCP")
    local = json.loads(clients.snippet("cursor", "local")[1])["mcpServers"]["fl-studio"]
    assert local["command"] == sys.executable and local["args"] == ["-m", "fl_mcp"]
    with pytest.raises(ValueError, match="source must"):
        clients.snippet("cursor", "ftp")
    with pytest.raises(ValueError, match="client must"):
        clients.snippet("emacs")
    with pytest.raises(SystemExit) as info:
        cli.main(["config", "codex", "--source", "git"])
    assert info.value.code == 0 and "[mcp_servers.fl-studio]" in capsys.readouterr().out


def test_desktop_control_is_switched_with_one_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    with pytest.raises(SystemExit) as info:
        cli.main(["desktop", "on"])
    assert info.value.code == 0 and (tmp_path / "desktop.on").is_file()
    assert "top-left corner" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["desktop", "off"])
    assert not (tmp_path / "desktop.on").exists()


def test_desktop_test_explains_an_unsupported_system(monkeypatch, tmp_path, capsys):
    from fl_mcp import desk

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setattr(desk.sys, "platform", "linux")
    with pytest.raises(SystemExit) as info:
        cli.main(["desktop", "test"])
    assert info.value.code == 1 and "Windows and macOS" in capsys.readouterr().err


def test_desktop_test_takes_a_screenshot_when_fl_is_there(monkeypatch, tmp_path, capsys):
    from fl_mcp import desk
    from tests.fakedesk import FL, FakeDesk

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setattr(desk, "make_backend", lambda: FakeDesk(front=FL))
    with pytest.raises(SystemExit) as info:
        cli.main(["desktop", "test"])
    out = capsys.readouterr().out
    assert info.value.code == 0 and "saved to" in out
    assert (tmp_path / "desk-test.png").read_bytes().startswith(b"\x89PNG")


def test_selftest_prints_a_report_and_saves_it(monkeypatch, fl, tmp_path, capsys):
    import fl_mcp.bridge

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setattr(fl_mcp.bridge, "Bridge", lambda: fl.bridge)
    with pytest.raises(SystemExit) as info:
        cli.main(["selftest"])
    out = capsys.readouterr().out
    assert info.value.code == 0 and "[ ok ] link" in out and "report saved to" in out
    assert (tmp_path / "data" / "selftest.json").is_file()
    with pytest.raises(SystemExit):
        cli.main(["selftest", "--json"])
    assert json.loads(capsys.readouterr().out)["summary"]["fail"] == 0


def test_selftest_exit_code_follows_failures(monkeypatch, tmp_path, capsys):
    import fl_mcp.bridge

    class Dead:
        def call(self, req):
            raise FLError("No answer from FL Studio")

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setattr(fl_mcp.bridge, "Bridge", Dead)
    with pytest.raises(SystemExit) as info:
        cli.main(["selftest"])
    assert info.value.code == 1 and "[FAIL] link" in capsys.readouterr().out


def test_setup_installs_checks_and_shows_what_is_left(monkeypatch, fl_home, tmp_path, capsys):
    from tests.test_bridge import FakeMido

    monkeypatch.setitem(sys.modules, "mido", FakeMido([], []))
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        Bridge, "call", lambda self, req: (_ for _ in ()).throw(FLError("No answer"))
    )
    with pytest.raises(SystemExit) as info:
        cli.main(["setup", "--fl-dir", str(fl_home), "--desktop"])
    out = capsys.readouterr().out
    assert info.value.code == 1
    assert "[ok] scripts" in out and "[!!] midi port" in out and "[ok] desktop" in out
    assert "Options > MIDI Settings" in out and "mcpServers" in out and "No answer" in out
    assert (tmp_path / "data" / "desktop.on").is_file()


def test_setup_without_fl_data_says_so(monkeypatch, tmp_path, capsys):
    from tests.test_bridge import FakeMido

    monkeypatch.setitem(sys.modules, "mido", FakeMido(["FL MCP 1"], ["FL MCP 1"]))
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setattr(Bridge, "call", lambda self, req: {"ver": 3})
    with pytest.raises(SystemExit) as info:
        cli.main(["setup", "--fl-dir", str(tmp_path / "nothing")])
    out = capsys.readouterr().out
    assert info.value.code == 0 and "[!!] scripts" in out and "[ok] midi port" in out
    assert "FL Studio answers" in out


def test_desktop_cli_supports_stealth_polite_foreground(tmp_path, monkeypatch, capsys):
    from fl_mcp import desk

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    for target in ("stealth", "polite", "foreground"):
        with pytest.raises(SystemExit) as e:
            cli.main(["desktop", target])
        assert e.value.code == 0
        out = capsys.readouterr().out
        assert f"Desktop control is on (mode={target})" in out
        assert desk.mode() == target
    with pytest.raises(SystemExit) as e_off:
        cli.main(["desktop", "off"])
    assert e_off.value.code == 0
    assert desk.mode() == "off"


def test_onboard_config_all_and_serve_profile_cli(monkeypatch, fl_home, tmp_path, capsys):
    from fl_mcp import clients, install, server

    seen = {}
    monkeypatch.setattr(
        install,
        "onboard",
        lambda **kw: (
            seen.update(onboard=kw)
            or {
                "ok": True,
                "steps": {"scripts": {"ok": True, "files": ["a", "b"]}},
                "fl_diagnostics": {"issues": []},
            }
        ),
    )
    with pytest.raises(SystemExit) as info:
        cli.main(["onboard", "--fl-dir", str(fl_home), "--no-service", "--no-fl-midi", "--json"])
    assert info.value.code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert seen["onboard"]["configure_midi"] is False

    monkeypatch.setattr(
        clients,
        "install_all",
        lambda *a, **kw: [{"id": "cursor", "label": "Cursor", "ok": True, "path": "c.json"}],
    )
    with pytest.raises(SystemExit) as cfg_exit:
        cli.main(["config", "all", "--write", "--source", "auto"])
    assert cfg_exit.value.code == 0 and "Cursor" in capsys.readouterr().out

    with pytest.raises(SystemExit) as auto_exit:
        cli.main(["setup", "--auto", "--json"])
    assert auto_exit.value.code == 0 and json.loads(capsys.readouterr().out)["ok"] is True

    prev_prof = os.environ.pop("FL_MCP_PROFILE", None)
    try:
        monkeypatch.setattr(
            server, "serve", lambda: seen.setdefault("served", os.environ.get("FL_MCP_PROFILE"))
        )
        cli.main(["serve", "--profile", "compact"])
        assert seen["served"] == "compact"
    finally:
        if prev_prof is None:
            os.environ.pop("FL_MCP_PROFILE", None)
        else:
            os.environ["FL_MCP_PROFILE"] = prev_prof
