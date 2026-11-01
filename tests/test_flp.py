"""FLP reader/patcher: structure-faithful synthetic projects, plus real FL files when available."""

import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

from fl_mcp import flp
from fl_mcp.flp import Event, Flp, FlpError, build, note_record
from fl_mcp.music import compose
from fl_mcp.music import theory as th
from tests.flpgen import item, template, txt


def test_round_trip_is_byte_exact_including_unknown_events():
    raw = template()
    parsed = Flp.parse(raw)
    assert parsed.dumps() == raw
    assert any(e.id == 250 and len(e.data) == 512 for e in parsed.events)


@pytest.mark.parametrize("size", [0, 1, 127, 128, 300, 16383, 16384, 70000])
def test_event_lengths_of_every_width(size):
    e = Event(210, bytes(size))
    raw = template()
    parsed = Flp.parse(raw)
    parsed.events.append(e)
    again = Flp.parse(parsed.dumps())
    assert again.events[-1].data == bytes(size) and again.events[-1].encode() == e.encode()


def test_fixed_size_events_are_checked():
    for bad in (Event(21, b"ab"), Event(64, b"a"), Event(156, b"abc")):
        with pytest.raises(FlpError, match="data bytes"):
            bad.encode()


def test_bad_files_are_refused_with_a_reason():
    raw = template()
    cases = {
        "missing FLhd": b"NoFL" + raw[4:],
        "header size": raw[:4] + struct.pack("<I", 8) + raw[8:],
        "scores or states": raw[:8] + struct.pack("<H", 256) + raw[10:],
        "timebase 0": raw[:12] + struct.pack("<H", 0) + raw[14:],
        "missing FLdt": raw[:14] + b"NoFL" + raw[18:],
        "does not match": raw[:18] + struct.pack("<I", 1) + raw[22:],
        "does not match ": raw[:-3],
        "": b"",
    }
    for message, data in cases.items():
        with pytest.raises(FlpError, match=message or "FLhd"):
            Flp.parse(data)


def test_event_overrunning_the_file_is_refused():
    body = bytes([210, 0x85, 0x01]) + b"abc"  # claims 133 bytes, has 3
    raw = (
        b"FLhd" + struct.pack("<IHHH", 6, 0, 1, 96) + b"FLdt" + struct.pack("<I", len(body)) + body
    )
    with pytest.raises(FlpError, match="runs past the end"):
        Flp.parse(raw)
    body = bytes([210, 0x85])  # length varint cut off
    raw = (
        b"FLhd" + struct.pack("<IHHH", 6, 0, 1, 96) + b"FLdt" + struct.pack("<I", len(body)) + body
    )
    with pytest.raises(FlpError, match="truncated"):
        Flp.parse(raw)


def test_every_truncation_fails_cleanly():
    raw = template()
    for cut in range(0, len(raw) - 1, 17):
        with pytest.raises(FlpError):
            Flp.parse(raw[:cut])


def test_reading_the_project():
    p = Flp.parse(template())
    assert p.version == "20.8.4.2576" and p.ppq == 96 and p.tempo == 120.0
    assert [(c.iid, c.name, c.kind) for c in p.channels()] == [
        (0, "Kick", "plugin"),
        (1, "808 Bass", "plugin"),
        (2, "Lead", "plugin"),
    ]
    assert p.patterns() == {1: {"name": "Seed", "notes": 1}}
    assert p.playlist_items() == [{"position": 0, "pattern": 1, "length": 384, "track": 1}]
    s = p.summary()
    assert s["named_pattern_with_notes"] and s["playlist_items"] == 1 and s["tempo"] == 120.0


def test_extra_name_events_after_the_arrangement_do_not_rename_channels():
    assert Flp.parse(template()).channels()[-1].name == "Lead"  # a later mixer-slot 203 is ignored


def test_sampler_channels_without_a_name_use_the_sample_file():
    p = Flp.parse(template())
    i = next(k for k, e in enumerate(p.events) if e.id == 203)
    p.events[i : i + 1] = [
        Event(196, txt("%FLStudioFactoryData%\\Data\\Packs\\Drums\\Kicks\\22in Kick.wav"))
    ]
    assert p.channels()[0].name == "22in Kick"


def test_tempo():
    p = Flp.parse(template())
    p.set_tempo(128.5)
    assert Flp.parse(p.dumps()).tempo == 128.5
    for bad in (9, 523):
        with pytest.raises(FlpError, match="10-522"):
            p.set_tempo(bad)


