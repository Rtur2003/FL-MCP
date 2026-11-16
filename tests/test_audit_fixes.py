"""Regression coverage for service, bridge sharing, clients HTTP/paths, fl_param, and UI schema."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from fl_mcp import clients, dashboard, produce, server, service_daemon

ROOT = Path(__file__).resolve().parents[1]


def _load_pkg_mod(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "packaging" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_daemon_templates_refresh_and_custom_port_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setenv("FL_MCP_PORT", "Studio Bus")
    monkeypatch.setattr(
        produce,
        "discover_templates",
        lambda limit=30: [{"name": "Club Starter", "tempo": 128, "channels": 6, "path": "a.flp"}],
    )
    app = service_daemon.create_asgi_app(poll=False)
    with TestClient(app) as client:
        res = client.post("/api/templates/refresh")
        assert res.status_code == 200
        body = res.json()
        assert body["count"] == 1
        assert body["templates"][0]["name"] == "Club Starter"

        st = client.get("/api/state")
        assert st.status_code == 200
        assert st.json()["meta"]["port"] == "Studio Bus"
        assert st.json()["doctor"]["port_name"] == "Studio Bus"


def test_poll_fl_once_reuses_shared_bridge_without_closing(fl, tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    closed = {"n": 0}
    orig_close = fl.bridge.close

    def tracked_close():
        closed["n"] += 1
        orig_close()

    monkeypatch.setattr(fl.bridge, "close", tracked_close)

    res = dashboard.poll_fl_once(bridge=fl.bridge)
    assert res["ok"] is True
    assert closed["n"] == 0

    res2 = dashboard.poll_fl_once(bridge_factory=lambda: fl.bridge)
    assert res2["ok"] is True
    assert closed["n"] == 1


def test_poller_skips_when_shared_bridge_locked(monkeypatch):
    lock = threading.Lock()
    lock.acquire()
    bridge = SimpleNamespace(_lock=lock)
    poller = service_daemon._BackgroundPoller(enabled=True, interval_s=2.0, bridge=bridge)
    poller._port_present = lambda: True  # type: ignore[method-assign]
    calls = {"n": 0}

    def fake_poll(**_kw):
        calls["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(dashboard, "poll_fl_once", fake_poll)
    original_wait = poller._wake.wait

    def stop_after_tick(timeout=None):
        poller._stop.set()
        return original_wait(0.0)

    poller._wake.wait = stop_after_tick  # type: ignore[method-assign]
    try:
        poller._loop()
    finally:
        lock.release()
    assert calls["n"] == 0
    assert poller.failures == 0


def test_doctor_respects_custom_fl_mcp_port(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setenv("FL_MCP_PORT", "Custom Loop")
    fake_mido = SimpleNamespace(
        get_input_names=lambda: ["Custom Loop"],
        get_output_names=lambda: ["Custom Loop"],
    )
    monkeypatch.setitem(sys.modules, "mido", fake_mido)
    doc = dashboard.doctor_info()
    assert doc["port_name"] == "Custom Loop"
    assert doc["midi_port_found"] is True


def test_clients_http_entries_antigravity_and_codex_py310_fallback(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    (fake_home / ".gemini" / "config").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setattr(clients, "_tomllib", lambda: None)

    assert (
        clients.config_path("antigravity") == fake_home / ".gemini" / "config" / "mcp_config.json"
    )
    (fake_home / ".gemini" / "antigravity").mkdir(parents=True)
    assert (
        clients.config_path("antigravity")
        == fake_home / ".gemini" / "antigravity" / "mcp_config.json"
    )

    cd_path = tmp_path / "claude_desktop_config.json"
    clients.install_client(
        "claude-desktop",
        transport="http",
        url="http://127.0.0.1:8765/mcp",
        path=cd_path,
    )
    cd_data = json.loads(cd_path.read_text(encoding="utf-8"))["mcpServers"]["fl-studio"]
    assert cd_data["command"] == "npx"
    assert "mcp-remote" in cd_data["args"]
    assert "url" not in cd_data

    _, cd_snip = clients.snippet("claude-desktop", transport="http")
    assert "mcp-remote" in cd_snip

    ws_path = tmp_path / "windsurf_mcp.json"
    clients.install_client("windsurf", transport="http", path=ws_path)
    ws_data = json.loads(ws_path.read_text(encoding="utf-8"))["mcpServers"]["fl-studio"]
    assert ws_data == {"serverUrl": clients.DEFAULT_HTTP_URL}

    codex_dir = tmp_path / "codex_home"
    codex_dir.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_dir))
    codex_cfg = codex_dir / "config.toml"
    codex_cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
    clients.install_client("codex", transport="http", path=codex_cfg)

    monkeypatch.setattr(
        clients,
        "config_path",
        lambda c, project=None: {
            "claude-desktop": cd_path,
            "windsurf": ws_path,
            "codex": codex_cfg,
        }.get(c),
    )
    by_id = {row["id"]: row for row in clients.client_statuses()}
    assert by_id["claude-desktop"]["configured"] is True
    assert by_id["claude-desktop"]["transport"] == "http"
    assert by_id["windsurf"]["configured"] is True
    assert by_id["windsurf"]["transport"] == "http"
    assert by_id["codex"]["configured"] is True
    assert by_id["codex"]["transport"] == "http"


@pytest.mark.anyio
async def test_fl_param_single_read_name_filter_and_slots(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))

    one = json.loads(await server.fl_param(target="channel", index=2, param="1"))
    assert len(one["rows"]) == 1
    assert one["rows"][0][0] == 1

    filt = json.loads(await server.fl_param(target="channel", index=2, param="cutoff"))
    assert filt["rows"]
    assert all("cutoff" in row[1].lower() for row in filt["rows"])

    slots = json.loads(await server.fl_param(target="mixer", index=10, slot=0, param="fl:slots"))
    assert "fx_slots" in slots
    assert "mix" in slots
    assert "muted" in slots


def test_dashboard_html_matches_real_telemetry_schemas():
    html = dashboard.load_html()
    for token in (
        "status.pat",
        "status.play",
        "au.lufs_i",
        "au.crest_db",
        "au.bands_db",
        "au.advice",
        "dbToMeterPct",
        "normNote",
        "Array.isArray(plan.patterns)",
        'copyBtn.textContent = "Copied"',
    ):
        assert token in html


def test_standalone_installer_and_mcpb_universal_env(tmp_path):
    build_release = _load_pkg_mod("build_release")
    build_mcpb = _load_pkg_mod("build_mcpb")

    ast.parse(build_release.STANDALONE_INSTALL_PY)

    out_dir = tmp_path / "dist"
    zpath = build_release.build_fl_scripts_zip(out_dir)
    bundle = tmp_path / "extracted"
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(bundle)

    fl_user = tmp_path / "CustomDrive" / "FL User"
    (fl_user / "Settings").mkdir(parents=True)
    custom_inbox = tmp_path / "CustomInbox"

    env = dict(os.environ)
    env["FL_MCP_FL_USER_DIR"] = str(fl_user)
    env["FL_MCP_DIR"] = str(custom_inbox)
    env["FL_MCP_PORT"] = "Studio Bridge"
    proc = subprocess.run(
        [sys.executable, str(bundle / "install-fl-scripts.py")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Studio Bridge" in proc.stdout
    assert (fl_user / "Settings" / "Hardware" / "FLMCP" / "device_FLMCP.py").is_file()
    pyscript = (fl_user / "Settings" / "Piano roll scripts" / "FL MCP.pyscript").read_text(
        encoding="utf-8"
    )
    assert json.dumps(str(custom_inbox.resolve())) in pyscript

    mf = build_mcpb.manifest()
    assert mf["user_config"]["port"]["default"] == "FL MCP"
    assert mf["server"]["mcp_config"]["env"]["FL_MCP_PORT"] == "${user_config.port}"
