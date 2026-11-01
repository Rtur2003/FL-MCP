"""Genre profiles: data only. Adding a genre never needs new code (tests cover every entry).

Bar patterns use the groove language (16 steps per bar). Progressions are Roman numerals
relative to the major scale of the key ('bVI' is the flat sixth, case = triad quality).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Bass:
    patterns: tuple[str, ...]  # onset grid; "follow" = play with the kick
    pitch: str = "root"  # root | root_oct | root_fifth
    max_len: float = 4.0  # steps
    slide: bool = False
    lo: int = 28
    hi: int = 50


@dataclass(frozen=True)
class Chords:
    patterns: tuple[str, ...]
    gate: float = 0.0  # steps; 0 = until the next onset
    per: int = 1  # bars per chord
    lo: int = 55
    hi: int = 77
    strum: int = 0  # max ticks between voices


@dataclass(frozen=True)
class Lead:
    cells: tuple[str, ...]  # one-bar rhythms
    lo: int = 64
    hi: int = 86
    ornament: bool = False  # grace notes (oriental styles)


@dataclass(frozen=True)
class Arp:
    pattern: str
    shape: str = "updown"  # up | down | updown | random
    lo: int = 60
    hi: int = 84


@dataclass(frozen=True)
class Sec:
    name: str
    kind: str  # intro | build | drop | break | verse | hook | bridge | outro
    bars: int
    intensity: float
    roles: tuple[str, ...]
    core: bool = True


@dataclass(frozen=True)
class Genre:
    key: str
    name: str
    bpm: tuple[int, int, int]
    swing: float
    progressions: dict[str, tuple[tuple[str, ...], ...]]
    mood_scale: dict[str, str]
    default_scale: str
    drums: dict[str, tuple[str, ...]]
    bass: Bass
    chords: Chords
    pad: Chords
    lead: Lead
    arp: Arp
    sections: tuple[Sec, ...]
    lufs: float
    sounds: dict[str, str]
    mix_over: dict[str, dict] = field(default_factory=dict)


MOODS = ("dark", "bright", "chill", "aggressive", "epic")

# role -> (peak_db, pan, reverb_db, delay_db, sidechain_db, hpf_hz, eq[[band, hz, db]])
BASE_MIX = {
    "kick": (-8.0, 0.0, None, None, 0.0, 25.0, [[0, 60, 1.5], [1, 350, -2.5]]),
    "snare": (-10.0, 0.0, -18.0, None, 0.0, 100.0, [[1, 200, 1.5], [2, 8000, 1.5]]),
    "clap": (-12.0, 0.0, -16.0, None, 0.0, 150.0, [[1, 1200, 1.0]]),
    "hat": (-20.0, 0.25, None, None, 0.0, 300.0, [[2, 9000, 1.0]]),
    "ohat": (-22.0, -0.2, -24.0, None, 0.0, 300.0, [[2, 9000, 1.0]]),
    "perc": (-20.0, 0.3, -22.0, -24.0, 0.0, 200.0, []),
    "bass": (-9.0, 0.0, None, None, -6.0, 30.0, [[1, 250, -2.0], [2, 2500, 1.0]]),
    "chords": (-16.0, 0.0, -20.0, None, -4.0, 120.0, [[0, 120, -3.0]]),
    "pad": (-20.0, 0.0, -14.0, None, -6.0, 150.0, [[0, 150, -4.0]]),
    "lead": (-14.0, 0.0, -18.0, -20.0, -2.0, 200.0, [[1, 3000, 1.5]]),
    "arp": (-18.0, -0.3, -22.0, -16.0, -3.0, 200.0, [[0, 150, -3.0]]),
    "fx": (-16.0, 0.0, -12.0, None, 0.0, 200.0, []),
}
ROLE_ORDER = (
    "kick",
    "snare",
    "clap",
    "hat",
    "ohat",
    "perc",
    "bass",
    "chords",
    "pad",
    "lead",
    "arp",
    "fx",
)
DRUM_ROLES = ("kick", "snare", "clap", "hat", "ohat", "perc")

_ELEC = ("kick", "clap", "hat", "ohat", "perc", "bass", "chords", "lead")
_SOUND = {
    "kick": "punchy kick, tuned to the key, short tail",
    "snare": "tight snare with short room",
    "clap": "layered clap, bright",
    "hat": "closed hat, short",
    "ohat": "open hat, medium decay",
    "perc": "shaker / rim / small percussion",
    "bass": "bass that matches the genre, mono below 120 Hz",
    "chords": "chord instrument fitting the genre",
    "pad": "wide sustained pad",
    "lead": "hook instrument, clear in the mids",
    "arp": "plucky arpeggiator sound",
    "fx": "riser / impact / sweep",
}


def _ln(*xs):
    return tuple(xs)


GENRE_LIST = (
    Genre(
        key="house",
        name="House",
        bpm=(120, 124, 128),
        swing=52,
        progressions={
            "minor": (
                ("i", "bVI", "bIII", "bVII"),
                ("i7", "iv7", "bVII", "bIII"),
                ("i", "bVII", "bVI", "bVII"),
            ),
            "dorian": (("i7", "IV", "i7", "IV"), ("i7", "ii7", "i7", "bVII")),
            "major": (("I", "V", "vi", "IV"), ("ii7", "V7", "Imaj7", "vi7")),
        },
        mood_scale={
            "dark": "minor",
            "bright": "major",
            "chill": "dorian",
            "aggressive": "minor",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X...X...X...X...",),
            "clap": ("....X.......X...", "....X.......X..,"),
            "hat": ("x,.,x,.,x,.,x,.,", "x,.,x,.,x,.,x,x,"),
            "ohat": ("..o...o...o...o.",),
            "perc": (".,..?..,.?..,..?",),
        },
        bass=Bass(("..x...x...x...x.", "..x...x...x..xx."), "root_oct", 3.0),
        chords=Chords(("x..x..x...x..x..", "X..x....X..x..x."), gate=2.0),
        pad=Chords(("X...............",), gate=16.0),
        lead=Lead(("x.....x.x.x.....", "..x...x...x.x...", "x..x..x...x.....")),
        arp=Arp("x.x.x.x.x.x.x.x."),
        sections=(
            Sec("Intro", "intro", 16, 0.4, ("kick", "hat", "perc")),
            Sec("Build", "build", 8, 0.65, ("kick", "clap", "hat", "perc", "chords", "fx")),
            Sec("Drop", "drop", 16, 1.0, _ELEC),
            Sec("Break", "break", 8, 0.35, ("pad", "chords", "arp", "perc"), False),
            Sec("Build 2", "build", 8, 0.7, ("kick", "clap", "hat", "perc", "pad", "fx"), False),
            Sec("Drop 2", "drop", 16, 1.0, _ELEC + ("arp",)),
            Sec("Outro", "outro", 16, 0.45, ("kick", "hat", "perc", "bass")),
        ),
        lufs=-8.0,
        sounds={
            **_SOUND,
            "bass": "round offbeat bass, short, sidechained",
            "chords": "classic organ or piano stabs",
            "lead": "pluck or soft saw hook",
        },
        mix_over={"hat": {"peak_db": -21.0}},
    ),
    Genre(
        key="techno",
        name="Techno",
        bpm=(125, 130, 140),
        swing=50,
        progressions={
            "minor": (("i", "i", "bII", "i"), ("i", "bVII", "i", "bVI")),
            "phrygian": (("i", "bII", "i", "bII"), ("i", "i", "bvii", "bII")),
        },
        mood_scale={
            "dark": "phrygian",
            "bright": "minor",
            "chill": "minor",
            "aggressive": "phrygian",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X...X...X...X...",),
            "clap": ("....x.......x...", "....x.......x..,"),
            "hat": ("x.x?x.x?x.x?x.x?", "x.x?x.xxx.x?x.x?"),
            "ohat": ("..o...o...o...o.",),
            "perc": (".?..,..?.,..?..,", "..?...?..?...?.."),
        },
        bass=Bass((".x.x.x.x.x.x.x.x", "..x...x...x...x."), "root_oct", 1.5),
        chords=Chords(("..x.....x.......", "..x...x........."), gate=1.5),
        pad=Chords(("X...............",), gate=16.0, per=2),
        lead=Lead(("x..x..x.........", "..x.x...x......."), lo=60, hi=79),
        arp=Arp("x.xxx.x.xx.xx.x.", "random", 60, 79),
        sections=(
            Sec("Intro", "intro", 16, 0.4, ("kick", "hat", "perc")),
            Sec("Groove", "drop", 16, 0.7, ("kick", "hat", "ohat", "perc", "bass")),
            Sec("Build", "build", 8, 0.8, ("kick", "clap", "hat", "perc", "bass", "fx")),
            Sec("Peak", "drop", 32, 1.0, _ELEC + ("arp",)),
            Sec("Break", "break", 16, 0.35, ("pad", "arp", "perc"), False),
            Sec("Peak 2", "drop", 16, 1.0, _ELEC + ("arp",), False),
            Sec("Outro", "outro", 16, 0.45, ("kick", "hat", "perc")),
        ),
        lufs=-8.0,
        sounds={
            **_SOUND,
            "kick": "long punchy techno kick with sub tail",
            "bass": "rumble or dark pulse bass",
            "chords": "dub chord stabs with delay",
            "lead": "sparse acid or metallic line",
        },
    ),
    Genre(
        key="trance",
        name="Trance",
        bpm=(132, 138, 145),
        swing=50,
        progressions={
            "minor": (
                ("i", "bVI", "bIII", "bVII"),
                ("i", "bVII", "bVI", "V"),
                ("bVI", "bVII", "i", "i"),
            ),
            "major": (("I", "V", "vi", "IV"), ("vi", "IV", "I", "V")),
        },
        mood_scale={
            "dark": "minor",
            "bright": "major",
            "chill": "major",
            "aggressive": "minor",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X...X...X...X...",),
            "clap": ("....x.......x...",),
            "hat": ("x?x?x?x?x?x?x?x?",),
            "ohat": ("..o...o...o...o.",),
            "perc": (".?..,..?.,..?..,",),
        },
        bass=Bass((".xxx.xxx.xxx.xxx", ".xxx.xxx.xxx.xxo"), "root_oct", 1.0),
        chords=Chords(("X...............",), gate=16.0),
        pad=Chords(("X...............",), gate=16.0),
        lead=Lead(("x..x..x.x.......", "x.x.x...x..x....", "X.......x..x..x."), lo=67, hi=91),
        arp=Arp("XoxoXoxoXoxoXoxo", "updown", 60, 84),
        sections=(
            Sec("Intro", "intro", 16, 0.4, ("kick", "hat", "perc")),
            Sec("Build", "build", 8, 0.7, ("kick", "clap", "hat", "bass", "arp", "fx")),
            Sec("Drop", "drop", 16, 1.0, _ELEC + ("arp",)),
            Sec("Breakdown", "break", 16, 0.4, ("pad", "chords", "lead", "arp"), False),
            Sec("Build 2", "build", 8, 0.85, ("kick", "clap", "hat", "bass", "arp", "fx")),
            Sec("Drop 2", "drop", 16, 1.0, _ELEC + ("arp", "pad")),
            Sec("Outro", "outro", 16, 0.45, ("kick", "hat", "bass")),
        ),
        lufs=-8.0,
        sounds={
            **_SOUND,
            "bass": "rolling saw bass, sidechained hard",
            "chords": "supersaw chords",
            "pad": "wide supersaw pad",
            "lead": "supersaw lead with delay",
            "arp": "bright pluck arp",
        },
        mix_over={"bass": {"sidechain_db": -9.0}, "chords": {"sidechain_db": -8.0}},
    ),
    Genre(
        key="dnb",
        name="Drum and Bass",
        bpm=(168, 174, 178),
        swing=50,
        progressions={
            "minor": (
                ("i", "bVI", "bIII", "bVII"),
                ("i", "bVII", "bVI", "bVII"),
                ("i7", "iv7", "bVI", "bVII"),
            ),
            "dorian": (("i7", "IV", "i7", "bVII"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "dorian",
            "chill": "dorian",
            "aggressive": "minor",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X.........x.....", "X.....x...x....."),
            "snare": ("....X.......X...", "....X..,....X..,"),
            "hat": ("x.x.x.x.x.x.x.x.", "x.xox.x.x.xox.x."),
            "perc": (".,..,..,.,..,..,",),
        },
        bass=Bass(("X.........x..x..", "X..x......x....."), "root_fifth", 6.0, lo=28, hi=46),
        chords=Chords(("X...............",), gate=16.0, per=2),
        pad=Chords(("X...............",), gate=32.0, per=2),
        lead=Lead(("x....x..x.......", "..x.....x..x...."), lo=62, hi=84),
        arp=Arp("x.x.x.x.x.x.x.x.", "up", 60, 84),
        sections=(
            Sec("Intro", "intro", 16, 0.4, ("hat", "perc", "pad")),
            Sec("Drop", "drop", 32, 1.0, ("kick", "snare", "hat", "perc", "bass", "pad")),
            Sec("Break", "break", 16, 0.35, ("pad", "chords", "arp"), False),
            Sec("Build", "build", 8, 0.8, ("snare", "hat", "pad", "fx"), False),
            Sec("Drop 2", "drop", 32, 1.0, ("kick", "snare", "hat", "perc", "bass", "pad", "lead")),
            Sec("Outro", "outro", 16, 0.4, ("kick", "snare", "hat", "pad")),
        ),
        lufs=-7.0,
        sounds={
            **_SOUND,
            "bass": "reese or neuro bass, mono sub layer below 100 Hz",
            "kick": "tight punchy kick",
            "snare": "snappy snare with crack",
        },
    ),
    Genre(
        key="dubstep",
        name="Dubstep",
        bpm=(138, 140, 142),
        swing=50,
        progressions={
            "minor": (("i", "i", "bVI", "bVII"), ("i", "bVI", "bVII", "i")),
            "phrygian": (("i", "bII", "i", "bVII"),),
        },
        mood_scale={
            "dark": "phrygian",
            "bright": "minor",
            "chill": "minor",
            "aggressive": "phrygian",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X.........x.....", "X.....x...x....."),
            "snare": ("........X.......",),
            "clap": ("........x.......",),
            "hat": ("x.x.x.x.x.x.x.x.", "x.x.x.x.x.x.xxx."),
            "perc": (".?..?..?.?..?..?",),
        },
        bass=Bass(
            ("X.......x.......", "X...............", "X.....x.X......."), "root", 8.0, lo=28, hi=46
        ),
        chords=Chords(("X...............",), gate=16.0),
        pad=Chords(("X...............",), gate=16.0, per=2),
        lead=Lead(("x..x....x.......", "x.....x.........")),
        arp=Arp("x.x.x.x.x.x.x.x.", "updown"),
        sections=(
            Sec("Intro", "intro", 16, 0.4, ("pad", "hat", "perc")),
            Sec("Build", "build", 8, 0.8, ("snare", "hat", "pad", "fx")),
            Sec("Drop", "drop", 16, 1.0, ("kick", "snare", "clap", "hat", "perc", "bass")),
            Sec("Break", "break", 8, 0.35, ("pad", "chords", "arp"), False),
            Sec("Build 2", "build", 8, 0.9, ("snare", "hat", "pad", "fx")),
            Sec(
                "Drop 2", "drop", 16, 1.0, ("kick", "snare", "clap", "hat", "perc", "bass", "lead")
            ),
            Sec("Outro", "outro", 8, 0.4, ("pad", "hat")),
        ),
        lufs=-6.0,
        sounds={
            **_SOUND,
            "bass": "wobble bass with LFO on the filter, mono sub underneath",
            "snare": "huge half-time snare",
            "kick": "short heavy kick",
        },
        mix_over={"bass": {"peak_db": -8.0}},
    ),
    Genre(
        key="trap",
        name="Trap",
        bpm=(130, 140, 160),
        swing=50,
        progressions={
            "minor": (
                ("i", "bVI", "bVII", "i"),
                ("i", "bVII", "bVI", "V"),
                ("i", "iv", "bVI", "bVII"),
            ),
            "harmonic_minor": (("i", "iv", "V", "i"), ("i", "bVI", "V", "i")),
            "phrygian": (("i", "bII", "i", "bVII"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "minor",
            "chill": "minor",
            "aggressive": "phrygian",
            "epic": "harmonic_minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X.....x...x.....", "X.......x.x.....", "X..x....x.....x."),
            "clap": ("........X.......",),
            "snare": ("........x.......",),
            "hat": ("x.x.x.x.x.x.x.x.", "x.x.x.xxx.x.x.x.", "x.x.x.x.x.x.xxxx"),
            "ohat": ("..............o.",),
            "perc": (".?....?....?...?",),
        },
        bass=Bass(("follow",), "root_oct", 12.0, True, 24, 44),
        chords=Chords(("x.......x.......", "x...x.......x..."), gate=4.0, per=1),
        pad=Chords(("X...............",), gate=16.0, per=2),
        lead=Lead(("x...x...x.......", "x.....x...x.....", "x..x....x......."), lo=69, hi=93),
        arp=Arp("x.x.x.x.x.x.x.x.", "random", 66, 90),
        sections=(
            Sec("Intro", "intro", 4, 0.35, ("lead", "pad")),
            Sec("Hook", "hook", 8, 1.0, ("kick", "clap", "hat", "ohat", "perc", "bass", "lead")),
            Sec(
                "Verse", "verse", 16, 0.8, ("kick", "clap", "hat", "ohat", "perc", "bass", "chords")
            ),
            Sec("Hook 2", "hook", 8, 1.0, ("kick", "clap", "hat", "ohat", "perc", "bass", "lead")),
            Sec(
                "Verse 2",
                "verse",
                16,
                0.85,
                ("kick", "clap", "hat", "ohat", "perc", "bass", "chords", "arp"),
                False,
            ),
            Sec(
                "Hook 3",
                "hook",
                8,
                1.0,
                ("kick", "clap", "hat", "ohat", "perc", "bass", "lead", "arp"),
                False,
            ),
            Sec("Outro", "outro", 4, 0.35, ("lead", "pad")),
        ),
        lufs=-8.0,
        sounds={
            **_SOUND,
            "bass": "808 sub with 150-400 ms tail and glide, tuned to the key",
            "kick": "short punchy kick that leaves room for the 808",
            "chords": "dark piano, bell or pluck",
            "lead": "bell, flute or pluck melody",
            "hat": "crisp closed hat with pitch/velocity variation",
        },
        mix_over={"bass": {"peak_db": -6.0, "sidechain_db": -3.0}, "hat": {"peak_db": -22.0}},
    ),
    Genre(
        key="hicaz_trap",
        name="Oriental trap (hicaz)",
        bpm=(130, 140, 150),
        swing=50,
        progressions={
            "hicaz": (
                ("I", "bII", "I", "bII"),
                ("I", "bII", "bvii", "I"),
                ("I", "bII", "bVI", "bII"),
            ),
        },
        mood_scale={m: "hicaz" for m in MOODS},
        default_scale="hicaz",
        drums={
            "kick": ("X.....x...x.....", "X.......x.x.....", "X..x....x.....x."),
            "clap": ("........X.......",),
            "hat": ("x.x.x.x.x.x.x.x.", "x.x.x.xxx.x.x.x.", "x.x.x.x.x.x.xxxx"),
            "ohat": ("..............o.",),
            "perc": (".?....?....?...?", "..?..?....?..?.."),
        },
        bass=Bass(("follow",), "root_oct", 12.0, True, 24, 44),
        chords=Chords(("x.......x.......",), gate=4.0),
        pad=Chords(("X...............",), gate=16.0, per=2),
        lead=Lead(
            ("x..x..x.x.......", "x.x...x...x.....", "x.....x.x.x....."),
            lo=64,
            hi=88,
            ornament=True,
        ),
        arp=Arp("x.x.x.x.x.x.x.x.", "updown", 60, 84),
        sections=(
            Sec("Intro", "intro", 4, 0.35, ("lead", "pad")),
            Sec("Hook", "hook", 8, 1.0, ("kick", "clap", "hat", "ohat", "perc", "bass", "lead")),
            Sec(
                "Verse", "verse", 16, 0.8, ("kick", "clap", "hat", "ohat", "perc", "bass", "chords")
            ),
            Sec("Hook 2", "hook", 8, 1.0, ("kick", "clap", "hat", "ohat", "perc", "bass", "lead")),
            Sec(
                "Verse 2",
                "verse",
                16,
                0.85,
                ("kick", "clap", "hat", "ohat", "perc", "bass", "chords", "arp"),
                False,
            ),
            Sec(
                "Hook 3",
                "hook",
                8,
                1.0,
                ("kick", "clap", "hat", "ohat", "perc", "bass", "lead", "arp"),
                False,
            ),
            Sec("Outro", "outro", 4, 0.35, ("lead", "pad")),
        ),
        lufs=-8.0,
        sounds={
            **_SOUND,
            "bass": "808 sub with glide",
            "lead": "saz, ney, zurna or bell sample line",
            "chords": "plucked oud or saz-like strings",
            "pad": "dark strings",
        },
        mix_over={"bass": {"peak_db": -6.0}},
    ),
    Genre(
        key="boom_bap",
        name="Boom bap",
        bpm=(84, 90, 96),
        swing=58,
        progressions={
            "minor": (("i7", "iv7", "i7", "bVII"), ("i7", "bVI", "bVII", "i7")),
            "dorian": (("i7", "IV", "i7", "bVII"),),
            "major": (("ii7", "V7", "Imaj7", "Imaj7"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "major",
            "chill": "dorian",
            "aggressive": "minor",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X.........x.....", "X.x.......x.....", "X.....x...x....."),
            "snare": ("....X.......X...", "....X..,....X..,"),
            "hat": ("x.x.x.x.x.x.x.x.", "x.xgx.x.x.xgx.x."),
            "ohat": ("......o.......o.",),
        },
        bass=Bass(("X.....x...x.....", "X.......x......."), "root_fifth", 4.0),
        chords=Chords(("X.....x.........", "X.......x.x....."), gate=0.0, per=1, strum=5),
        pad=Chords(("X...............",), gate=16.0, per=2),
        lead=Lead(("x..x....x.......", "x.....x...x....."), lo=60, hi=79),
        arp=Arp("x.x.x.x.x.x.x.x.", "up"),
        sections=(
            Sec("Intro", "intro", 4, 0.4, ("chords", "hat")),
            Sec("Verse", "verse", 8, 0.85, ("kick", "snare", "hat", "ohat", "bass", "chords")),
            Sec("Hook", "hook", 8, 1.0, ("kick", "snare", "hat", "ohat", "bass", "chords", "lead")),
            Sec(
                "Verse 2",
                "verse",
                8,
                0.85,
                ("kick", "snare", "hat", "ohat", "bass", "chords"),
                False,
            ),
            Sec(
                "Hook 2",
                "hook",
                8,
                1.0,
                ("kick", "snare", "hat", "ohat", "bass", "chords", "lead"),
                False,
            ),
            Sec("Outro", "outro", 4, 0.4, ("chords", "hat")),
        ),
        lufs=-10.0,
        sounds={
            **_SOUND,
            "kick": "dusty sampled kick",
            "snare": "crispy sampled snare, slightly late",
            "chords": "sampled Rhodes or jazz guitar",
            "bass": "upright or deep sine bass",
        },
    ),
    Genre(
        key="lofi",
        name="Lo-fi hip hop",
        bpm=(68, 76, 84),
        swing=60,
        progressions={
            "major": (
                ("ii7", "V7", "Imaj7", "vi7"),
                ("Imaj7", "vi7", "ii7", "V7"),
                ("Imaj7", "iii7", "vi7", "IVmaj7"),
            ),
            "dorian": (("i7", "IV", "i7", "bVII"),),
            "minor": (("i7", "iv7", "bVII", "bIII"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "major",
            "chill": "major",
            "aggressive": "minor",
            "epic": "dorian",
        },
        default_scale="major",
        drums={
            "kick": ("X.......x.x.....", "X.....x...x....."),
            "snare": ("....o.......o...", "....o..g....o..g"),
            "hat": ("x.g.x.g.x.g.x.g.", "o.g.x.g.o.g.x.g."),
            "perc": (".g..,..g.,..g...",),
        },
        bass=Bass(("X.......x.......", "X.....x.........", "X..........x...."), "root_fifth", 6.0),
        chords=Chords(("X.....x.........", "X.......x......."), gate=0.0, per=1, strum=7),
        pad=Chords(("X...............",), gate=16.0, per=2),
        lead=Lead(("x.....x...x.....", "..x.......x....."), lo=64, hi=84),
        arp=Arp("x...x...x...x...", "updown", 60, 79),
        sections=(
            Sec("Intro", "intro", 4, 0.4, ("chords", "perc")),
            Sec("A", "verse", 8, 0.8, ("kick", "snare", "hat", "perc", "bass", "chords")),
            Sec("B", "hook", 8, 1.0, ("kick", "snare", "hat", "perc", "bass", "chords", "lead")),
            Sec("A 2", "verse", 8, 0.8, ("kick", "snare", "hat", "perc", "bass", "chords"), False),
            Sec(
                "B 2",
                "hook",
                8,
                1.0,
                ("kick", "snare", "hat", "perc", "bass", "chords", "lead", "arp"),
                False,
            ),
            Sec("Outro", "outro", 4, 0.35, ("chords", "perc")),
        ),
        lufs=-13.0,
        sounds={
            **_SOUND,
            "chords": "warm Rhodes or soft piano with tape wobble",
            "kick": "soft dusty kick",
            "hat": "quiet vinyl-style hat",
            "bass": "warm sine or upright bass",
            "lead": "mellow piano, guitar or kalimba",
        },
        mix_over={
            "hat": {"peak_db": -24.0},
            "kick": {"peak_db": -10.0},
            "chords": {"peak_db": -14.0},
        },
    ),
    Genre(
        key="pop",
        name="Pop",
        bpm=(100, 112, 125),
        swing=52,
        progressions={
            "major": (("I", "V", "vi", "IV"), ("vi", "IV", "I", "V"), ("I", "vi", "IV", "V")),
            "minor": (("i", "bVI", "bIII", "bVII"), ("i", "bVII", "bVI", "bVII")),
            "mixolydian": (("I", "bVII", "IV", "I"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "major",
            "chill": "mixolydian",
            "aggressive": "minor",
            "epic": "major",
        },
        default_scale="major",
        drums={
            "kick": ("X...X...X...X...", "X.....x.X.....x."),
            "snare": ("....X.......X...",),
            "clap": ("....x.......x...",),
            "hat": ("x.x.x.x.x.x.x.x.", "x.xox.x.x.xox.xx"),
            "ohat": ("......o.......o.",),
            "perc": (".?..,..?.,..?..,",),
        },
        bass=Bass(("x.x.x.x.x.x.x.x.", "X.....x.X.x....."), "root_oct", 2.0),
        chords=Chords(("x..x..x.x..x..x.", "X.......X......."), gate=0.0),
        pad=Chords(("X...............",), gate=16.0),
        lead=Lead(("x..x..x...x.....", "x.x...x.x......."), lo=64, hi=86),
        arp=Arp("x.x.x.x.x.x.x.x.", "updown"),
        sections=(
            Sec("Intro", "intro", 8, 0.4, ("chords", "hat", "perc")),
            Sec("Verse", "verse", 16, 0.65, ("kick", "snare", "hat", "perc", "bass", "chords")),
            Sec(
                "Pre-chorus",
                "build",
                8,
                0.8,
                ("kick", "snare", "hat", "bass", "chords", "pad", "fx"),
                False,
            ),
            Sec(
                "Chorus",
                "hook",
                16,
                1.0,
                ("kick", "snare", "clap", "hat", "ohat", "perc", "bass", "chords", "pad", "lead"),
            ),
            Sec(
                "Verse 2",
                "verse",
                16,
                0.7,
                ("kick", "snare", "hat", "perc", "bass", "chords", "arp"),
                False,
            ),
            Sec(
                "Chorus 2",
                "hook",
                16,
                1.0,
                ("kick", "snare", "clap", "hat", "ohat", "perc", "bass", "chords", "pad", "lead"),
                False,
            ),
            Sec("Bridge", "bridge", 8, 0.5, ("pad", "chords", "lead", "perc"), False),
            Sec(
                "Final chorus",
                "hook",
                16,
                1.0,
                (
                    "kick",
                    "snare",
                    "clap",
                    "hat",
                    "ohat",
                    "perc",
                    "bass",
                    "chords",
                    "pad",
                    "lead",
                    "arp",
                ),
                False,
            ),
            Sec("Outro", "outro", 8, 0.4, ("chords", "pad")),
        ),
        lufs=-9.0,
        sounds={
            **_SOUND,
            "chords": "piano or clean electric guitar",
            "lead": "bright synth or vocal-like lead",
        },
    ),
    Genre(
        key="synthwave",
        name="Synthwave",
        bpm=(84, 100, 118),
        swing=50,
        progressions={
            "minor": (
                ("i", "bVI", "bIII", "bVII"),
                ("i", "bVII", "bVI", "bVII"),
                ("i", "bIII", "bVII", "bVI"),
            ),
            "major": (("I", "V", "vi", "IV"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "major",
            "chill": "minor",
            "aggressive": "minor",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X.......X.......", "X...X...X...X..."),
            "snare": ("....X.......X...",),
            "hat": ("x.x.x.x.x.x.x.x.", "x.xox.x.x.xox.x."),
            "perc": (".?..,..?.,..?..,",),
        },
        bass=Bass(("XoxoXoxoXoxoXoxo",), "root_oct", 1.0, lo=28, hi=48),
        chords=Chords(("X...............",), gate=16.0),
        pad=Chords(("X...............",), gate=16.0),
        lead=Lead(("x..x..x.x.......", "x.x.x...x..x....", "X.......x..x..x."), lo=64, hi=88),
        arp=Arp("XoxoXoxoXoxoXoxo", "updown", 60, 84),
        sections=(
            Sec("Intro", "intro", 8, 0.4, ("pad", "arp", "kick")),
            Sec("Verse", "verse", 16, 0.75, ("kick", "snare", "hat", "perc", "bass", "pad", "arp")),
            Sec(
                "Chorus",
                "hook",
                16,
                1.0,
                ("kick", "snare", "hat", "perc", "bass", "chords", "pad", "lead", "arp"),
            ),
            Sec(
                "Verse 2",
                "verse",
                16,
                0.8,
                ("kick", "snare", "hat", "perc", "bass", "pad", "arp"),
                False,
            ),
            Sec(
                "Chorus 2",
                "hook",
                16,
                1.0,
                ("kick", "snare", "hat", "perc", "bass", "chords", "pad", "lead", "arp"),
                False,
            ),
            Sec("Bridge", "bridge", 8, 0.5, ("pad", "arp", "lead"), False),
            Sec("Outro", "outro", 8, 0.4, ("pad", "arp")),
        ),
        lufs=-10.0,
        sounds={
            **_SOUND,
            "snare": "gated reverb snare",
            "bass": "analog saw bass, 16th pulse",
            "pad": "warm analog pad with chorus",
            "lead": "analog lead with portamento",
            "arp": "analog arp with delay",
        },
    ),
    Genre(
        key="reggaeton",
        name="Reggaeton",
        bpm=(88, 94, 100),
        swing=50,
        progressions={
            "minor": (
                ("i", "bVII", "bVI", "V"),
                ("i", "bVI", "bVII", "i"),
                ("i", "iv", "bVII", "bIII"),
            ),
            "phrygian": (("i", "bII", "i", "bVII"),),
        },
        mood_scale={
            "dark": "minor",
            "bright": "minor",
            "chill": "minor",
            "aggressive": "phrygian",
            "epic": "minor",
        },
        default_scale="minor",
        drums={
            "kick": ("X...X...X...X...",),
            "snare": ("...x..x....x..x.", "...x..x....x..xx"),
            "hat": ("x.x.x.x.x.x.x.x.", "x.xgx.x.x.xgx.x."),
            "perc": (".,.,.,.,.,.,.,.,",),
        },
        bass=Bass(("X..x....X..x..x.", "X..x....X......."), "root_oct", 5.0, True, 26, 46),
        chords=Chords(("x..x..x.........", "X..x....x..x...."), gate=2.0),
        pad=Chords(("X...............",), gate=16.0),
        lead=Lead(("x..x..x.x.......", "x.....x..x......"), lo=64, hi=86),
        arp=Arp("x.x.x.x.x.x.x.x.", "updown"),
        sections=(
            Sec("Intro", "intro", 8, 0.4, ("chords", "perc", "lead")),
            Sec("Verse", "verse", 16, 0.8, ("kick", "snare", "hat", "perc", "bass", "chords")),
            Sec(
                "Chorus",
                "hook",
                16,
                1.0,
                ("kick", "snare", "hat", "perc", "bass", "chords", "lead"),
            ),
            Sec(
                "Verse 2",
                "verse",
                16,
                0.85,
                ("kick", "snare", "hat", "perc", "bass", "chords", "arp"),
                False,
            ),
            Sec(
                "Chorus 2",
                "hook",
                16,
                1.0,
                ("kick", "snare", "hat", "perc", "bass", "chords", "lead", "arp"),
                False,
            ),
            Sec("Outro", "outro", 8, 0.4, ("chords", "perc")),
        ),
        lufs=-8.0,
        sounds={
            **_SOUND,
            "snare": "dembow rim or snare",
            "bass": "808 bass with short glide",
            "lead": "plucked synth or marimba hook",
            "chords": "latin guitar or piano stabs",
        },
        mix_over={"bass": {"peak_db": -6.0}},
    ),
)
GENRES: dict[str, Genre] = {g.key: g for g in GENRE_LIST}
