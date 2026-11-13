"""Plugin knowledge, chain analysis and the mix doctor."""

import json

import pytest
from mcp import Client

from fl_mcp import chain, mixing, plugins_kb, server, units
from tests.test_produce import NAMES, TRACKS

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Fruity Parametric EQ 2", "eq"),
        ("Fruity Limiter", "limiter"),
        ("Fruity Compressor", "comp"),
        ("Maximus", "comp"),
        ("Fruity Reeverb 2", "reverb"),
        ("LuxeVerb", "reverb"),
        ("Fruity Delay 3", "delay"),
        ("Fruity Fast Dist", "drive"),
        ("Fruity Chorus", "mod"),
        ("Fruity Stereo Shaper", "stereo"),
        ("Fruity Love Philter", "filter"),
        ("Fruity Filter", "filter"),
        ("Wave Candy", "meter"),
        ("FabFilter Pro-Q 3", "eq"),
        ("FabFilter Pro-L 2", "limiter"),
        ("FabFilter Pro-C 2", "comp"),
        ("Valhalla VintageVerb", "reverb"),
        ("OTT", "comp"),
        ("Gross Beat", "mod"),
        ("Mystery FX", "other"),
    ],
)
def test_plugins_are_classified_by_what_they_do(name, kind):
    assert plugins_kb.classify(name) == kind


def test_the_host_and_the_script_read_value_text_the_same_way(fl):
    texts = [
        "-6.0 dB", "+3.5dB", "2.50 kHz", "440 Hz", "120 ms", "0.50 s", ".5 s", "35%", "4.0:1",
        "L 50%", "R 30%", "-inf dB", "12 st", "0.50", "Off", "", "1.5 oct", "-0.5", "x2", "120 BPM",
    ]  # fmt: skip
    for text in texts:
        assert units.parse_value(text) == fl.script._parse_text(text), text


def test_instruments_are_recognised_by_name():
    assert plugins_kb.instrument("Sytrus")["name"] == "sytrus"
    assert "bass" in plugins_kb.instrument("Transistor Bass")["role"]
    assert plugins_kb.instrument("Unknown Synth 9000") is None


def test_a_chain_is_compared_with_what_its_role_wants():
    rep = plugins_kb.analyze_chain("bass", [(0, "Fruity Parametric EQ 2")])
    assert rep["missing"] == ["comp"] and rep["order_ok"] and rep["free"] == 9
    assert "Maximus" in rep["add"][0] and rep["why"]
    ok = plugins_kb.analyze_chain("bass", [(0, "Fruity Parametric EQ 2"), (1, "Fruity Compressor")])
    assert ok["missing"] == [] and ok["add"] == []
    backwards = plugins_kb.analyze_chain(
        "master", [(0, "Fruity Limiter"), (1, "Fruity Parametric EQ 2")]
    )
    assert backwards["order_ok"] is False and backwards["missing"] == []
    assert plugins_kb.analyze_chain("master", [])["missing"] == ["limiter"]
    assert plugins_kb.analyze_chain("hat", [])["missing"] == []
    assert plugins_kb.analyze_chain("unheard-of", [])["why"] == ""


def test_settings_find_the_parameter_that_means_it():
    rows = [
        [0, "Gain", 0.5, "3.0 dB"],
        [1, "Comp threshold", 0.6, "-12.0 dB"],
        [2, "Comp ratio", 0.2, "2.0:1"],
        [3, "Attack", 0.1, "10 ms"],
        [4, "Release", 0.3, "100 ms"],
        [5, "Sidechain", 0.0, "Off"],
        [6, "Threshold freq", 0.5, "1.0 kHz"],
    ]
    wants = {"threshold": "-18 dB", "ratio": "4:1", "attack": "20 ms", "release": "150 ms"}
    items, missing = plugins_kb.match_intents(rows, wants)
    assert {(i["p"], i["r"]) for i in items} == {
        (1, "-18 dB"),
        (2, "4:1"),
        (3, "20 ms"),
        (4, "150 ms"),
    }
    assert missing == {}
    items, missing = plugins_kb.match_intents(
        rows, {"ceiling": "-1 dB", "width": "50%", "bogus": "1"}
    )
    assert items == [] and set(missing) == {"ceiling", "width", "bogus"}
    assert missing["bogus"] == "unknown setting"


