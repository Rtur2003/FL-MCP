"""Release packaging helpers: per-channel MIDI, YouTube chapters and MP3 with embedded cover.

Ported from the D:\\FL `senses_deliver.py` delivery packager so any score JSON (the same shape
`flp.build_from_score` reads) can be shipped with one call.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import mido

_MIN_CHAPTER_S = 10.0  # YouTube ignores chapters shorter than 10 s
_MIN_CHAPTERS = 3


def write_midi(
    events: Sequence[Sequence[Any]], out_mid: str | Path, *, bpm: float = 120.0
) -> Path:
    """Write `[start_sec, dur_sec, pitch, vel]` events as a single-track Standard MIDI File."""
    tpb = 480
    ticks_per_sec = tpb * bpm / 60.0
    mid = mido.MidiFile(ticks_per_beat=tpb)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
    msgs: list[tuple[int, int, mido.Message]] = []
    for row in events:
        t, d, p, v = row[:4]
        t0 = max(0, round(float(t) * ticks_per_sec))
        t1 = max(t0 + 20, round((float(t) + float(d)) * ticks_per_sec))
        note = max(0, min(127, int(p)))
        vel = max(1, min(127, round(float(v))))
        msgs.append((t0, 1, mido.Message("note_on", note=note, velocity=vel)))
        msgs.append((t1, 0, mido.Message("note_off", note=note, velocity=0)))
    msgs.sort(key=lambda x: (x[0], x[1]))
    prev = 0
    for tick, _order, msg in msgs:
        msg.time = max(0, tick - prev)
        prev = tick
        track.append(msg)
    out = Path(out_mid).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    mid.save(out)
    return out


def export_channel_midis(
    channels: Mapping[str, Sequence[Sequence[Any]]],
    out_dir: str | Path,
    *,
    skip: Sequence[str] = (),
    combined: str | None = "FULL_SUITE",
    bpm: float = 120.0,
) -> dict[str, str]:
    """One `.mid` per non-empty channel plus an optional combined file; returns name -> path."""
    folder = Path(out_dir).expanduser()
    skipped = {s.lower() for s in skip}
    written: dict[str, str] = {}
    everything: list[Sequence[Any]] = []
    for name, events in channels.items():
        if not events or name.lower() in skipped:
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name) or "channel"
        written[name] = str(write_midi(events, folder / f"{safe}.mid", bpm=bpm))
        everything.extend(events)
    if combined and everything:
        everything.sort(key=lambda r: (float(r[0]), int(r[2])))
        written[combined] = str(write_midi(everything, folder / f"{combined}.mid", bpm=bpm))
    return written


def _stamp(seconds: float) -> str:
    total = int(round(max(0.0, seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def youtube_chapters(
    sections: Sequence[Sequence[Any]], total_sec: float | None = None
) -> dict[str, Any]:
    """YouTube chapter list from `[[start_sec, title], ...]` that satisfies YouTube's rules.

    The first chapter is forced to 00:00, chapters shorter than 10 s merge into the previous
    one, and at least three chapters are needed (otherwise `valid` is false with `issues`).
    """
    ordered = sorted((max(0.0, float(t)), str(name)) for t, name in sections)
    issues: list[str] = []
    if not ordered:
        return {"text": "", "chapters": [], "valid": False, "issues": ["no sections"]}
    if ordered[0][0] > 0.5:
        if ordered[0][0] < _MIN_CHAPTER_S:
            issues.append(f"first chapter '{ordered[0][1]}' moved to 00:00")
            ordered[0] = (0.0, ordered[0][1])
        else:
            ordered.insert(0, (0.0, "Intro"))
            issues.append("first chapter moved to 00:00 (inserted 'Intro')")
    else:
        ordered[0] = (0.0, ordered[0][1])
    kept: list[tuple[float, str]] = []
    for idx, (t0, name) in enumerate(ordered):
        end = ordered[idx + 1][0] if idx + 1 < len(ordered) else total_sec
        if kept and end is not None and end - t0 < _MIN_CHAPTER_S:
            issues.append(f"'{name}' is shorter than 10 s: merged into '{kept[-1][1]}'")
            continue
        kept.append((t0, name))
    while len(kept) > 1 and kept[1][0] - kept[0][0] < _MIN_CHAPTER_S:
        issues.append(f"'{kept[0][1]}' is shorter than 10 s: '{kept[1][1]}' merged into it")
        kept.pop(1)
    if len(kept) < _MIN_CHAPTERS:
        issues.append(f"YouTube needs at least {_MIN_CHAPTERS} chapters (have {len(kept)})")
    text = "\n".join(f"{_stamp(t)} {name}" for t, name in kept)
    return {
        "text": text,
        "chapters": [[round(t, 2), name] for t, name in kept],
        "valid": len(kept) >= _MIN_CHAPTERS,
        "issues": issues,
    }


def build_mp3_cmd(
    ffmpeg: str,
    wav: str | Path,
    mp3: str | Path,
    *,
    cover: str | Path | None = None,
    title: str = "",
    artist: str = "",
    album: str = "",
    bitrate: str = "320k",
) -> list[str]:
    """ffmpeg argv: CBR MP3 with ID3v2.3 tags and (optionally) an attached cover picture."""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav)]
    if cover is not None:
        cmd += ["-i", str(cover), "-map", "0:a:0", "-map", "1:v:0"]
    cmd += ["-c:a", "libmp3lame", "-b:a", bitrate]
    if cover is not None:
        cmd += ["-c:v", "mjpeg", "-disposition:v:0", "attached_pic"]
    cmd += ["-id3v2_version", "3"]
    for key, val in (("title", title), ("artist", artist), ("album", album)):
        if val:
            cmd += ["-metadata", f"{key}={val}"]
    cmd.append(str(mp3))
    return cmd


def encode_mp3(
    wav: str | Path,
    mp3: str | Path,
    *,
    cover: str | Path | None = None,
    title: str = "",
    artist: str = "",
    album: str = "",
    bitrate: str = "320k",
    ffmpeg: str | None = None,
) -> dict[str, Any]:
    """Encode `wav` to a tagged MP3 (cover art embedded when `cover` exists)."""
    ff = ffmpeg or shutil.which("ffmpeg")
    if not ff:
        raise ValueError("ffmpeg not found on PATH (required for MP3 encoding)")
    src = Path(wav).expanduser()
    if not src.is_file():
        raise ValueError(f"wav not found: {src}")
    art = Path(cover).expanduser() if cover else None
    if art is not None and not art.is_file():
        raise ValueError(f"cover image not found: {art}")
    out = Path(mp3).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_mp3_cmd(
        ff, src, out, cover=art, title=title, artist=artist, album=album, bitrate=bitrate
    )
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0 or not out.is_file():
        raise ValueError(f"ffmpeg MP3 encode failed (code {res.returncode}): {res.stderr[-300:]}")
    return {"mp3": str(out), "bytes": out.stat().st_size, "cover": bool(art)}


def package(
    score: Mapping[str, Any],
    out_dir: str | Path,
    *,
    wav: str | Path | None = None,
    cover: str | Path | None = None,
    title: str = "",
    artist: str = "",
    album: str = "",
) -> dict[str, Any]:
    """Ship a score: per-channel MIDI, `chapters.txt`, and (with `wav`) a tagged MP3."""
    folder = Path(out_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    channels: dict[str, list[Any]] = {}
    skip: list[str] = []
    for idx, spec in enumerate(score.get("channels") or []):
        key = str(spec.get("id") or spec.get("name") or f"ch{idx}")
        channels[key] = list(spec.get("events") or [])
        if spec.get("kind") in ("wav", "sampler"):
            skip.append(key)
    bpm = float(score.get("tempo") or 120.0)
    # events are in seconds, so write them against a fixed 120 BPM grid (1 beat = 0.5 s)
    midis = export_channel_midis(channels, folder / "MIDI", skip=skip, bpm=120.0)
    total = max(
        (float(e[0]) + float(e[1]) for evs in channels.values() for e in evs), default=None
    )
    chapters = youtube_chapters(score.get("sections") or [], total)
    chapters_file = folder / "chapters.txt"
    chapters_file.write_text(chapters["text"] + "\n", encoding="utf-8")
    result: dict[str, Any] = {
        "dir": str(folder),
        "tempo": bpm,
        "midi": midis,
        "chapters_file": str(chapters_file),
        "chapters_valid": chapters["valid"],
        "chapter_issues": chapters["issues"],
    }
    if wav is not None:
        result["mp3"] = encode_mp3(
            wav,
            folder / f"{Path(wav).stem}.mp3",
            cover=cover,
            title=title,
            artist=artist,
            album=album,
        )
    return result
