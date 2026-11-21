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
            for offset in range(0, len(tracks), 64):
                chunk = tracks[offset : offset + 64]
                for t, v in zip(chunk, call({"o": "peaks", "t": chunk})["p"], strict=True):
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
        "clipping": [t for t, v in result.items() if v["clips"]],
    }


def fix_mix(
    call: Call,
    seconds: float = 3.0,
    bar: float | None = None,
    plan_id: str | None = None,
    style: str | None = None,
) -> dict[str, Any]:
    """One-call automated cleanup: pan spread by role, mud cuts/HPF EQ, and clip protection."""
    from fl_mcp import chain, plugins_kb
    from fl_mcp.produce import detect_roles, mapping_for, rows, send

    mixer = rows(call, "mixer")
    chans = rows(call, "channels")
    names = {r[0]: r[1] for r in mixer}
    acoustic = chain._is_acoustic_project(chans, style)
    ch_names = [c[1] for c in chans]
    try:
        roles = mapping_for(plan_id or "", ch_names) if plan_id else detect_roles(ch_names)[0]
    except ValueError:
        roles = detect_roles(ch_names)[0]
    prefs = plugins_kb.fx_prefs()
    pan_pref = prefs.get("pan") if isinstance(prefs.get("pan"), dict) else {}
    default_pans = {
        "hat": -0.18,
        "ohat": 0.22,
        "perc": 0.28,
        "clap": -0.06,
        "chords": -0.12,
        "pad": 0.14,
        "arp": 0.2,
    }

    # Build (role, ch_idx) list covering primary mapped channels plus duplicate-role channels
    role_channels: list[tuple[str, int]] = list(roles.items())
    mapped_chs = {ch for _, ch in role_channels}
    for ch_idx, c in enumerate(chans):
        if ch_idx in mapped_chs:
            continue
        extra_role = next(iter(detect_roles([c[1]])[0]), "")
        if extra_role:
            role_channels.append((extra_role, ch_idx))

    ops: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "acoustic": acoustic,
        "panned": [],
        "eq": [],
        "attenuated": [],
        "warnings": [],
    }
    seen_tracks: set[int] = set()

    for role, ch in role_channels:
        if ch >= len(chans):
            continue
        track = int(chans[ch][7])
        if track == 0 and chans[ch][2] != "automation":
            summary["warnings"].append(f"{chans[ch][1]} (ch {ch}) goes straight to Master (fx=0)")
            continue
        if track in seen_tracks:
            continue
        seen_tracks.add(track)
        if acoustic:
            # Preserve natural acoustic/piano tone & stereo image; only run clip protection below.
            continue
        pan = pan_pref.get(role, default_pans.get(role))
        if pan is not None:
            p_val = max(-0.85, min(0.85, float(pan)))
            ops.append({"o": "set", "k": "mixer", "i": track, "p": {"pan": p_val}})
            summary["panned"].append([track, role, p_val])
        # Gentle 3-band built-in mixer EQ cleanup
        if role in (
            "snare",
            "clap",
            "hat",
            "ohat",
            "perc",
            "chords",
            "pad",
            "lead",
            "arp",
            "vocal",
        ):
            hpf = 200 if role in ("hat", "ohat") else 110
            ops.append({"o": "eq", "i": track, "b": 0, "f": hpf, "g": -4.0})
            ops.append({"o": "eq", "i": track, "b": 1, "f": 320, "g": -1.5})
            summary["eq"].append([track, role, f"low-shelf {hpf}Hz -4dB, mud 320Hz -1.5dB"])
        elif role == "bass":
            ops.append({"o": "eq", "i": track, "b": 1, "f": 260, "g": -1.5})
            summary["eq"].append([track, role, "mud 260Hz -1.5dB"])

    # Quick clip safety check if audio is playing
    used = [r[0] for r in mixer if r[0] > 0]
    if used:
        seen = meter(call, [0, *used], max(1.0, min(seconds, 4.0)), bar)
        for trk, v in seen.items():
            if v["clips"] or v["peak_db"] >= -0.3:
                delta = round(-max(1.5, v["peak_db"] + 1.5), 1)
                ops.append({"o": "gain", "t": "mixer", "i": trk, "d": max(-12.0, delta)})
                summary["attenuated"].append([trk, names.get(trk, f"Insert {trk}"), delta])

    bad = send(call, ops, "mix.fix") if ops else 0
    summary["applied_ops"] = len(ops)
    summary["not_kept"] = bad
    return summary


def setup_bus(
    call: Call,
    sources: list[int],
    bus_track: int,
    *,
    name: str | None = None,
    send_level: float = 0.35,
    replace_bus_send: bool = False,
) -> dict[str, Any]:
    """Route one or more source mixer inserts into a dedicated bus/send track cleanly."""
    from fl_mcp.produce import send

    if bus_track < 1:
        raise ValueError("bus_track must be >= 1")
    level = max(0.0, min(1.0, float(send_level)))
    ops: list[dict[str, Any]] = []
    if name:
        ops.append({"o": "set", "k": "mixer", "i": bus_track, "p": {"name": name}})
    routed: list[int] = []
    for src in sources:
        if src == bus_track or src < 1:
            continue
        ops.append({"o": "call", "c": [["mixer.setRouteTo", src, bus_track, True, True]]})
        ops.append({"o": "call", "c": [["mixer.setRouteToLevel", src, bus_track, level]]})
        if replace_bus_send:
            ops.append({"o": "call", "c": [["mixer.setRouteTo", src, 0, False, True]]})
        routed.append(src)
    if not routed:
        raise ValueError("setup_bus needs at least one source track >= 1 distinct from bus_track")
    ops.append({"o": "call", "c": [["mixer.afterRoutingChanged"]]})
    bad = send(call, ops, f"mix.bus->{bus_track}")
    return {
        "bus_track": bus_track,
        "name": name,
        "sources": routed,
        "send_level": level,
        "isolated": replace_bus_send,
        "not_kept": bad,
    }
