"""The real controller script, driven through the real framing, against the stateful FL fake."""

import pytest

from fl_mcp import bridge as b
from fl_mcp.bridge import FLError
from tests.fakefl import event


def test_ping_and_status(fl):
    assert fl.call({"o": "ping"})["proto"] == b.PROTO
    s = fl.call({"o": "status"})
    assert s["bpm"] == 140.0 and s["mode"] == "pattern" and s["pat"] == 1 and s["n_ch"] == 3
    assert s["pos"] == "1:01:00" and s["api"] == 37 and not s["play"]


def test_unknown_op_and_bad_json_shapes(fl):
    with pytest.raises(FLError, match="unknown op"):
        fl.call({"o": "nope"})
    with pytest.raises(FLError, match="ValueError"):
        fl.call({"o": "set", "k": "channel", "i": "x", "p": {"name": "a"}})


def test_list_channels(fl):
    r = fl.call({"o": "list", "k": "channels"})
    assert r["cols"][:3] == ["i", "name", "type"]
    assert [row[1] for row in r["rows"]] == ["Kick", "Snare", "Lead"]
    assert r["rows"][2][2] == "plugin" and r["rows"][2][8] == "Sytrus"


def test_list_mixer_skips_default_tracks_and_pages(fl):
    assert [row[0] for row in fl.call({"o": "list", "k": "mixer"})["rows"]] == [0, 10]
    first = fl.call({"o": "list", "k": "mixer", "a": 1, "n": 5})
    assert [row[0] for row in first["rows"]] == [0, 1, 2, 3, 4] and first["next"] == 5
    second = fl.call({"o": "list", "k": "mixer", "a": 1, "n": 5, "s": 5})
    assert second["rows"][0][0] == 5
    row = fl.call({"o": "list", "k": "mixer"})["rows"][1]
    assert row[1] == "Drums" and row[6] == "Fruity Reeverb 2" and row[7] == [0]
    last = fl.call({"o": "list", "k": "mixer", "a": 1, "s": 120, "n": 200})
    assert last["rows"][-1][0] == 125 and last["next"] is None


def test_list_patterns_and_playlist(fl):
    fl.fake.tlist[3]["name"] = "Drums"
    fl.fake.tlist[7]["mute"] = 1
    assert [r[0] for r in fl.call({"o": "list", "k": "playlist"})["rows"]] == [3, 7]
    fl.call({"o": "set", "k": "pattern", "i": 4, "p": {"name": "Verse"}})
    rows = fl.call({"o": "list", "k": "patterns"})["rows"]
    assert [r[:2] for r in rows] == [[1, "Pattern 1"], [4, "Verse"]]
    assert len(fl.call({"o": "list", "k": "patterns", "a": 1, "n": 200})["rows"]) == 200


def test_list_rejects_bad_kind(fl):
    with pytest.raises(FLError, match="kind must be"):
        fl.call({"o": "list", "k": "plugins"})


def test_set_channel_all_properties(fl):
    props = {
        "name": "Kick 2",
        "color": "#ff8000",
        "volume": 0.5,
        "pan": -0.25,
        "mute": True,
        "solo": True,
        "fx": 4,
        "select": True,
    }
    row = fl.call({"o": "set", "k": "channel", "i": 1, "p": props})
    chan = fl.fake.chans[1]
    assert row["name"] == "Kick 2" and row["vol"] == 0.5 and row["pan"] == -0.25
    assert chan["color"] == 0x0080FF  # FL stores 0xBBGGRR
    assert (chan["mute"], chan["solo"], chan["fx"], fl.fake.sel_channel) == (1, 1, 4, 1)
    fl.call({"o": "set", "k": "channel", "i": 1, "p": {"solo": True, "mute": False}})
    assert (chan["solo"], chan["mute"]) == (1, 0)  # solo is set, not toggled
    fl.call({"o": "set", "k": "channel", "i": 1, "p": {"solo": False}})
    assert chan["solo"] == 0


def test_set_mixer_track_and_sends(fl):
    row = fl.call(
        {
            "o": "set",
            "k": "mixer",
            "i": 5,
            "p": {"name": "Bass", "volume": 0.6, "solo": True, "select": True},
        }
    )
    assert row["name"] == "Bass" and row["solo"] == 1 and fl.fake.cur_track == 5
    fl.call({"o": "set", "k": "mixer", "i": 5, "p": {"send": 10, "level": 0.6}})
    assert fl.fake.tracks[5]["routes"] == {0, 10} and fl.fake.tracks[5]["levels"] == {10: 0.6}
    fl.call({"o": "set", "k": "mixer", "i": 5, "p": {"send": 10, "level": -1}})
    assert fl.fake.tracks[5]["routes"] == {0}
    with pytest.raises(FLError, match="level needs send"):
        fl.call({"o": "set", "k": "mixer", "i": 5, "p": {"level": 0.5}})


