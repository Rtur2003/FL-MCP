"""Single-channel FL Studio stem calibrator (CAL_STEMS_V2 workflow).

FluidSynth and FL Studio's Fruity Soundfont Player differ by 12-16 dB across SoundFonts, and
different libraries have wildly different attack times and note-off release drops. This module:
  1. Builds a 1-note-per-channel calibration `.flp` at unity mixer faders & 0 dB channel level.
  2. Renders it headlessly through FL Studio (`render.render`).
  3. Measures each channel's true FL `body_rms_db`, attack latency (`atk_ms`), note-off drop
     (`drop_at_off_db`), `+0.5s`/`+1.5s` tail energy, flags `SLAM_CUT` / `DRY_TAIL`, and computes
     the exact channel level adjustment (`recommended_level_db = target_eff_db - body_rms_db`).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from fl_mcp import audio, flp, render
from fl_mcp.flp import (
    CHAN_COLOR,
    CHAN_LEVELS,
    CHAN_NAME,
    CHAN_ROUTE,
    CHAN_ROUTE_16,
    NEW_CHAN,
    PLUGIN_NAME,
    PLUGIN_PARAMS,
    Event,
    Flp,
    FlpError,
    _text,
    _utf16,
    note_record,
    patch_soundfont_blob,
)


def build_calibration_flp(
    template: str | Path,
    channel_specs: list[dict[str, Any]],
    out_flp: str | Path,
    *,
    note_s: float = 1.0,
    tail_s: float = 5.0,
    velocity: int = 75,
    bpm: float = 120.0,
) -> dict[str, Any]:
    """Create a sequential 1-note-per-channel FL Studio calibration `.flp` at unity levels."""
    if not channel_specs:
        raise FlpError("calibration needs at least one channel spec")
    proj = Flp.open(template)
    proj.set_tempo(bpm)
    ppq = proj.ppq
    ticks_per_sec = ppq * (bpm / 60.0)
    step_s = float(note_s) + float(tail_s)

    spans = proj._channel_spans()
    sf_seed = next(
        (
            c.iid
            for c in proj.channels()
            if "soundfont"
            in next(
                (
                    _text(ev.data).lower()
                    for ev in proj.events[spans[c.iid][0] : spans[c.iid][1]]
                    if ev.id == PLUGIN_NAME
                ),
                "",
            )
        ),
        None,
    )
    if sf_seed is None:
        raise FlpError("calibration template needs a SoundFont Player channel to clone")

    seed_events = proj.events[spans[sf_seed][0] : spans[sf_seed][1]]
    first_pos = min(s for s, _ in spans.values())
    for s, e in sorted(spans.values(), key=lambda pair: pair[0], reverse=True):
        del proj.events[s:e]

    new_events: list[Event] = []
    timeline: list[dict[str, Any]] = []
    notes: list[bytes] = []
    mixer_setup: list[tuple[int, str]] = []

    for idx, spec in enumerate(channel_specs):
        name = str(spec.get("name") or f"Ch{idx + 1}")
        sf2_path = str(spec["sf2"])
        insert = int(spec.get("insert", idx + 1))
        pitch = int(spec.get("pitch", 60))
        vel = max(1, min(127, int(spec.get("velocity", velocity))))
        sf_rel = spec.get("sf2_release")

        block = [Event(e.id, e.data) for e in seed_events]
        block[0] = Event(NEW_CHAN, idx.to_bytes(2, "little"))

        def _set(eid: int, payload: bytes, blk: list[Event] = block) -> None:
            for k, ev in enumerate(blk):
                if ev.id == eid:
                    blk[k] = Event(eid, payload)
                    return
            blk.append(Event(eid, payload))

        _set(CHAN_NAME, _utf16(name))
        _set(CHAN_COLOR, bytes([110, 150, 210, 0]))
        for ev in block:
            if ev.id == PLUGIN_PARAMS:
                ev.data = patch_soundfont_blob(ev.data, sf2_path, release=sf_rel)
                ev.raw = None
            elif ev.id == CHAN_LEVELS and len(ev.data) >= 8:
                # Unity 0 dB channel level (10000) and center pan (6400)
                buf = bytearray(ev.data)
                buf[0:4] = (6400).to_bytes(4, "little")
                buf[4:8] = (10000).to_bytes(4, "little")
                ev.data, ev.raw = bytes(buf), None
        if insert > 255 or any(ev.id == CHAN_ROUTE_16 for ev in block):
            block[:] = [ev for ev in block if ev.id != CHAN_ROUTE]
            _set(CHAN_ROUTE_16, insert.to_bytes(2, "little"))
        else:
            _set(CHAN_ROUTE, bytes([insert]))
        new_events.extend(block)
        mixer_setup.append((insert, name))

        start_s = 0.5 + idx * step_s
        t_tick = int(round(start_s * ticks_per_sec))
        d_tick = max(1, int(round(note_s * ticks_per_sec)))
        notes.append(note_record(t_tick, d_tick, pitch, idx, vel))
        timeline.append(
            {
                "index": idx,
                "name": name,
                "sf2": sf2_path,
                "insert": insert,
                "pitch": pitch,
                "velocity": vel,
                "start_s": round(start_s, 3),
                "note_s": float(note_s),
                "tail_s": float(tail_s),
            }
        )

    proj.events[first_pos:first_pos] = new_events
    proj.header = proj.header[:10] + len(channel_specs).to_bytes(2, "little") + proj.header[12:]
    for insert, name in mixer_setup:
        proj.set_mixer_fader(insert, 0.0)
        proj.set_mixer_pan(insert, 0.0)
        proj.rename_insert(insert, name)

    total_ticks = int(round((0.5 + len(channel_specs) * step_s + 1.0) * ticks_per_sec))
    proj.put_pattern(1, notes, "Calibration", force_synthesize=True)
    proj.set_playlist([(0, 1, total_ticks, 1)], mode="replace", synthesize=True)
    proj.set_end_marker(total_ticks, create=True)

    out = Path(out_flp).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(proj.dumps())
    return {
        "flp": str(out),
        "bpm": bpm,
        "ppq": ppq,
        "channels": timeline,
        "total_s": round(0.5 + len(channel_specs) * step_s + 1.0, 2),
    }


def analyze_calibration_wav(
    wav_path: str | Path,
    timeline: list[dict[str, Any]],
    *,
    target_eff_db: float = -38.0,
) -> dict[str, Any]:
    """Measure true FL Studio bodyRMS, attack latency, note-off drop & tail per channel."""
    np, _, _ = audio._libs()
    x, fs = audio.load(wav_path)
    mono = x.mean(axis=1).astype("float64")

    def _rms_db(t0: float, t1: float) -> float:
        a = max(0, min(len(mono), int(round(t0 * fs))))
        b = max(a + 1, min(len(mono), int(round(t1 * fs))))
        seg = mono[a:b]
        if len(seg) == 0:
            return -120.0
        val = float(np.sqrt(np.mean(seg * seg)))
        return round(20.0 * math.log10(max(val, 1e-6)), 2)

    rows: list[dict[str, Any]] = []
    for slot in timeline:
        s0 = float(slot["start_s"])
        dur = float(slot["note_s"])
        body_db = _rms_db(s0 + 0.15 * dur, s0 + 0.85 * dur)
        pre_off_db = _rms_db(s0 + max(0.0, dur - 0.15), s0 + dur)
        post_off_db = _rms_db(s0 + dur + 0.05, s0 + dur + 0.20)
        tail_05_db = _rms_db(s0 + dur + 0.40, s0 + dur + 0.65)
        tail_15_db = _rms_db(s0 + dur + 1.35, s0 + dur + 1.65)
        drop_at_off = round(pre_off_db - post_off_db, 1)

        # Measure attack latency (time from note-on to 50% of local peak envelope)
        a_win = mono[int(round(s0 * fs)) : min(len(mono), int(round((s0 + dur) * fs)))]
        atk_ms: float | None = None
        if len(a_win) >= int(0.05 * fs):
            env = np.abs(a_win)
            pk = float(np.max(env))
            if pk > 1e-5:
                hits = np.flatnonzero(env >= 0.5 * pk)
                if len(hits):
                    atk_ms = round(int(hits[0]) * 1000.0 / fs, 1)

        flags: list[str] = []
        if body_db < -90.0:
            flags.append("SILENT")
        else:
            if drop_at_off >= 14.0 and pre_off_db > -55.0:
                flags.append("SLAM_CUT")
            if tail_05_db < body_db - 35.0:
                flags.append("DRY_TAIL")
        status = "OK" if not flags else ",".join(flags)
        rec_db = None if body_db < -90.0 else round(float(target_eff_db) - body_db, 1)

        rows.append(
            {
                "name": slot["name"],
                "insert": slot["insert"],
                "pitch": slot["pitch"],
                "body_rms_db": body_db,
                "atk_ms": atk_ms,
                "drop_at_off_db": drop_at_off,
                "tail_05s_db": tail_05_db,
                "tail_15s_db": tail_15_db,
                "recommended_level_db": rec_db,
                "status": status,
            }
        )

    return {
        "wav": str(Path(wav_path)),
        "target_eff_db": target_eff_db,
        "all_ok": all(r["status"] == "OK" for r in rows),
        "channels": rows,
    }


def calibrate_stems(
    template: str | Path,
    channel_specs: list[dict[str, Any]],
    *,
    workdir: str | Path | None = None,
    note_s: float = 1.0,
    tail_s: float = 5.0,
    velocity: int = 75,
    target_eff_db: float = -38.0,
) -> dict[str, Any]:
    """Build, headlessly render in FL Studio, and measure a full single-channel calibration set."""
    from fl_mcp import paths

    base = Path(workdir).expanduser().resolve() if workdir else paths.data_dir() / "calibration"
    base.mkdir(parents=True, exist_ok=True)
    cal_flp = base / "calibration.flp"
    built = build_calibration_flp(
        template, channel_specs, cal_flp, note_s=note_s, tail_s=tail_s, velocity=velocity
    )
    rendered = render.render(cal_flp, fmt="wav", out_dir=base, always_copy=True, keep=False)
    report = analyze_calibration_wav(
        rendered["audio"], built["channels"], target_eff_db=target_eff_db
    )
    return {**report, "flp": built["flp"], "render": rendered}


__all__ = ["analyze_calibration_wav", "build_calibration_flp", "calibrate_stems", "flp"]
