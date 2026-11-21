"""Music theory helpers. Pitches are MIDI numbers with C4 = 60."""

import re
from typing import Any

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLATS = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
MAJOR = (0, 2, 4, 5, 7, 9, 11)

SCALES: dict[str, tuple[int, ...]] = {
    "major": MAJOR,
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "hicaz": (0, 1, 4, 5, 7, 8, 10),  # phrygian dominant
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "blues": (0, 3, 5, 6, 7, 10),
}
ALIASES = {
    "aeolian": "minor",
    "natural minor": "minor",
    "min": "minor",
    "m": "minor",
    "ionian": "major",
    "maj": "major",
    "": "major",
    "phrygian dominant": "hicaz",
    "hijaz": "hicaz",
    "harmonic": "harmonic_minor",
}


def pc_of(name: str) -> int:
    """Pitch class of a note name such as 'F#', 'Bb', 'a'."""
    n = name.strip().upper().replace("♯", "#").replace("♭", "B")
    n = _FLATS.get(n, n)
    if n not in NOTE_NAMES:
        raise ValueError(f"unknown note '{name}'")
    return NOTE_NAMES.index(n)


def parse_key(text: str) -> tuple[int, str]:
    """'F# minor' -> (6, 'minor'); 'C' -> (0, 'major'); 'A hicaz' -> (9, 'hicaz')."""
    m = re.fullmatch(r"\s*([A-Ga-g][#b♯♭]?)\s*(.*?)\s*", text)
    if not m:
        raise ValueError(f"cannot read key '{text}' (examples: 'A minor', 'F# dorian', 'C')")
    scale = m.group(2).lower().replace("-", "_")
    scale = ALIASES.get(scale, scale).replace(" ", "_")
    if scale not in SCALES:
        raise ValueError(f"unknown scale '{m.group(2)}'; use one of {', '.join(SCALES)}")
    return pc_of(m.group(1)), scale


def key_name(tonic: int, scale: str) -> str:
    return f"{NOTE_NAMES[tonic % 12]} {scale.replace('_', ' ')}"


def note_name(pitch: int, fl_style: bool = False) -> str:
    """'C4' for 60 (standard); with fl_style the label FL's piano roll shows ('C5')."""
    octave = pitch // 12 - 1 + (1 if fl_style else 0)
    return f"{NOTE_NAMES[pitch % 12]}{octave}"


def scale_pcs(tonic: int, scale: str) -> frozenset[int]:
    return frozenset((tonic + i) % 12 for i in SCALES[scale])


def scale_pitches(tonic: int, scale: str, lo: int, hi: int) -> list[int]:
    pcs = scale_pcs(tonic, scale)
    return [p for p in range(lo, hi + 1) if p % 12 in pcs]


_NUMERAL = re.compile(
    r"^([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(maj7|m7|7|9|m9|sus2|sus4|dim|aug)?$"
)
_DEGREE = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}
_TRIADS = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
}


def parse_chord(token: str) -> tuple[int, tuple[int, ...]]:
    """Roman numeral relative to the major scale of the tonic -> (root offset, intervals).

    'i' minor, 'bVI' major on the flat sixth, 'V7' dominant seventh, 'ii7' minor seventh,
    'Imaj7', 'IVsus2', 'bII'. Case gives the triad quality; the suffix adds colour.
    """
    m = _NUMERAL.match(token)
    if not m:
        raise ValueError(f"bad chord '{token}'")
    alter, numeral, ext = m.groups()
    root = MAJOR[_DEGREE[numeral.lower()]] + {"b": -1, "#": 1, "": 0}[alter]
    quality = "major" if numeral.isupper() else "minor"
    if ext in ("dim", "aug", "sus2", "sus4"):
        return root % 12, _TRIADS[ext]
    iv = list(_TRIADS[quality])
    if ext in ("7", "m7"):
        iv.append(10 if (ext == "m7" or quality == "minor" or ext == "7") else 11)
    elif ext == "maj7":
        iv.append(11)
    elif ext in ("9", "m9"):
        iv += [10, 14]
    return root % 12, tuple(iv)


