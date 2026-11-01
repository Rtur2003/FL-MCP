"""fl_produce and fl_mix through the MCP surface."""

import json

import pytest
from mcp import Client

from fl_mcp import mixing, server
from tests.test_produce import NAMES, TRACKS

pytestmark = pytest.mark.anyio


@pytest.fixture
def studio(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    f = fl.fake
    f.chans = [f._chan(n, 0) for n in NAMES]
    for c in f.chans:
        c["fx"] = TRACKS.get(c["name"], 0)
    f.pats = {}
    fl.dir = tmp_path
    return fl


async def run(tool, args=None, expect_error=False):
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args or {})
    assert result.is_error is expect_error, result.content[0].text
    text = result.content[0].text
    return text if expect_error else json.loads(text)


async def test_genres_are_listed():
    genres = await run("fl_produce", {"action": "genres"})
    assert "house" in genres and genres["trap"][0] == "Trap" and len(genres) == 12


async def test_make_delivers_live_and_exports_midi(studio):
    out = await run(
        "fl_produce", {"genre": "house", "key": "A minor", "seed": 7, "length": "sketch"}
    )
    plan, live = out["plan"], out["live"]
    assert plan["key"] == "A minor" and plan["genre"] == "house" and plan["id"] == "house-7"
    assert live["not_kept_by_fl"] == 0 and live["channels"]["kick"] == 0
    assert studio.fake.bpm == plan["bpm"] and studio.fake.markers
    assert out["midi"].endswith("house-7.mid") and (studio.dir / "out" / "house-7.mid").is_file()
    assert len(json.dumps(out)) < 5000, "the model never receives the notes"


async def test_make_without_fl_still_hands_over_the_midi_file(studio):
    studio.bridge.drop_next = 99
    out = await run("fl_produce", {"genre": "trap", "seed": 1, "length": "sketch"})
    assert out["live"].startswith("not applied: No answer from FL Studio") and out["midi"]
    error = await run(
        "fl_produce", {"action": "apply", "plan": out["plan"]["id"]}, expect_error=True
    )
    assert "No answer from FL Studio" in error


async def test_plan_apply_show_and_midi_actions(studio):
    plan = (
        await run("fl_produce", {"action": "plan", "genre": "dnb", "seed": 3, "length": "sketch"})
    )["plan"]
    assert "live" not in plan and studio.fake.pats == {}
    shown = await run(
        "fl_produce", {"action": "show", "plan": plan["id"], "pattern": "Drop A - Drums"}
    )
    assert shown["pattern"] == "Drop A - Drums" and shown["parts"]["kick"][0][2] == 60
    only_bass = await run(
        "fl_produce", {"action": "show", "pattern": "Drop A - Bass", "role": "bass"}
    )
    assert list(only_bass["parts"]) == ["bass"]
    applied = await run("fl_produce", {"action": "apply", "channels": "kick=5"})
    assert applied["live"]["channels"]["kick"] == 5
    midi = await run("fl_produce", {"action": "midi", "plan": plan["id"]})
    assert midi["midi"].endswith("dnb-3.mid") and "live" not in midi


async def test_queue_sends_one_melodic_part_to_the_piano_roll(studio):
    plan = (await run("fl_produce", {"genre": "house", "seed": 2, "length": "sketch"}))["plan"]
    q = await run("fl_produce", {"action": "queue", "pattern": "Drop A - Bass", "role": "bass"})
    inbox = json.loads((studio.dir / "inbox.json").read_text())
    assert q["queued"] == len(inbox["notes"]) > 8 and inbox["replace"] is True
    assert q["channel"] == 6 and "Tools > Scripting > FL MCP" in q["next"]
    live = json.loads((studio.dir / "plans" / f"{plan['id']}.live").read_text())
    index = next(i for i, p in enumerate(plan["patterns"]) if p[1] == "Drop A - Bass")
    assert q["pattern"] == live["base"] + 1 + index and studio.fake.windows == [3]
    assert all(1 <= n[3] <= 127 and 0 <= n[0] <= 127 for n in inbox["notes"])


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"action": "plan"}, "genre is required"),
        ({"action": "queue", "genre": "house"}, "queue needs pattern and role"),
        ({"action": "apply", "plan": "nope-1"}, "unknown plan"),
        ({"action": "plan", "genre": "house", "key": "Q"}, "cannot read key"),
        ({"action": "plan", "genre": "house", "roles": "tuba"}, "unknown roles"),
        ({"action": "plan", "genre": "polka"}, "validation error"),
        ({"action": "apply", "channels": "kick"}, "look like"),
    ],
)
async def test_produce_errors_are_explained(studio, args, message):
    if args.get("action") in ("apply", "queue") and args.get("plan") != "nope-1":
        await run("fl_produce", {"action": "plan", "genre": "house", "seed": 1, "length": "sketch"})
    assert message in await run("fl_produce", args, expect_error=True)


async def test_queue_errors(studio):
    await run("fl_produce", {"action": "plan", "genre": "house", "seed": 1, "length": "sketch"})
    assert "apply this plan" in await run(
        "fl_produce",
        {"action": "queue", "pattern": "Drop A - Bass", "role": "bass"},
        expect_error=True,
    )
    await run("fl_produce", {"action": "apply"})
    assert "has no lead" in await run(
        "fl_produce",
        {"action": "queue", "pattern": "Drop A - Bass", "role": "lead"},
        expect_error=True,
    )
    assert "pattern not found" in await run(
        "fl_produce", {"action": "queue", "pattern": "Nope", "role": "bass"}, expect_error=True
    )


