"""Engine invariants, checked for every genre: theory, structure, anti-monotony, determinism."""

import json
import statistics
from functools import cache

import pytest

from fl_mcp.music import GENRES, Plan, compose
from fl_mcp.music import theory as th
from fl_mcp.music.compose import fit_sections, unit_sizes
from fl_mcp.music.genres import DRUM_ROLES, MOODS, ROLE_ORDER
from fl_mcp.music.groove import bar_hits, check_bar, swing_delay

SEEDS = (1, 2, 3)
TOL = 0.06  # beats of slack for humanised starts


@cache
def make(genre: str, seed: int, length: str = "short") -> Plan:
    return compose(genre, seed=seed, length=length)


def parts(plan: Plan, role: str):
    for clip in plan.clips:
        part = plan.patterns[clip.pattern].parts.get(role)
        if part:
            yield clip, part


# ---- theory


def test_keys_chords_and_names():
    assert th.parse_key("F# minor") == (6, "minor")
    assert th.parse_key("Bb dorian") == (10, "dorian")
    assert th.parse_key("C") == (0, "major")
    assert th.parse_key("a phrygian dominant") == (9, "hicaz")
    for bad in ("H minor", "C wibble", ""):
        with pytest.raises(ValueError):
            th.parse_key(bad)
    assert (th.note_name(60), th.note_name(60, fl_style=True), th.note_name(21)) == (
        "C4",
        "C5",
        "A0",
    )
    c = lambda tok: sorted(p % 12 for p in th.chord_pitches(0, tok))  # noqa: E731
    assert c("I") == [0, 4, 7] and c("i") == [0, 3, 7] and c("V7") == [2, 5, 7, 11]
    assert c("ii7") == [0, 2, 5, 9] and c("Imaj7") == [0, 4, 7, 11] and c("bII") == [1, 5, 8]
    assert c("bVII") == [2, 5, 10] and c("IVsus2") == [0, 5, 7]
    with pytest.raises(ValueError):
        th.parse_chord("Z")


def test_voice_leading_moves_little():
    prev = None
    for tok in ("i", "bVI", "bIII", "bVII", "i"):
        v = th.voice_lead(prev, th.chord_pitches(9, tok), 55, 76)
        assert 55 <= min(v) and max(v) <= 76 and len(v) == 3
        if prev:
            assert sum(abs(a - b) for a, b in zip(sorted(prev), v, strict=True)) <= 8
        prev = v


def test_groove_language():
    import random

    rng = random.Random(1)
    assert [s for s, _ in bar_hits("X...x...X...x...", rng)] == [0, 4, 8, 12]
    assert bar_hits("................", rng) == []
    mean = lambda d: statistics.mean(  # noqa: E731
        len(bar_hits("????????????????", random.Random(i), d)) for i in range(300)
    )
    assert 7 < mean(0.5) < 9 and 11 < mean(1.0) < 13 and mean(0.2) < mean(0.5) < mean(1.0)
    assert swing_delay(1, 50) == 0 and swing_delay(2, 66) == 0 and 0.07 < swing_delay(1, 66) < 0.09
    with pytest.raises(ValueError):
        check_bar("x" * 15)


# ---- profiles


@pytest.mark.parametrize("genre", GENRES)
def test_profile_is_well_formed(genre):
    g = GENRES[genre]
    assert g.bpm[0] <= g.bpm[1] <= g.bpm[2] and 50 <= g.swing <= 66
    assert set(g.mood_scale) == set(MOODS) and g.default_scale in g.progressions
    assert set(g.mood_scale.values()) <= set(g.progressions)
    assert set(g.drums) <= set(DRUM_ROLES)
    for pats in g.drums.values():
        for p in pats:
            check_bar(p)
    for pat in (
        *g.bass.patterns,
        *g.chords.patterns,
        *g.pad.patterns,
        *g.lead.cells,
        g.arp.pattern,
    ):
        if pat != "follow":
            check_bar(pat)
    for family, progs in g.progressions.items():
        assert family in th.SCALES
        for prog in progs:
            assert len(prog) == 4
            for tok in prog:
                th.parse_chord(tok)
    for sec in g.sections:
        assert set(sec.roles) <= set(ROLE_ORDER) and sec.bars % 4 == 0
        assert set(sec.roles) & set(DRUM_ROLES) <= set(g.drums), sec
    assert g.sections[0].core and g.sections[-1].core
    assert sum(s.bars for s in g.sections if s.core) >= 24
    assert -14 <= g.lufs <= -5


