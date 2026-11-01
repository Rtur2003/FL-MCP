"""Loudness, peaks, stereo and spectrum of a rendered mix (ITU-R BS.1770-4 / EBU R128).

Needs numpy, scipy and soundfile: install the `audio` extra. Everything here is a pure function
of an audio file, so it is the same measurement whatever the DAW or the model."""

import math
from pathlib import Path
from typing import Any

BANDS = (
    ("sub", 20, 60),
    ("bass", 60, 250),
    ("low_mid", 250, 500),
    ("mid", 500, 2000),
    ("high_mid", 2000, 6000),
    ("high", 6000, 12000),
    ("air", 12000, 20000),
)
FLOOR_DB = -120.0
STREAMING_LUFS = -14.0  # what the big streaming services turn a track to


def _libs() -> tuple[Any, Any, Any]:
    try:
        import numpy
        import soundfile
        from scipy import signal
    except ImportError:
        raise ValueError(
            "audio analysis needs numpy, scipy and soundfile: install fl-mcp with the audio "
            "extra (uv tool install 'fl-mcp[audio]')"
        ) from None
    return numpy, signal, soundfile


def available() -> bool:
    try:
        _libs()
    except ValueError:
        return False
    return True


def load(path: str | Path) -> tuple[Any, int]:
    """Samples as float32 of shape (frames, channels) and the sample rate."""
    np, _, sf = _libs()
    p = Path(path).expanduser()
    if not p.is_file():
        raise ValueError(f"audio file not found: {p}")
    try:
        data, sr = sf.read(str(p), always_2d=True, dtype="float32")
    except Exception as exc:
        raise ValueError(f"cannot read {p.name} as audio ({exc})") from None
    if data.shape[0] < sr // 10:
        raise ValueError("the file is shorter than a tenth of a second")
    return data, int(sr)


def _db(x: float) -> float:
    return round(20 * math.log10(max(x, 1e-6)), 2)


def _lufs(energy: float) -> float:
    return FLOOR_DB if energy <= 0 else max(FLOOR_DB, -0.691 + 10 * math.log10(energy))


