"""MCP surface: schemas every model can read, and the tools end to end against the FL fake."""

import json
import re

import pytest
from mcp import Client

from fl_mcp import server
from tests.names import NAMES

pytestmark = pytest.mark.anyio

BANNED = {
    "$ref",
    "$defs",
    "anyOf",
    "oneOf",
    "allOf",
    "not",
    "title",
    "pattern",
    "format",
    "additionalProperties",
    "examples",
    "const",
}


@pytest.fixture
def wired(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    fl.dir = tmp_path
    return fl


async def run(tool, args=None, expect_error=False):
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args or {})
    assert result.is_error is expect_error, result.content[0].text
    assert result.structured_content is None
    text = result.content[0].text
    return text if expect_error or tool == "fl_api" else json.loads(text)


def walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            if key != "properties":
                yield from walk(value, f"{path}/{key}")
            else:
                for name, sub in value.items():
                    yield from walk(sub, f"{path}/{name}")
    elif isinstance(node, list):
        for item in node:
            yield from walk(item, path)


async def test_tool_list_is_small_flat_and_portable():
    async with Client(server.mcp) as client:
        tools = (await client.list_tools()).tools
        assert client.instructions and "fl_produce" in client.instructions
    assert [t.name for t in tools] == NAMES
    for tool in tools:
        assert re.fullmatch(r"[a-z][a-z0-9_]{2,40}", tool.name)
        assert 30 < len(tool.description) < 650 and tool.output_schema is None
        schema = tool.input_schema
        assert schema["type"] == "object" and "properties" in schema
        assert set(schema.get("required", [])) <= set(schema["properties"])
        for path, key, value in walk(schema):
            assert key not in BANNED, f"{tool.name}{path}: {key}"
            assert not (key == "default" and value is None)
        for name, prop in schema["properties"].items():
            assert "type" in prop or "enum" in prop, f"{tool.name}.{name}"
            assert prop.get("type") != "array" or "items" in prop, f"{tool.name}.{name}"
            assert prop.get("type") != "array" or "type" in prop["items"]
    size = len(json.dumps([t.model_dump(by_alias=True, exclude_none=True) for t in tools]))
    assert size < 16500, size  # about 4k tokens for the always-on tools


async def test_annotations_tell_clients_what_is_safe():
    async with Client(server.mcp) as client:
        tools = {t.name: t.annotations for t in (await client.list_tools()).tools}
    assert all(a.open_world_hint is False for a in tools.values())
    for name in ("fl_status", "fl_list", "fl_api"):
        assert tools[name].read_only_hint is True
    assert tools["fl_call"].destructive_hint is True and tools["fl_set"].destructive_hint is False


async def test_status_and_list(wired):
    status = await run("fl_status")
    assert status["bpm"] == 140.0 and status["n_ch"] == 3
    rows = (await run("fl_list", {"kind": "channels"}))["rows"]
    assert [r[1] for r in rows] == ["Kick", "Snare", "Lead"]
    mixer = await run("fl_list", {"kind": "mixer", "count": 1, "include_unused": True})
    assert len(mixer["rows"]) == 1 and mixer["next"] == 1


async def test_transport(wired):
    assert (await run("fl_transport", {"action": "play"}))["play"] is True
    assert (await run("fl_transport", {"action": "tempo", "value": 150}))["bpm"] == 150.0
    assert "needs a value" in await run("fl_transport", {"action": "tempo"}, expect_error=True)
    assert "needs a value" in await run("fl_transport", {"action": "loop"}, expect_error=True)
    assert "validation error" in await run("fl_transport", {"action": "jump"}, expect_error=True)


async def test_set_accepts_nulls_and_reports_problems(wired):
    row = await run(
        "fl_set",
        {
            "kind": "mixer",
            "index": 3,
            "name": "Bus",
            "volume": 0.5,
            "color": None,
            "mute": None,
            "pan": None,
        },
    )
    assert row["name"] == "Bus" and row["vol"] == 0.5
    assert "nothing to change" in await run(
        "fl_set", {"kind": "mixer", "index": 3}, expect_error=True
    )
    error = await run("fl_set", {"kind": "channel", "index": 9, "name": "x"}, expect_error=True)
    assert "channel 9 out of range 0..2" in error
    error = await run("fl_set", {"kind": "channel", "index": 0, "send": 1}, expect_error=True)
    assert "not settable on channel: send" in error