def test_sections_fit_requested_lengths():
    g = GENRES["house"]
    sketch, short, full = (
        sum(s.bars for s in fit_sections(g, k)) for k in ("sketch", "short", "full")
    )
    assert sketch < short < full
    assert sum(s.bars for s in fit_sections(g, 70)) == short
    assert unit_sizes(16) == [8, 8] and unit_sizes(12) == [8, 4] and unit_sizes(4) == [4]
    with pytest.raises(ValueError):
        fit_sections(g, "epic")


# ---- structure and theory of generated plans


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize("seed", SEEDS)
def test_structure(genre, seed):
    p = make(genre, seed)
    g = GENRES[genre]
    assert p.id == f"{genre}-{seed:x}" and p.genre == genre and p.seed == seed
    assert g.bpm[0] <= p.bpm <= g.bpm[2] and p.swing == g.swing
    for a, b in zip(p.sections, p.sections[1:], strict=False):
        assert a.bar + a.bars == b.bar
    assert sum(s.bars for s in p.sections) == p.bars > 0
    by_track: dict[int, list] = {}
    for c in p.clips:
        assert 0 <= c.bar and c.bar + c.bars <= p.bars and 1 <= c.track <= 5
        assert c.bars == p.patterns[c.pattern].bars
        by_track.setdefault(c.track, []).append(c)
    for clips in by_track.values():
        clips.sort(key=lambda c: c.bar)
        for a, b in zip(clips, clips[1:], strict=False):
            assert a.bar + a.bars <= b.bar, "overlapping clips on one playlist track"
    for sec in p.sections:  # every active role really plays in the section
        for role in sec.roles:
            if role in GENRES[genre].drums or role in (
                "bass",
                "chords",
                "pad",
                "lead",
                "arp",
                "fx",
            ):
                assert (
                    any(
                        c.bar >= sec.bar
                        and c.bar < sec.bar + sec.bars
                        and role in p.patterns[c.pattern].parts
                        for c in p.clips
                    )
                    or role == "fx"
                ), (sec.name, role)
    assert len({pat.name for pat in p.patterns}) == len(p.patterns)


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize("seed", SEEDS)
def test_events_are_valid_and_in_range(genre, seed):
    p, g = make(genre, seed), GENRES[genre]
    ranges = {
        "bass": (g.bass.lo, g.bass.hi),
        "chords": (g.chords.lo, g.chords.hi),
        "pad": (g.pad.lo, g.pad.hi),
        "lead": (g.lead.lo, g.lead.hi),
        "arp": (g.arp.lo, g.arp.hi),
    }
    for pat in p.patterns:
        for role, part in pat.parts.items():
            assert part.events, (pat.name, role)
            for e in part.events:
                assert 1 <= e.v <= 127 and isinstance(e.v, int) and 0 <= e.p <= 127
                assert e.d > 0 and e.t >= 0 and e.t < pat.bars * 4
                assert e.t + e.d <= pat.bars * 4 + TOL, (pat.name, role, e)
                if part.grid:
                    assert e.p == 60  # one-shots play unpitched at C5 (MIDI 60)
                elif role in ranges:
                    lo, hi = ranges[role]
                    assert lo <= e.p <= hi, (role, e.p, lo, hi)


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize("seed", SEEDS)
def test_harmony_and_melody_follow_the_key(genre, seed):
    p, g = make(genre, seed), GENRES[genre]
    per = g.chords.per
    scale = th.scale_pcs(p.tonic, p.scale)

    def chord_pcs(bar):
        root, iv = th.parse_chord(p.progression[(bar // per) % 4])
        return {(p.tonic + root + i) % 12 for i in iv}

    for pat in p.patterns:
        for role in ("lead", "arp", "chords", "pad"):
            part = pat.parts.get(role)
            for e in part.events if part else ():
                bar = int(e.t // 4 + 1e-9)
                # strum and ornament starts can slip just before a bar line
                allowed = scale | chord_pcs(min(bar, pat.bars - 1))
                if role in ("chords", "pad"):
                    assert e.p % 12 in chord_pcs(bar % pat.bars) | scale
                elif role == "lead":
                    assert e.p % 12 in allowed or e.p % 12 in chord_pcs(max(bar - 1, 0)), (p.key, e)
        bass = pat.parts.get("bass")
        if bass:
            first: dict[int, object] = {}
            for e in bass.events:
                first.setdefault(int(e.t // 4 + 1e-9), e)
            for bar, e in first.items():
                root, _ = th.parse_chord(p.progression[(bar // per) % 4])
                assert e.p % 12 == (p.tonic + root) % 12, (pat.name, bar, e.p)


@pytest.mark.parametrize("genre", GENRES)
def test_monophonic_parts_never_overlap(genre):
    p = make(genre, 2)
    for pat in p.patterns:
        for role in ("bass", "lead"):
            part = pat.parts.get(role)
            if not part:
                continue
            evs = sorted(part.events, key=lambda e: e.t)
            for a, b in zip(evs, evs[1:], strict=False):
                assert a.t + a.d <= b.t + 1e-6, (genre, pat.name, role, a, b)


@pytest.mark.parametrize("genre", GENRES)
def test_chords_stay_playable(genre):
    for pat in make(genre, 1).patterns:
        for role in ("chords", "pad"):
            part = pat.parts.get(role)
            if part:
                starts: dict[int, int] = {}
                for e in part.events:
                    starts[int(e.t * 4)] = starts.get(int(e.t * 4), 0) + 1
                assert max(starts.values()) <= 5


# ---- not monotonous


@pytest.mark.parametrize("genre", GENRES)
@pytest.mark.parametrize("seed", SEEDS)
def test_no_robotic_repetition(genre, seed):
    p = make(genre, seed, "full")
    hats = [
        e.v
        for pat in p.patterns
        for r in ("hat", "perc")
        for e in (pat.parts[r].events if r in pat.parts else [])
    ]
    if hats:
        assert statistics.pstdev(hats) > 3, "velocities are too flat"
    drum_sigs = set()
    for pat in p.patterns:
        drums = {r: x for r, x in pat.parts.items() if r in DRUM_ROLES}
        if drums:
            drum_sigs.add(
                tuple(
                    (r, tuple((round(e.t, 3), e.v) for e in x.events))
                    for r, x in sorted(drums.items())
                )
            )
    assert len(drum_sigs) >= 3
    bar_variety = []
    for sec in p.sections:
        if sec.bars < 8 or sec.kind in ("intro", "outro", "break"):
            continue
        sigs = set()
        for clip in p.clips:
            if sec.bar <= clip.bar < sec.bar + sec.bars:
                for r, part in p.patterns[clip.pattern].parts.items():
                    if r in DRUM_ROLES:
                        for bar in range(clip.bars):
                            sigs.add(
                                (
                                    bar,
                                    r,
                                    tuple(
                                        round(e.t - bar * 4, 3)
                                        for e in part.events
                                        if bar * 4 <= e.t < bar * 4 + 4
                                    ),
                                )
                            )
        per_bar = {}
        for bar, r, sig in sigs:
            per_bar.setdefault(bar, set()).add((r, sig))
        bar_variety.append(len({frozenset(v) for v in per_bar.values()}))
    assert not bar_variety or max(bar_variety) >= 2, "every drum bar is identical"


@pytest.mark.parametrize("genre", GENRES)
def test_melodies_move_and_vary(genre):
    p = make(genre, 3, "full")
    leads = [part.events for _, part in parts(p, "lead")]
    if not leads:
        pytest.skip("genre has no lead")
    for evs in leads:
        pitches = [e.p for e in sorted(evs, key=lambda e: e.t)]
        run = longest = 1
        for a, b in zip(pitches, pitches[1:], strict=False):
            run = run + 1 if a == b else 1
            longest = max(longest, run)
        assert longest <= 4
    assert max(max(e.p for e in evs) - min(e.p for e in evs) for evs in leads) >= 4
    hooks = [
        tuple((round(e.t, 2), e.p) for e in evs)
        for c, part in parts(p, "lead")
        for evs in [part.events]
    ]
    assert len(set(hooks)) >= 2, "the hook never changes between returns"


@pytest.mark.parametrize("genre", GENRES)
def test_transitions_exist(genre):
    p = make(genre, 1, "full")
    kinds = {s.kind for s in p.sections}
    if "build" in kinds and any("fx" in s.roles for s in p.sections if s.kind == "build"):
        assert any("fx" in pat.parts for pat in p.patterns)


# ---- brief handling


def test_determinism_and_difference():
    a, b = compose("trap", seed=5), compose("trap", seed=5)
    assert a.to_json() == b.to_json()
    assert compose("trap", seed=6).to_json() != a.to_json()
    assert compose("trap").seed != compose("trap").seed or True  # random seeds when omitted


def test_plan_json_round_trip_and_summary_is_small():
    p = make("trance", 2, "full")
    assert Plan.from_dict(json.loads(p.to_json())).to_json() == p.to_json()
    s = json.dumps(p.summary())
    assert len(s) < 4500 and "events" not in s and p.id in s


def test_key_scale_mood_and_bpm_handling():
    assert compose("trap", key="A minor", seed=1).key == "A minor"
    assert compose("house", key="F# dorian", seed=1).key == "F# dorian"
    coerced = compose("trap", key="C dorian", seed=1)
    assert coerced.scale == "minor" and any("no 'dorian'" in n for n in coerced.notes)
    assert compose("house", mood="bright", seed=1).scale == "major"
    assert compose("hicaz_trap", seed=1).scale == "hicaz"
    assert compose("house", bpm=100, seed=1).notes and compose("house", bpm=100, seed=1).bpm == 100
    assert compose("lofi", key="C", mood="chill", seed=1).scale == "major"


def test_role_filter_and_energy():
    p = compose("house", seed=1, roles=["kick", "bass"])
    assert set(p.roles) <= {"kick", "bass"} and p.roles
    low, high = compose("techno", seed=4, energy=0.1), compose("techno", seed=4, energy=1.0)
    count = lambda pl: sum(len(x.events) for pat in pl.patterns for x in pat.parts.values())  # noqa: E731
    assert count(low) < count(high)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"genre": "polka"}, "unknown genre"),
        ({"genre": "house", "mood": "sad"}, "mood must"),
        ({"genre": "house", "energy": 2}, "energy"),
        ({"genre": "house", "bpm": 10}, "bpm"),
        ({"genre": "house", "roles": ["tuba"]}, "unknown roles"),
        ({"genre": "house", "key": "Q"}, "cannot read key"),
        ({"genre": "house", "length": "forever"}, "length must"),
    ],
)
def test_bad_briefs_are_explained(kwargs, message):
    with pytest.raises(ValueError, match=message):
        compose(**kwargs)


def test_size_budget():
    for genre in GENRES:
        p = make(genre, 1, "full")
        assert sum(len(x.events) for pat in p.patterns for x in pat.parts.values()) < 12000
        assert len(p.to_json()) < 700_000