def k_filters(fs: int) -> list[tuple[list[float], list[float]]]:
    """The two BS.1770 pre-filters (head-related shelf, then the revised low-cut) at any rate."""
    f0, gain, q = 1681.9744509555319, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10 ** (gain / 20)
    vb = vh**0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = (
        [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0],
        [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0],
    )
    f0, q = 38.13547087613982, 0.5003270373253953
    k = math.tan(math.pi * f0 / fs)
    a0 = 1 + k / q + k * k
    low_cut = ([1.0, -2.0, 1.0], [1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    return [shelf, low_cut]


def _windows(np: Any, sq: Any, size: int, hop: int) -> Any:
    """Mean square of every window of `size` frames, `hop` apart: shape (windows, channels)."""
    csum = np.concatenate([np.zeros((1, sq.shape[1])), np.cumsum(sq, axis=0)])
    starts = np.arange(0, sq.shape[0] - size + 1, hop)
    if len(starts) == 0:
        return np.zeros((0, sq.shape[1]))
    return (csum[starts + size] - csum[starts]) / size


def loudness(x: Any, fs: int) -> dict[str, float | None]:
    """Integrated, short-term and momentary loudness (LUFS) and loudness range (LU)."""
    np, signal, _ = _libs()
    y = np.empty(x.shape, dtype="float64")
    for c in range(x.shape[1]):
        v = x[:, c].astype("float64")
        for b, a in k_filters(fs):
            v = signal.lfilter(b, a, v)
        y[:, c] = v
    sq = y * y
    weights = np.array([1.0] * x.shape[1])
    momentary = _windows(np, sq, int(round(0.4 * fs)), int(round(0.1 * fs))) @ weights
    short = _windows(np, sq, int(round(3.0 * fs)), int(round(1.0 * fs))) @ weights
    out: dict[str, float | None] = {
        "lufs_i": None,
        "lufs_s_max": None,
        "lufs_m_max": None,
        "lra": None,
    }
    if len(momentary):
        block = np.array([_lufs(e) for e in momentary])
        out["lufs_m_max"] = round(float(block.max()), 1)
        above = block > -70.0
        if above.any():
            gate = _lufs(float(momentary[above].mean())) - 10.0
            kept = momentary[block > max(-70.0, gate)]
            out["lufs_i"] = round(_lufs(float(kept.mean())), 1) if len(kept) else None
    if len(short):
        st = np.array([_lufs(e) for e in short])
        out["lufs_s_max"] = round(float(st.max()), 1)
        above = st > -70.0
        if above.sum() >= 2:
            rel = _lufs(float(short[above].mean())) - 20.0
            kept = st[st > max(-70.0, rel)]
            if len(kept) >= 2:
                out["lra"] = round(float(np.percentile(kept, 95) - np.percentile(kept, 10)), 1)
    return out


def true_peak(x: Any) -> float:
    """Highest level between the samples (4x oversampled), in dBTP."""
    np, signal, _ = _libs()
    best = 0.0
    for c in range(x.shape[1]):
        best = max(
            best, float(np.max(np.abs(signal.resample_poly(x[:, c].astype("float64"), 4, 1))))
        )
    return _db(best)


def stereo(x: Any, fs: int) -> dict[str, float] | None:
    np, signal, _ = _libs()
    if x.shape[1] < 2:
        return None
    left, right = x[:, 0].astype("float64"), x[:, 1].astype("float64")
    mid, side = (left + right) / 2, (left - right) / 2
    e_mid, e_side = float((mid**2).sum()), float((side**2).sum())
    std = left.std() * right.std()
    corr = float(np.corrcoef(left, right)[0, 1]) if std > 1e-12 else 1.0
    sos = signal.butter(4, 120, "lowpass", fs=fs, output="sos")
    low_mid, low_side = signal.sosfilt(sos, mid), signal.sosfilt(sos, side)
    lm, ls = float((low_mid**2).sum()), float((low_side**2).sum())
    mono = (left + right) / 2
    mono_loss = 10 * math.log10(
        max(float((mono**2).mean()), 1e-12)
        / max(float(((left**2).mean() + (right**2).mean()) / 2), 1e-12)
    )
    return {
        "corr": round(corr, 2),
        "side_db": round(10 * math.log10(max(e_side, 1e-12) / max(e_mid, 1e-12)), 1),
        "low_side_db": round(10 * math.log10(max(ls, 1e-12) / max(lm, 1e-12)), 1),
        "mono_loss_db": round(mono_loss, 1),
    }


def spectrum(x: Any, fs: int) -> dict[str, float]:
    """Share of the energy in each band, in dB relative to the total (the bands add up to 0 dB)."""
    np, signal, _ = _libs()
    mono = x.mean(axis=1).astype("float64")
    freqs, power = signal.welch(mono, fs, window="hann", nperseg=min(8192, len(mono)))
    total = float(power[(freqs >= 20) & (freqs <= 20000)].sum()) or 1e-20
    out = {}
    for name, lo, hi in BANDS:
        share = float(power[(freqs >= lo) & (freqs < hi)].sum()) / total
        out[name] = round(10 * math.log10(max(share, 1e-9)), 1)
    return out


def peaks(x: Any) -> dict[str, Any]:
    np, _, _ = _libs()
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x.astype("float64") ** 2)))
    hot = (np.abs(x) >= 0.999).any(axis=1)
    flat = int(np.sum(hot[:-2] & hot[1:-1] & hot[2:])) if len(x) > 2 else 0
    quiet = np.abs(x).max(axis=1) < 1e-4
    lead = int(np.argmin(quiet)) if not quiet.all() else len(x)
    trail = int(np.argmin(quiet[::-1])) if not quiet.all() else len(x)
    body = x[lead : len(x) - trail] if lead + trail < len(x) else x
    return {
        "peak_db": _db(peak),
        "rms_db": _db(rms),
        "crest_db": round(_db(peak) - _db(rms), 1),
        "dc": round(float(np.abs(body.mean(axis=0)).max()), 5),
        "clipped": flat,
        "lead": lead,
        "trail": trail,
    }