async def test_steps(wired):
    r = await run("fl_steps", {"channel": 0, "on": [0, 4, 8, 12], "velocity": 110, "pitch": 36})
    assert r["on"] == [0, 4, 8, 12]
    assert wired.fake.chans[0]["sp"][(1, 4, 1)] == 110
    r = await run("fl_steps", {"channel": 0, "clear": True, "on": [2], "off": None})
    assert r["on"] == [2]
    assert (await run("fl_steps", {"channel": 0}))["on"] == [2]


async def test_param(wired):
    r = await run("fl_param", {"target": "channel", "index": 2, "find": "cut"})
    assert r["rows"] == [[0, "Cutoff", 0.5, "0.50"]]
    r = await run("fl_param", {"target": "mixer", "index": 10, "slot": 0, "param": "0", "value": 1})
    assert r["param"][2] == 1.0
    assert "need param" in await run(
        "fl_param", {"target": "channel", "index": 2, "value": 1}, expect_error=True
    )
    assert "needs slot" in await run(
        "fl_param", {"target": "mixer", "index": 10}, expect_error=True
    )


async def test_param_by_name_real_units_settings_and_presets(wired):
    f = wired.fake
    f.tracks[12]["slots"] = {0: "Fruity Limiter"}
    f.params[("m", 12, 0)] = [
        ["Gain", 0.5, "db"],
        ["Threshold", 0.7, "db"],
        ["Attack", 0.1, "ms"],
        ["Ratio", 0.2, "ratio"],
    ]
    f.presets[("m", 12, 0)] = ["Default", "Gentle", "Hard"]
    f.preset_at[("m", 12, 0)] = 0
    base = {"target": "mixer", "index": 12, "slot": 0}

    r = await run("fl_param", {**base, "param": "thresh", "real": "-18 dB"})
    assert r["param"][1] == "Threshold" and r["param"][4] == 1
    assert abs(float(r["param"][3].split()[0]) + 18) < 0.2

    r = await run("fl_param", {**base, "settings": "Attack=5 ms, Ratio=8:1, Gain=0.6"})
    assert [row[3] for row in r["params"]] == ["5 ms", "8.0:1", "-24.0 dB"]
    r = await run("fl_param", {**base, "settings": "Gain=~0.25"})
    assert r["params"][0][1] == "Gain"

    assert (await run("fl_param", {**base, "view": "info"}))["presets"] == 3
    assert (await run("fl_param", {**base, "view": "presets"}))["cur"] == "Default"
    assert (await run("fl_param", {**base, "preset": "hard"}))["preset"] == "Hard"
    assert (await run("fl_param", {**base, "preset": "-1"}))["preset"] == "Gentle"
    assert (await run("fl_param", {**base, "preset": "+2"}))["preset"] == "Default"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"settings": "Gain"}, "should look like Name=value"),
        ({"settings": " , "}, "settings is empty"),
        ({"param": "Gain", "value": 0.5, "real": "3 dB"}, "not both"),
        ({"real": "3 dB"}, "need param"),
        ({"param": "nope", "value": 0.5}, "no parameter named"),
    ],
)
async def test_param_errors(wired, args, message):
    wired.fake.tracks[12]["slots"] = {0: "Fruity Limiter"}
    wired.fake.params[("m", 12, 0)] = [["Gain", 0.5, "db"]]
    error = await run(
        "fl_param", {"target": "mixer", "index": 12, "slot": 0, **args}, expect_error=True
    )
    assert message in error


async def test_plugin_inventory_through_fl_list(wired):
    out = await run("fl_list", {"kind": "plugins"})
    assert out["cols"][:2] == ["i", "at"]
    assert {row[4] for row in out["rows"]} == {"Sytrus", "Fruity Reeverb 2"}


async def test_status_can_probe_and_show_the_script_log(wired):
    out = await run("fl_status", {"probe": True, "log": True})
    assert out["probe"]["has"]["ui.navigateBrowser"] is True
    assert out["log"] == {"n": out["log"]["n"], "failed": 0, "errors": []}
    await run("fl_call", {"calls": '[["os.system", "x"]]'}, expect_error=True)
    assert (await run("fl_status", {"log": True}))["log"]["failed"] == 1