def test_set_pattern_and_playlist_track(fl):
    fl.call({"o": "set", "k": "pattern", "i": 3, "p": {"name": "Chorus", "select": True}})
    assert fl.fake.pat == 3 and fl.fake.pats[3]["name"] == "Chorus"
    row = fl.call(
        {
            "o": "set",
            "k": "playlist",
            "i": 2,
            "p": {"name": "Vox", "mute": True, "solo": True, "color": "#00ff00"},
        }
    )
    assert row == {"i": 2, "name": "Vox", "mute": 1, "solo": 1}
    assert fl.fake.tlist[2]["color"] == 0x00FF00


@pytest.mark.parametrize(
    ("req", "message"),
    [
        ({"k": "channel", "i": 9, "p": {"name": "x"}}, "channel 9 out of range 0..2"),
        ({"k": "mixer", "i": 126, "p": {"name": "x"}}, "out of range 0..125"),
        ({"k": "pattern", "i": 0, "p": {"name": "x"}}, "pattern 0 out of range"),
        ({"k": "channel", "i": 0, "p": {"send": 1}}, "not settable on channel: send"),
        ({"k": "channel", "i": 0, "p": {"volume": 1.5}}, "volume 1.5 out of range"),
        ({"k": "channel", "i": 0, "p": {"color": "red"}}, "color must be #RRGGBB"),
        ({"k": "bogus", "i": 0, "p": {}}, "kind must be"),
    ],
)
def test_set_validation_errors(fl, req, message):
    with pytest.raises(FLError, match=message):
        fl.call({"o": "set", **req})


def test_set_validates_before_touching_anything(fl):
    before = fl.fake.tracks[5]["routes"].copy()
    with pytest.raises(FLError):
        fl.call({"o": "set", "k": "mixer", "i": 5, "p": {"send": 10, "bogus": 1}})
    assert fl.fake.tracks[5]["routes"] == before


def test_transport(fl):
    f = fl.fake
    fl.call({"o": "tr", "a": "play"})
    fl.call({"o": "tr", "a": "play"})
    assert f.playing
    assert fl.call({"o": "tr", "a": "pause"})["play"] is False
    fl.call({"o": "tr", "a": "pause"})
    assert not f.playing
    fl.call({"o": "tr", "a": "play"})
    fl.call({"o": "tr", "a": "stop"})
    assert not f.playing
    fl.call({"o": "tr", "a": "record", "v": 1})
    fl.call({"o": "tr", "a": "record", "v": 1})
    assert f.recording
    fl.call({"o": "tr", "a": "record"})
    assert not f.recording
    assert fl.call({"o": "tr", "a": "loop", "v": 1})["mode"] == "song"
    assert fl.call({"o": "tr", "a": "loop", "v": 0})["mode"] == "pattern"
    fl.call({"o": "tr", "a": "metronome", "v": 1})
    fl.call({"o": "tr", "a": "metronome", "v": 1})
    assert f.metro
    fl.call({"o": "tr", "a": "metronome"})
    assert not f.metro
    assert fl.call({"o": "tr", "a": "tempo", "v": 128.5})["bpm"] == 128.5
    assert f.rec_events[-1][1] == 128500
    r = fl.call({"o": "tr", "a": "position", "v": 3})
    assert f.ticks == 2 * 384 and r["pos"].startswith("3:")
    fl.call({"o": "tr", "a": "save"})
    fl.call({"o": "tr", "a": "undo"})
    assert (f.saves, f.undos) == (1, 1)


@pytest.mark.parametrize(
    ("req", "message"),
    [
        ({"a": "tempo", "v": 9}, "tempo 9.0 out of range"),
        ({"a": "tempo", "v": 523}, "out of range"),
        ({"a": "position", "v": 0}, "bar 0.0 out of range"),
        ({"a": "dance"}, "unknown action"),
    ],
)
def test_transport_errors(fl, req, message):
    with pytest.raises(FLError, match=message):
        fl.call({"o": "tr", **req})


