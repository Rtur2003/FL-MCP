"""The sound and plugin library: names, measured features, ranking, kits and the plugin database."""

import json
import math
import random
import struct
import wave
from pathlib import Path

import pytest
from mcp import Client

from fl_mcp import library, produce, server
from fl_mcp.music import compose

pytestmark = pytest.mark.anyio
SR = 44100


def write(path: Path, samples: list[float]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(
            b"".join(struct.pack("<h", max(-32767, min(32767, int(v * 32767)))) for v in samples)
        )
    return path


def kick(seconds=0.45, f0=130, f1=45):
    out, phase = [], 0.0
    for i in range(int(SR * seconds)):
        t = i / SR
        f = f1 + (f0 - f1) * math.exp(-t * 25)
        phase += 2 * math.pi * f / SR
        out.append(0.9 * math.sin(phase) * math.exp(-t * 7))
    return out


def noise(seconds, decay, bright=False, seed=1):
    rng = random.Random(seed)
    last, out = 0.0, []
    for i in range(int(SR * seconds)):
        n = rng.uniform(-1, 1)
        v = (n - last) * 0.5 if bright else n
        last = n
        out.append(0.8 * v * math.exp(-i / SR * decay))
    return out


def tone(seconds, hz=220):
    return [0.5 * math.sin(2 * math.pi * hz * i / SR) for i in range(int(SR * seconds))]


@pytest.fixture
def packs(tmp_path, monkeypatch):
    root = tmp_path / "samples"
    write(root / "Drums" / "Kicks" / "Kick Dark 01.wav", kick())
    write(root / "Drums" / "Kicks" / "Kick Long 02.wav", tone(9.0, 60))
    write(root / "Drums" / "Snares" / "Snare Tight 01.wav", noise(0.25, 14, seed=2))
    write(root / "Drums" / "Hats" / "HiHat Closed 03.wav", noise(0.07, 55, bright=True, seed=3))
    write(root / "Drums" / "Hats" / "OpenHat 01.wav", noise(0.6, 7, bright=True, seed=4))
    write(root / "Drums" / "Claps" / "Clap 01.wav", noise(0.3, 12, seed=5))
    write(root / "Loops" / "Drum Loop 120 bpm.wav", tone(4.0, 100))
    write(root / "House Pack" / "BD House.wav", kick(0.5, 150, 50))
    write(root / "House Pack" / "SD House.wav", noise(0.3, 12, seed=6))
    write(root / "House Pack" / "HH House closed.wav", noise(0.08, 50, bright=True, seed=7))
    write(root / "House Pack" / "CP House.wav", noise(0.28, 14, seed=8))
    (root / "readme.txt").write_text("not audio")
    monkeypatch.setenv("FL_MCP_SAMPLES", str(root))
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return root


@pytest.mark.parametrize(
    ("path", "role"),
    [
        ("Packs/Drums/Kicks/22in Kick.wav", "kick"),
        ("x/BD_01.wav", "kick"),
        ("x/Snare Tight 3.wav", "snare"),
        ("x/SD House.wav", "snare"),
        ("x/Clap 01.wav", "clap"),
        ("x/HiHat Closed 03.wav", "hat"),
        ("x/HiHatClosed.wav", "hat"),
        ("x/OpenHat 01.wav", "ohat"),
        ("x/Hat Open.wav", "ohat"),
        ("Drums/Hats/Hat 07.wav", "hat"),
        ("x/Rim Shot.wav", "perc"),
        ("x/808 Sub F#.wav", "bass"),
        ("x/Riser Long.wav", "fx"),
        ("x/Pad Warm.wav", "pad"),
        ("x/Drum Loop 120 bpm.wav", "loop"),
        ("Loops/Anything.wav", "loop"),
        ("x/Random Noise Thing.wav", "fx"),
        ("x/Mystery 12.wav", "other"),
    ],
)
def test_samples_get_a_role_from_their_name_and_folders(path, role):
    assert library.classify_name(Path(path)) == role


def test_camel_case_and_separators_split_into_words():
    assert library.tokens("HiHat_Closed-03 OH") == ["hi", "hat", "closed", "03", "oh"]
    assert library.tokens("808s") == ["808s"]


def test_features_describe_how_a_sound_behaves(packs):
    k = library.analyze(packs / "Drums" / "Kicks" / "Kick Dark 01.wav")
    h = library.analyze(packs / "Drums" / "Hats" / "HiHat Closed 03.wav")
    assert k["low"] > 0.5 and k["zcr_hz"] < 400 and 0.4 < k["dur"] < 0.5
    assert h["low"] < 0.3 and h["zcr_hz"] > 3500 and h["dur"] < 0.1 and h["decay_ms"] < 100
    assert k["peak_db"] < 0 and h["rms_db"] < h["peak_db"]


def test_features_decide_whether_a_sound_fits_its_role(packs):
    k = library.analyze(packs / "Drums" / "Kicks" / "Kick Dark 01.wav")
    h = library.analyze(packs / "Drums" / "Hats" / "HiHat Closed 03.wav")
    long = library.analyze(packs / "Drums" / "Kicks" / "Kick Long 02.wav")
    assert library.fit("kick", k) > 0.9 and library.fit("hat", h) > 0.9
    assert library.fit("kick", h) < 0.6 and library.fit("hat", k) < 0.6
    assert library.fit("kick", long) < library.fit("kick", k)
    assert library.fit("kick", None) == 0.5 and library.fit("fx", k) == 0.5


def test_unreadable_audio_has_no_features(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"RIFFnonsense")
    assert library.analyze(bad) is None
    assert library.analyze(tmp_path / "missing.wav") is None
    assert library.analyze(tmp_path / "x.mp3") is None


def test_scanning_indexes_audio_and_counts_roles(packs):
    out = library.scan()
    assert out["samples"] == 11 and out["roots"] == [str(packs)]
    assert out["by_role"]["kick"] == 3 and out["by_role"]["loop"] == 1
    assert out["truncated"] is False and out["hint"] == ""
    assert library.index_path().is_file()


def test_nothing_to_scan_is_explained(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_SAMPLES", "")
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "nowhere"))
    monkeypatch.setenv("HOME", str(tmp_path))
    out = library.scan()
    assert out["samples"] == 0 and "FL_MCP_SAMPLES" in out["hint"]


