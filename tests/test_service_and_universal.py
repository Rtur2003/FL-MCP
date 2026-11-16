"""Tests for universal paths, multi-transport AI clients, background service, and visual assets."""

import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from fl_mcp import cli, clients, dashboard, paths, server, service_daemon
from fl_mcp.assets import load_asset

pytestmark = pytest.mark.anyio


# ---- Universal paths & environment resolution


def test_fl_user_dir_env_override_and_redirected_candidates(tmp_path, monkeypatch):
    custom = tmp_path / "CustomUser"
    monkeypatch.setenv("FL_MCP_FL_USER_DIR", str(custom))
    assert paths.fl_user_dir() == custom.resolve()

    monkeypatch.delenv("FL_MCP_FL_USER_DIR")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))

    # When isolated home lacks FL Studio and Windows registry/OneDrive detection is enabled
    od = fake_home / "OneDrive" / "Documents" / "Image-Line" / "FL Studio"
    (od / "Settings").mkdir(parents=True)
    monkeypatch.setattr(paths, "_is_isolated_home", lambda h: False)
    if sys.platform == "win32":
        monkeypatch.setattr(paths, "_win_user_data_candidates", lambda: ([], None))
    assert paths.fl_user_dir() == od

    # Registry / Shell documents candidate outside %USERPROFILE% (e.g. D:\Documents) takes priority
    moved = tmp_path / "D_Drive" / "Documents" / "Image-Line" / "FL Studio"
    (moved / "Settings").mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(paths, "_win_user_data_candidates", lambda: ([moved], moved.parent.parent))
    assert paths.fl_user_dir() == moved


def test_paths_diagnostics_and_mcp_resource(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "app"))
    monkeypatch.setenv("FL_MCP_FL_USER_DIR", str(tmp_path / "fl_user"))
    monkeypatch.setenv("FL_MCP_LICENSE", "secret-should-not-leak")
    diag = paths.diagnostics()
    assert diag["data_dir"] == str((tmp_path / "app").resolve())
    assert diag["logs_dir"] == str((tmp_path / "app" / "logs").resolve())
    assert diag["fl_user_dir"] == str((tmp_path / "fl_user").resolve())
    assert "FL_MCP_LICENSE" not in diag["env"]

    raw = server._info_paths()
    parsed = json.loads(raw)
    assert parsed["data_dir"] == diag["data_dir"]
    assert "service" in parsed


# ---- Multi-client discovery, Antigravity & HTTP transport config