async def test_fl_ui_windows_keys_and_browser(wired):
    state = await run("fl_ui", {"action": "window", "window": "mixer", "mode": "show"})
    assert state["visible"] is True
    out = await run("fl_ui", {"action": "key", "keys": "down, down ,enter,f8"})
    assert out["n"] == 4
    scan = await run("fl_ui", {"action": "browser", "browse": "scan", "count": 5})
    assert scan["rows"][0][0] == "Generators"
    wired.fake.browser_at = 0
    found = await run("fl_ui", {"action": "browser", "browse": "goto", "query": "sytrus"})
    assert found["found"] is True
    await run("fl_ui", {"action": "browser", "browse": "select"})
    assert ("select", "Sytrus") in wired.fake.browser_log
    assert (await run("fl_ui", {"action": "form"}))["plugin"] == "Fruity Limiter"
    for args, message in [
        ({"action": "window"}, "needs which"),
        ({"action": "key"}, "needs keys"),
        ({"action": "browser", "browse": "goto"}, "needs query"),
        ({"action": "key", "keys": "launch"}, "unknown key"),
    ]:
        assert message in await run("fl_ui", args, expect_error=True)


async def test_call(wired):
    out = await run("fl_call", {"calls": '[["mixer.getTrackName", 10], ["ui.getProgTitle"]]'})
    assert out == ["Drums", "My Song - FL Studio"]
    assert await run("fl_call", {"calls": '["transport.isPlaying"]'}) == [False]
    for bad in ("nope", "[]", "[[1]]", "{}", json.dumps([["transport.stop"]] * 101)):
        assert "calls must be" in await run("fl_call", {"calls": bad}, expect_error=True)
    error = await run("fl_call", {"calls": '[["os.system", "x"]]'}, expect_error=True)
    assert "not allowed" in error


async def test_notes_are_queued_for_the_piano_roll_script(wired):
    notes = [[60, 0, 1], [64, 1, 1, 90], [67.0, 2, 0.5, 127.0]]
    r = await run("fl_notes", {"notes": notes, "replace": True, "channel": 2, "pattern": 3})
    assert r["queued"] == 3 and r["channel"] == 2 and r["pattern"] == 3
    assert "Tools > Scripting > FL MCP" in r["next"]
    queue = json.loads((wired.dir / "inbox.json").read_text())
    assert queue["notes"] == [[60, 0.0, 1.0, 100], [64, 1.0, 1.0, 90], [67, 2.0, 0.5, 127]]
    assert queue["replace"] is True and isinstance(queue["serial"], int)
    assert wired.fake.windows == [3] and wired.fake.pat == 3


@pytest.mark.parametrize(
    ("notes", "message"),
    [
        ([[60, 0]], "each note is"),
        ([[128, 0, 1]], "midi_note"),
        ([[60.5, 0, 1]], "midi_note"),
        ([[60, -1, 1]], "start must be"),
        ([[60, 0, 0]], "length > 0"),
        ([[60, 0, 1, 0]], "velocity"),
        ([], "pass notes"),
        ([[60, 0, 1]] * 1001, "at most 1000"),
    ],
)
async def test_notes_validation(wired, notes, message):
    assert message in await run("fl_notes", {"notes": notes}, expect_error=True)
    assert not (wired.dir / "inbox.json").exists()


async def test_notes_read_back(wired):
    assert "No export yet" in await run("fl_notes", {"read": True}, expect_error=True)
    await run("fl_notes", {"notes": [[60, 0, 1]]})
    serial = json.loads((wired.dir / "inbox.json").read_text())["serial"]
    state = {
        "ts": __import__("time").time() - 5,
        "count": 1,
        "serial": serial,
        "notes": [[60, 0, 1, 100]],
    }
    (wired.dir / "state.json").write_text(json.dumps(state))
    r = await run("fl_notes", {"read": True})
    assert r["applied"] is True and r["count"] == 1 and r["notes"] == [[60, 0, 1, 100]]
    assert 4 <= r["age_s"] <= 8 and r["truncated"] is False
    state["serial"] = serial + 1
    (wired.dir / "state.json").write_text(json.dumps(state))
    assert (await run("fl_notes", {"read": True}))["applied"] is False


