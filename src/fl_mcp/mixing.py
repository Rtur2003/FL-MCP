"""Measurement-driven mixing: read the mixer's peak meters while the project plays."""

import math
import time
from collections.abc import Callable
from typing import Any

Call = Callable[[dict[str, Any]], Any]


def to_db(x: float) -> float:
    return round(20 * math.log10(max(x, 1e-6)), 1)


def meter(
    call: Call,
    tracks: list[int],
    seconds: float = 6.0,
    bar: float | None = None,
    interval: float = 0.04,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[int, dict[str, float]]:
    """Play (from `bar` if given), sample peaks of `tracks`, stop again if we started playback."""
    was_playing = call({"o": "status"})["play"]
    if bar is not None:
        call({"o": "tr", "a": "position", "v": bar})
    if not was_playing:
        call({"o": "tr", "a": "play"})
    peak = {t: 0.0 for t in tracks}
    active = dict.fromkeys(tracks, 0)
    clips = dict.fromkeys(tracks, 0)
    n = 0
    end = clock() + seconds
    try:
        while clock() < end:
            for t, v in zip(tracks, call({"o": "peaks", "t": tracks})["p"], strict=True):
                peak[t] = max(peak[t], v)
                active[t] += v > 0.001
                clips[t] += v > 1.0
            n += 1
            sleep(interval)
    finally:
        if not was_playing:
            call({"o": "tr", "a": "stop"})
    return {
        t: {"peak_db": to_db(peak[t]), "active": round(active[t] / max(n, 1), 2), "clips": clips[t]}
        for t in tracks
    }


def balance(
    call: Call,
    targets: dict[int, float],
    seconds: float = 6.0,
    bar: float | None = None,
    passes: int = 2,
    tolerance: float = 1.5,
    master_ceiling: float = -3.0,
    **kw: Any,
) -> dict[str, Any]:
    """Move faders so each peak lands on its target (dBFS); keep the master under the ceiling."""
    tracks = sorted({0, *targets})
    moved: dict[int, float] = {}
    skipped: dict[int, str] = {}
    limited: set[int] = set()
    result = meter(call, tracks, seconds, bar, **kw)
    for _ in range(passes):
        changed = False
        for t, target in targets.items():
            if result[t]["active"] < 0.05:
                skipped[t] = "silent in the measured window"
                continue
            delta = max(-12.0, min(12.0, target - result[t]["peak_db"]))
            if abs(delta) > tolerance:
                if call({"o": "gain", "t": "mixer", "i": t, "d": delta})["clamped"]:
                    limited.add(t)  # the fader ran out of travel
                moved[t] = round(moved.get(t, 0.0) + delta, 1)
                changed = True
        if not changed:
            break
        result = meter(call, tracks, seconds, bar, **kw)
    over = result[0]["peak_db"] - master_ceiling
    if over > 0:  # bring everything down together so the master has headroom
        for t in targets:
            if t not in skipped:
                call({"o": "gain", "t": "mixer", "i": t, "d": -over})
                moved[t] = round(moved.get(t, 0.0) - over, 1)
        result = meter(call, tracks, seconds, bar, **kw)
    return {
        "moved_db": moved,
        "skipped": skipped,
        "fader_limit": sorted(limited),
        "peaks_db": {t: result[t]["peak_db"] for t in tracks},
        "master_peak_db": result[0]["peak_db"],
        "clipping": [t for t in tracks if result[t]["clips"]],
    }