def test_clients_http_transport_antigravity_and_uvx_fallback(tmp_path, monkeypatch):
    # Antigravity default JSON registration
    ws_ag = tmp_path / "ag.json"
    res = clients.install_client("antigravity", source="local", path=ws_ag)
    assert res["written"] is True
    assert (
        json.loads(ws_ag.read_text(encoding="utf-8"))["mcpServers"]["fl-studio"]["command"]
        == sys.executable
    )

    # HTTP transport snippets and config writes across formats
    _, http_code = clients.snippet("claude-code", transport="http", url="http://127.0.0.1:9123/mcp")
    assert http_code == "claude mcp add --transport http fl-studio http://127.0.0.1:9123/mcp"

    _, http_vs = clients.snippet("vscode", transport="http")
    assert json.loads(http_vs)["servers"]["fl-studio"] == {
        "type": "http",
        "url": clients.DEFAULT_HTTP_URL,
    }

    _, http_gem = clients.snippet("gemini", transport="http")
    assert json.loads(http_gem)["mcpServers"]["fl-studio"] == {"httpUrl": clients.DEFAULT_HTTP_URL}

    codex_cfg = tmp_path / "codex.toml"
    codex_cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
    clients.install_client(
        "codex", path=codex_cfg, transport="http", url="http://127.0.0.1:8765/mcp"
    )
    assert 'url = "http://127.0.0.1:8765/mcp"' in codex_cfg.read_text(encoding="utf-8")

    # resolve_uvx falls back to ~/.local/bin/uvx(.exe) when PATH misses it
    fake_home = tmp_path / "uvhome"
    exe_name = "uvx.exe" if os.name == "nt" else "uvx"
    local_uvx = fake_home / ".local" / "bin" / exe_name
    local_uvx.parent.mkdir(parents=True)
    local_uvx.write_text("", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert clients.resolve_uvx() == str(local_uvx)


def test_client_statuses_and_cli_config_status(tmp_path, monkeypatch, capsys):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    ag = fake_home / ".gemini" / "antigravity" / "mcp_config.json"
    clients.install_client("antigravity", transport="http", path=ag)

    statuses = {row["id"]: row for row in clients.client_statuses(project=tmp_path)}
    assert statuses["antigravity"]["configured"] is True
    assert statuses["antigravity"]["transport"] == "http"

    with pytest.raises(SystemExit) as ex:
        cli.main(["config", "--status", "--project", str(tmp_path)])
    assert ex.value.code == 0
    assert "antigravity" in capsys.readouterr().out


# ---- Assets, Dashboard UI & Background Service ASGI Stack


def test_assets_loader_and_dashboard_web_contract(tmp_path, monkeypatch):
    for name in (
        "icon-64.png",
        "icon-128.png",
        "icon-256.png",
        "favicon.png",
        "logo.svg",
        "studio-backdrop.webp",
    ):
        loaded = load_asset(name)
        assert loaded is not None, f"missing asset {name}"
        assert len(loaded[0]) > 100
    assert load_asset("../server.py") is None
    assert load_asset("nested/icon.png") is None

    html = dashboard.load_html()
    for required in (
        '<html lang="en">',
        '<meta name="color-scheme" content="dark">',
        "/assets/icon-64.png",
        "/assets/studio-backdrop.webp",
        'role="tablist"',
        'role="tabpanel"',
        'aria-live="polite"',
        "content-visibility: auto",
        "Traffic &amp; Bridge",
    ):
        assert required in html
    assert "Gelen / Giden Trafik" not in html


def test_background_service_asgi_endpoints_and_mcp_http(tmp_path, monkeypatch):
    import asyncio

    import uvicorn

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "svcdata"))
    monkeypatch.setenv("FL_MCP_DESKTOP", "0")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    app = service_daemon.create_asgi_app(host="127.0.0.1", port=port, poll=False)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=lambda: asyncio.run(uv_server.serve()), daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/api/service", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.05)

        with urllib.request.urlopen(f"{base}/assets/icon-64.png", timeout=3) as r:
            assert r.status == 200 and r.read().startswith(b"\x89PNG")

        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(f"{base}/assets/missing.png", timeout=3)
        assert err.value.code == 404

        with urllib.request.urlopen(f"{base}/api/paths", timeout=3) as r:
            assert json.loads(r.read().decode("utf-8"))["data_dir"] == str(
                (tmp_path / "svcdata").resolve()
            )

        with urllib.request.urlopen(f"{base}/api/clients", timeout=3) as r:
            cli_data = json.loads(r.read().decode("utf-8"))
            assert any(c["id"] == "antigravity" for c in cli_data["clients"])

        # Toggle background poller via REST API
        req_cfg = urllib.request.Request(
            f"{base}/api/service/poll-config",
            data=json.dumps({"enabled": True, "interval_s": 12.0}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_cfg, timeout=3) as r:
            poller = json.loads(r.read().decode("utf-8"))
            assert poller["enabled"] is True and poller["interval_s"] == 12.0

        # Real Streamable-HTTP MCP initialize request succeeds on /mcp
        mcp_req = urllib.request.Request(
            f"{base}/mcp",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1.0"},
                    },
                }
            ).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        with urllib.request.urlopen(mcp_req, timeout=5) as r:
            assert r.status == 200
            body = r.read().decode("utf-8")
            assert "fl_mcp" in body
    finally:
        uv_server.should_exit = True
        thread.join(timeout=5)


def test_service_pid_cleanup_and_autostart_generators(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "svc"))
    service_daemon.pid_path().parent.mkdir(parents=True, exist_ok=True)
    service_daemon.pid_path().write_text("999999\n", encoding="utf-8")
    service_daemon.state_path().write_text(
        json.dumps({"pid": 999999, "port": 8765}), encoding="utf-8"
    )
    monkeypatch.setattr(service_daemon, "is_pid_alive", lambda _pid: False)

    st = service_daemon.service_status()
    assert st["running"] is False and st["pid"] is None
    assert not service_daemon.pid_path().exists()

    # Cross-platform autostart unit generation (sandboxed to tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(sys, "platform", "linux")
    res = service_daemon.enable_autostart(port=8899, poll=False)
    unit = Path(res["path"])
    assert res["enabled"] and unit.is_file()
    content = unit.read_text(encoding="utf-8")
    assert "ExecStart=" in content and "--port 8899" in content and "--no-poll" in content
    assert service_daemon.autostart_status()["enabled"] is True
    assert service_daemon.disable_autostart()["enabled"] is False
    assert not unit.exists()

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "mac"))
    res_mac = service_daemon.enable_autostart(port=8765, poll=True)
    plist = Path(res_mac["path"])
    assert res_mac["enabled"] and plist.is_file()
    assert "<string>io.flmcp.service</string>" in plist.read_text(encoding="utf-8")
    service_daemon.disable_autostart()
    assert not plist.exists()

    # CLI service status --json
    with pytest.raises(SystemExit) as ex:
        cli.main(["service", "status", "--json"])
    assert ex.value.code == 0
    assert json.loads(capsys.readouterr().out)["running"] is False


def test_mcpb_bundle_contains_icon_and_assets(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_mcpb", Path("packaging/build_mcpb.py"))
    bm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bm)
    bundle = bm.build(tmp_path)
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert manifest["icon"] == "icon.png"
    assert "icon.png" in names
    assert "src/fl_mcp/assets/icon-256.png" in names
    assert "src/fl_mcp/assets/studio-backdrop.webp" in names