async def test_mix_meter_and_balance(studio, monkeypatch):
    f = studio.fake
    monkeypatch.setattr(mixing.time, "sleep", lambda _: None)
    f.tracks[1]["name"] = "Kick"
    f.peaks = {0: 0.8, 1: 0.9, 3: 0.2}
    meter = await run("fl_mix", {"seconds": 1})
    assert meter["cols"] == ["track", "name", "peak_db", "active"]
    row = next(r for r in meter["rows"] if r[0] == 1)
    assert row[1] == "Kick" and row[2] == -0.9
    base = {1: 0.9, 3: 0.28}
    f.peak_fn = lambda i: (
        0.3 * sum(base[t] * f.tracks[t]["vol"] / 0.8 for t in base)
        if i == 0
        else base.get(i, 0.0) * f.tracks[i]["vol"] / 0.8
    )
    out = await run("fl_mix", {"action": "balance", "seconds": 1, "targets": "1=-8,3=-9"})
    assert out["names"]["1"] == "Kick" and abs(out["peaks_db"]["1"] + 8) <= 1.6
    assert abs(out["peaks_db"]["3"] + 9) <= 1.6


async def test_mix_balance_uses_the_plan_targets(studio, monkeypatch):
    f = studio.fake
    monkeypatch.setattr(mixing.time, "sleep", lambda _: None)
    await run("fl_produce", {"genre": "house", "seed": 1, "length": "sketch"})
    base = {1: 0.9, 3: 0.28, 4: 0.12, 2: 0.2}
    f.peak_fn = lambda i: (
        0.2 * sum(base[t] * f.tracks[t]["vol"] / 0.8 for t in base)
        if i == 0
        else base.get(i, 0.0) * f.tracks[i]["vol"] / 0.8
    )
    out = await run("fl_mix", {"action": "balance", "seconds": 1})
    assert abs(out["peaks_db"]["1"] + 8) <= 1.6  # kick target
    assert abs(out["peaks_db"]["3"] + 9) <= 1.6  # bass target
    assert out["peaks_db"]["4"] < -17  # hats stay quiet


async def test_mix_errors(studio):
    assert "targets look like" in await run(
        "fl_mix", {"action": "balance", "targets": "kick"}, expect_error=True
    )
    assert "no plan yet" in await run("fl_mix", {"action": "balance"}, expect_error=True)
    for c in studio.fake.chans:
        c["fx"] = 0
    await run("fl_produce", {"action": "plan", "genre": "house", "seed": 1, "length": "sketch"})
    assert "own mixer track" in await run("fl_mix", {"action": "balance"}, expect_error=True)


async def test_flp_template_flow(studio, tmp_path):
    from fl_mcp.flp import Flp
    from tests.flpgen import template

    src = tmp_path / "template.flp"
    src.write_bytes(template(channels=("Kick", "808 Bass", "Lead Synth", "Odd one")))
    info = await run("fl_produce", {"action": "inspect", "template": str(src)})
    assert info["roles"] == {"kick": 0, "bass": 1, "lead": 2} and info["named_pattern_with_notes"]
    assert info["unmatched"] == ["Odd one"] and info["playlist_items"] == 1

    out = await run(
        "fl_produce",
        {"action": "flp", "genre": "house", "seed": 2, "length": "sketch", "template": str(src)},
    )
    built = out["flp"]
    assert built["tempo"] == out["plan"]["bpm"] and built["channels"] == {
        "kick": 0,
        "bass": 1,
        "lead": 2,
    }
    project = Flp.parse((studio.dir / "out" / "house-2.flp").read_bytes())
    assert len(project.patterns()) == 1 + len(out["plan"]["patterns"])
    assert "live" not in out and "midi" not in out

    again = await run(
        "fl_produce",
        {"action": "flp", "plan": "house-2", "template": str(src), "replace_playlist": True},
    )
    items = Flp.parse((studio.dir / "out" / "house-2.flp").read_bytes()).playlist_items()
    assert again["flp"]["clips"] == len(items)  # the template's own clip was dropped

    full = await run(
        "fl_produce", {"genre": "lofi", "seed": 1, "length": "sketch", "template": str(src)}
    )
    assert full["flp"]["file"].endswith("lofi-1.flp") and full["midi"] and full["live"]["bpm"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"action": "inspect"}, "inspect needs template"),
        ({"action": "flp", "genre": "house"}, "flp needs template"),
        ({"action": "inspect", "template": "/nope.flp"}, "existing .flp"),
        ({"action": "inspect", "template": "/etc/passwd"}, "existing .flp"),
    ],
)
async def test_flp_errors(studio, args, message):
    assert message in await run("fl_produce", args, expect_error=True)


async def test_a_template_that_is_not_an_flp_is_refused(studio, tmp_path):
    bad = tmp_path / "x.flp"
    bad.write_bytes(b"not a project")
    assert "not an FL Studio project" in await run(
        "fl_produce", {"action": "inspect", "template": str(bad)}, expect_error=True
    )