async def test_fl_errors_reach_the_model_with_guidance(wired):
    wired.bridge.drop_next = 99
    error = await run("fl_status", expect_error=True)
    assert "No answer from FL Studio" in error and "fl-mcp doctor" in error


async def test_api_reference_needs_no_fl():
    assert (await run("fl_api")).startswith("modules: transport(20), mixer(")
    text = await run("fl_api", {"module": "mixer", "query": "settrackvolume"})
    assert text == "setTrackVolume(index:int, volume:float, pickupMode:int=midi.PIM_None)"
    assert "REC_Tempo=" in await run("fl_api", {"module": "midi", "query": "rec_tempo"})
    assert "more; narrow" in await run("fl_api", {"module": "midi"})
    assert await run("fl_api", {"module": "mixer", "query": "zzz"}) == "no match"
    assert "unknown module" in await run("fl_api", {"module": "os"}, expect_error=True)


# ---- fl_audio


@pytest.fixture
def mixdown(tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("scipy")
    t = np.arange(48000 * 12) / 48000
    tone = (10 ** (-23 / 20) * np.sin(2 * np.pi * 997 * t)).astype("float32")
    path = tmp_path / "mix.wav"
    sf.write(str(path), np.stack([tone, tone], axis=1), 48000, subtype="FLOAT")
    return path


async def test_audio_analysis_is_judged_against_the_genre_loudness(mixdown):
    out = await run("fl_audio", {"path": str(mixdown), "genre": "house"})
    assert out["lufs_i"] == pytest.approx(-23.0, abs=0.2) and out["target_lufs"] == -8.0
    assert any("quieter than the -8.0 target" in tip for tip in out["advice"])
    plain = await run("fl_audio", {"path": str(mixdown)})
    assert plain["target_lufs"] == -14.0
    custom = await run("fl_audio", {"path": str(mixdown), "target": -23.0})
    assert not [t for t in custom["advice"] if "louder" in t or "quieter" in t]


async def test_audio_without_a_path_takes_the_latest_export(mixdown, monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_PROJECTS", str(mixdown.parent))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert (await run("fl_audio", {}))["file"] == str(mixdown)
    monkeypatch.setenv("FL_MCP_PROJECTS", str(tmp_path / "empty"))
    assert "no audio file" in await run("fl_audio", {}, expect_error=True)


async def test_audio_render_exports_with_fl_then_analyzes(mixdown, monkeypatch, tmp_path):
    from fl_mcp.bridge import Bridge

    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")
    calls = []

    def fake_render(project, fmt):
        calls.append((str(project), fmt))
        return {"audio": str(mixdown), "fmt": fmt, "seconds": 12.0}

    monkeypatch.setattr(server, "bridge", Bridge("__offline__"))
    monkeypatch.setattr(server.render, "render", fake_render)
    monkeypatch.setenv("FL_MCP_DESKTOP", "0")
    out = await run("fl_audio", {"action": "render", "project": str(flp), "fmt": "flac"})
    assert calls == [(str(project), fmt) for project, fmt in [(flp, "flac")]]
    assert out["render"]["seconds"] == 12.0 and out["lufs_i"] == pytest.approx(-23.0, abs=0.2)
    monkeypatch.setenv("FL_MCP_PROJECTS", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    error = await run("fl_audio", {"action": "render"}, expect_error=True)
    assert "save it in FL" in error


async def test_audio_compare_needs_a_reference(mixdown, tmp_path):
    error = await run("fl_audio", {"action": "compare", "path": str(mixdown)}, expect_error=True)
    assert "needs reference" in error
    out = await run(
        "fl_audio", {"action": "compare", "path": str(mixdown), "reference": str(mixdown)}
    )
    assert out["loudness_diff_lu"] == 0 and out["tonal"] == []


async def test_audio_status_and_missing_libraries_are_explained(mixdown, monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("FL_MCP_PROJECTS", str(tmp_path / "empty"))
    status = await run("fl_audio", {"action": "status"})
    assert status["analysis"] is True and status["fl_exe"] is None

    def missing():
        raise ValueError("audio analysis needs numpy, scipy and soundfile: install the audio extra")

    monkeypatch.setattr(server.audio, "_libs", missing)
    assert (await run("fl_audio", {"action": "status"}))["analysis"] is False
    error = await run("fl_audio", {"path": str(mixdown)}, expect_error=True)
    assert "audio extra" in error


# ---- fl_desk (opt-in)

import base64  # noqa: E402
from pathlib import Path  # noqa: E402

from mcp.server import MCPServer  # noqa: E402

from fl_mcp import desk  # noqa: E402
from fl_mcp.desk import Desk  # noqa: E402
from tests.fakedesk import FL, FakeDesk  # noqa: E402


@pytest.fixture
def screen(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setenv("FL_MCP_DESKTOP", "1")
    fake = FakeDesk(front=FL)
    desk.use(Desk(fake, sleep=lambda s: None, platform="win32"))
    yield fake
    desk.use(None)


async def desk_call(args, expect_error=False):
    srv = MCPServer("t", tools=server._tools(True))
    async with Client(srv) as client:
        result = await client.call_tool("fl_desk", args)
    assert result.is_error is expect_error, result.content[0].text
    return result


async def test_the_desk_tool_exists_only_when_asked_for():
    assert "fl_desk" not in [t.name for t in server._tools(False) and server._tools(False)]
    assert "fl_desk" in [t.name for t in server._tools(True)]
    async with Client(server.mcp) as client:
        names = [t.name for t in (await client.list_tools()).tools]
    assert ("fl_desk" in names) is desk.enabled()


async def test_the_desk_schema_is_as_plain_as_the_others():
    srv = MCPServer("t", tools=server._tools(True))
    async with Client(srv) as client:
        tool = next(t for t in (await client.list_tools()).tools if t.name == "fl_desk")
    schema = tool.input_schema
    assert set(schema["required"]) == {"action"} and 100 < len(tool.description) < 700
    for path, key, value in walk(schema):
        assert key not in BANNED, f"{path}: {key}"
        assert not (key == "default" and value is None)
    for name, prop in schema["properties"].items():
        assert "type" in prop or "enum" in prop, name
    assert tool.annotations.destructive_hint is True


async def test_a_shot_returns_text_and_a_picture(screen):
    result = await desk_call({"action": "shot"})
    kinds = [c.type for c in result.content]
    assert kinds == ["text", "image"]
    assert json.loads(result.content[0].text)["size"] == [1568, 882]
    assert base64.b64decode(result.content[1].data).startswith(b"\x89PNG")
    assert result.content[1].mime_type == "image/png"


async def test_clicks_keys_and_text_reach_fl_and_come_back_with_a_picture(screen):
    await desk_call({"action": "shot"})
    out = await desk_call({"action": "click", "x": 784, "y": 441})
    assert json.loads(out.content[0].text) == {"did": "click", "at": [784, 441]}
    assert [c.type for c in out.content] == ["text", "image"]
    assert screen.log[-1][:4] == ("mouse", "click", 900, 500)
    keyed = await desk_call({"action": "key", "keys": "mod+s", "look": False})
    assert [c.type for c in keyed.content] == ["text"] and screen.log[-1] == ("key", "ctrl+s")
    typed = await desk_call({"action": "type", "text": "Lead 2", "look": False})
    assert json.loads(typed.content[0].text)["typed"] == 6 and screen.log[-1] == ("type", "Lead 2")


async def test_desk_errors_are_explained_to_the_model(screen):
    assert (
        "take a screenshot first"
        in (await desk_call({"action": "click", "x": 1, "y": 1}, True)).content[0].text
    )
    assert "needs x and y" in (await desk_call({"action": "double"}, True)).content[0].text
    assert "blocked" in (await desk_call({"action": "key", "keys": "alt+f4"}, True)).content[0].text
    assert "macro needs" in (await desk_call({"action": "macro"}, True)).content[0].text
    screen.front, screen.can_focus = server_browser(), False
    assert (
        "could not bring FL Studio"
        in (await desk_call({"action": "key", "keys": "space"}, True)).content[0].text
    )


def server_browser():
    from tests.fakedesk import BROWSER

    return BROWSER


async def test_status_and_wait_need_no_picture(screen):
    status = await desk_call({"action": "status"})
    assert [c.type for c in status.content] == ["text"]
    assert json.loads(status.content[0].text)["backend"] == "fake"
    waited = await desk_call({"action": "wait", "ms": 20})
    assert json.loads(waited.content[0].text) == {"waited_ms": 20}


async def test_the_script_macro_focuses_the_piano_roll_then_runs_it(screen, wired, tmp_path):
    (tmp_path / "inbox.json").write_text(json.dumps({"serial": 9, "notes": []}))

    def fl_runs(combo):
        (tmp_path / "state.json").write_text(json.dumps({"ts": 1, "serial": 9}))

    screen.on_key = fl_runs
    out = await desk_call({"action": "macro", "macro": "script", "look": False})
    assert json.loads(out.content[0].text)["applied"] is True
    assert screen.log == [("key", "ctrl+alt+y")]


async def test_the_export_macro_names_its_own_file(screen, tmp_path):
    def fl_renders(combo):
        typed = [e for e in screen.log if e[0] == "type"]
        if sum(1 for e in screen.log if e == ("key", "enter")) == 2 and typed:
            Path(typed[0][1]).write_bytes(b"x" * 64)

    screen.on_key = fl_renders
    out = await desk_call({"action": "macro", "macro": "export", "look": False})
    info = json.loads(out.content[0].text)
    assert info["audio"].startswith(str(tmp_path / "exports")) and info["fmt"] == "wav"


async def test_fl_audio_render_uses_the_open_project_when_the_desk_is_on(
    screen, tmp_path, monkeypatch
):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("scipy")
    t = np.arange(48000 * 12) / 48000
    tone = (10 ** (-20 / 20) * np.sin(2 * np.pi * 997 * t)).astype("float32")

    def watch(combo):
        typed = [e for e in screen.log if e[0] == "type"]
        if (
            combo.key == "enter"
            and typed
            and sum(1 for e in screen.log if e == ("key", "enter")) == 2
        ):
            sf.write(typed[0][1], np.stack([tone, tone], axis=1), 48000, subtype="FLOAT")

    screen.on_key = watch
    out = await run("fl_audio", {"action": "render", "fmt": "wav"})
    assert out["render"]["fmt"] == "wav" and out["lufs_i"] == pytest.approx(-20.0, abs=0.2)
    assert ("key", "ctrl+r") in screen.log


async def test_notes_point_at_the_macro_when_the_desk_is_on(screen, wired):
    out = await run("fl_notes", {"notes": [[60, 0, 1]]})
    assert out["next"] == "Call fl_desk macro=script to apply them."


async def test_without_a_supported_system_the_tool_explains(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    desk.use(None)
    monkeypatch.setattr(desk.sys, "platform", "linux")
    error = (await desk_call({"action": "status"}, True)).content[0].text
    assert "Windows and macOS" in error


async def test_fl_audio_master_needs_the_desk_and_a_limiter(wired, monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_DESKTOP", "0")
    assert "needs fl_desk" in await run("fl_audio", {"action": "master"}, expect_error=True)


async def test_fl_audio_master_brings_the_mix_to_the_target(screen, wired, tmp_path):
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("scipy")
    wired.fake.add_master_limiter(gain=0.5)  # Gain shows -30 dB
    t = np.arange(48000 * 10) / 48000

    def fl_exports(combo):
        typed = [e for e in screen.log if e[0] == "type"]
        if (
            combo.key == "enter"
            and typed
            and sum(1 for e in screen.log if e == ("key", "enter")) % 2 == 0
        ):
            gain_db = -60 + 60 * wired.fake.params[("m", 0, 9)][0][1]
            level = 10 ** ((gain_db - 5) / 20)  # louder limiter gain, louder file
            tone = (level * np.sin(2 * np.pi * 997 * t)).astype("float32")
            sf.write(typed[-1][1], np.stack([tone, tone], axis=1), 48000, subtype="FLOAT")

    screen.on_key = fl_exports
    out = await run("fl_audio", {"action": "master", "target": -23.0})
    assert out["reached"] is True and abs(out["passes"][-1]["lufs"] + 23.0) <= 0.5
    assert (
        out["limiter"]["plugin"] == "Fruity Limiter" and out["limiter"]["original_gain_db"] == -30.0
    )
    assert out["limiter"]["final_gain_db"] == pytest.approx(-18.0, abs=1.0)


async def test_command_line_render_refuses_while_fl_is_open(wired, tmp_path):
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")
    error = await run("fl_audio", {"action": "render", "project": str(flp)}, expect_error=True)
    assert "FL Studio is open" in error and "fl_desk" in error