def test_a_parameter_is_used_once_and_units_must_agree():
    rows = [[0, "Level", 0.5, "-6.0 dB"], [1, "Output level", 0.5, "0.0 dB"]]
    items, _ = plugins_kb.match_intents(rows, {"makeup": "2 dB", "ceiling": "-1 dB"})
    assert [i["p"] for i in items] == [0] or len(items) == 1
    freq = [[0, "Release", 0.5, "2.0 kHz"]]
    assert plugins_kb.match_intents(freq, {"release": "100 ms"})[0] == []


@pytest.fixture
def studio(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "bridge", fl.bridge)
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.setattr(mixing.time, "sleep", lambda _: None)
    f = fl.fake
    f.chans = [f._chan(n, 0) for n in NAMES]
    for c in f.chans:
        c["fx"] = TRACKS.get(c["name"], 0)
    f.tracks[3]["name"] = "Bass"
    f.tracks[3]["slots"] = {0: "Fruity Parametric EQ 2", 1: "Fruity Compressor"}
    f.params[("m", 3, 1)] = [
        ["Gain", 0.5, "db"],
        ["Threshold", 0.7, "db"],
        ["Ratio", 0.2, "ratio"],
        ["Attack", 0.1, "ms"],
        ["Release", 0.3, "ms"],
    ]
    f.tracks[0]["slots"] = {0: "Fruity Limiter"}
    f.params[("m", 0, 0)] = [["Ceiling", 0.9, "db"], ["Gain", 0.5, "db"]]
    f.tracks[1]["name"] = "Kick"
    fl.dir = tmp_path
    return fl


def test_the_inventory_and_targets_follow_the_routing(studio):
    have = chain.inventory(studio.call)
    assert have[3] == [(0, "Fruity Parametric EQ 2"), (1, "Fruity Compressor")]
    assert have[0] == [(0, "Fruity Limiter")]
    pairs = chain.targets(studio.call, None, None, None)
    assert (0, "master") in pairs and (3, "bass") in pairs and (1, "kick") in pairs
    assert chain.targets(studio.call, None, 3, None) == [(3, "bass")]
    assert chain.targets(studio.call, None, 0, None) == [(0, "master")]
    assert chain.targets(studio.call, None, None, "bass") == [(3, "bass")]
    with pytest.raises(ValueError, match="does not exist"):
        chain.targets(studio.call, None, 400, None)


def test_the_role_values_are_applied_to_the_plugins_that_are_there(studio):
    reports = chain.analyze(studio.call, [(3, "bass"), (0, "master")])
    assert reports[0]["missing"] == [] and reports[1]["missing"] == []
    done = chain.apply(studio.call, reports)
    bass = next(d for d in done if d["track"] == 3)
    shown = {name: text for name, text, _ in bass["set"]}
    assert shown["Ratio"] == "4.0:1" and shown["Attack"] == "20 ms" and shown["Release"] == "150 ms"
    assert abs(float(shown["Threshold"].split()[0]) + 18) < 0.3
    master = next(d for d in done if d["track"] == 0)
    assert master["set"][0][0] == "Ceiling" and master["set"][0][1].startswith("-1.0")
    assert "not_found" not in bass


async def run(args):
    async with Client(server.mcp) as client:
        result = await client.call_tool("fl_mix", args)
    assert result.is_error is False, result.content[0].text
    return json.loads(result.content[0].text)


async def test_fl_mix_chain_reports_and_applies(studio):
    out = await run({"action": "chain", "track": 3})
    assert out["tracks"][0]["role"] == "bass" and "applied" not in out
    out = await run({"action": "chain", "role": "bass", "apply": True})
    assert out["applied"][0]["plugin"] == "Fruity Compressor"
    out = await run({"action": "chain"})
    assert {t["role"] for t in out["tracks"]} >= {"master", "bass", "kick"}


async def test_the_doctor_lists_problems_worst_first(studio):
    f = studio.fake
    f.peaks = {0: 0.9, 1: 1.2, 3: 0.05, 4: 0.001}
    out = await run({"action": "doctor", "seconds": 1})
    sev = [x["sev"] for x in out["findings"]]
    assert sev == sorted(sev, key=["high", "med", "low", "info"].index)
    whats = " | ".join(x["what"] for x in out["findings"])
    assert "Kick clips" in whats and "master peaks at" in whats
    assert "channels go straight to the master" in whats
    assert any("no compressor" in x["what"] or "no comp" in x["what"] for x in out["findings"])
    assert out["playing"] is True


async def test_the_doctor_says_when_there_is_nothing_to_hear(studio):
    studio.fake.peaks = {}
    out = await run({"action": "doctor", "seconds": 1})
    assert out["playing"] is False and out["findings"][0]["sev"] == "high"
    assert "no signal at the master" in out["findings"][0]["what"]
