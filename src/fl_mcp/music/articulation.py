"""Physically authentic instrument articulation for SoundFont playback inside FL Studio.

FL's SoundFont Player plays raw MIDI: no sustain pedal, no bow, no finger lift. Naive notes choke
plucked/struck tails, smear chords across bar lines, clash on monophonic winds and sound like a
piano pretending to be a guitar. This module ports the battle-tested D:\\FL articulation engine:

  * `apply_instrument_physics`: let-ring, chord-boundary pedal, monophonic legato, perc ring.
  * `solve_guitar6_voicing` / `pick_guitar6_section`: real 6-string fret solver with per-string
    ring and choke tracking.
  * `audit_melody_clashes`: melody notes vs active chord tones plus 9/11/13 extensions.

Events are `[start_sec, dur_sec, midi_pitch, velocity]` lists. Pure Python (no numpy).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Any

from fl_mcp.music import harm

GUITAR_OPEN = [40, 45, 50, 55, 59, 64]  # E2 A2 D3 G3 B3 E4
MODES = (
    "guitar6", "let_ring", "pedal_keys", "pedal_mel", "mono_wind", "mono_bow", "choir", "perc",
)  # fmt: skip
_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def solve_guitar6_voicing(
    sym: str, prev_strings: list[tuple[int, int]] | None = None
) -> list[tuple[int, int]]:
    """Playable 4-string acoustic voicing: `[(string_idx 0..5, midi_pitch)]` sorted low to high.

    Bass on E2/A2/D3 (fret <= 5, open strings preferred) plus three treble strings inside a
    4-fret hand box carrying 3rd, 7th/9th, 5th/root.
    """
    r_pc, ivs, sl = harm.parse(sym)
    b_pc = sl if sl is not None else r_pc
    chord_pcs = {(r_pc + x) % 12 for x in ivs}
    color_pcs = {(r_pc + x) % 12 for x in ivs if x in (2, 3, 4, 6, 9, 10, 11)}

    bass_cands: list[tuple[float, int, int, int]] = []
    for s in (0, 1, 2):
        fr = (b_pc - GUITAR_OPEN[s]) % 12
        if fr <= 5:
            score = (0 if fr == 0 else fr * 1.4) + (0.6 if s == 2 else 0.0)
            bass_cands.append((score, s, GUITAR_OPEN[s] + fr, fr))
    if not bass_cands:
        for s in (0, 1, 2):
            fr = (b_pc - GUITAR_OPEN[s]) % 12
            bass_cands.append((float(fr), s, GUITAR_OPEN[s] + fr, fr))
    bass_cands.sort()
    _, bs, bp, bfr = bass_cands[0]

    def string_opts(s: int) -> list[tuple[int, int]]:
        opts = []
        for fr in range(0, 8):
            p = GUITAR_OPEN[s] + fr
            if p % 12 in chord_pcs and p > bp + 2:
                opts.append((fr, p))
        return opts or [(0, GUITAR_OPEN[s])]

    best_voicing: list[tuple[int, int]] | None = None
    best_score = 1e9
    for tup in ((2, 3, 4), (3, 4, 5), (2, 3, 5), (2, 4, 5)):
        if tup[0] <= bs:
            continue
        o0, o1, o2 = string_opts(tup[0]), string_opts(tup[1]), string_opts(tup[2])
        for f0, p0 in o0:
            for f1, p1 in o1:
                if p1 <= p0:
                    continue
                for f2, p2 in o2:
                    if p2 <= p1:
                        continue
                    frets = [f for f in (bfr, f0, f1, f2) if f > 0]
                    span = (max(frets) - min(frets)) if frets else 0
                    if span > 4:
                        continue
                    pcs_hit = {bp % 12, p0 % 12, p1 % 12, p2 % 12}
                    n_color = len(pcs_hit & color_pcs)
                    n_open = sum(1 for f in (bfr, f0, f1, f2) if f == 0)
                    mud = 4.0 if (p0 - bp <= 4 and p0 < 52) or (p1 - p0 <= 2 and p1 < 57) else 0.0
                    vl = 0.0
                    if prev_strings:
                        prev_p = [pp for _, pp in prev_strings[1:]]
                        vl = sum(min(abs(px - pp) for pp in prev_p) for px in (p0, p1, p2)) * 0.12
                    sc = (
                        -len(pcs_hit) * 3.5
                        - n_color * 2.2
                        - n_open * 1.1
                        + span * 0.9
                        + (max(frets) if frets else 0) * 0.35
                        + mud
                        + vl
                    )
                    if sc < best_score:
                        best_score = sc
                        best_voicing = [(bs, bp), (tup[0], p0), (tup[1], p1), (tup[2], p2)]
    if best_voicing is None:
        v = list(harm.voicing(sym, None, lo=50, hi=67, n=3))
        while len(v) < 3:
            v.append(v[0] + 12)
        best_voicing = [(bs, bp), (3, v[0]), (4, v[1]), (5, v[2])]
    return best_voicing


def pick_guitar6_section(
    chords: Sequence[Sequence[str]],
    start_bar: int,
    bar_len: float,
    T: Callable[[float], float],
    style: str = "folk4",
    base_vel: float = 64.0,
    rng: random.Random | None = None,
    dyn_mult: float = 1.0,
    roll_phrase_starts: bool = True,
) -> list[list[float]]:
    """Fingerstyle guitar events with true per-string ring and chord-boundary release.

    `chords[i]` lists the chord symbols inside bar `start_bar + i` (1-based bars, `bar_len`
    time units per bar); `T(units_from_bar1)` maps musical position to seconds. A string rings
    until the same string is plucked again or the chord changes (18-22 ms early lift).
    """
    rng = rng or random.Random(101)
    raw: list[tuple[float, float, int, int, float]] = []  # t0, chord_end, string, pitch, vel
    prev_v: list[tuple[int, int]] | None = None
    for i, chs in enumerate(chords):
        b = start_bar + i
        u = bar_len / float(len(chs))
        for k, sym in enumerate(chs):
            voic = solve_guitar6_voicing(sym, prev_v)
            prev_v = voic
            o0 = (b - 1) * bar_len + k * u
            ch_end = T(o0 + u)

            if style == "strum_soft" or (roll_phrase_starts and i % 8 == 0 and k == 0 and u >= 6.0):
                for idx_s, (st, p) in enumerate(voic):
                    t0 = T(o0) + idx_s * 0.024 + rng.gauss(0, 0.004)
                    dv = 4.0 if idx_s == 0 else (-2.0 if idx_s < 3 else 1.5)
                    vel = _clip((base_vel + dv) * dyn_mult + rng.gauss(0, 1.8), 28, 92)
                    raw.append((max(0.0, t0), ch_end, st, p, vel))
                if style != "strum_soft" and u >= 6.0:
                    tail = (
                        [(1, 3.0, -4), (2, 4.0, -1), (3, 5.0, 1), (2, 6.0, -3)]
                        if u >= 7.5
                        else [(2, 3.0, -2), (3, 4.0, 0), (1, 5.0, -4)]
                    )
                    for ti, pos_e, dv in tail:
                        if pos_e < u - 0.1:
                            st, p = voic[ti % len(voic)]
                            t0 = T(o0 + pos_e) + rng.gauss(0, 0.006)
                            vel = _clip((base_vel + dv) * dyn_mult + rng.gauss(0, 1.8), 26, 88)
                            raw.append((max(0.0, t0), ch_end, st, p, vel))
                continue

            if style == "folk4" and u >= 7.5:
                seq = [
                    (0, 0.0, 4.5, -0.007), (1, 1.0, -4.0, 0.003), (2, 2.0, -1.0, 0.0),
                    (3, 3.0, 1.5, 0.004), (1, 4.0, -2.5, -0.003), (2, 5.0, -3.5, 0.004),
                    (3, 6.0, 0.5, 0.0), (2, 7.0, -4.5, 0.005),
                ]  # fmt: skip
            elif style == "folk4" and u >= 3.5:
                seq = [
                    (0, 0.0, 4.5, -0.006), (1, 1.0, -3.5, 0.002), (2, 2.0, -1.0, 0.0),
                    (3, 3.0, 1.5, 0.004),
                ]  # fmt: skip
            elif style in ("waltz3", "barc6") and u >= 5.5:
                seq = [
                    (0, 0.0, 4.5, -0.007), (1, 1.0, -3.5, 0.003), (2, 2.0, -1.0, 0.0),
                    (3, 3.0, 2.0, 0.002), (2, 4.0, -2.5, 0.004), (1, 5.0, -4.5, 0.005),
                ]  # fmt: skip
            elif style in ("waltz3", "barc6") and u >= 2.5:
                seq = [(0, 0.0, 4.5, -0.006), (2, 1.0, -2.0, 0.002), (3, 2.0, 0.5, 0.004)]
            else:
                n_st = max(2, round(u))
                pat = [0, 1, 2, 3, 2, 1, 3, 2, 1, 2]
                seq = [
                    (
                        pat[s % len(pat)],
                        float(s),
                        4.0 if s == 0 else (-2.0 if s % 2 else 0.0),
                        -0.006 if s == 0 else 0.003,
                    )
                    for s in range(n_st)
                ]
            for ti, pos_e, dv, dt_micro in seq:
                if pos_e >= u - 1e-6:
                    continue
                st, p = voic[ti % len(voic)]
                t0 = T(o0 + pos_e) + dt_micro + rng.gauss(0, 0.006)
                vel = _clip((base_vel + dv) * dyn_mult + rng.gauss(0, 1.8), 26, 92)
                raw.append((max(0.0, t0), ch_end, st, p, vel))

    raw.sort(key=lambda x: (x[0], x[2]))
    by_str: dict[int, list[tuple[float, int]]] = {}
    for idx, row in enumerate(raw):
        by_str.setdefault(row[2], []).append((row[0], idx))
    evs: list[list[float]] = []
    for idx, (t0, ch_end, st, p, vel) in enumerate(raw):
        end = max(t0 + 0.55, ch_end - 0.022)
        nxt_same = [nt for nt, ni in by_str[st] if ni > idx and nt > t0 + 0.03]
        if nxt_same:
            end = min(end, nxt_same[0] - 0.018)
            if nxt_same[0] <= ch_end + 0.05:
                end = max(end, nxt_same[0] - 0.018)
        evs.append([round(t0, 4), round(max(0.18, min(end - t0, 4.8)), 4), int(p), round(vel, 1)])
    return evs


def chord_slots(blocks: Sequence[Any]) -> list[tuple[float, float, str]]:
    """Expand `(chords_list, start_bar, bar_len, T_fn)` blocks into `(t0, t1, symbol)` slots."""
    slots: list[tuple[float, float, str]] = []
    for chs_list, sb, bl, T in blocks:
        for i, chs in enumerate(chs_list):
            b = sb + i
            u = bl / float(len(chs))
            for k, sym in enumerate(chs):
                slots.append(
                    (float(T((b - 1) * bl + k * u)), float(T((b - 1) * bl + (k + 1) * u)), sym)
                )
    return slots


def extract_chord_boundaries(chords_blocks: Sequence[Any]) -> list[float]:
    """Sorted chord-change times (seconds) from `(chords_list, start_bar, bar_len, T_fn)` blocks."""
    bounds: list[float] = []
    for chs_list, sb, bl, T in chords_blocks:
        for i, chs in enumerate(chs_list):
            b = sb + i
            u = bl / float(len(chs))
            for k in range(len(chs)):
                bounds.append(float(T((b - 1) * bl + k * u)))
        bounds.append(float(T((sb + len(chs_list) - 1) * bl)))
    return sorted({round(x, 4) for x in bounds})


def slot_boundaries(slots: Sequence[tuple[float, float, str]]) -> list[float]:
    """Chord-change times from `(t0, t1, symbol)` slots (every start plus the final end)."""
    if not slots:
        return []
    marks = {round(float(a), 4) for a, _b, _s in slots}
    marks.add(round(float(max(b for _a, b, _s in slots)), 4))
    return sorted(marks)


def apply_instrument_physics(
    events: Sequence[Sequence[Any]],
    mode: str,
    chord_bounds: Sequence[float] | None = None,
    attack_pre_ms: float = 0.0,
    pitch_lo: int = 21,
    pitch_hi: int = 108,
    min_ring: float = 2.8,
) -> list[list[Any]]:
    """Apply acoustic physics to one channel's `[t, d, pitch, vel]` events.

    Modes: guitar6 (range clamp only; strings already solved), let_ring (harp, celesta, music
    box, kalimba, glock, timpani, marimba, pizzicato), pedal_keys (chord-boundary sustain pedal),
    pedal_mel (singing keyboard line, 55 ms legato), mono_wind / mono_bow (strictly monophonic,
    38 / 48 ms joins), choir (chord-aligned crossfades) and perc (generous body ring).
    """
    if mode not in MODES:
        raise ValueError(f"unknown instrument mode '{mode}'; use one of {', '.join(MODES)}")
    if not events:
        return []
    pre_s = attack_pre_ms * 0.001
    ev: list[list[Any]] = []
    for row in events:
        t, d, p, v = row[:4]
        p = int(p)
        while p < pitch_lo:
            p += 12
        while p > pitch_hi:
            p -= 12
        vel = _clip(float(v), 18.0, 114.0)
        ev.append([max(0.0, float(t) - pre_s), max(0.06, float(d)), p, vel])
    ev.sort(key=lambda x: (x[0], x[2]))

    if mode == "perc":
        for i, e in enumerate(ev):
            e[1] = max(e[1], 0.55 if e[2] >= 60 else 1.85)
            if i + 1 < len(ev) and (ev[i + 1][0] - (e[0] + e[1])) > 0.35:
                e[1] = min(ev[i + 1][0] - e[0] - 0.04, max(e[1], 1.65))
            elif i + 1 == len(ev):
                e[1] = max(e[1], 2.0)
        return ev

    by_p_merge: dict[int, list[Any]] = {}
    merged_all: list[list[Any]] = []
    for e in ev:
        prev = by_p_merge.get(e[2])
        if prev is not None and (e[0] - prev[0]) < 0.38:
            prev[1] = max(prev[1], (e[0] + e[1]) - prev[0], 0.45)
            prev[3] = max(prev[3], e[3])
        else:
            by_p_merge[e[2]] = e
            merged_all.append(e)
    ev = sorted(merged_all, key=lambda x: (x[0], x[2]))

    if mode == "guitar6":
        for i, e in enumerate(ev):
            e[1] = max(0.38, e[1])
            if i + 1 < len(ev) and (ev[i + 1][0] - (e[0] + e[1])) > 0.35:
                e[1] = min(ev[i + 1][0] - e[0] - 0.05, max(e[1], 1.65))
            elif i + 1 == len(ev):
                e[1] = max(e[1], 2.20)
        return ev

    cb = sorted(chord_bounds) if chord_bounds else []

    def next_cb(t_now: float) -> float | None:
        for b in cb:
            if b > t_now + 0.08:
                return b
        return None

    by_p: dict[int, list[float]] = {}
    for e in ev:
        by_p.setdefault(e[2], []).append(e[0])

    if mode == "let_ring":
        for i, e in enumerate(ev):
            t, d, p, _v = e
            end = max(t + d, t + min_ring)
            if cb and min_ring >= 2.5:
                nc = next_cb(t)
                if nc is not None and nc - t >= 0.40:
                    end = min(end, nc - 0.025)
            nx = [o for o in by_p[p] if o > t + 0.02]
            if nx:
                end = min(end, nx[0] - 0.015)
            if i + 1 < len(ev) and (ev[i + 1][0] - end) > 0.35:
                end = min(ev[i + 1][0] - 0.05, max(end, t + 1.65))
            elif i + 1 == len(ev):
                end = max(end, t + 2.20)
            e[1] = max(0.36, min(end - t, 6.5))
        return ev

    if mode in ("pedal_keys", "pedal_mel"):
        ons = sorted({round(e[0], 4) for e in ev})
        for i, e in enumerate(ev):
            t, d, p, _v = e
            end = max(t + d, t + 0.42)
            if mode == "pedal_keys":
                nc = next_cb(t)
                if nc is not None and 0.42 <= (nc - t) <= 6.5:
                    end = max(end, nc - 0.028)
                else:
                    end = max(end, t + 1.8)
            else:
                nxt = next((o for o in ons if o > t + 0.04), None)
                if nxt is not None and (nxt - end) <= 0.32:
                    end = max(end, nxt + 0.055)
                if d < 1.6:
                    nc = next_cb(t)
                    smeared = nc is not None and end > nc + 0.10 and (t + d) <= nc + 0.04
                    if smeared and nc is not None and nc - t >= 0.42:
                        end = nc - 0.020
            nx = [o for o in by_p[p] if o > t + 0.02]
            if nx:
                end = min(end, nx[0] - 0.012)
            if i + 1 < len(ev) and (ev[i + 1][0] - end) > 0.35:
                end = min(ev[i + 1][0] - 0.05, max(end, t + 1.65))
            elif i + 1 == len(ev):
                end = max(end, t + 2.20)
            e[1] = max(0.36, min(end - t, 8.0))
        return ev

    if mode in ("mono_wind", "mono_bow"):
        join = 0.038 if mode == "mono_wind" else 0.048
        merged: list[list[Any]] = []
        for e in ev:
            if merged and e[2] == merged[-1][2] and (e[0] - merged[-1][0]) < 0.36:
                merged[-1][1] = max(merged[-1][1], (e[0] + e[1]) - merged[-1][0])
                merged[-1][3] = max(merged[-1][3], e[3])
            else:
                merged.append(e)
        clusters: list[list[list[Any]]] = []
        for e in merged:
            if clusters and abs(e[0] - clusters[-1][0][0]) <= 0.025:
                clusters[-1].append(e)
            else:
                clusters.append([e])
        for ci, cl in enumerate(clusters):
            nxt_t = clusters[ci + 1][0][0] if ci + 1 < len(clusters) else None
            for e in cl:
                t, d, p, _v = e
                end = max(t + d, t + 0.42)
                if nxt_t is not None:
                    gap = nxt_t - end
                    if gap <= 0.36 or end > nxt_t + join:
                        end = nxt_t + join
                    else:
                        end = min(nxt_t - 0.12, max(end, t + 1.65))
                    if any(ne[2] == p for ne in clusters[ci + 1]):
                        end = min(end, nxt_t - 0.018)
                else:
                    end = max(end, t + 2.20)
                e[1] = max(0.36, min(end - t, 7.5))
        return merged

    # choir
    for e in ev:
        t, d, _p, _v = e
        nc = next_cb(t)
        end = max(t + d, (nc - 0.02) if (nc and nc - t <= 7.0) else t + d)
        e[1] = max(0.4, min(end - t, 8.0))
    return ev


def audit_melody_clashes(
    mel_events: Sequence[Sequence[Any]],
    slots: Sequence[tuple[float, float, str]],
    pre_ms: float = 0.0,
) -> list[tuple[float, str, str]]:
    """Melody notes (>= 0.30 s) outside the active chord's tones and 9/11/13 extensions.

    `slots` are `(t0, t1, chord_symbol)`. Each note is judged at its musical beat
    (`t + pre_ms + 40 ms`) so an attack pre-roll never crosses into the previous bar.
    Returns `(time_s, note_name, chord)` per clash.
    """
    parsed = [(float(a), float(b), s, harm.chord_pcs(s)[1]) for a, b, s in slots]
    pre_s = pre_ms * 0.001
    clashes: list[tuple[float, str, str]] = []
    for row in mel_events:
        t, d, p = float(row[0]), float(row[1]), int(row[2])
        if d < 0.30:
            continue
        t_eval = t + pre_s + 0.04
        for t0, t1, sym, ext in parsed:
            if t0 <= t_eval < t1:
                if p % 12 not in ext:
                    clashes.append((round(t_eval, 2), f"{_NAMES[p % 12]}{p // 12 - 1}", sym))
                break
    return clashes
