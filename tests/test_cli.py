import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from fl_mcp import cli, paths
from fl_mcp.bridge import Bridge, FLError
from fl_mcp.install import DEVICE, PYSCRIPT, install, targets

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
    from tests.test_bridge import FakeMido

    monkeypatch.setitem(sys.modules, "mido", FakeMido(["FL MCP 1"], ["FL MCP 1"]))
    monkeypatch.setattr(paths, "fl_user_dir", lambda: fl_home)
    monkeypatch.setattr(
        Bridge, "call", lambda self, req: {"fl": "Producer v25", "api": 37, "ver": 1, "proto": 1}
    )
    assert cli._doctor() == 0
    out = capsys.readouterr().out
    assert "port 'FL MCP': found" in out and "FL script   : missing" in out
    assert "piano roll  : missing" in out
    assert "FL Studio   : ok, Producer v25 (API 37, script v1)" in out
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
        assert len((await client.list_tools()).tools) == 11
        api = await client.call_tool("fl_api", {"module": "transport", "query": "isplaying"})
        assert api.content[0].text == "isPlaying()->bool"
        status = await client.call_tool("fl_status", {})
    assert (
        status.is_error
        and "MIDI" in status.content[0].text
        or "FL Studio" in status.content[0].text
    )
