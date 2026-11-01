import io

import mido
import pytest

from fl_mcp.music import GENRES, compose
from fl_mcp.music.midi import DRUMS, GM_DRUMS, TICKS, plan_to_midi


def read(plan, mode="fl"):
    return mido.MidiFile(file=io.BytesIO(plan_to_midi(plan, mode)))


def notes(track):
    t, on, out = 0, {}, []
    for m in track:
        t += m.time
        if m.type == "note_on" and m.velocity:
            on.setdefault(m.note, []).append((t, m.velocity))
        elif m.type in ("note_off", "note_on"):
            s, v = on[m.note].pop(0)
            out.append((s, t, m.note, v))
    assert not any(on.values()), "unterminated notes"
    return out


@pytest.mark.parametrize("genre", GENRES)
def test_smf_is_valid_and_matches_the_plan(genre):
    plan = compose(genre, seed=2, length="sketch")
    mf = read(plan)
    assert mf.type == 1 and mf.ticks_per_beat == TICKS
    meta = mf.tracks[0]
    assert [m.tempo for m in meta if m.type == "set_tempo"] == [round(60_000_000 / plan.bpm)]
    assert [m.text for m in meta if m.type == "marker"][:-1] == [s.name for s in plan.sections]
    assert any(m.type == "time_signature" and (m.numerator, m.denominator) == (4, 4) for m in meta)
    assert abs(mf.length - plan.bars * 4 * 60 / plan.bpm) < 0.01
    names = [t.name for t in mf.tracks[1:]]
    assert names == [r for r in plan.roles if r in names]
    for track in mf.tracks[1:]:
        got = notes(track)
        assert got and all(s < e and 1 <= v <= 127 for s, e, _, v in got)
        by_pitch: dict[int, list] = {}
        for s, e, pitch, _ in sorted(got):
            by_pitch.setdefault(pitch, []).append((s, e))
        for spans in by_pitch.values():  # no overlapping notes of one pitch on one track
            assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    expected = {}
    for clip in plan.clips:
        for role, part in plan.patterns[clip.pattern].parts.items():
            expected[role] = expected.get(role, 0) + len(part.events)
    for track in mf.tracks[1:]:
        assert len(notes(track)) == expected[track.name]


def test_fl_mode_keeps_one_shots_on_c5_and_gm_mode_maps_drums():
    plan = compose("house", seed=1, length="sketch")
    fl, gm = read(plan, "fl"), read(plan, "gm")
    for track in fl.tracks[1:]:
        if track.name in DRUMS:
            assert {n[2] for n in notes(track)} == {60}
    drums = next(t for t in gm.tracks if t.name == "drums")
    assert {n[2] for n in notes(drums)} <= set(GM_DRUMS.values())
    assert all(m.channel == 9 for m in drums if hasattr(m, "channel"))
    assert [t.name for t in gm.tracks[1:]][0] == "drums"
    assert len(gm.tracks) < len(fl.tracks)
    channels = {m.channel for t in fl.tracks[1:] for m in t if hasattr(m, "channel")}
    assert 9 not in channels


def test_unknown_drum_mode():
    with pytest.raises(ValueError, match="drums must be"):
        plan_to_midi(compose("house", seed=1), "xyz")