def test_steps(fl):
    r = fl.call({"o": "steps", "c": 0, "on": [0, 4, 8, 12]})
    assert r["on"] == [0, 4, 8, 12] and r["pat"] == 1 and r["n"] == 16
    r = fl.call({"o": "steps", "c": 0, "off": [4], "on": [2], "vel": 90, "pit": 60})
    assert r["on"] == [0, 2, 8, 12]
    assert fl.fake.chans[0]["sp"][(1, 2, 1)] == 90 and fl.fake.chans[0]["sp"][(1, 2, 0)] == 60
    assert fl.call({"o": "steps", "c": 0})["on"] == [0, 2, 8, 12]
    assert fl.call({"o": "steps", "c": 0, "clear": 1, "on": [1]})["on"] == [1]
    r = fl.call({"o": "steps", "c": 1, "p": 2, "on": [3], "n": 32})
    assert r["pat"] == 2 and fl.fake.pat == 2 and r["on"] == [3]
    assert fl.call({"o": "steps", "c": 1, "p": 1})["on"] == []


@pytest.mark.parametrize(
    ("req", "message"),
    [
        ({"c": 7}, "channel 7 out of range"),
        ({"c": 0, "on": [600]}, "step 600 out of range"),
        ({"c": 0, "on": [1], "vel": 200}, "velocity 200 out of range"),
        ({"c": 0, "n": 999}, "length 999 out of range"),
    ],
)
def test_steps_errors(fl, req, message):
    with pytest.raises(FLError, match=message):
        fl.call({"o": "steps", **req})


def test_params(fl):
    r = fl.call({"o": "param", "t": "channel", "i": 2})
    assert r["plugin"] == "Sytrus" and r["count"] == 4
    assert [row[1] for row in r["rows"]] == [
        "Cutoff",
        "Resonance",
        "Master level",
    ]  # unnamed hidden
    assert [
        row[0] for row in fl.call({"o": "param", "t": "channel", "i": 2, "f": "LEVEL"})["rows"]
    ] == [3]
    assert len(fl.call({"o": "param", "t": "channel", "i": 2, "n": 1})["rows"]) == 1
    r = fl.call({"o": "param", "t": "channel", "i": 2, "p": 0, "v": 0.9})
    assert r["param"] == [0, "Cutoff", 0.9, "0.90"]
    r = fl.call({"o": "param", "t": "mixer", "i": 10, "s": 0, "p": 1, "v": 0.25})
    assert r["plugin"] == "Fruity Reeverb 2" and r["param"][1:3] == ["Decay", 0.25]


@pytest.mark.parametrize(
    ("req", "message"),
    [
        ({"t": "channel", "i": 0}, "no plugin there"),
        ({"t": "mixer", "i": 10}, "mixer needs slot"),
        ({"t": "mixer", "i": 10, "s": 3}, "no plugin there"),
        ({"t": "mixer", "i": 10, "s": 12}, "slot 12 out of range"),
        ({"t": "channel", "i": 2, "p": 9, "v": 0.5}, "param 9 out of range"),
        ({"t": "channel", "i": 2, "p": 0, "v": 2}, "value 2.0 out of range"),
        ({"t": "pattern", "i": 0}, "target must be"),
    ],
)
def test_param_errors(fl, req, message):
    with pytest.raises(FLError, match=message):
        fl.call({"o": "param", **req})


def test_roll_selects_and_opens_the_piano_roll(fl):
    r = fl.call({"o": "roll", "c": 2, "p": 5})
    assert r == {"pat": 5, "ch": 2, "ppq": 96} and fl.fake.windows == [3]
    with pytest.raises(FLError, match="channel 8 out of range"):
        fl.call({"o": "roll", "c": 8})


def test_call_runs_allowlisted_api_with_constants(fl):
    r = fl.call(
        {
            "o": "call",
            "c": [
                ["mixer.getTrackName", 10],
                ["channels.channelCount", True],
                ["transport.globalTransport", "midi.FPT_Metronome", 1],
            ],
        }
    )
    assert r == ["Drums", 3, 0] and fl.fake.metro


def test_call_enforces_the_allowlist(fl):
    for name in ("device.midiOutMsg", "os.system", "mixer._x", "mixer", "midi.FPT_Play", "..x"):
        with pytest.raises(FLError, match="not allowed"):
            fl.call({"o": "call", "c": [[name, 1]]})
    with pytest.raises(FLError, match="bad constant"):
        fl.call({"o": "call", "c": [["mixer.muteTrack", 1, "midi.FPT_Play|os.name"]]})
    with pytest.raises(FLError, match="call 1 .*mixer.getTrackName.*failed after 1 ok"):
        fl.call({"o": "call", "c": [["mixer.trackCount"], ["mixer.getTrackName", 9999]]})


def test_duplicate_requests_are_answered_from_cache(fl):
    out = []
    fl.fake.out_hook = out.append
    frame = b.encode(21, b.dumps({"o": "tr", "a": "undo"}))
    for _ in range(2):
        for cc, value in frame:
            fl.script.OnMidiMsg(event(cc, value))
    assert fl.fake.undos == 1
    half = len(out) // 2
    assert half > 4 and out[:half] == out[half:]