def test_untouched_events_stay_byte_identical():
    raw = template()
    p = Flp.parse(raw)
    before = {i: e.encode() for i, e in enumerate(p.events)}
    p.set_tempo(99)
    p.put_pattern(1, [note_record(0, 24, 60, 0, 90)], "Renamed")
    changed = {
        i for i, e in enumerate(p.events[: len(before)]) if i in before and e.encode() != before[i]
    }
    assert {p.events[i].id for i in changed} <= {156, 224, 193}


def test_notes_replace_in_place_and_are_sorted():
    p = Flp.parse(template())
    notes = [note_record(96, 48, 64, 1, 80), note_record(0, 24, 60, 0, 100, slide=True)]
    p.put_pattern(1, notes, "Verse", (10, 20, 30))
    out = Flp.parse(p.dumps())
    assert out.patterns() == {1: {"name": "Verse", "notes": 2}}
    blob = next(e.data for e in out.events if e.id == 224)
    first = struct.unpack("<IHHIHH8B", blob[:24])
    assert first[0] == 0 and first[1] == 0x4008 and first[4] == 60 and first[5] == 0
    assert next(e.data for e in out.events if e.id == 150) == bytes([10, 20, 30, 0])
    assert next(e.data for e in out.events if e.id == 193) == txt("Verse")


def test_new_patterns_clone_the_structure_flp_wrote():
    p = Flp.parse(template())
    p.put_pattern(2, [note_record(0, 24, 60, 0, 90)], "Chorus")
    p.put_pattern(3, [], "Empty")
    chan0 = next(i for i, e in enumerate(p.events) if e.id == 64)
    first_pat = next(i for i, e in enumerate(p.events) if e.id == 65)
    early = p.events[first_pat:chan0]
    assert [e.id for e in early] == [
        65,
        224,
        226,
        226,
        226,
    ] * 3  # notes blocks sit before channel 0
    assert [struct.unpack("<H", e.data)[0] for e in early if e.id == 65] == [1, 2, 3]
    consts = [e.data for e in early if e.id == 226]
    assert consts == consts[:3] * 3  # the constant defaults are cloned verbatim
    later = [struct.unpack("<H", e.data)[0] for e in p.events[chan0:] if e.id == 65]
    assert later == [1, 2, 3]  # and the pattern list grows after the existing entry
    again = Flp.parse(p.dumps())
    assert again.patterns() == {
        1: {"name": "Seed", "notes": 1},
        2: {"name": "Chorus", "notes": 1},
        3: {"name": "Empty", "notes": 0},
    }


def test_a_template_without_a_seed_pattern_is_explained():
    p = Flp.parse(template(seed=False))
    with pytest.raises(FlpError, match="named pattern that contains a note"):
        p.put_pattern(2, [], "x")


def test_playlist_modes():
    p = Flp.parse(template(clips=2))
    n = p.set_playlist([(0, 2, 384, 3), (384, 3, 768, 1)], "append")
    items = Flp.parse(p.dumps()).playlist_items()
    assert n == 2 and len(items) == 4
    assert items[2:] == [
        {"position": 768, "pattern": 2, "length": 384, "track": 3},
        {"position": 1152, "pattern": 3, "length": 768, "track": 1},
    ]
    p.set_playlist([(0, 2, 384, 7)], "replace")
    assert Flp.parse(p.dumps()).playlist_items() == [
        {"position": 0, "pattern": 2, "length": 384, "track": 7}
    ]
    with pytest.raises(FlpError, match="append or replace"):
        p.set_playlist([(0, 1, 96, 1)], "oops")
    with pytest.raises(FlpError, match="1-500"):
        p.set_playlist([(0, 1, 96, 501)], "append")


def test_playlist_new_format_keeps_the_unknown_tail():
    tail = bytes(range(28))
    raw = template(version="2025.1.0.1", wide=True)
    p = Flp.parse(raw)
    p.events[next(i for i, e in enumerate(p.events) if e.id == 233)].data = item(0, 1, 384, 1, tail)
    p.set_playlist([(384, 2, 384, 2)], "append")
    data = next(e.data for e in p.events if e.id == 233)
    assert (
        len(data) == 120 and data[60 + 32 : 120] == tail and data[:60] == item(0, 1, 384, 1, tail)
    )
    assert Flp.parse(p.dumps()).playlist_items()[1]["pattern"] == 2


