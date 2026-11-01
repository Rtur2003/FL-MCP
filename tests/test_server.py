"""MCP surface: schemas every model can read, and the tools end to end against the FL fake."""

import json
import re

import pytest
from mcp import Client

from fl_mcp import server

pytestmark = pytest.mark.anyio

NAMES = [
    "fl_status",
    "fl_produce",
    "fl_mix",
    "fl_list",
    "fl_transport",
    "fl_set",
    "fl_steps",
    "fl_notes",
    "fl_param",
    "fl_call",
    "fl_api",
]
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
    assert size < 11500, size  # roughly 2.8k tokens for the whole server


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
    assert r["rows"] == [[0, "Cutoff", 0.5]]
    r = await run("fl_param", {"target": "mixer", "index": 10, "slot": 0, "param": 0, "value": 1})
    assert r["param"][2] == 1.0
    assert "needs param" in await run(
        "fl_param", {"target": "channel", "index": 2, "value": 1}, expect_error=True
    )
    assert "needs slot" in await run(
        "fl_param", {"target": "mixer", "index": 10}, expect_error=True
    )


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