def chord_pitches(tonic: int, token: str, octave_c: int = 60) -> list[int]:
    """Closed root-position chord with the root at or above `octave_c` (a C)."""
    root, intervals = parse_chord(token)
    base = octave_c + (tonic + root) % 12
    return [base + i for i in intervals]


def voice_lead(prev: list[int] | None, pitches: list[int], lo: int, hi: int) -> list[int]:
    """Re-voice a chord (inversions / octaves) to move as little as possible from `prev`."""
    pcs = [p % 12 for p in pitches]
    target = sum(prev) / len(prev) if prev else (lo + hi) / 2
    best, best_cost = None, 1e9
    for inversion in range(len(pcs)):
        order = pcs[inversion:] + pcs[:inversion]
        for octave in range(lo // 12 - 1, hi // 12 + 1):
            voicing, last = [], octave * 12 - 1
            for pc in order:  # stack upwards from the lowest note
                p = last + 1 + (pc - (last + 1)) % 12
                voicing.append(p)
                last = p
            if voicing[0] < lo or voicing[-1] > hi:
                continue
            if prev and len(prev) == len(voicing):
                cost = sum(abs(a - b) for a, b in zip(sorted(prev), voicing, strict=True))
            else:
                cost = abs(sum(voicing) / len(voicing) - target)
            if cost < best_cost:
                best, best_cost = voicing, cost
    if best is None:  # range too tight: fall back to the plain voicing
        return sorted(pitches)
    return best


_CHORD_INTERVALS: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "maj": (0, 4, 7),
    "m": (0, 3, 7),
    "min": (0, 3, 7),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "5": (0, 7),
    "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9),
    "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10),
    "m7b5": (0, 3, 6, 10),
    "dim7": (0, 3, 6, 9),
    "add9": (0, 4, 7, 14),
    "madd9": (0, 3, 7, 14),
    "9": (0, 4, 7, 10, 14),
    "maj9": (0, 4, 7, 11, 14),
    "m9": (0, 3, 7, 10, 14),
}
_NAMED_CHORD = re.compile(r"^([A-Ga-g][#b♯♭]?)(.*?)$")


def _norm_quality(raw_q: str) -> str:
    q = raw_q.strip()
    if q.startswith("min") and q not in _CHORD_INTERVALS:
        q = "m" + q[3:]
    return q


def parse_named_chord(name: str, default_octave: int = 4) -> list[int]:
    """Parse chord name like 'Am7', 'Cmaj7', 'G7', 'F#3m9', 'Bbdim' into MIDI pitches."""
    m = _NAMED_CHORD.match(name.strip())
    if not m:
        raise ValueError(f"unknown chord '{name}'")
    root_s, tail = m.groups()
    pc = pc_of(root_s)
    tail_s = _norm_quality(tail)
    octave = default_octave
    if tail_s in _CHORD_INTERVALS:
        q_norm = tail_s
    elif tail.strip() and tail.strip()[0] in "012345678":
        octave = int(tail.strip()[0])
        q_norm = _norm_quality(tail.strip()[1:])
    else:
        q_norm = tail_s
    intervals = _CHORD_INTERVALS.get(q_norm)
    if intervals is None:
        raise ValueError(
            f"unsupported chord quality '{tail}' in '{name}'; "
            f"supported: {', '.join(k or 'maj' for k in _CHORD_INTERVALS)}"
        )
    base = (octave + 1) * 12 + pc
    return [p for p in (base + iv for iv in intervals) if 0 <= p <= 127]


def parse_timing_pos(token: str, beats_per_bar: int = 4) -> float:
    """Parse beat offset ('0', '4.5') or 1-based bar|beat ('1|1' -> 0.0, '2|3' -> 6.0)."""
    raw = token.strip()
    if "|" in raw:
        bar_s, beat_s = raw.split("|", 1)
        bar = float(bar_s.strip())
        beat = float(beat_s.strip())
        if bar < 1 or beat < 1:
            raise ValueError(f"bar|beat must be >= 1|1, got '{token}'")
        return (bar - 1.0) * beats_per_bar + (beat - 1.0)
    val = float(raw)
    if val < 0:
        raise ValueError(f"timing position must be >= 0, got '{token}'")
    return val