def test_same_id_with_a_different_request_is_not_mistaken_for_a_retry(fl):
    out = []
    fl.fake.out_hook = out.append
    for _ in range(2):  # two different clients that happen to pick the same id
        for cc, value in b.encode(21, b.dumps({"o": "tr", "a": "undo", "n": len(out)})):
            fl.script.OnMidiMsg(event(cc, value))
    assert fl.fake.undos == 2


def test_corrupt_request_gets_a_bad_frame_reply(fl):
    got = []
    fl.fake.out_hook = lambda packed: got.append(((packed >> 8) & 255, (packed >> 16) & 255))
    frame = b.encode(30, b.dumps({"o": "tr", "a": "undo"}))
    frame[-1] = (frame[-1][0], (frame[-1][1] + 1) % 127)
    for cc, value in frame:
        fl.script.OnMidiMsg(event(cc, value))
    assert fl.fake.undos == 0
    dec, res = b.Decoder(), None
    for cc, value in got:
        res = dec.feed(cc, value) or res
    assert res[0] == 30 and b"bad frame" in res[1]


def test_midi_channels_are_filtered(fl):
    echo = event(b.BEGIN, 5, channel=b.RES_CH)
    ours = event(b.BEGIN, 5)
    note = event(60, 100, channel=0)
    note.status = 0x90
    other = event(b.BEGIN, 5, channel=3)
    for ev in (echo, ours, note, other):
        fl.script.OnMidiMsg(ev)
    assert (echo.handled, ours.handled, note.handled, other.handled) == (True, True, False, False)


def test_channel_is_found_even_when_fl_reports_it_in_midichan(fl):
    got = []
    fl.fake.out_hook = got.append
    frame = b.encode(40, b.dumps({"o": "ping"}))
    for cc, value in frame:
        ev = event(cc, value)
        ev.status, ev.midiId, ev.midiChan = 0xB0, 0xB0, b.REQ_CH  # channel nibble missing
        fl.script.OnMidiMsg(ev)
        assert ev.handled
    assert len(got) > 10
    ghost = event(b.BEGIN, 1)
    ghost.status, ghost.midiId, ghost.midiChan = 0x90, 0x90, b.REQ_CH  # a note, not a CC
    fl.script.OnMidiMsg(ghost)
    assert not ghost.handled


def test_oversized_results_are_refused(fl):
    fl.script._MAX_OUT = 200
    with pytest.raises(FLError, match="result too large"):
        fl.call({"o": "list", "k": "channels"})
    assert fl.call({"o": "ping"})["proto"] == b.PROTO  # still healthy afterwards


def test_script_survives_garbage_events(fl):
    class Broken:
        status = 0xBE

        @property
        def data1(self):
            raise RuntimeError("boom")

        handled = False

    fl.script.OnMidiMsg(Broken())
    assert fl.call({"o": "ping"})["proto"] == b.PROTO


def test_unlinked_output_is_reported_not_fatal(fl, capsys):
    fl.fake.assigned = False
    with pytest.raises(FLError, match="No answer"):
        fl.call({"o": "ping"})
    assert "no MIDI output linked" in capsys.readouterr().out
    fl.script.OnInit()
    assert "set the MIDI output port" in capsys.readouterr().out


# ---- real units, metering, batches


def test_gain_finds_the_fader_position_for_a_db_target(fl):
    r = fl.call({"o": "gain", "t": "mixer", "i": 3, "db": -6})
    assert abs(r["db"] + 6) <= 0.05 and not r["clamped"]
    assert abs(fl.fake.tracks[3]["vol"] - 0.8 * 10 ** (-6 / 20)) < 0.003
    r = fl.call({"o": "gain", "t": "channel", "i": 1, "db": -3})
    assert (
        abs(r["db"] + 3) <= 0.05
        and abs(fl.fake.chans[1]["vol"] - 0.78125 * 10 ** (-3 / 20)) < 0.003
    )
    far = fl.call({"o": "gain", "t": "mixer", "i": 3, "db": 6})  # this fader tops out near +1.9 dB
    assert far["clamped"] and far["v"] > 0.99
    assert fl.call({"o": "gain", "t": "mixer", "i": 3, "db": 0})["v"] == pytest.approx(
        0.8, abs=0.006
    )
    for bad in (
        {"t": "mixer", "i": 3, "db": 40},
        {"t": "bus", "i": 0, "db": 0},
        {"t": "mixer", "i": 300, "db": 0},
    ):
        with pytest.raises(FLError):
            fl.call({"o": "gain", **bad})


