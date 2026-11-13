"""Tests for telemetry logging, snapshot cache, live dashboard HTTP server, CLI, and DX tools."""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest
from mcp import Client

from fl_mcp import cli, dashboard, desk, server, telemetry
from fl_mcp.bridge import FLError
from fl_mcp.desk import Desk
from tests import flpgen
from tests.fakedesk import FL, FakeDesk


def test_midi_telemetry_records_tx_rx_retries_and_errors(fl, tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))

    status = fl.bridge.call({"o": "status"})
    assert status["bpm"] == 140.0

    fl.bridge.drop_next = 1
    assert fl.bridge.call({"o": "status"})["bpm"] == 140.0

    with pytest.raises(FLError):
        fl.bridge.call({"o": "nope"})

    report = telemetry.read_traffic(limit=50)
    st = report["stats"]
    assert st["midi_calls"] == 4
    assert st["midi_errors"] == 1
    assert st["retries"] >= 1
    assert st["tx_bytes"] > 0 and st["rx_bytes"] > 0
    assert st["tx_frames"] >= 4 and st["rx_frames"] >= 3

    events = report["events"]
    assert [e["op"] for e in events] == ["ping", "status", "status", "nope"]
    assert events[1]["dir"] == "midi" and events[1]["ok"] is True
    assert events[2]["attempts"] == 2
    assert events[3]["ok"] is False and events[3]["error"]

    err_only = telemetry.read_traffic(only_errors=True)["events"]
    assert len(err_only) == 1 and err_only[0]["op"] == "nope"

    monkeypatch.setenv("FL_MCP_TRAFFIC", "0")
    fl.bridge.call({"o": "status"})
    monkeypatch.delenv("FL_MCP_TRAFFIC")
    assert telemetry.read_traffic()["stats"]["midi_calls"] == 4

    assert telemetry.clear_traffic() == 4
    assert telemetry.read_traffic()["stats"]["total"] == 0


@pytest.mark.anyio
async def test_server_tools_resources_steps_notes_and_produce(fl, tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "bridge", fl.bridge)

    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(parents=True)
    tpl_file = tpl_dir / "Minimal Beat.flp"
    tpl_file.write_bytes(flpgen.template(sampler=True))
    monkeypatch.setenv("FL_MCP_TEMPLATES", str(tpl_dir))

    async with Client(server.mcp) as client:
        step_res = json.loads(
            (
                await client.call_tool(
                    "fl_steps",
                    {"channel": 0, "grid": "f...|8...|a...|....", "swing": 6},
                )
            )
            .content[0]
            .text
        )
        assert step_res["on"] == [0, 4, 8]
        assert step_res["view"] == "f...8...a......."
        assert step_res["bad"] == 0

        plain_steps = json.loads(
            (await client.call_tool("fl_steps", {"channel": 0, "on": [0, 4], "clear": True}))
            .content[0]
            .text
        )
        assert plain_steps["on"] == [0, 4]
        assert plain_steps["view"] == "x...x..........."

        monkeypatch.setenv("FL_MCP_DESKTOP", "0")
        err_res = await client.call_tool("fl_notes", {"notes": [[60, 0, 1, 100]], "apply": True})
        assert err_res.is_error
        assert "desktop control" in err_res.content[0].text

        monkeypatch.setenv("FL_MCP_DESKTOP", "1")
        now = [0.0]
        backend = FakeDesk(front=FL, windows=[FL])

        def fake_fl_script(combo):
            if "y" in combo.key and "alt" in combo.mods:
                nd = tmp_path / "data"
                inbox = json.loads((nd / "inbox.json").read_text(encoding="utf-8"))
                (nd / "state.json").write_text(
                    json.dumps(
                        {
                            "serial": inbox["serial"],
                            "ts": int(time.time()),
                            "count": len(inbox["notes"]),
                            "notes": inbox["notes"],
                        }
                    ),
                    encoding="utf-8",
                )

        backend.on_key = fake_fl_script
        fake_desk = Desk(
            backend, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0]
        )
        monkeypatch.setattr(desk, "get", lambda: fake_desk)

        notes_res = json.loads(
            (
                await client.call_tool(
                    "fl_notes",
                    {
                        "notes": [[60, 0, 1, 100], [64, 1, 1, 95]],
                        "replace": True,
                        "apply": True,
                    },
                )
            )
            .content[0]
            .text
        )
        assert notes_res["queued"] == 2
        assert notes_res["applied"] is True

        status_res = json.loads(
            (await client.call_tool("fl_status", {"log": True})).content[0].text
        )
        assert "log" in status_res and "traffic" in status_res
        assert status_res["traffic"]["midi_calls"] >= 1
        assert status_res["traffic"]["tx_bytes"] > 0

        listed = json.loads(
            (await client.call_tool("fl_produce", {"action": "inspect", "template": "list"}))
            .content[0]
            .text
        )
        assert listed["count"] == 1 and listed["ready_count"] == 1
        assert listed["templates"][0]["sampler_ready"] is True

        built = json.loads(
            (
                await client.call_tool(
                    "fl_produce",
                    {
                        "action": "flp",
                        "genre": "house",
                        "seed": 7,
                        "length": "sketch",
                        "template": "auto",
                    },
                )
            )
            .content[0]
            .text
        )
        assert built["flp"]["template"] == str(tpl_file)

        live_traffic = json.loads(
            (await client.read_resource("fl://live/traffic")).contents[0].text
        )
        assert live_traffic["stats"]["mcp_calls"] >= 4
        assert live_traffic["stats"]["midi_calls"] >= 1

        live_studio = json.loads((await client.read_resource("fl://live/studio")).contents[0].text)
        assert live_studio["status"]["bpm"] == 140.0
        assert live_studio["piano_inbox"]["count"] == 2
        assert live_studio["piano_state"]["applied"] is True


