"""Plan: a complete, serialisable song description. Times are in beats (quarter notes)."""

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Ev:
    t: float  # start, beats from the pattern start
    d: float  # duration in beats
    p: int  # MIDI pitch, C4 = 60 (FL's piano roll labels 60 as C5)
    v: int  # velocity 1..127
    slide: bool = False  # 808-style glide into this note


@dataclass
class Part:
    role: str
    grid: bool  # one-shot / step-sequencer friendly (drums, percussion)
    events: list[Ev] = field(default_factory=list)


@dataclass
class Pattern:
    name: str
    bars: int
    parts: dict[str, Part] = field(default_factory=dict)


@dataclass
class Clip:
    pattern: int  # index into Plan.patterns
    bar: int  # 0-based start bar
    bars: int
    track: int  # playlist track, 1-based


@dataclass
class Section:
    name: str
    kind: str
    bar: int
    bars: int
    intensity: float
    roles: list[str]


@dataclass
class Mix:
    role: str
    peak_db: float  # target peak at the track meter (dBFS)
    pan: float = 0.0
    eq: list[list[float]] = field(default_factory=list)  # [band 0 low/1 mid/2 high, Hz, dB]
    hpf_hz: float = 0.0  # needs an EQ plugin with a high-pass; the built-in EQ only has shelves
    reverb_db: float | None = None  # send level
    delay_db: float | None = None
    sidechain_db: float = 0.0  # kick-driven ducking depth
    sound: str = ""


@dataclass
class Plan:
    id: str
    genre: str
    seed: int
    bpm: int
    swing: float  # MPC-style percentage, 50 = straight
    key: str
    tonic: int
    scale: str
    progression: list[str]
    roles: list[str]
    sections: list[Section]
    patterns: list[Pattern]
    clips: list[Clip]
    mix: dict[str, Mix]
    master: dict[str, float]
    notes: list[str] = field(default_factory=list)

    @property
    def bars(self) -> int:
        return max((c.bar + c.bars for c in self.clips), default=0)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Plan":
        patterns = [
            Pattern(
                p["name"],
                p["bars"],
                {
                    r: Part(x["role"], x["grid"], [Ev(**e) for e in x["events"]])
                    for r, x in p["parts"].items()
                },
            )
            for p in d["patterns"]
        ]
        return cls(
            **{
                **d,
                "sections": [Section(**s) for s in d["sections"]],
                "patterns": patterns,
                "clips": [Clip(**c) for c in d["clips"]],
                "mix": {r: Mix(**m) for r, m in d["mix"].items()},
            }
        )

    def summary(self) -> dict[str, Any]:
        """Compact view for the model: structure and counts, never the notes."""
        return {
            "id": self.id,
            "genre": self.genre,
            "bpm": self.bpm,
            "key": self.key,
            "swing": self.swing,
            "bars": self.bars,
            "seed": self.seed,
            "progression": self.progression,
            "sections": [[s.name, s.bar + 1, s.bars] for s in self.sections],
            "patterns": [
                [i, p.name, p.bars, {r: len(x.events) for r, x in p.parts.items()}]
                for i, p in enumerate(self.patterns)
            ],
            "roles": {r: m.sound for r, m in self.mix.items() if r in self.roles},
            "notes": self.notes,
        }