def test_playlist_sizes_that_fit_both_layouts_follow_the_project_version():
    old = Flp.parse(template(clips=15))  # 15 x 32 bytes is also 8 x 60
    assert len(old.playlist_items()) == 15
    old.set_playlist([(0, 2, 384, 3)], "append")
    assert len(Flp.parse(old.dumps()).playlist_items()) == 16
    new = Flp.parse(template(version="2025.1.0.1", clips=8, wide=True))  # 8 x 60 = 15 x 32
    assert len(new.playlist_items()) == 8
    new.set_playlist([(0, 2, 384, 3)], "append")
    assert len(Flp.parse(new.dumps()).playlist_items()) == 9


def test_a_playlist_that_fits_no_layout_is_refused():
    p = Flp.parse(template())
    p.events[next(i for i, e in enumerate(p.events) if e.id == 233)].data = bytes(50)
    with pytest.raises(FlpError, match="unrecognised playlist"):
        p.playlist_items()


def test_empty_playlists():
    old = Flp.parse(template(clips=0))  # FL 20: the 32-byte record can be written from known fields
    assert old.set_playlist([(0, 1, 384, 1)], "replace") == 1
    assert Flp.parse(old.dumps()).playlist_items()[0]["length"] == 384
    new = Flp.parse(template(version="21.0.3.3517", clips=0))
    with pytest.raises(FlpError, match="needs one clip"):
        new.set_playlist([(0, 1, 384, 1)])


def test_note_record_layout():
    rec = note_record(480, 96, 62, 5, 77)
    assert len(rec) == 24
    assert struct.unpack("<IHHIHH8B", rec) == (
        480,
        0x4000,
        5,
        96,
        62,
        0,
        120,
        0,
        64,
        0,
        64,
        77,
        128,
        128,
    )
    assert note_record(0, 1, 60, 0, 100, slide=True)[4:6] == struct.pack("<H", 0x4008)


# ---- plan -> project


def test_build_from_a_plan(tmp_path):
    src = tmp_path / "template.flp"
    src.write_bytes(template(channels=("Kick", "Sub Bass", "Lead", "Whatever")))
    before = src.read_bytes()
    plan = compose("trap", seed=3, length="sketch")
    out = tmp_path / "songs" / "x.flp"
    report = build(src, plan, out)
    assert src.read_bytes() == before, "the template must never change"
    assert report["patterns"] == [2, 1 + len(plan.patterns)] and report["tempo"] == plan.bpm
    assert report["channels"] == {"kick": 0, "bass": 1, "lead": 2}
    assert (
        set(report["no_channel"]) >= {"clap", "hat", "ohat", "perc"}
        and "Whatever" in report["unmatched_channels"]
    )
    result = Flp.parse(out.read_bytes())
    assert result.tempo == plan.bpm and len(result.patterns()) == 1 + len(plan.patterns)
    for i, pat in enumerate(plan.patterns):
        got = result.patterns()[2 + i]
        expected = sum(len(x.events) for r, x in pat.parts.items() if r in report["channels"])
        assert got == {"name": pat.name, "notes": expected}
    assert len(result.playlist_items()) == 1 + len(plan.clips)
    assert {i["track"] for i in result.playlist_items()[1:]} <= {1, 2, 3, 4, 5}
    blob = b"".join(e.data for e in result.events if e.id == 224)
    keys = {struct.unpack_from("<H", blob, k + 12)[0] for k in range(0, len(blob), 24)}
    assert 60 in keys and all(0 <= k <= 127 for k in keys)


def test_build_override_and_errors(tmp_path):
    src = tmp_path / "t.flp"
    src.write_bytes(template(channels=("A", "B", "C")))
    plan = compose("house", seed=1, length="sketch")
    report = build(src, plan, tmp_path / "o.flp", {"kick": 2, "bass": 0})
    assert report["channels"] == {"kick": 2, "bass": 0}
    with pytest.raises(FlpError, match="out of range"):
        build(src, plan, tmp_path / "o2.flp", {"kick": 9})
    src.write_bytes(template(seed=False))
    with pytest.raises(FlpError, match="named pattern"):
        build(src, plan, tmp_path / "o3.flp")
    assert not (tmp_path / "o3.flp").exists()


def test_build_replace_mode_drops_the_templates_demo_clips(tmp_path):
    src = tmp_path / "t.flp"
    src.write_bytes(template(clips=3, channels=("Kick", "808 Bass")))
    plan = compose("lofi", seed=2, length="sketch")
    build(src, plan, tmp_path / "o.flp", playlist="replace")
    items = Flp.parse((tmp_path / "o.flp").read_bytes()).playlist_items()
    assert len(items) == len(plan.clips) and items[0]["position"] == 0