def parse_duration(token: str, beats_per_bar: int = 4) -> float:
    """Parse beat duration ('4', '0.5') or note value ('n/8', '1/8', '1/16', '3/8')."""
    raw = token.strip().lower()
    if "/" in raw:
        num_s, den_s = raw.split("/", 1)
        num = 1.0 if num_s.strip() in ("", "n") else float(num_s.strip())
        den = float(den_s.strip())
        if num <= 0 or den <= 0:
            raise ValueError(f"invalid note duration '{token}'")
        return beats_per_bar * num / den
    val = float(raw)
    if val <= 0:
        raise ValueError(f"note duration must be > 0, got '{token}'")
    return val


def chords_to_notes(spec: str, beats_per_bar: int = 4) -> list[list[float]]:
    """Expand 'Am7@0:4, Dm7@1|1:n/8, G7@6:2@95' into [midi, start, length, vel] rows."""
    rows: list[list[float]] = []
    cursor = 0.0
    for raw in filter(None, (p.strip() for p in re.split(r"[,;]+", spec))):
        parts = raw.split("@")
        chord_name = parts[0].strip()
        start = cursor
        length = 2.0
        vel = 100
        if len(parts) >= 2 and parts[1].strip():
            timing = parts[1].strip().split(":")
            start = parse_timing_pos(timing[0], beats_per_bar)
            if len(timing) >= 2 and timing[1].strip():
                length = parse_duration(timing[1], beats_per_bar)
        if len(parts) >= 3 and parts[2].strip():
            vel = int(float(parts[2].strip()))
        if start < 0 or length <= 0 or not 1 <= vel <= 127:
            raise ValueError(f"invalid chord timing/velocity in '{raw}'")
        for pitch in parse_named_chord(chord_name):
            rows.append([pitch, round(start, 4), round(length, 4), vel])
        cursor = start + length
    if not rows:
        raise ValueError("chords string is empty")
    return rows


