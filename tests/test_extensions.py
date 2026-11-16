"""Regression tests covering Win32 resource cleanup, focus restoration, parallel render
collision safety, .fst/.sf2 indexing & resolution, and CLI/server guards."""

import importlib
import json
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fl_mcp import (
    bridge,
    cli,
    clients,
    desk,
    desk_win,
    flp,
    library,
    paths,
    produce,
    render,
    server,
)
from tests.fakedesk import BROWSER, FL, FakeDesk
from tests.flpgen import template, txt

pytestmark = pytest.mark.anyio


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, dt: float) -> None:
        self.t += dt

    def now(self) -> float:
        return self.t


class _RestorableDesk(FakeDesk):
    def __init__(self, front=BROWSER, **kw):
        super().__init__(front=front, **kw)
        self.restored = []
        self.saw_render_title = False
        self.restored_during_render = False

    def idle_seconds(self):
        return 5.0

    def capture_window(self, win, max_width, *, allow_unminimize=False):
        return None

    def restore_window(self, win):
        self.restored.append(win)
        self.front = win
        if self.saw_render_title:
            self.restored_during_render = True
        return True


def _make_fst(path, plugin="Fruity DX10", name="Pluck", sf2_path=None):
    events = [
        flp.Event(flp.PLUGIN_NAME, txt(plugin)),
        flp.Event(flp.CHAN_NAME, txt(name)),
    ]
    if sf2_path is not None:
        raw_path = sf2_path.encode("utf-8")
        blob = bytes(flp._SF2_HEADER_BYTES) + flp._encode_varint(len(raw_path)) + raw_path
        events.append(flp.Event(flp.PLUGIN_PARAMS, blob))
    body = b"".join(e.encode() for e in events)
    header = b"FLhd" + struct.pack("<IHHh", 6, flp.FST_FORMAT, 0, 96)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"FLdt" + struct.pack("<I", len(body)) + body)
    return path


def test_desk_win_dpi_aware_closes_station_and_desktop_handles():
    calls = []

    class FakeUser32:
        def OpenWindowStationW(self, name, inherit, access):  # noqa: N802
            calls.append(("open_ws", name))
            return 101

        def SetProcessWindowStation(self, h):  # noqa: N802
            calls.append(("set_ws", h))
            return 1

        def CloseWindowStation(self, h):  # noqa: N802
            calls.append(("close_ws", h))
            return 1

        def OpenDesktopW(self, name, flags, inherit, access):  # noqa: N802
            calls.append(("open_dt", name))
            return 202

        def SetThreadDesktop(self, h):  # noqa: N802
            calls.append(("set_dt", h))
            return 1

        def CloseDesktop(self, h):  # noqa: N802
            calls.append(("close_dt", h))
            return 1

        def SetProcessDpiAwarenessContext(self, ctx):  # noqa: N802
            return 1

    desk_win.WindowsBackend._dpi_aware(FakeUser32())
    assert ("close_dt", 202) in calls
    assert ("close_ws", 101) in calls


def test_desk_win_capture_restores_old_gdi_object_and_guards_failure():
    ops = []
    backend = object.__new__(desk_win.WindowsBackend)
    backend.GetDC = lambda hwnd: 10
    backend.CreateCompatibleDC = lambda dc: 20
    backend.CreateCompatibleBitmap = lambda dc, w, h: 30
    backend.SelectObject = lambda dc, obj: ops.append(("select", dc, obj)) or 99
    backend.BitBlt = lambda *a: 1
    backend.DeleteObject = lambda obj: ops.append(("delete_obj", obj)) or 1
    backend.DeleteDC = lambda dc: ops.append(("delete_dc", dc)) or 1
    backend.ReleaseDC = lambda hwnd, dc: ops.append(("release_dc", dc)) or 1
    backend._bitmap_to_png = lambda *a, **kw: (b"png", 4, 4)

    out = backend.capture((0, 0, 4, 4), 100)
    assert out == (b"png", 4, 4)
    assert ops == [
        ("select", 20, 30),
        ("select", 20, 99),
        ("delete_obj", 30),
        ("delete_dc", 20),
        ("release_dc", 10),
    ]

    backend.CreateCompatibleBitmap = lambda dc, w, h: 0
    with pytest.raises(desk.DeskError, match="refused the screen capture"):
        backend.capture((0, 0, 4, 4), 100)