# ---- real FL Studio files: set FL_MCP_FLP_FIXTURES (fl2084.flp, multi.flp, corrupted/)

FIXTURES = os.environ.get("FL_MCP_FLP_FIXTURES")
ORACLE = os.environ.get("FL_MCP_ORACLE_PYTHON")
real = pytest.mark.skipif(not FIXTURES, reason="no real FL projects available")


@real
def test_real_projects_round_trip_losslessly():
    for name in ("fl2084.flp", "multi.flp"):
        raw = (Path(FIXTURES) / name).read_bytes()
        assert Flp.parse(raw).dumps() == raw


@real
def test_real_corrupted_files_are_rejected():
    files = sorted((Path(FIXTURES) / "corrupted").glob("*.flp"))
    assert len(files) >= 6
    for f in files:
        with pytest.raises(FlpError):
            Flp.parse(f.read_bytes())


@real
def test_real_project_is_understood():
    p = Flp.parse((Path(FIXTURES) / "fl2084.flp").read_bytes())
    s = p.summary()
    assert s["version"].startswith("20.8.4") and s["tempo"] == 69.42 and len(s["channels"]) == 19
    assert s["named_pattern_with_notes"] and s["playlist_items"] == 22
    assert p.channels()[0].name == "BooBass" and p.channels()[9].name == "Fruit Kick"