def test_eq_sets_real_frequency_and_gain(fl):
    r = fl.call({"o": "eq", "i": 5, "b": 1, "f": 250, "g": -3})
    assert abs(r["f"] - 250) <= 2.6 and abs(r["g"] + 3) <= 0.05
    r = fl.call({"o": "eq", "i": 5, "b": 0, "g": 2.5, "w": 0.3})
    assert "f" not in r and r["w"] == 0.3 and abs(r["g"] - 2.5) <= 0.05
    for bad in ({"i": 5, "b": 3}, {"i": 5, "b": 0, "f": 5}, {"i": 5, "b": 0, "g": 99}):
        with pytest.raises(FLError):
            fl.call({"o": "eq", **bad})


def test_peaks_markers_and_audition(fl):
    fl.fake.peaks = {0: 0.91234, 3: 0.5}
    assert fl.call({"o": "peaks", "t": [0, 3, 4]}) == {"p": [0.912, 0.5, 0.0]}
    assert fl.call({"o": "markers", "m": [[0, "Intro"], [1536, "Drop"]]}) == {"n": 2}
    assert fl.fake.markers == [(0, "Intro"), (1536, "Drop")]
    fl.call({"o": "note", "c": 2, "n": 48, "v": 90})
    assert fl.fake.auditioned == [(2, 48, 90)]
    with pytest.raises(FLError, match="note 200"):
        fl.call({"o": "note", "c": 2, "n": 200})


def test_grid_replaces_steps_with_velocities_and_swing(fl):
    r = fl.call({"o": "grid", "c": 0, "p": 2, "g": "z...y...x.z..w..", "sw": 5})
    sp = fl.fake.chans[0]["sp"]
    assert r == {"on": 5, "bad": 0} and fl.fake.pat == 2
    assert fl.fake.chans[0]["steps"][2] == {0, 4, 8, 10, 13}
    assert sp[(2, 0, 1)] == 127 and sp[(2, 4, 1)] == round(34 * 127 / 35) and (2, 13, 7) in sp
    assert (2, 0, 7) not in sp  # swing only on off-16ths
    fl.call({"o": "grid", "c": 0, "p": 2, "g": "z..............."})  # replaces, not merges
    assert fl.fake.chans[0]["steps"][2] == {0}
    fl.fake.step_limit = 16
    assert fl.call({"o": "grid", "c": 1, "p": 3, "g": "z" * 20})["bad"] == 4  # FL dropped steps
    for bad in (
        {"c": 0, "p": 2, "g": "xx!"},
        {"c": 0, "p": 0, "g": "z"},
        {"c": 0, "p": 2, "g": "z" * 600},
    ):
        with pytest.raises(FLError):
            fl.call({"o": "grid", **bad})


def test_batch_runs_in_order_with_one_undo_step(fl):
    steps = [
        {"o": "tr", "a": "tempo", "v": 150},
        {"o": "grid", "c": 0, "p": 4, "g": "z...z...z...z..."},
        {"o": "set", "k": "pattern", "i": 4, "p": {"name": "Drop A - Drums"}},
    ]
    r = fl.call({"o": "batch", "u": "produce house", "s": steps})
    assert (
        r == {"n": 3, "bad": 0}
        and fl.fake.bpm == 150
        and fl.fake.pats[4]["name"] == "Drop A - Drums"
    )
    assert fl.fake.undo_points == [("FL MCP: produce house", 1 | 2 | 4 | 32)]
    fl.call({"o": "batch", "s": [{"o": "tr", "a": "stop"}]})
    assert len(fl.fake.undo_points) == 1  # no name, no undo point


def test_batch_reports_the_failing_step_and_refuses_nesting(fl):
    steps = [
        {"o": "tr", "a": "tempo", "v": 111},
        {"o": "set", "k": "channel", "i": 99, "p": {"name": "x"}},
    ]
    with pytest.raises(FLError, match=r"step 1 \(set\): ValueError: channel 99 out of range"):
        fl.call({"o": "batch", "u": "x", "s": steps})
    assert (
        fl.fake.bpm == 111 and fl.fake.undo_points == []
    )  # earlier steps stay, no half-named undo
    with pytest.raises(FLError, match="cannot run inside a batch"):
        fl.call({"o": "batch", "s": [{"o": "batch", "s": []}]})
    with pytest.raises(FLError, match="at most 300"):
        fl.call({"o": "batch", "s": [{"o": "tr", "a": "stop"}] * 301})


def test_save_new_version(fl):
    fl.call({"o": "tr", "a": "savenew"})
    assert fl.fake.saves == 10
