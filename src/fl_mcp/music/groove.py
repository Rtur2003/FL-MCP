"""Rhythm helpers: a tiny pattern language, swing and humanisation.

One bar is 16 characters (16th notes). X accent, x hit, o soft, g ghost, ? 50% hit,
, 35% ghost, . rest.
"""

import random

PPQ = 96  # FL's default ticks per beat
STEP = 0.25  # beats per 16th
VEL = {"X": 118, "x": 100, "o": 78, "g": 46, "?": 98, ",": 46}
CHANCE = {"?": 0.5, ",": 0.35}


def check_bar(pattern: str) -> str:
    if len(pattern) != 16 or any(c not in "XxogX?,." for c in pattern):
        raise ValueError(f"bad bar pattern '{pattern}'")
    return pattern


def bar_hits(pattern: str, rng: random.Random, density: float = 1.0) -> list[tuple[int, int]]:
    """(step, velocity) for one bar; chance steps scale with `density` (0..1.5)."""
    out = []
    for step, c in enumerate(pattern):
        if c == ".":
            continue
        if c in CHANCE and rng.random() > min(1.0, CHANCE[c] * (0.5 + density)):
            continue
        out.append((step, VEL[c]))
    return out


def swing_delay(step: int, swing_pct: float) -> float:
    """Beats by which an off-16th is delayed; 50 = straight, 66 = triplet feel."""
    return (swing_pct / 100 - 0.5) * 0.5 if step % 2 else 0.0


def jitter_vel(v: int, rng: random.Random, spread: float = 5.0, lo: int = 1) -> int:
    return max(lo, min(127, round(v + rng.gauss(0, spread))))


def micro(rng: random.Random, max_ticks: int) -> float:
    """Small non-negative timing offset in beats (delays only, so starts never go negative)."""
    return rng.randint(0, max_ticks) / PPQ
