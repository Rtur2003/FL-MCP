"""Chord symbols -> pitch classes, rootless piano voicings and bass lines (pure Python).

parse("Ab7#11") -> (root_pc, intervals, slash_pc); voicing("Ebm9", prev) -> sorted MIDI list;
bass("F7alt", prev) -> MIDI bass note; walk(chords, next_chord, prev) -> walking-bass notes.
Grammar: root [A-G][b#]? + quality in `Q` + optional /bass.
"""

from __future__ import annotations

import itertools
import re

NN = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
PC = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4, "E#": 5, "F": 5,
    "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
    "B#": 0,
}  # fmt: skip
Q: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "m": (0, 3, 7),
    "maj7": (0, 4, 7, 11),
    "maj9": (0, 4, 7, 11, 14),
    "maj7#11": (0, 4, 7, 11, 18),
    "maj9#11": (0, 4, 7, 11, 14, 18),
    "6": (0, 4, 7, 9),
    "6/9": (0, 4, 7, 9, 14),
    "add9": (0, 4, 7, 14),
    "m6": (0, 3, 7, 9),
    "m6/9": (0, 3, 7, 9, 14),
    "m7": (0, 3, 7, 10),
    "m9": (0, 3, 7, 10, 14),
    "m11": (0, 3, 7, 10, 14, 17),
    "m(maj7)": (0, 3, 7, 11),
    "m(maj9)": (0, 3, 7, 11, 14),
    "m7b5": (0, 3, 6, 10),
    "7": (0, 4, 7, 10),
    "9": (0, 4, 7, 10, 14),
    "13": (0, 4, 10, 14, 21),
    "7b9": (0, 4, 7, 10, 13),
    "7#9": (0, 4, 7, 10, 15),
    "7alt": (0, 4, 10, 13, 15, 18, 20),
    "7#11": (0, 4, 10, 14, 18),
    "7sus4": (0, 5, 7, 10),
    "9sus4": (0, 5, 7, 10, 14),
    "7b13": (0, 4, 10, 14, 20),
    "dim7": (0, 3, 6, 9),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "aug": (0, 4, 8),
    "7b9#11": (0, 4, 10, 13, 18),
}
SEVENTH = (9, 10, 11)
_CHORD_RE = re.compile(r"^([A-G][b#]?)(.*?)(?:/([A-G][b#]?))?$")


def name(m: int) -> str:
    return f"{NN[m % 12]}{m // 12 - 1}"


def parse(sym: str) -> tuple[int, tuple[int, ...], int | None]:
    """Chord symbol -> (root pitch class, interval tuple, slash-bass pitch class or None)."""
    m = _CHORD_RE.match(str(sym).strip())
    if not m or m.group(2) not in Q:
        raise ValueError(f"cannot read chord {sym!r}")
    return PC[m.group(1)], Q[m.group(2)], (PC[m.group(3)] if m.group(3) else None)


def chord_pcs(sym: str) -> tuple[set[int], set[int]]:
    """(core, extended) pitch-class sets used for melody-vs-chord harmony audits."""
    r_pc, ivs, sl = parse(sym)
    core = {(r_pc + x) % 12 for x in ivs}
    if sl is not None:
        core.add(sl)
    ext = set(core) | {(r_pc + 2) % 12, (r_pc + 9) % 12}
    if 6 not in ivs and 8 not in ivs:
        ext.add((r_pc + 7) % 12)
    if 3 in ivs:
        ext.add((r_pc + 5) % 12)  # minor 11th
    if 4 in ivs:
        ext.add((r_pc + 6) % 12)  # Lydian #11
    return core, ext


def _guides(iv: tuple[int, ...]) -> tuple[int, int | None, list[int]]:
    third = next((i for i in iv if i in (3, 4, 5, 2)), iv[1] if len(iv) > 1 else 4)
    sev = next((i for i in iv if i in SEVENTH), None)
    colour = [i for i in iv if i not in (0, third, sev, 7) and i % 12 != 0]
    if sev is None:
        sev = 7 if 7 in iv else None
    return third, sev, colour


def voicing(
    sym: str, prev: list[int] | None = None, lo: int = 53, hi: int = 72, n: int = 4
) -> list[int]:
    """Rootless left-hand voicing inside [lo, hi]: guide tones + colours, smooth from `prev`."""
    r, iv, _sl = parse(sym)
    third, sev, colour = _guides(iv)
    want = [third] + ([sev] if sev is not None else []) + colour[: max(0, n - 2)]
    if len(want) < 3 and 7 in iv and 7 not in want:
        want.append(7)
    want = want[:n]
    pcs = list(dict.fromkeys((r + i) % 12 for i in want))
    cands = [[m for m in range(lo, hi + 1) if m % 12 == pc] for pc in pcs]
    best: tuple[float, list[int]] | None = None
    centre = (lo + hi) / 2.0
    for combo in itertools.product(*cands):
        v = sorted(combo)
        if len(set(v)) < len(v) or v[-1] - v[0] > 12:
            continue
        cost = 0.4 * abs(sum(v) / len(v) - centre)
        if prev:
            pv = sorted(prev)
            k = min(len(pv), len(v))
            cost += sum(abs(v[i] - pv[i]) for i in range(k)) + 2.0 * abs(len(v) - len(pv))
        gaps = zip(v, v[1:], strict=False)
        cost += sum(4.0 if b - a < 2 else (0.8 if b - a == 2 else 0.0) for a, b in gaps)
        if best is None or cost < best[0]:
            best = (cost, v)
    if best is None:
        raise ValueError(f"no voicing for {sym} in {lo}..{hi}")
    return best[1]


def bass(sym: str, prev: int | None = None, lo: int = 33, hi: int = 45) -> int:
    """MIDI bass note (slash bass honoured) nearest `prev` inside [lo, hi]."""
    r, _iv, sl = parse(sym)
    pc = sl if sl is not None else r
    cands = [m for m in range(lo, hi + 1) if m % 12 == pc]
    ref = prev if prev is not None else 36
    return min(cands, key=lambda m: abs(m - ref))


def _nearest(pc: int, ref: int, lo: int = 33, hi: int = 49) -> int:
    cands = [m for m in range(lo, hi + 1) if m % 12 == pc % 12]
    return min(cands, key=lambda m: abs(m - ref))


def walk(chords: list[str], nxt: str | None, prev: int | None = None) -> list[int]:
    """Quarter-note walking bass for one bar; the last note approaches the next root."""
    out: list[int] = []
    cur = prev
    for sym in chords:
        r, iv, _sl = parse(sym)
        b = bass(sym, cur)
        tones = [(r + i) % 12 for i in iv if i in (3, 4, 5, 7, 10, 11)]
        if len(chords) == 1:
            n2 = _nearest(tones[0], b + 3)
            n3 = _nearest(tones[1] if len(tones) > 1 else (r + 7) % 12, n2 + 2)
            out += [b, n2, n3]
        else:
            n2 = _nearest((r + (7 if 7 in iv else iv[1])) % 12, b + 2)
            out += [b, n2]
        cur = out[-1]
    tgt = bass(nxt, cur) if nxt else None
    if tgt is not None:
        ap = tgt + (1 if cur < tgt else -1) if abs(cur - tgt) > 1 else tgt - 1
        if len(chords) == 1:
            out.append(ap)
        else:
            out[-1] = ap
    elif len(chords) == 1:
        out.append(out[-1] - 2)
    return out