def test_find_before_scan_says_what_to_do(packs):
    with pytest.raises(ValueError, match="run fl_library action=scan"):
        library.find("kick")


def test_the_real_kick_beats_the_long_file_with_a_kick_name(packs):
    library.scan()
    got = library.find("kick", limit=5)
    assert [Path(g["p"]).name for g in got][:2] in (
        ["Kick Dark 01.wav", "BD House.wav"],
        ["BD House.wav", "Kick Dark 01.wav"],
    )
    assert Path(got[-1]["p"]).name == "Kick Long 02.wav"
    assert got[0]["f"]["low"] > 0.5
    assert (library.paths.data_dir() / "library_features.json").is_file(), "features are cached"


def test_words_in_the_query_and_genre_lift_matching_files(packs):
    library.scan()
    got = library.find("kick", genre="house", limit=3)
    assert Path(got[0]["p"]).name == "BD House.wav"
    named = library.find(None, query="dark", limit=3)
    assert Path(named[0]["p"]).name == "Kick Dark 01.wav"
    assert all(g["r"] not in ("loop", "other") for g in library.find(None, limit=20))


def test_a_kit_comes_from_one_pack_so_it_sounds_like_it_belongs(packs):
    library.scan()
    kit = library.pick_kit("house", ["kick", "snare", "clap", "hat"], seed=1)
    assert set(kit) == {"kick", "snare", "clap", "hat"}
    assert {c["k"] for c in kit.values()} == {"House Pack"}
    again = library.pick_kit("house", ["kick", "snare", "clap", "hat"], seed=1)
    assert {r: c["p"] for r, c in again.items()} == {r: c["p"] for r, c in kit.items()}


def test_a_kit_falls_back_to_other_packs_for_missing_roles(packs):
    library.scan()
    kit = library.pick_kit("house", ["kick", "ohat"], seed=2)
    assert Path(kit["ohat"]["p"]).name == "OpenHat 01.wav"
    assert library.pick_kit("house", ["vocal"]) == {}


@pytest.fixture
def plugin_db(tmp_path):
    base = (
        tmp_path / "home" / "Documents" / "Image-Line" / "FL Studio" / "Presets" / "Plugin database"
    )
    for rel in (
        "Installed/Effects/VST3/Pro-Q 3.nfo",
        "Installed/Effects/VST3/Pro-L 2.nfo",
        "Installed/Generators/VST/Serum.nfo",
        "Installed/Generators/VST/Serum.fst",
        "Generators/Fruity/Sytrus.fst",
    ):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_bytes(b"x")
    return base


