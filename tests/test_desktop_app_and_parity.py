"""Tests for cross-OS Desktop App runtime, icons/manifest, packaging, and competitor parity."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from fl_mcp import app as studio_app
from fl_mcp import clients, server, service_daemon
from fl_mcp.assets import load_asset
from fl_mcp.music import theory

ROOT = Path(__file__).resolve().parents[1]


def _load_pkg_mod(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_os_and_arch_normalization():
    assert studio_app.normalize_os("win32") == "windows"
    assert studio_app.normalize_os("darwin") == "macos"
    assert studio_app.normalize_os("linux") == "linux"
    assert studio_app.normalize_arch("AMD64") == "x86_64"
    assert studio_app.normalize_arch("x86_64") == "x86_64"
    assert studio_app.normalize_arch("arm64") == "arm64"
    assert studio_app.normalize_arch("aarch64") == "arm64"


def test_cross_platform_icons_and_manifest_assets():
    ico = load_asset("icon.ico")
    icns = load_asset("icon.icns")
    assert ico is not None and ico[1] == "image/x-icon"
    assert ico[0][:4] == b"\x00\x00\x01\x00"
    assert icns is not None and icns[1] == "image/x-icns"
    assert icns[0][:4] == b"icns"

    app = service_daemon.create_asgi_app(poll=False)
    with TestClient(app) as client:
        mf = client.get("/manifest.webmanifest")
        assert mf.status_code == 200
        assert "application/manifest+json" in mf.headers["content-type"]
        body = mf.json()
        assert body["name"] == "FL-MCP Studio"
        assert body["display"] == "standalone"
        assert client.get("/assets/icon.ico").status_code == 200
        assert client.get("/assets/icon.icns").status_code == 200
        state = client.get("/api/state").json()
        assert state["meta"]["platform"] in ("windows", "macos", "linux")
        assert state["meta"]["arch"]


def test_open_studio_window_prefers_chromeless_app_over_browser(monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    fake_exe = tmp_path / "msedge.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(studio_app, "find_chromium_app_runtime", lambda: fake_exe)
    monkeypatch.setattr(studio_app, "_has_module", lambda name: name == "tkinter")
    studio_app._RUNTIME_CACHE = None

    def fake_spawn(cmd):
        spawned.append(cmd)
        return SimpleNamespace(wait=lambda: 0, poll=lambda: 0)

    monkeypatch.setattr(studio_app, "_detached_spawn", fake_spawn)
    used = studio_app.open_studio_window("http://127.0.0.1:8765/", backend="auto")
    assert used == "app-window"
    assert spawned and "--app=http://127.0.0.1:8765/?app=1" in spawned[0]
    assert "--class=FL-MCP-Studio" in spawned[0]
    studio_app._RUNTIME_CACHE = None


def test_app_shortcuts_generate_native_bundles_across_oses(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(studio_app, "_shortcut_exec_parts", lambda: ("/usr/bin/fl-mcp-studio", []))

    monkeypatch.setattr(sys, "platform", "win32")
    res_w = studio_app.install_app_shortcut(base_dir=tmp_path / "win_start")
    assert Path(res_w["path"]).is_file()
    assert Path(res_w["icon"]).is_file()

    monkeypatch.setattr(sys, "platform", "darwin")
    res_m = studio_app.install_app_shortcut(base_dir=tmp_path / "mac_apps")
    mac_bundle = Path(res_m["path"])
    assert (mac_bundle / "Contents" / "Info.plist").is_file()
    assert (mac_bundle / "Contents" / "Resources" / "AppIcon.icns").is_file()
    assert (mac_bundle / "Contents" / "MacOS" / "FL-MCP Studio").is_file()

    monkeypatch.setattr(sys, "platform", "linux")
    res_l = studio_app.install_app_shortcut(base_dir=tmp_path / "xdg_data")
    assert Path(res_l["path"]).is_file()
    assert (
        tmp_path / "xdg_data" / "icons" / "hicolor" / "256x256" / "apps" / "fl-mcp-studio.png"
    ).is_file()


def test_frozen_binary_cli_and_clients_commands(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert service_daemon._base_cli_command() == [sys.executable, "--cli"]
    cmd, args = clients.command("local")
    assert cmd == sys.executable and args == ["--mcp"]


def test_build_app_pyinstaller_command_and_names(tmp_path):
    build_app = _load_pkg_mod("build_app")
    assert (
        build_app.artifact_filename("0.3.0", "windows", "arm64")
        == "fl-mcp-studio-0.3.0-windows-arm64.zip"
    )
    assert (
        build_app.artifact_filename("0.3.0", "macos", "arm64")
        == "fl-mcp-studio-0.3.0-macos-arm64.zip"
    )
    assert (
        build_app.artifact_filename("0.3.0", "linux", "arm64")
        == "fl-mcp-studio-0.3.0-linux-arm64.tar.gz"
    )

    mac_cmd = build_app.pyinstaller_args(
        dist_dir=tmp_path, work_dir=tmp_path, spec_dir=tmp_path, os_name="macos"
    )
    assert "--windowed" in mac_cmd
    assert "--osx-bundle-identifier" in mac_cmd
    assert any(a.endswith("icon.icns") for a in mac_cmd)

    win_cmd = build_app.pyinstaller_args(
        dist_dir=tmp_path, work_dir=tmp_path, spec_dir=tmp_path, os_name="windows"
    )
    assert any(a.endswith("icon.ico") for a in win_cmd)
    out = build_app.build_app_archive(tmp_path, os_name="linux", arch="arm64", dry_run=True)
    assert out.name.endswith("-linux-arm64.tar.gz")


def test_theory_named_chords_and_progression_expansion():
    assert theory.parse_named_chord("Am7") == [69, 72, 76, 79]
    assert theory.parse_named_chord("Cmaj7") == [60, 64, 67, 71]
    assert theory.parse_named_chord("G7") == [67, 71, 74, 77]
    assert theory.parse_named_chord("F#3m9") == [54, 57, 61, 64, 68]
    assert theory.parse_named_chord("D5") == [62, 69]
    rows = theory.chords_to_notes("Cm@0:2@90, G7@2:2")
    assert len(rows) == 7
    assert rows[0] == [60, 0.0, 2.0, 90]
    assert rows[3] == [67, 2.0, 2.0, 100]


@pytest.mark.anyio
async def test_competitor_parity_mixer_status_and_piano_roll(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))

    status = json.loads(await server.fl_status())
    assert status["len_bars"] == 16 and status["len_ms"] == 32000

    mx = json.loads(await server.fl_set(kind="mixer", index=10, stereo=0.45, arm=True))
    assert mx["stereo"] == 0.45 and mx["arm"] is True

    ch_res = json.loads(await server.fl_notes(chords="Am7@0:4, Fmaj7@4:4"))
    assert ch_res["queued"] == 8

    del_res = json.loads(await server.fl_notes(delete=[[60, 0]]))
    assert del_res["deleted"] == 1
    inbox = json.loads((tmp_path / "inbox.json").read_text(encoding="utf-8"))
    assert inbox["delete"] == [[60, 0.0]]

    clr_q = json.loads(await server.fl_notes(clear="queue"))
    assert clr_q["cleared_queue"] is True
    assert not (tmp_path / "inbox.json").exists()

    clr_r = json.loads(await server.fl_notes(clear="roll"))
    assert clr_r["cleared"] is True and clr_r["queued"] == 0

    prev = json.loads(
        await server.fl_notes(notes=[[60, 0, 0.5, 110], [64, 0, 0.5, 100]], channel=2, preview=True)
    )
    assert prev == {"previewed": 2, "channel": 2}
    assert (2, 60, 110) in fl.fake.auditioned
    assert (2, 60, 0) in fl.fake.auditioned