def report(
    path: str | Path, target_lufs: float | None = None, ceiling_db: float = -1.0
) -> dict[str, Any]:
    """Everything measured about a file, plus plain advice."""
    x, fs = load(path)
    p = peaks(x)
    loud = loudness(x, fs)
    tp = true_peak(x)
    wide = stereo(x, fs)
    bands = spectrum(x, fs)
    out: dict[str, Any] = {
        "file": str(Path(path)),
        "dur_s": round(len(x) / fs, 1),
        "rate": fs,
        "channels": int(x.shape[1]),
        **loud,
        "true_peak_db": tp,
        "peak_db": p["peak_db"],
        "crest_db": p["crest_db"],
        "stereo": wide,
        "bands_db": bands,
    }
    out["advice"] = advise(out, p, target_lufs, ceiling_db, fs)
    return out


def advise(
    m: dict[str, Any], p: dict[str, Any], target: float | None, ceiling_db: float, fs: int
) -> list[str]:
    tips: list[str] = []
    lufs = m.get("lufs_i")
    if lufs is None:
        tips.append("no sound above -70 LUFS: the file is silent or nearly so")
        return tips
    goal = STREAMING_LUFS if target is None else target
    if lufs > goal + 1.5:
        tips.append(
            f"{lufs} LUFS is {lufs - goal:.1f} LU louder than the {goal} target: lower the master "
            "limiter's input by about that much, or ease the loudest parts"
        )
    elif lufs < goal - 1.5:
        tips.append(
            f"{lufs} LUFS is {goal - lufs:.1f} LU quieter than the {goal} target: raise the "
            "limiter's input (gain into it), not the faders, once the balance is right"
        )
    if m["true_peak_db"] > ceiling_db + 0.05:
        tips.append(
            f"true peak {m['true_peak_db']} dBTP is over {ceiling_db}: set the limiter ceiling to "
            f"{ceiling_db} so lossy encoders do not distort"
        )
    if m["crest_db"] < 6:
        tips.append(
            f"crest factor {m['crest_db']} dB is very low: it is over-compressed or over-limited"
        )
    elif m["crest_db"] > 16:
        tips.append(
            f"crest factor {m['crest_db']} dB is very high: some bus compression would glue it"
        )
    if p["clipped"]:
        tips.append(f"{p['clipped']} samples are digitally clipped (flat at full scale)")
    if p["dc"] > 0.002:
        tips.append(f"DC offset {p['dc']}: add a high-pass at 20 Hz on the master")
    wide = m.get("stereo")
    if wide:
        if wide["corr"] < 0.2:
            tips.append(f"left and right barely correlate ({wide['corr']}): check phase and mono")
        if wide["mono_loss_db"] < -3:
            tips.append(
                f"summed to mono it loses {-wide['mono_loss_db']} dB: something is out of phase"
            )
        if wide["low_side_db"] > -14:
            tips.append(
                "the low end is not centred: keep kick, bass and sub in mono below about 120 Hz"
            )
    bands = m.get("bands_db", {})
    if bands and bands.get("high", 0) < -45 and bands.get("air", 0) < -45:
        tips.append("almost nothing above 6 kHz: it sounds dull or low-passed")
    if p["lead"] > fs:
        tips.append(f"{p['lead'] / fs:.1f} s of silence at the start")
    if p["trail"] > 3 * fs:
        tips.append(f"{p['trail'] / fs:.1f} s of silence at the end")
    return tips


def compare(path: str | Path, reference: str | Path) -> dict[str, Any]:
    """The mix against a reference track: loudness, dynamics, width and tonal balance."""
    a, b = report(path), report(reference)
    moves = []
    for band, _, _ in BANDS:
        delta = round(a["bands_db"][band] - b["bands_db"][band], 1)
        if abs(delta) >= 1.5:
            more = "more" if delta > 0 else "less"
            moves.append(
                {
                    "band": band,
                    "delta_db": delta,
                    "advice": f"{abs(delta)} dB {more} {band} than the reference: "
                    f"{'cut' if delta > 0 else 'lift'} it by about {min(abs(delta), 4.0):.1f} dB",
                }
            )
    return {
        "mix": {k: a[k] for k in ("lufs_i", "true_peak_db", "crest_db")},
        "reference": {k: b[k] for k in ("lufs_i", "true_peak_db", "crest_db")},
        "loudness_diff_lu": None
        if a["lufs_i"] is None or b["lufs_i"] is None
        else round(a["lufs_i"] - b["lufs_i"], 1),
        "width_diff_db": None
        if not a["stereo"] or not b["stereo"]
        else round(a["stereo"]["side_db"] - b["stereo"]["side_db"], 1),
        "tonal": moves,
    }