def transform_notes(
    rows: list[list[float]],
    spec: str,
    *,
    key: str | None = None,
    seed: int = 0,
    beats_per_bar: int = 4,
) -> list[list[float]]:
    """Apply comma-separated non-destructive transforms to [midi, start, len, vel] notes.

    Supported ops: transpose=+/-st, snap[=key], quantize=step, swing=0..1|pct,
    humanize=vel[,dt], legato[=factor], ramp=lo..hi, ratchet=n[@beats],
    vel_scale=f, dur_scale=f.
    """
    import random

    if not rows:
        return []
    out = [
        [int(r[0]), float(r[1]), float(r[2]), int(r[3]) if len(r) > 3 else 100, *r[4:]]
        for r in rows
    ]
    rng = random.Random(seed)
    ops = [op.strip() for op in re.split(r"[,;]+", spec) if op.strip()]
    if not ops:
        raise ValueError("transform is empty")

    for op in ops:
        if "=" in op:
            name, has_eq, arg = op.partition("=")
        else:
            name, has_eq, arg = op.partition(":")
        cmd = name.strip().lower()
        val = arg.strip()

        if cmd == "transpose":
            if not has_eq or not val:
                raise ValueError("transpose needs semitones, e.g. transpose=-12 or transpose=+7")
            st = int(float(val))
            for n in out:
                n[0] = max(0, min(127, int(n[0]) + st))
        elif cmd == "snap":
            target_key = val or key
            if not target_key:
                raise ValueError("snap needs a key like snap=A minor")
            tonic, scale = parse_key(target_key)
            allowed = scale_pitches(tonic, scale, 0, 127)
            for n in out:
                n[0] = min(allowed, key=lambda p, cur=n[0]: abs(p - cur))
        elif cmd == "quantize":
            if not has_eq or not val:
                raise ValueError("quantize needs grid step, e.g. quantize=0.25 or quantize=1/16")
            step = parse_duration(val, beats_per_bar)
            for n in out:
                n[1] = round(max(0.0, round(n[1] / step) * step), 4)
        elif cmd == "swing":
            if not has_eq or not val:
                raise ValueError("swing needs amount, e.g. swing=0.6 or swing=62")
            amt = float(val)
            frac = amt / 100.0 if amt > 1.0 else amt
            if not 0.5 <= frac <= 0.78:
                raise ValueError("swing must be between 0.50..0.78 (or 50..78)")
            shift = (frac - 0.5) * 0.5  # up to ~1/8th beat push on offbeat 16ths/8ths
            for n in out:
                pos_in_half = n[1] % 0.5
                if abs(pos_in_half - 0.25) < 0.04:
                    n[1] = round(n[1] + shift, 4)
        elif cmd == "humanize":
            parts = [p.strip() for p in val.split(":") if p.strip()] if val else ["10"]
            dv = min(32, max(0, int(float(parts[0]))))
            dt = min(0.08, max(0.0, float(parts[1]))) if len(parts) > 1 else 0.025
            for n in out:
                t_eff = dt * (0.25 if abs(n[1] % beats_per_bar) < 0.02 else 1.0)
                n[1] = round(max(0.0, n[1] + rng.uniform(-t_eff, t_eff)), 4)
                n[3] = max(1, min(127, int(n[3]) + rng.randint(-dv, dv)))
        elif cmd == "legato":
            factor = float(val) if has_eq and val else 0.98
            factor = max(0.5, min(1.0, factor))
            by_time = sorted(range(len(out)), key=lambda idx: (out[idx][1], out[idx][0]))
            distinct_starts = sorted({out[i][1] for i in by_time})
            for i in range(len(distinct_starts) - 1):
                cur_t, next_t = distinct_starts[i], distinct_starts[i + 1]
                span = max(0.03125, (next_t - cur_t) * factor)
                for idx in by_time:
                    if abs(out[idx][1] - cur_t) < 1e-6:
                        out[idx][2] = round(span, 4)
        elif cmd == "ramp":
            if ".." not in val:
                raise ValueError("ramp needs range like ramp=55..115")
            lo_s, hi_s = val.split("..", 1)
            v0 = max(1, min(127, int(float(lo_s.strip()))))
            v1 = max(1, min(127, int(float(hi_s.strip()))))
            t_min = min(n[1] for n in out)
            t_max = max(n[1] for n in out)
            span = max(1e-6, t_max - t_min)
            for n in out:
                frac = (n[1] - t_min) / span if len(out) > 1 and t_max > t_min else 1.0
                n[3] = max(1, min(127, round(v0 + (v1 - v0) * frac)))
        elif cmd == "ratchet":
            sub_s, _, win_s = val.partition("@")
            sub = max(2, min(8, int(float(sub_s or "2"))))
            win = float(win_s) if win_s else 0.0
            end_t = max(n[1] for n in out)
            new_out: list[list[float]] = []
            for n in out:
                if win <= 0 or n[1] >= end_t - win - 1e-6:
                    step = max(0.03125, n[2] / sub)
                    base_v = int(n[3])
                    for k in range(sub):
                        nv = max(1, min(127, round(base_v * (0.72 + 0.28 * k / max(1, sub - 1)))))
                        new_out.append(
                            [n[0], round(n[1] + k * step, 4), round(step * 0.9, 4), nv, *n[4:]]
                        )
                else:
                    new_out.append(n)
            out = new_out
        elif cmd == "vel_scale":
            factor = float(val)
            for n in out:
                n[3] = max(1, min(127, round(n[3] * factor)))
        elif cmd == "dur_scale":
            factor = float(val)
            for n in out:
                n[2] = round(max(0.03125, n[2] * factor), 4)
        else:
            raise ValueError(
                f"unknown transform '{cmd}'; use transpose, snap, quantize, swing, humanize, "
                "legato, ramp, ratchet, vel_scale, or dur_scale"
            )
    return out


def notes_summary(rows: list[list[Any]], beats_per_bar: int = 4) -> dict[str, Any]:
    """Compact summary statistics for a note list (readback)."""
    if not rows:
        return {"count": 0, "bars": 0.0}
    pitches = [int(r[0]) for r in rows]
    starts = [float(r[1]) for r in rows]
    ends = [float(r[1]) + float(r[2]) for r in rows]
    vels = [int(r[3]) if len(r) > 3 else 100 for r in rows]
    bars = round(max(ends) / beats_per_bar, 2)
    return {
        "count": len(rows),
        "bars": bars,
        "span_beats": [round(min(starts), 2), round(max(ends), 2)],
        "pitch_range": [note_name(min(pitches)), note_name(max(pitches))],
        "vel_range": [min(vels), max(vels)],
        "avg_vel": round(sum(vels) / len(vels), 1),
    }