def test_dashboard_http_and_poll(fl, tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "Starter.flp").write_bytes(flpgen.template(sampler=True))
    monkeypatch.setenv("FL_MCP_TEMPLATES", str(tpl_dir))

    srv = dashboard.start_server("127.0.0.1", 0, bridge_factory=lambda: fl.bridge)
    port = srv.server_port
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(f"{base}/", timeout=5) as r:
            assert r.status == 200
            html = r.read().decode("utf-8")
            assert "FL-MCP Studio" in html

        req = urllib.request.Request(f"{base}/api/poll", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            polled = json.loads(r.read().decode("utf-8"))
        assert polled["ok"] is True
        assert polled["status"]["bpm"] == 140.0

        with urllib.request.urlopen(f"{base}/api/state?refresh_templates=1", timeout=5) as r:
            state = json.loads(r.read().decode("utf-8"))
        assert state["studio"]["status"]["bpm"] == 140.0
        assert len(state["studio"]["channels"]["rows"]) == 3
        assert len(state["studio"]["peaks"]["rows"]) >= 1
        assert state["traffic"]["stats"]["midi_calls"] >= 5
        assert state["templates"][0]["name"] == "Starter"

        with urllib.request.urlopen(f"{base}/api/doctor", timeout=5) as r:
            doc = json.loads(r.read().decode("utf-8"))
        assert doc["port_name"] == "FL MCP"

        clr = urllib.request.Request(f"{base}/api/traffic/clear", data=b"", method="POST")
        with urllib.request.urlopen(clr, timeout=5) as r:
            cleared = json.loads(r.read().decode("utf-8"))
        assert cleared["cleared"] >= 5
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_traffic_and_templates(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "Trap Starter.flp").write_bytes(flpgen.template(sampler=True))
    monkeypatch.setenv("FL_MCP_TEMPLATES", str(tpl_dir))

    telemetry.record_midi(
        op="status",
        fid=1,
        req={"o": "status"},
        tx_bytes=12,
        tx_frames=6,
        rx_bytes=48,
        rx_frames=24,
        attempts=1,
        ms=12.4,
        ok=True,
        res={"bpm": 140.0},
    )
    telemetry.record_mcp(tool="fl_status", args={}, out_bytes=120, ms=14.0, ok=True)

    with pytest.raises(SystemExit) as e1:
        cli.main(["traffic", "--json"])
    assert e1.value.code == 0
    traf = json.loads(capsys.readouterr().out)
    assert traf["stats"]["midi_calls"] == 1 and traf["stats"]["mcp_calls"] == 1

    with pytest.raises(SystemExit) as e2:
        cli.main(["templates", "--json"])
    assert e2.value.code == 0
    tpls = json.loads(capsys.readouterr().out)
    assert len(tpls) == 1 and tpls[0]["sampler_ready"] is True

    with pytest.raises(SystemExit) as e3:
        cli.main(["traffic", "--clear"])
    assert e3.value.code == 0
    assert "cleared 2 traffic events" in capsys.readouterr().out


def test_read_snapshot_ignores_non_dict_piano_and_plan_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    nd = tmp_path / "home" / ".fl-mcp"
    nd.mkdir(parents=True)
    (nd / "inbox.json").write_text("[1, 2]", encoding="utf-8")
    (nd / "state.json").write_text('"bad"', encoding="utf-8")
    plans = tmp_path / "data" / "plans"
    plans.mkdir(parents=True)
    (plans / "p.json").write_text("[]", encoding="utf-8")

    snap = telemetry.read_snapshot()
    assert "piano_inbox" not in snap
    assert "piano_state" not in snap
    assert "latest_plan" not in snap