def test_desk_win_restore_window_skips_iconic_and_post_key_waits(monkeypatch):
    ops = []
    backend = object.__new__(desk_win.WindowsBackend)
    backend.IsWindowVisible = lambda hwnd: 1
    backend.IsIconic = lambda hwnd: 1
    backend.SetForegroundWindow = lambda hwnd: ops.append(("fg", hwnd)) or 1
    win = desk.Win(11, "Other", (0, 0, 100, 100), "other.exe")
    assert backend.restore_window(win) is False
    assert ops == []

    events = []
    backend._vk = lambda k: (0x59, False, False)
    backend.GetWindowThreadProcessId = lambda hwnd, pid: 500
    backend.GetCurrentThreadId = lambda: 600
    backend.GetGUIThreadInfo = lambda tid, ptr: 0
    backend.AttachThreadInput = lambda a, b, f: events.append(("attach", f)) or 1
    backend.GetKeyboardState = lambda buf: 1
    backend.SetKeyboardState = lambda buf: events.append("set_kb_state") or 1
    backend.MapVirtualKeyW = lambda vk, kind: 0x15
    backend.PostMessageW = lambda hwnd, msg, vk, lp: events.append(("post", msg)) or 1
    monkeypatch.setattr(desk_win.time, "sleep", lambda s: events.append(("sleep", s)))

    assert backend.post_key(win, desk.Combo(("ctrl",), "y")) is True
    last_post = max(i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == "post")
    sleep_idx = next(i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == "sleep")
    restore_kb = max(i for i, e in enumerate(events) if e == "set_kb_state")
    detach_idx = max(i for i, e in enumerate(events) if isinstance(e, tuple) and e[0] == "attach")
    assert last_post < sleep_idx < restore_kb < detach_idx


def test_export_restores_focus_before_poll_completes(tmp_path):
    target = tmp_path / "out.wav"
    clock = _Clock()

    class ExportDesk(_RestorableDesk):
        def windows(self):
            if self.clock_ref.now() < 6.0:
                self.saw_render_title = True
                return [replace(FL, title="Rendering to out.wav"), BROWSER]
            return [FL, BROWSER]

    fake = ExportDesk(front=BROWSER)
    fake.clock_ref = clock
    d = desk.Desk(fake, clock.sleep, clock.now, platform="win32", mode="polite")

    orig_sleep = clock.sleep

    def step_sleep(dt):
        orig_sleep(dt)
        if clock.now() >= 6.0 and not target.exists():
            target.write_bytes(b"RIFF" + b"\x00" * 64)

    d.sleep = step_sleep
    res = d.export(target, "wav", timeout=30.0)
    assert res["fmt"] == "wav"
    assert fake.restored_during_render is True
    assert fake.front == BROWSER
    assert fake.restored == [BROWSER]


def test_render_allocates_unique_dirs_same_second_and_terminates_popen_on_timeout(tmp_path):
    flp_path = tmp_path / "beat.flp"
    flp_path.write_bytes(b"FLhd-test")
    work = tmp_path / "work"
    seen_out = []

    def fake_popen(cmd):
        out = cmd.split('/O"')[1].rstrip('"')
        seen_out.append(out)
        wav = os.path.join(out, "beat.wav")
        os.makedirs(out, exist_ok=True)
        with open(wav, "wb") as f:
            f.write(b"123456")
        os.utime(wav, (1001, 1001))
        return SimpleNamespace(poll=lambda: 0)

    clk = [0.0]
    r1 = render.render(
        flp_path,
        exe=tmp_path / "FL64.exe",
        popen=fake_popen,
        sleep=lambda s: clk.__setitem__(0, clk[0] + s),
        clock=lambda: clk[0],
        wall=lambda: 1000.0,
        workdir=work,
    )
    r2 = render.render(
        flp_path,
        exe=tmp_path / "FL64.exe",
        popen=fake_popen,
        sleep=lambda s: clk.__setitem__(0, clk[0] + s),
        clock=lambda: clk[0],
        wall=lambda: 1000.0,
        workdir=work,
    )
    assert r1["audio"] != r2["audio"]
    assert seen_out[0] != seen_out[1]

    terminated = []

    class HangingProc:
        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

    clk2 = [0.0]
    with pytest.raises(ValueError, match="no audio appeared"):
        render.render(
            flp_path,
            timeout=2.0,
            exe=tmp_path / "FL64.exe",
            popen=lambda _c: HangingProc(),
            sleep=lambda s: clk2.__setitem__(0, clk2[0] + s),
            clock=lambda: clk2[0],
            wall=lambda: 2000.0,
            workdir=work,
        )
    assert terminated == [True]