_DEFAULT_HARMONY_PRIO: dict[str, int] = {
    "box": 10,
    "flute": 9,
    "clarinet": 9,
    "lead": 9,
    "viola": 8,
    "fhorn": 8,
    "intimate": 8,
    "grand": 8,
    "piano": 8,
    "cello": 7,
    "pizz": 7,
    "timpani": 7,
    "bass": 7,
    "marimba": 5,
    "kalimba": 5,
    "celesta": 5,
    "guitar_harm": 5,
    "glock": 5,
    "harp": 4,
    "guitar": 4,
    "chords": 4,
    "pad": 4,
}
_DEFAULT_MONO_CHANNELS = frozenset({"flute", "clarinet", "viola", "fhorn", "cello", "oboe", "sax"})
_DEFAULT_SKIP_CHANNELS = frozenset({"shaker", "drum", "drums", "perc", "kick", "snare", "hat"})


def resolve_ensemble_harmony(
    ch_events: dict[str, list[list[Any]]],
    total_sec: float | None = None,
    *,
    skip_channels: tuple[str, ...] | frozenset[str] = _DEFAULT_SKIP_CHANNELS,
    mono_channels: tuple[str, ...] | frozenset[str] = _DEFAULT_MONO_CHANNELS,
    priorities: dict[str, int] | None = None,
) -> dict[str, list[list[Any]]]:
    """Ensemble-wide harmonic damper, low-register mud guard, phrase bloom & dead-air solver (E20).

    Events per channel are `[start_sec, dur_sec, midi_pitch, velocity, ...]`. Pure Python
    (no numpy required). Guarantees:
      1. Coalesces <0.38 s same-pitch bounces and prevents <0.35 s choppy notes.
      2. Eliminates within-channel & cross-channel b2/m9 semitone bleeds.
      3. Eliminates <MIDI 54 low-register mud collisions (1, 2, 3, 4, 6 semitones).
      4. Blooms phrase-ending notes before >0.35 s rests to >=1.55 s (>=2.00 s final).
      5. Bridges full-ensemble dead-air gaps where 0 pitched notes sound.
    """
    prio = {**_DEFAULT_HARMONY_PRIO, **(priorities or {})}
    skip = {s.lower() for s in skip_channels}
    mono = {m.lower() for m in mono_channels}
    result: dict[str, list[list[Any]]] = {
        ch: [[float(e[0]), float(e[1]), int(e[2]), int(e[3]), *e[4:]] for e in evs]
        for ch, evs in ch_events.items()
    }
    pitched_chs = [
        ch
        for ch, evs in result.items()
        if evs and ch.lower() not in skip and not ch.lower().endswith("_wav")
    ]
    if not pitched_chs:
        return result

    if total_sec is None:
        total_sec = max(e[0] + e[1] for ch in pitched_chs for e in result[ch])

    # Detect the piece's 7 primary diatonic pitch classes so pitch shifts stay in key
    pc_counts: dict[int, float] = {}
    for ch in pitched_chs:
        for _t, d, p, *_ in result[ch]:
            pc = int(p) % 12
            pc_counts[pc] = pc_counts.get(pc, 0.0) + float(d)
    diatonic_pcs = set(sorted(pc_counts, key=lambda k: -pc_counts[k])[:7]) or set(range(12))

    def _clashes(pa: int, pb: int, same_ch: bool = False) -> bool:
        diff = abs(pa - pb)
        if diff % 12 == 1 and diff <= 13:
            return True
        return bool(same_ch and pa < 54 and pb < 54 and diff in (1, 2, 3, 4, 6))

    def _pick_consonant(
        orig_p: int,
        ch_name: str,
        t0: float,
        t1: float,
        all_notes: list[tuple[str, list[Any]]],
        self_obj: list[Any],
    ) -> int:
        # Local key: the 7 heaviest pitch classes within +-12 s, so modulating multi-movement
        # scores never get pulled back into the global key.
        active: list[tuple[bool, int]] = []
        loc_cnt: dict[int, float] = {}
        for sch, eo in all_notes:
            if abs(eo[0] - t0) <= 12.0:
                pc_o = int(eo[2]) % 12
                loc_cnt[pc_o] = loc_cnt.get(pc_o, 0.0) + float(eo[1])
            if eo is self_obj:
                continue
            ov = min(t1, eo[0] + eo[1]) - max(t0, eo[0])
            if ov >= 0.15:
                active.append((sch == ch_name, int(eo[2])))
        loc_pcs = (
            set(sorted(loc_cnt, key=lambda k: -loc_cnt[k])[:7])
            if len(loc_cnt) >= 5
            else diatonic_pcs
        )
        lo_lim = (
            54
            if (orig_p < 54 and any(sc and ap < 54 for sc, ap in active))
            else max(36, orig_p - 12)
        )
        cands: list[tuple[int, int, int, int]] = []
        for delta in (0, 2, -2, 3, -3, 4, -4, 5, -5, 7, -7, 12, -12, 1, -1, 9, -9):
            c = orig_p + delta
            if c < lo_lim or c > 96:
                continue
            in_key = 0 if (c % 12) in loc_pcs else 1
            bad = sum(1 for sc, ap in active if _clashes(c, ap, same_ch=sc))
            cands.append((bad, in_key, abs(delta), c))
            if bad == 0 and in_key == 0 and abs(delta) <= 4:
                return int(c)
        cands.sort()
        return int(cands[0][3]) if cands else int(orig_p)

    # Step 1: Within-channel coalesce (<0.38 s same-pitch bounces, <0.36 s mono grace overlaps)
    for ch in pitched_chs:
        evs = sorted(result[ch], key=lambda x: (x[0], x[2]))
        by_p: dict[int, list[Any]] = {}
        merged: list[list[Any]] = []
        is_mono = ch.lower() in mono
        for e in evs:
            e[1] = max(0.38, float(e[1]))
            p = int(e[2])
            if is_mono and merged and 0.025 < (e[0] - merged[-1][0]) < 0.36:
                merged[-1][1] = max(merged[-1][1], (e[0] + e[1]) - merged[-1][0], 0.45)
                merged[-1][3] = max(merged[-1][3], e[3])
                continue
            prev = by_p.get(p)
            if prev is not None and (e[0] - prev[0]) < 0.38:
                prev[1] = max(prev[1], (e[0] + e[1]) - prev[0], 0.45)
                prev[3] = max(prev[3], e[3])
            else:
                by_p[p] = e
                merged.append(e)
        result[ch] = sorted(merged, key=lambda x: (x[0], x[2]))

    def _build_grid() -> list[int]:
        grid_hz = 20
        n_bins = max(1, int((total_sec + 12.0) * grid_hz))
        grid = [0] * n_bins
        for ch in pitched_chs:
            for t0, d, *_ in result[ch]:
                a = max(0, int(t0 * grid_hz))
                b = min(n_bins, int((t0 + d) * grid_hz))
                for idx_b in range(a, b):
                    grid[idx_b] += 1
        return grid

    # Step 2: Bridge full-ensemble dead-air holes
    grid_hz = 20
    grid = _build_grid()
    act = [idx for idx, v in enumerate(grid) if v > 0]
    if act:
        i_start, i_end = act[0], act[-1]
        cur: list[float] | None = None
        holes: list[tuple[float, float]] = []
        for idx_g in range(i_start, i_end + 1):
            t_g = idx_g / float(grid_hz)
            if grid[idx_g] == 0:
                if cur is None:
                    cur = [t_g, t_g]
                else:
                    cur[1] = t_g
            elif cur is not None:
                holes.append((cur[0], cur[1]))
                cur = None
        for h0, h1 in holes:
            cands = [
                (abs(h0 - (e[0] + e[1])), e)
                for ch in pitched_chs
                for e in result[ch]
                if e[0] <= h0 and (h0 - (e[0] + e[1])) <= 2.0
            ]
            cands.sort(key=lambda x: x[0])
            for _, e in cands[:2]:
                e[1] = max(e[1], (h1 + 0.25) - e[0])

    # Step 3: Phrase-continuity bloom & monophonic join enforcement
    for ch in pitched_chs:
        evs = sorted(result[ch], key=lambda x: (x[0], x[2]))
        if ch.lower() in mono:
            clusters: list[list[list[Any]]] = []
            for e in evs:
                if clusters and abs(e[0] - clusters[-1][0][0]) <= 0.025:
                    clusters[-1].append(e)
                else:
                    clusters.append([e])
            for ci, cl in enumerate(clusters):
                nxt_t = clusters[ci + 1][0][0] if ci + 1 < len(clusters) else None
                for e in cl:
                    t0, d, p = e[0], e[1], int(e[2])
                    end = max(t0 + d, t0 + 0.38)
                    if nxt_t is not None:
                        if nxt_t - t0 < 1.60:
                            end = min(nxt_t + 0.038, max(end, nxt_t - 0.02))
                        else:
                            end = min(nxt_t - 0.05, max(end, t0 + 1.55))
                        if any(int(ne[2]) == p for ne in clusters[ci + 1]):
                            end = min(end, nxt_t - 0.015)
                    else:
                        end = max(end, t0 + 2.00)
                    e[1] = max(0.36, end - t0)
        else:
            for i, e in enumerate(evs):
                t0, d = e[0], e[1]
                nxt_t = evs[i + 1][0] if i + 1 < len(evs) else None
                if nxt_t is not None:
                    gap = nxt_t - (t0 + d)
                    if gap > 0.35 and d < 1.55:
                        e[1] = max(d, 1.55) if nxt_t - t0 >= 1.60 else max(d, nxt_t - t0 - 0.03)
                else:
                    e[1] = max(d, 2.00)
        result[ch] = evs

    # Step 4: Iterative ensemble-wide b2/m9 & low-mud (<54) resolution (10 passes for dense scores)
    for _pass in range(10):
        all_notes = [(ch, e) for ch in pitched_chs for e in result[ch]]
        next_in_ch: dict[int, float | None] = {}
        for ch in pitched_chs:
            evs = result[ch]
            for i, e in enumerate(evs):
                next_in_ch[id(e)] = evs[i + 1][0] if i + 1 < len(evs) else None
        all_refs = [(prio.get(ch.lower(), 5), ch, e) for ch, e in all_notes]
        all_refs.sort(key=lambda x: (x[2][0], -x[0]))
        changed = 0
        for i in range(len(all_refs)):
            pr1, ch1, e1 = all_refs[i]
            for j in range(i + 1, len(all_refs)):
                pr2, ch2, e2 = all_refs[j]
                if e2[0] >= e1[0] + e1[1] - 0.05:
                    break
                ov = min(e1[0] + e1[1], e2[0] + e2[1]) - max(e1[0], e2[0])
                same_c = ch1 == ch2
                p1, p2 = int(e1[2]), int(e2[2])
                is_mud = (
                    same_c
                    and p1 < 54
                    and p2 < 54
                    and abs(p1 - p2) in (1, 2, 3, 4, 6)
                    and ov >= 0.05
                )
                is_b2 = abs(p1 - p2) % 12 == 1 and abs(p1 - p2) <= 13 and ov >= 0.22
                if not (is_mud or is_b2):
                    continue
                new_d1 = e2[0] - e1[0] + 0.02
                nxt1 = next_in_ch.get(id(e1))
                creates_abrupt = (
                    nxt1 is not None and (nxt1 - (e1[0] + new_d1)) > 0.35 and new_d1 < 1.52
                ) or (nxt1 is None and new_d1 < 1.82)
                if new_d1 >= 0.38 and not creates_abrupt:
                    e1[1] = new_d1
                    changed += 1
                else:
                    target_ch, target_e = (ch2, e2) if pr2 <= pr1 else (ch1, e1)
                    target_e[2] = _pick_consonant(
                        int(target_e[2]),
                        target_ch,
                        target_e[0],
                        target_e[0] + target_e[1],
                        all_notes,
                        target_e,
                    )
                    changed += 1
        if changed == 0:
            break

    # Step 5: Bridge any micro dead-air bin uncovered by damping
    grid2 = _build_grid()
    act2 = [idx for idx, v in enumerate(grid2) if v > 0]
    if act2:
        for idx_g in range(act2[0], act2[-1] + 1):
            if grid2[idx_g] == 0:
                t_g = idx_g / float(grid_hz)
                cands = [
                    (abs(t_g - (e[0] + e[1])), e)
                    for ch in pitched_chs
                    for e in result[ch]
                    if e[0] <= t_g
                ]
                cands.sort(key=lambda x: x[0])
                if cands:
                    cands[0][1][1] = max(cands[0][1][1], (t_g + 0.18) - cands[0][1][0])

    return result


