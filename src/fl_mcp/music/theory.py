"""Music theory helpers. Pitches are MIDI numbers with C4 = 60."""

import re

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