def test_cli_desktop_status_and_setup_mode(tmp_path, monkeypatch, capsys):
    from tests.test_bridge import FakeMido

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    with pytest.raises(SystemExit) as e1:
        cli.main(["desktop", "status"])
    assert e1.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enabled"] is False and payload["mode"] == "off"

    fl_home = tmp_path / "Image-Line" / "FL Studio"
    (fl_home / "Settings").mkdir(parents=True)
    monkeypatch.setitem(sys.modules, "mido", FakeMido(["FL MCP"], ["FL MCP"]))
    monkeypatch.setattr(
        bridge.Bridge, "call", lambda self, r: {"fl": "FL", "api": 40, "ver": 1, "proto": 1}
    )
    with pytest.raises(SystemExit) as e2:
        cli.main(["setup", "--fl-dir", str(fl_home), "--desktop", "stealth"])
    assert e2.value.code == 0
    assert "stealth:" in capsys.readouterr().out
    assert desk.mode() == "stealth"


async def test_library_produce_and_server_extensions(tmp_path, monkeypatch):
    fl_user = tmp_path / "Image-Line" / "FL Studio"
    db_gen = fl_user / "Presets" / "Plugin database" / "Installed" / "Generators" / "Fruity"
    db_gen.mkdir(parents=True)
    _make_fst(db_gen / "Sytrus.fst", plugin="Sytrus", name="Sytrus")
    _make_fst(
        db_gen / "Fruity Soundfont Player.fst",
        plugin="Fruity Soundfont Player",
        name="SoundFont Player",
        sf2_path="/init/init.sf2",
    )
    user_pre = fl_user / "Presets" / "Plugin presets" / "Generators"
    user_pre.mkdir(parents=True)
    _make_fst(user_pre / "MyPluck.fst", plugin="Fruity DX10", name="MyPluck")

    samples = tmp_path / "Samples"
    (samples / "Drums").mkdir(parents=True)
    (samples / "Drums" / "kick_tight.wav").write_bytes(b"RIFF" + b"\x00" * 40)
    sf2_dir = fl_user / "Presets" / "SoundFonts"
    sf2_dir.mkdir(parents=True)
    sf2_file = sf2_dir / "WarmSub.sf2"
    sf2_file.write_bytes(b"RIFF" + b"\x00" * 32)

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FL_MCP_SAMPLES", str(samples))
    monkeypatch.setattr(paths, "fl_user_dir", lambda: fl_user)
    monkeypatch.setattr(paths, "fl_install_dirs", lambda: [])

    res = library.scan()
    assert res["samples"] == 1
    assert res["soundfonts"] == 1
    assert res["presets"] >= 2
    assert set(library.list_plugins("sytrus")[0].keys()) == {"name", "kind", "format", "installed"}
    assert library.find_preset("MyPluck") == user_pre / "MyPluck.fst"
    assert library.find_soundfont("warmsub") == sf2_file

    sf_rows = json.loads(await server.fl_library(action="plugins", role="soundfont"))
    assert sf_rows["rows"][0][0] == "WarmSub"
    pre_rows = json.loads(await server.fl_library(action="plugins", role="preset", query="MyPluck"))
    assert pre_rows["rows"][0][0] == "MyPluck"

    fst_info = produce.inspect_flp(user_pre / "MyPluck.fst")
    assert fst_info["kind"] == "preset" and fst_info["plugin"] == "Fruity DX10"
    assert "params" not in fst_info

    tpl = tmp_path / "tpl.flp"
    tpl.write_bytes(template())
    from fl_mcp.music.compose import compose

    plan = compose("house", key="A minor", bpm=124, length="sketch", seed=1)
    picked = produce.parse_sounds(plan, str(tpl), None, "lead=fst:MyPluck; bass=WarmSub.sf2")
    assert picked["lead"].endswith("MyPluck.fst")
    assert picked["bass"] == str(sf2_file)
    report = produce.export_flp(plan, str(tpl), sounds="lead=fst:MyPluck; bass=WarmSub.sf2")
    assert report["verify"]["ok"] is True

    # Server status, stealth master guard, and polite fl_desk focus restoration
    monkeypatch.setenv("FL_MCP_DESKTOP", "stealth")
    monkeypatch.setattr(server.bridge, "call", lambda req: {"fl": "FL", "bpm": 128, "n_ch": 2})

    status = json.loads(await server.fl_status())
    assert status["desktop"] == "stealth"

    with pytest.raises(server.ToolError, match="polite.*foreground"):
        await server.fl_audio(action="master")

    monkeypatch.setenv("FL_MCP_DESKTOP", "polite")
    fake = _RestorableDesk(front=BROWSER)
    d = desk.Desk(fake, mode="polite")
    d.sleep = lambda _s: None
    monkeypatch.setattr(server.desk, "get", lambda: d)

    await server.fl_desk(action="shot")
    assert fake.restored == [BROWSER] and fake.front == BROWSER
    fake.restored.clear()
    out = await server.fl_desk(action="click", x=10, y=20, look=True)
    assert out and fake.restored == [BROWSER] and fake.front == BROWSER