def audit_ensemble_harmony(
    ch_events: dict[str, list[list[Any]]],
    total_sec: float | None = None,
    *,
    skip_channels: tuple[str, ...] | frozenset[str] = _DEFAULT_SKIP_CHANNELS,
    mono_channels: tuple[str, ...] | frozenset[str] = _DEFAULT_MONO_CHANNELS,
) -> dict[str, Any]:
    """Pre-render harmonic & continuity audit (shorts, mono overlaps, mud, b2/m9, dead-air)."""
    skip = {s.lower() for s in skip_channels}
    mono = {m.lower() for m in mono_channels}
    pitched = [
        ch
        for ch, evs in ch_events.items()
        if evs and ch.lower() not in skip and not ch.lower().endswith("_wav")
    ]
    if not pitched:
        return {
            "ok": True,
            "shorts": 0,
            "mono_overlaps": 0,
            "abrupt_phrase_ends": 0,
            "low_mud": 0,
            "b2_m9_clashes": 0,
            "dead_air_holes": 0,
        }
    if total_sec is None:
        total_sec = max(float(e[0]) + float(e[1]) for ch in pitched for e in ch_events[ch])

    shorts = mono_ov = abrupt = low_mud = b2_m9 = 0
    all_n: list[tuple[float, float, int, str]] = []
    for ch in pitched:
        evs = sorted(ch_events[ch], key=lambda x: (float(x[0]), int(x[2])))
        is_mono = ch.lower() in mono
        for i, e in enumerate(evs):
            t0, d, p = float(e[0]), float(e[1]), int(e[2])
            if d < 0.35:
                shorts += 1
            if i + 1 < len(evs):
                nt = float(evs[i + 1][0])
                if is_mono and nt - t0 > 0.03 and (t0 + d) - nt > 0.060:
                    mono_ov += 1
                if (nt - (t0 + d)) > 0.35 and d < 1.50:
                    abrupt += 1
            if p < 54:
                for j in range(i + 1, len(evs)):
                    nt = float(evs[j][0])
                    if nt >= t0 + d - 0.05:
                        break
                    np_ = int(evs[j][2])
                    if np_ < 54 and abs(np_ - p) in (1, 2, 3, 4, 6):
                        low_mud += 1
            all_n.append((t0, t0 + d, p, ch))

    all_n.sort()
    for i in range(len(all_n)):
        t0, t1, p1, _ = all_n[i]
        for j in range(i + 1, len(all_n)):
            s0, s1, p2, _ = all_n[j]
            if s0 >= t1 - 0.08:
                break
            if min(t1, s1) - max(t0, s0) >= 0.25 and abs(p1 - p2) % 12 == 1 and abs(p1 - p2) <= 13:
                b2_m9 += 1

    grid_hz = 20
    n_bins = max(1, int((total_sec + 10.0) * grid_hz))
    grid = [0] * n_bins
    for t0, t1, _, _ in all_n:
        for b in range(max(0, int(t0 * grid_hz)), min(n_bins, int(t1 * grid_hz))):
            grid[b] += 1
    act = [idx for idx, v in enumerate(grid) if v > 0]
    holes = sum(1 for v in grid[act[0] : act[-1] + 1] if v == 0) if act else 0
    return {
        "ok": not (shorts or mono_ov or abrupt or low_mud or b2_m9 or holes),
        "shorts": shorts,
        "mono_overlaps": mono_ov,
        "abrupt_phrase_ends": abrupt,
        "low_mud": low_mud,
        "b2_m9_clashes": b2_m9,
        "dead_air_holes": holes,
    }