@real
@pytest.mark.skipif(not ORACLE, reason="needs FL_MCP_ORACLE_PYTHON: Python <= 3.10 with pyflp")
def test_real_project_patch_is_read_back_identically_by_an_independent_parser(tmp_path):
    plan = compose("house", seed=5, length="sketch")
    out = tmp_path / "out.flp"
    report = build(Path(FIXTURES) / "fl2084.flp", plan, out)
    dump = Path(__file__).parent / "oracle_dump.py"

    def oracle(path):
        return json.loads(
            subprocess.run(
                [ORACLE, str(dump), str(path)], check=True, capture_output=True, text=True
            ).stdout
        )

    got, orig = oracle(out), oracle(Path(FIXTURES) / "fl2084.flp")
    assert got["tempo"] == plan.bpm and got["ppq"] == 96
    iid = report["channels"]
    base = report["patterns"][0] - 1
    for i, pat in enumerate(plan.patterns):
        p = got["patterns"][str(base + 1 + i)]
        expected = sorted(
            [
                round(e.t * 96),
                max(1, round(e.d * 96)),
                th.note_name(e.p, fl_style=True),
                e.v,
                iid[r],
            ]
            for r, x in pat.parts.items()
            if r in iid
            for e in x.events
        )
        assert p["name"] == pat.name and p["notes"] == expected
        assert p["color"] is not None
    assert all(got["patterns"][k] == v for k, v in orig["patterns"].items())
    added = [x for x in got["playlist"] if x not in orig["playlist"]]
    start = -(-max(p + n for p, n, *_ in orig["playlist"]) // 384) * 384
    assert added == sorted(
        [start + round(c.bar * 384), round(c.bars * 384), base + 1 + c.pattern, c.track]
        for c in plan.clips
    )
    assert all(x in got["playlist"] for x in orig["playlist"])


@real
@pytest.mark.skipif(not ORACLE, reason="needs FL_MCP_ORACLE_PYTHON: Python <= 3.10 with pyflp")
def test_real_project_with_sample_channels_is_read_back_by_an_independent_parser(tmp_path):
    plan = compose("house", seed=5, length="sketch")
    out = tmp_path / "samples.flp"
    sounds = {"kick": r"C:\Samples\My Kick.wav", "clap": r"C:\Samples\My Clap.wav"}
    report = build(Path(FIXTURES) / "fl2084.flp", plan, out, sounds=sounds)
    dump = Path(__file__).parent / "oracle_dump.py"
    got = json.loads(
        subprocess.run(
            [ORACLE, str(dump), str(out)], check=True, capture_output=True, text=True
        ).stdout
    )
    orig = json.loads(
        subprocess.run(
            [ORACLE, str(dump), str(Path(FIXTURES) / "fl2084.flp")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert len(got["channels"]) == len(orig["channels"]) + 2
    for role, made in report["created"].items():
        detail = got["detail"][str(made["iid"])]
        assert detail["name"] == role.capitalize()
        assert detail["insert"] == made["insert"] and detail["sample"] == sounds[role]
        assert detail["volume"] == 10000 and isinstance(detail["pan"], int)
    assert all(got["detail"][k] == v for k, v in orig["detail"].items()), "old channels unchanged"
    notes = [n for p in got["patterns"].values() for n in p["notes"]]
    owners = {n[4] for n in notes}
    assert {m["iid"] for m in report["created"].values()} <= owners


def test_module_constants_match_the_observed_format():
    assert flp.NOTE.size == 24 and flp.PL_BASE == 20480


# ---- sample channels cloned from a Sampler channel FL wrote


def test_a_sampler_channel_is_cloned_with_its_own_sample_insert_colour_and_levels():
    raw = template(sampler=True)
    p = Flp.parse(raw)
    before = [e.encode() for e in p.events]
    iid = p.add_sampler(
        "Kick", r"C:\Samples\Kick 01.wav", insert=3, color=(1, 2, 3), volume=0.5, pan=-1.0
    )
    assert iid == 4
    out = Flp.parse(p.dumps())
    by = {c.iid: c for c in out.channels()}
    assert (by[4].name, by[4].kind) == ("Kick", "sampler") and len(by) == 5
    assert struct.unpack("<H", p.dumps()[10:12])[0] == 5, "the header counts the channels"
    start, stop = out._channel_spans()[4]
    block = {e.id: e.data for e in out.events[start:stop]}
    assert block[196].decode("utf-16le").rstrip("\x00") == r"C:\Samples\Kick 01.wav"
    assert block[22] == b"\x03" and block[128] == bytes([1, 2, 3, 0])
    assert struct.unpack("<II", block[219][:8]) == (0, 6400)
    assert out.events[stop].id == flp.NEW_ARR, "inserted before the arrangement"
    rest = [e.encode() for e in out.events[:start]] + [e.encode() for e in out.events[stop:]]
    assert rest == before, "everything that was there is untouched"
    assert out.inserts_in_use() == {0, 3}


def test_sampler_clones_get_distinct_ids_and_keep_the_pattern_list_intact():
    p = Flp.parse(template(sampler=True))
    ids = [p.add_sampler(n, f"/s/{n}.wav", insert=i + 1) for i, n in enumerate(["a", "b", "c"])]
    assert ids == [4, 5, 6]
    out = Flp.parse(p.dumps())
    assert [c.name for c in out.channels()][-3:] == ["a", "b", "c"]
    assert out.patterns() == Flp.parse(template(sampler=True)).patterns()


def test_sample_channels_need_a_sampler_in_the_template_and_a_valid_insert():
    with pytest.raises(FlpError, match="needs one Sampler channel"):
        Flp.parse(template()).add_sampler("Kick", "/s/k.wav")
    with pytest.raises(FlpError, match="insert must be 0-125"):
        Flp.parse(template(sampler=True)).add_sampler("Kick", "/s/k.wav", insert=126)


def test_build_with_sounds_makes_a_channel_per_role_on_its_own_insert(tmp_path):
    src = tmp_path / "template.flp"
    src.write_bytes(template(channels=("Lead", "Sub Bass"), sampler=True))
    plan = compose("house", seed=3, length="sketch")
    sounds = {"kick": "/s/kick.wav", "clap": "/s/clap.wav", "hat": "/s/hat.wav"}
    report = build(src, plan, tmp_path / "x.flp", sounds=sounds)
    made = report["created"]
    assert set(made) == {"kick", "clap", "hat"}
    assert report["channels"]["kick"] == made["kick"]["iid"]
    assert [made[r]["insert"] for r in ("kick", "clap", "hat")] == [1, 2, 3]
    assert "kick" not in report["no_channel"] and "perc" in report["no_channel"]
    result = Flp.parse((tmp_path / "x.flp").read_bytes())
    assert {c.name for c in result.channels()} >= {"Kick", "Clap", "Hat"}
    blob = b"".join(e.data for e in result.events if e.id == 224)
    owners = {struct.unpack_from("<H", blob, k + 6)[0] for k in range(0, len(blob), 24)}
    assert made["kick"]["iid"] in owners and made["clap"]["iid"] in owners


def test_sounds_can_share_the_master_and_a_missing_sampler_is_reported(tmp_path):
    src = tmp_path / "t.flp"
    src.write_bytes(template(sampler=True))
    plan = compose("house", seed=3, length="sketch")
    report = build(src, plan, tmp_path / "x.flp", sounds={"kick": "/s/k.wav"}, inserts=False)
    assert report["created"]["kick"]["insert"] == 0
    src.write_bytes(template())
    with pytest.raises(FlpError, match="needs one Sampler channel"):
        build(src, plan, tmp_path / "y.flp", sounds={"kick": "/s/k.wav"})
