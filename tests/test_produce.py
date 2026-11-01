"""Fast production end to end: plan -> live delivery into the (fake) FL project, MIDI, mixing."""

import json

import mido
import pytest

from fl_mcp import mixing, produce
from fl_mcp.bridge import MAX_REQ
from fl_mcp.music import compose
from fl_mcp.music.plan import Part

NAMES = [
    "Kick",
    "Snare",
    "Clap",
    "Hat",
    "Open Hat",
    "Perc",
    "808 Bass",
    "Keys",
    "Lead Synth",
    "Pad",
    "Riser FX",
    "Mystery",
]
TRACKS = {"Kick": 1, "Clap": 2, "Hat": 4, "808 Bass": 3, "Open Hat": 4, "Perc": 4}


@pytest.fixture(autouse=True)
def folder(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def studio(fl):
    f = fl.fake
    f.chans = [f._chan(n, 0) for n in NAMES]
    for c in f.chans:
        c["fx"] = TRACKS.get(c["name"], 0)
    f.pats = {}
    return fl


def test_roles_are_detected_from_channel_names():
    mapping, unmatched = produce.detect_roles(NAMES)
    assert mapping == {
        "kick": 0,
        "snare": 1,
        "clap": 2,
        "hat": 3,
        "ohat": 4,
        "perc": 5,
        "bass": 6,
        "chords": 7,
        "pad": 9,
        "lead": 8,
        "fx": 10,
    }
    assert unmatched == ["Mystery"]
    assert produce.detect_roles(["Bass Drum", "Sub Bass"])[0] == {"kick": 0, "bass": 1}
    assert produce.detect_roles(["Hi-Hat 2", "Synth Pad", "Open Hat 1"])[0] == {
        "hat": 0,
        "pad": 1,
        "ohat": 2,
    }
    assert produce.detect_roles(["abdul", "Kicks"])[0] == {
        "kick": 1
    }  # short aliases need a whole word
    assert produce.detect_roles([]) == ({}, [])


def test_channel_map_parsing():
    assert produce.parse_map("kick=0, bass=3") == {"kick": 0, "bass": 3}
    for bad in ("kick", "tuba=1", "kick=x"):
        with pytest.raises(ValueError, match="look like"):
            produce.parse_map(bad)


def test_grid_strings_carry_velocity():
    plan = compose("house", seed=1, length="sketch")
    part = plan.patterns[0].parts["kick"]
    g = produce.grid_string(part, plan.patterns[0].bars)
    assert len(g) == plan.patterns[0].bars * 16
    assert [i for i, c in enumerate(g) if c != "."] == sorted({round(e.t * 4) for e in part.events})
    loud = produce.grid_string(Part("kick", True, [type(part.events[0])(0.0, 0.1, 60, 127)]), 1)
    assert loud[0] == "z" and set(loud[1:]) == {"."}


def test_plans_are_stored_and_found(folder):
    a, b = compose("trap", seed=1), compose("house", seed=2)
    produce.save_plan(a)
    produce.save_plan(b)
    assert produce.load_plan().id == b.id and produce.load_plan(a.id).to_json() == a.to_json()
    for bad, message in (("../x", "bad plan id"), ("trap-ff", "unknown plan")):
        with pytest.raises(ValueError, match=message):
            produce.load_plan(bad)


def test_no_plan_yet(folder):
    with pytest.raises(ValueError, match="no plan yet"):
        produce.load_plan()


def test_live_delivery_fills_the_channel_rack(studio):
    plan = compose("house", seed=1, length="sketch")
    f = studio.fake
    f.pats[2] = {"name": "Existing", "color": 0, "len": 4}  # the user's own pattern must survive
    report = produce.apply_live(studio.call, plan)
    base = 2
    assert (
        report["patterns"] == [base + 1, base + len(plan.patterns)]
        and report["not_kept_by_fl"] == 0
    )
    assert f.bpm == plan.bpm and f.pats[2]["name"] == "Existing"
    for i, pat in enumerate(plan.patterns):
        assert f.pats[base + 1 + i]["name"] == pat.name
    drop = next(i for i, p in enumerate(plan.patterns) if p.name == "Drop A - Drums")
    kick = plan.patterns[drop].parts["kick"]
    steps = f.chans[0]["steps"][base + 1 + drop]
    assert steps == {round(e.t * 4) for e in kick.events}
    first = min(kick.events, key=lambda e: e.t)
    got = f.chans[0]["sp"][(base + 1 + drop, round(first.t * 4), 1)]
    assert abs(got - first.v) <= 4
    assert report["channels"]["kick"] == 0 and report["channels"]["bass"] == 6
    assert (
        set(report["needs_notes"]) >= {"bass", "chords", "lead"} and "arp" in report["no_channel"]
    )
    assert "Mystery" in report["unmatched_channels"]
    assert f.markers == [(s.bar * 384, s.name) for s in plan.sections]
    assert f.tracks[1]["name"] == "Insert 1" and (1, 0) in f.eq  # EQ went onto the kick insert
    assert f.undo_points and all(n.startswith("FL MCP: produce house") for n, _ in f.undo_points)
    assert produce.live_state(plan.id)["base"] == base


def test_swing_is_written_as_step_shift(studio):
    plan = compose("lofi", seed=1, length="sketch")  # 60% swing
    produce.apply_live(studio.call, plan)
    sp = studio.fake.chans[5]["sp"]  # Perc plays off-16ths in this groove
    shifts = {k: v for k, v in sp.items() if k[2] == 7}
    assert shifts and all(k[1] % 2 == 1 for k in shifts)
    assert set(shifts.values()) == {round(0.1 * 0.5 * 96)}


def test_large_plans_are_sent_in_safe_batches(studio, monkeypatch):
    monkeypatch.setattr(produce, "BATCH_BYTES", 3000)
    plan = compose("trance", seed=1, length="full")
    sizes = []
    original = studio.bridge.call

    def spy(req, timeout=None):
        sizes.append(len(json.dumps(req)))
        return original(req, timeout)

    studio.bridge.call = spy
    report = produce.apply_live(spy, plan)
    batches = [s for s in sizes if s > 1000]
    assert len(studio.fake.undo_points) >= 3 and max(sizes) < MAX_REQ and batches
    assert report["not_kept_by_fl"] == 0
    assert studio.fake.undo_points[-1][0].endswith(
        f"{len(studio.fake.undo_points)}/{len(studio.fake.undo_points)}"
    )


def test_delivery_reports_what_fl_could_not_keep(studio):
    studio.fake.step_limit = 32
    report = produce.apply_live(studio.call, compose("house", seed=1, length="sketch"))
    assert report["not_kept_by_fl"] > 0


def test_delivery_errors(studio):
    plan = compose("house", seed=1, length="sketch")
    with pytest.raises(ValueError, match="out of range for kick"):
        produce.apply_live(studio.call, plan, {"kick": 99})
    for i in range(990):
        studio.fake.pats[i + 1] = {"name": "x", "color": 0, "len": 4}
    with pytest.raises(ValueError, match="999 patterns"):
        produce.apply_live(studio.call, plan)
    with pytest.raises(ValueError, match="apply this plan"):
        produce.live_state("house-dead")


def test_override_beats_detection(studio):
    plan = compose("house", seed=1, length="sketch")
    report = produce.apply_live(studio.call, plan, {"kick": 5})
    assert report["channels"]["kick"] == 5


def test_midi_export(folder):
    plan = compose("dnb", seed=4, length="sketch")
    path = produce.export_midi(plan)
    assert path.parent == folder / "out" and path.suffix == ".mid"
    assert mido.MidiFile(str(path)).type == 1
    assert produce.export_midi(plan, "gm").name.endswith("-gm.mid")


# ---- mixing


def amplitude(fake, base):
    return lambda i: base.get(i, 0.0) * fake.tracks[i]["vol"] / 0.8


def test_meter_reads_peaks_and_restores_transport(studio):
    f = studio.fake
    f.peaks = {0: 0.5, 1: 1.2, 2: 0.0}
    out = mixing.meter(studio.call, [0, 1, 2], seconds=0.05, sleep=lambda _: None, interval=0.0)
    assert out[0]["peak_db"] == -6.0 and out[1]["clips"] > 0 and out[2]["active"] == 0
    assert not f.playing  # we started it, we stop it
    f.playing = True
    mixing.meter(studio.call, [0], seconds=0.01, bar=3, sleep=lambda _: None)
    assert f.playing and f.ticks == 2 * 384  # was playing: left playing, jumped to bar 3


def test_balance_moves_faders_to_targets(studio):
    f = studio.fake
    base = {1: 0.9, 3: 0.28, 4: 0.12, 5: 0.0, 6: 0.01}
    f.peak_fn = lambda i: (
        0.35 * sum(amplitude(f, base)(t) for t in base) if i == 0 else amplitude(f, base)(i)
    )
    targets = {1: -8.0, 3: -9.0, 4: -20.0, 5: -10.0, 6: -15.0}
    report = mixing.balance(studio.call, targets, seconds=0.02, sleep=lambda _: None, interval=0.0)
    for t in (1, 3, 4):
        assert abs(report["peaks_db"][t] - targets[t]) <= 1.6, (t, report)
    assert report["moved_db"][3] > 0 > report["moved_db"][1]
    assert 5 in report["skipped"] and report["fader_limit"] == [6]  # silent / out of fader travel
    assert report["master_peak_db"] < -3


def test_balance_keeps_headroom_on_the_master(studio):
    f = studio.fake
    base = {1: 0.4, 3: 0.4}
    f.peak_fn = lambda i: (
        1.1 * sum(amplitude(f, base)(t) for t in base) if i == 0 else amplitude(f, base)(i)
    )
    report = mixing.balance(
        studio.call, {1: -8.0, 3: -8.0}, seconds=0.02, sleep=lambda _: None, interval=0.0
    )
    assert report["master_peak_db"] <= -2.9
    assert report["moved_db"][1] < 0 and report["moved_db"][3] < 0


def test_balance_leaves_a_good_mix_alone(studio):
    f = studio.fake
    f.peak_fn = lambda i: {1: 0.4, 0: 0.5}.get(i, 0.0) * f.tracks[i]["vol"] / 0.8
    report = mixing.balance(studio.call, {1: -8.0}, seconds=0.01, sleep=lambda _: None)
    assert report["moved_db"] == {} and report["clipping"] == []