async def test_param_pagination_and_token_matching(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    fl.fake.params[("c", 2, -1)] = [
        ["Filter 1 Cutoff Frequency", 0.5, "hz"],
        ["", 0.0],
        ["Env 1 Rate", 0.2],
        ["", 0.0],
        ["Env 2 Rate", 0.4],
        ["", 0.0],
        ["Drive Amount", 0.3],
    ]

    p1 = json.loads(await server.fl_param(target="channel", index=2, limit=2))
    assert [r[1] for r in p1["rows"]] == ["Filter 1 Cutoff Frequency", "Env 1 Rate"]
    assert p1["next"] == 4

    p2 = json.loads(await server.fl_param(target="channel", index=2, start=p1["next"], limit=3))
    assert [r[1] for r in p2["rows"]] == ["Env 2 Rate", "Drive Amount"]
    assert p2["next"] is None

    hit = json.loads(
        await server.fl_param(target="channel", index=2, param="cutoff freq", real="2.5 kHz")
    )
    assert hit["param"][1] == "Filter 1 Cutoff Frequency" and hit["param"][3] == "2.50 kHz"

    with pytest.raises(
        server.ToolError, match="matches several parameters.*Env 1 Rate.*Env 2 Rate"
    ):
        await server.fl_param(target="channel", index=2, param="env rate", value=0.6)

    with pytest.raises(server.ToolError, match="try: 0 Filter 1 Cutoff Frequency"):
        await server.fl_param(target="channel", index=2, param="cutoff boost", value=0.1)


async def test_plugin_io_view_and_wrapper_controls(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))

    fl.fake.chans[2]["fx"] = 10
    fl.fake.chans[2]["midi_in"] = 4
    fl.fake.chans[2]["activity"] = 0.725
    fl.fake.out_ctrls[("c", 2, -1)] = ["Mod Env", "LFO A"]
    fl.fake.out_voices[("c", 2, -1)] = ["Sub Voice"]
    fl.fake.semitones[("c", 2, -1)] = {36: "Kick", 42: "Closed Hat"}
    fl.fake.tracks[10]["routes"] = {0, 5}
    fl.fake.tracks[10]["levels"][5] = 0.45
    fl.fake.tracks[3]["routes"].add(10)
    fl.fake.tracks[3]["levels"][10] = 0.8

    ch_io = json.loads(await server.fl_param(target="channel", index=2, view="io"))
    assert ch_io["kind"] == "channel"
    assert ch_io["midi_in_port"] == 4 and ch_io["activity"] == 0.725
    assert ch_io["target_track"] == 10 and ch_io["target_track_name"] == "Drums"
    assert ch_io["out_ctrls"] == [[0, "Mod Env"], [1, "LFO A"]]
    assert ch_io["out_voices"] == [[0, "Sub Voice"]]
    assert ch_io["notes"] == [[36, "Kick"], [42, "Closed Hat"]]
    assert ch_io["fx_slots"] == [[0, "FRUITY REEVERB 2", 0, 1.0]]
    assert [0, "Master", 1.0] in ch_io["sends"] and [5, "Insert 5", 0.45] in ch_io["sends"]

    res = json.loads(
        await server.fl_param(
            target="mixer",
            index=10,
            slot=0,
            settings="Wet=0.5, fl:mix=35%, fl:mute=1, fl:slots=0, fl:focus=1",
        )
    )
    assert [r[1] for r in res["params"]] == ["Wet", "fl:mix", "fl:mute", "fl:slots", "fl:focus"]
    assert fl.fake.slot_ctrl[(10, 0)]["mix"] == pytest.approx(0.35, abs=1e-3)
    assert fl.fake.slot_ctrl[(10, 0)]["muted"] is True
    assert fl.fake.tracks[10]["slots_enabled"] is False
    assert fl.fake.focused_editor == ("m", 10, 0)

    mx_io = json.loads(await server.fl_param(target="mixer", index=10, slot=0, view="io"))
    assert mx_io["kind"] == "mixer"
    assert mx_io["muted"] is True and mx_io["mix"] == 0.35
    assert mx_io["track_slots_enabled"] is False and mx_io["automation_enabled"] is True
    assert [2, "Lead"] in mx_io["in_channels"]
    assert [3, "Insert 3", 0.8] in mx_io["in_tracks"]

    # Focus shorthand without explicit value/real
    await server.fl_param(target="channel", index=2, param="fl:focus")
    assert fl.fake.focused_editor == ("c", 2, -1)

    with pytest.raises(server.ToolError, match="applies to mixer effect slots only"):
        await server.fl_param(target="channel", index=2, param="fl:mix", real="50%")
    with pytest.raises(server.ToolError, match="expects 0-1 or %"):
        await server.fl_param(target="mixer", index=10, slot=0, param="fl:mix", real="50 Hz")