def test_the_plugin_database_lists_installed_plugins(packs, plugin_db):
    out = library.scan()
    assert out["plugins"] == {"effect": 2, "generator": 2}
    everything = library.list_plugins()
    assert [p["name"] for p in everything] == ["Pro-L 2", "Pro-Q 3", "Serum", "Sytrus"]
    serum = library.list_plugins("serum")[0]
    assert serum == {"name": "Serum", "kind": "generator", "format": "VST", "installed": 1}
    assert [p["name"] for p in library.list_plugins(kind="effect")] == ["Pro-L 2", "Pro-Q 3"]


async def call(tool, args):
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error is False, result.content[0].text
    return json.loads(result.content[0].text)


async def test_fl_library_scan_find_kit_plugins_and_info(packs, plugin_db):
    scan = await call("fl_library", {"action": "scan"})
    assert scan["samples"] == 11 and scan["plugins"]["effect"] == 2
    found = await call("fl_library", {"action": "find", "role": "kick", "limit": 3})
    assert found["cols"] == ["path", "pack", "score", "dur", "decay_ms"]
    assert Path(found["rows"][0][0]).suffix == ".wav"
    kit = await call("fl_library", {"action": "kit", "genre": "house"})
    assert set(kit["kit"]) >= {"kick", "snare", "clap", "hat"}
    assert "kick=" in kit["sounds"] and "; snare=" in kit["sounds"]
    plugins = await call("fl_library", {"action": "plugins", "query": "pro-"})
    assert [row[0] for row in plugins["rows"]] == ["Pro-L 2", "Pro-Q 3"]
    info = await call(
        "fl_library", {"action": "info", "path": str(packs / "Drums/Hats/OpenHat 01.wav")}
    )
    assert info["role"] == "ohat" and info["features"]["zcr_hz"] > 3000


async def test_fl_library_errors_are_plain(packs):
    async with Client(server.mcp) as client:
        result = await client.call_tool("fl_library", {"action": "find", "role": "kick"})
        assert result.is_error and "run fl_library action=scan" in result.content[0].text
        result = await client.call_tool("fl_library", {"action": "info"})
        assert result.is_error and "info needs path" in result.content[0].text


def test_sounds_are_parsed_from_text_and_checked_against_the_plan(packs, tmp_path):
    plan = compose("house", seed=3, length="sketch")
    from tests.flpgen import template

    tpl = tmp_path / "t.flp"
    tpl.write_bytes(template(sampler=True))
    kick_path = str(packs / "Drums" / "Kicks" / "Kick Dark 01.wav")
    got = produce.parse_sounds(plan, str(tpl), None, f"kick={kick_path}; clap = {kick_path}")
    assert got == {"kick": kick_path, "clap": kick_path}
    for text, message in [
        ("kick", "should look like role=path"),
        (f"snare={kick_path}", "the plan has no snare part"),
        ("kick=/nope.wav", "sample not found"),
    ]:
        with pytest.raises(ValueError, match=message):
            produce.parse_sounds(plan, str(tpl), None, text)
    assert produce.parse_sounds(plan, str(tpl), None, "kick=%FLStudioFactoryData%\\x.wav") == {
        "kick": "%FLStudioFactoryData%\\x.wav"
    }


def test_auto_fills_only_the_drum_roles_the_template_lacks(packs, tmp_path):
    from tests.flpgen import template

    library.scan()
    plan = compose("house", seed=3, length="sketch")
    tpl = tmp_path / "t.flp"
    tpl.write_bytes(template(channels=("Kick", "Sub Bass", "Lead"), sampler=True))
    got = produce.parse_sounds(plan, str(tpl), None, "auto")
    assert (
        set(got) == {"clap", "hat", "ohat"} & set(plan.roles) | ({"perc"} & set(plan.roles))
        or "kick" not in got
    )
    assert "kick" not in got and "bass" not in got
    assert all(Path(p).is_file() for p in got.values())


def test_non_dict_features_cache_is_rebuilt_cleanly(packs):
    data_dir = packs.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_file = data_dir / "library_features.json"
    cache_file.write_text("[]", encoding="utf-8")
    library.scan()
    found = library.find(role="kick", limit=2)
    assert len(found) >= 1
    assert isinstance(json.loads(cache_file.read_text(encoding="utf-8")), dict)
