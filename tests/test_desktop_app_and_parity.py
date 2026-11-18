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

    import packaging  # ensure PyPA packaging does not shadow local packaging/build_release.py

    assert hasattr(packaging, "__file__")
    stage = tmp_path / "stage_extras"
    stage.mkdir()
    build_app._stage_common_extras(stage, "linux")
    assert (stage / "fl-mcp-studio.desktop").is_file()
    assert (stage / "FL-Studio-Scripts" / "install-fl-scripts.py").is_file()


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

    # Normalized float velocity (0 < v < 1) and 3-digit hex color (#RGB)
    assert server._note([60, 0, 1, 0.8]) == [60, 0.0, 1.0, 102]
    mx_col = json.loads(await server.fl_set(kind="mixer", index=1, color="#F72"))
    assert mx_col["i"] == 1


def test_app_mcp_dispatch_and_windowed_stdio_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    called = []
    monkeypatch.setattr(server, "serve", lambda: called.append("served"))
    studio_app.main(["--mcp"])
    assert called == ["served"]

    orig_out, orig_err = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        studio_app._ensure_stdio()
        assert sys.stdout is not None and sys.stderr is not None
        sys.stdout.write("windowed ok\n")
        sys.stdout.flush()
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err


def test_build_app_linux_tar_has_no_duplicate_members(monkeypatch, tmp_path):
    import subprocess
    import tarfile

    build_app = _load_pkg_mod("build_app")

    def fake_run(cmd, **kwargs):
        dist_arg = cmd[cmd.index("--distpath") + 1]
        bundle = Path(dist_arg) / "fl-mcp-studio"
        (bundle / "_internal").mkdir(parents=True, exist_ok=True)
        (bundle / "fl-mcp-studio").write_text("#!/bin/sh\n", encoding="utf-8")
        (bundle / "_internal" / "base.dat").write_text("ok", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    tar_out = build_app.build_app_archive(tmp_path, os_name="linux", arch="x86_64", dry_run=False)
    assert tar_out.is_file()
    with tarfile.open(tar_out, "r:gz") as tf:
        names = tf.getnames()
    assert len(names) == len(set(names)), f"Duplicate tar entries: {names}"
    assert "fl-mcp-studio/fl-mcp-studio" in names
    assert "fl-mcp-studio/FL-Studio-Scripts/install-fl-scripts.py" in names


def test_interactive_studio_action_endpoint(fl, monkeypatch, tmp_path):
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FL_MCP_FL_USER_DIR", str(tmp_path / "fl_user"))
    monkeypatch.setattr(server, "bridge", fl.bridge)
    app = service_daemon.create_asgi_app(poll=False)
    with TestClient(app) as client:
        r_desk = client.post(
            "/api/action", json={"action": "desktop_mode", "mode": "stealth"}
        ).json()
        assert r_desk["ok"] is True and r_desk["desktop_mode"] == "stealth"

        r_plan = client.post(
            "/api/action",
            json={"action": "produce_plan", "genre": "techno", "key": "Fm", "length": 4},
        ).json()
        assert r_plan["ok"] is True and r_plan["plan"]["genre"] == "techno"

        r_notes = client.post(
            "/api/action", json={"action": "notes", "chords": "Am7@0:4, Fmaj7@4:4"}
        ).json()
        assert r_notes["ok"] is True and r_notes["queued"] == 8

        r_prev = client.post(
            "/api/action",
            json={"action": "notes", "chords": "Cmaj7@0:2", "preview": True, "channel": 1},
        ).json()
        assert r_prev["ok"] is True and r_prev["previewed"] == 4 and r_prev["channel"] == 1

        r_clr = client.post("/api/action", json={"action": "notes", "clear": "queue"}).json()
        assert r_clr["ok"] is True and r_clr["cleared_queue"] is True

        r_tr = client.post(
            "/api/action",
            json={"action": "transport", "transport_action": "tempo", "value": 134},
        ).json()
        assert r_tr["ok"] is True and r_tr["result"]["bpm"] == 134.0

        r_set = client.post(
            "/api/action",
            json={"action": "set", "kind": "mixer", "index": 2, "props": {"mute": 1}},
        ).json()
        assert r_set["ok"] is True and r_set["result"]["mute"] == 1

        r_step = client.post(
            "/api/action",
            json={"action": "step", "channel": 0, "step": 4, "on": True},
        ).json()
        assert r_step["ok"] is True and 4 in r_step["result"]["on"]

        r_scr = client.post("/api/action", json={"action": "install_scripts"}).json()
        assert r_scr["ok"] is True and len(r_scr["installed"]) == 2


def test_install_all_and_install_client_action(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_appdata = tmp_path / "appdata"
    fake_codex = tmp_path / "codex"
    fake_home.mkdir()
    fake_appdata.mkdir()
    fake_codex.mkdir()
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CODEX_HOME", str(fake_codex))
    monkeypatch.setattr(clients.shutil, "which", lambda _name: None)

    batch = clients.install_all(source="local", transport="http", url="http://127.0.0.1:9123/mcp")
    by_id = {row["client"]: row for row in batch}
    assert by_id["cursor"]["written"] is True
    assert by_id["cursor"]["label"] == "Cursor"
    assert by_id["windsurf"]["written"] is True
    assert by_id["gemini"]["written"] is True
    assert by_id["antigravity"]["written"] is True
    assert by_id["codex"]["written"] is True
    assert by_id["claude-desktop"]["written"] is True
    assert by_id["claude-code"]["ok"] is True and by_id["claude-code"]["skipped"] is True
    assert by_id["vscode"]["ok"] is True and by_id["vscode"]["skipped"] is True

    app = service_daemon.create_asgi_app(poll=False)
    with TestClient(app) as client:
        r_single = client.post(
            "/api/action",
            json={
                "action": "install_client",
                "client": "cursor",
                "transport": "http",
                "http_url": "http://127.0.0.1:9200/mcp",
            },
        ).json()
        assert r_single["ok"] is True
        assert r_single["result"]["label"] == "Cursor"
        cursor_cfg = json.loads((fake_home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
        assert cursor_cfg["mcpServers"]["fl-studio"] == {"url": "http://127.0.0.1:9200/mcp"}

        r_all = client.post(
            "/api/action",
            json={"action": "install_client", "client": "all", "transport": "http"},
        ).json()
        assert r_all["ok"] is True
        assert isinstance(r_all["result"], list)
        statuses = {row["id"]: row for row in r_all["clients"]}
        assert statuses["cursor"]["configured"] is True
        assert statuses["codex"]["configured"] is True