def test_clients_install_json_toml_backup_and_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)

    # JSON client (VS Code uses 'servers', others use 'mcpServers') preserving sibling config + .bak
    ws_dir = tmp_path / "proj"
    ws_dir.mkdir()
    cursor_cfg = ws_dir / ".cursor" / "mcp.json"
    cursor_cfg.parent.mkdir(parents=True)
    cursor_cfg.write_text(
        json.dumps({"mcpServers": {"other": {"command": "echo"}}}), encoding="utf-8"
    )

    r1 = clients.install_client("cursor", source="local", project=ws_dir)
    assert r1["written"] and r1["backup"] == str(cursor_cfg) + ".bak"
    assert (ws_dir / ".cursor" / "mcp.json.bak").exists()
    merged = json.loads(cursor_cfg.read_text(encoding="utf-8"))
    assert merged["mcpServers"]["other"] == {"command": "echo"}
    assert merged["mcpServers"]["fl-studio"] == {
        "command": sys.executable,
        "args": ["-m", "fl_mcp"],
    }

    r1_again = clients.install_client("cursor", source="local", project=ws_dir)
    assert not r1_again["changed"] and not r1_again["written"]

    vs_res = clients.install_client("vscode", source="local", project=ws_dir)
    vs_data = json.loads((ws_dir / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert vs_res["written"] and vs_data["servers"]["fl-studio"]["type"] == "stdio"

    # Codex TOML merge preserves unrelated settings and replaces prior [mcp_servers.fl-studio]
    codex_dir = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_dir))
    codex_dir.mkdir()
    codex_cfg = codex_dir / "config.toml"
    codex_cfg.write_text(
        'model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "node"\nargs = ["x.js"]\n\n'
        '[mcp_servers.fl-studio]\ncommand = "old"\nargs = ["bad"]\n',
        encoding="utf-8",
    )
    c_res = clients.install_client("codex", source="local")
    assert c_res["written"] and c_res["backup"] == str(codex_cfg) + ".bak"
    tl = importlib.import_module("tomllib") if sys.version_info >= (3, 11) else None
    if tl:
        parsed_toml = tl.loads(codex_cfg.read_text(encoding="utf-8"))
        assert parsed_toml["model"] == "gpt-5"
        assert parsed_toml["mcp_servers"]["other"]["command"] == "node"
        assert parsed_toml["mcp_servers"]["fl-studio"] == {
            "command": sys.executable,
            "args": ["-m", "fl_mcp"],
        }

    # Malformed config refuses cleanly without overwriting
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        clients.install_client("gemini", path=bad_cfg)
    assert bad_cfg.read_text(encoding="utf-8") == "{not-json"

    # Claude Code CLI invocation & missing CLI handling
    with pytest.raises(RuntimeError, match="claude CLI not found"):
        clients.install_client("claude-code", source="local")
    seen = []
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: seen.append(argv) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    cc_res = clients.install_client("claude-code", source="local")
    assert cc_res["written"] and seen == [
        ["/bin/claude", "mcp", "add", "fl-studio", "--", sys.executable, "-m", "fl_mcp"]
    ]

    # CLI config --write guard and success
    with pytest.raises(SystemExit) as bad_exit:
        cli.main(["config", "--write"])
    assert bad_exit.value.code == 2
    with pytest.raises(SystemExit) as ok_exit:
        cli.main(["config", "vscode", "--write", "--source", "local", "--project", str(ws_dir)])
    assert ok_exit.value.code == 0

    # CLI setup --write registers client cleanly alongside script install
    fl_home = tmp_path / "fl_home"
    (fl_home / "Settings").mkdir(parents=True)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "app_data"))
    monkeypatch.setattr(bridge.Bridge, "call", lambda self, req: {"ok": True})
    with pytest.raises(SystemExit) as setup_exit:
        cli.main(
            ["setup", "--fl-dir", str(fl_home), "--client", "codex", "--source", "local", "--write"]
        )
    assert setup_exit.value.code == 0
    if tl:
        assert (
            tl.loads(codex_cfg.read_text(encoding="utf-8"))["mcp_servers"]["fl-studio"]["command"]
            == sys.executable
        )
