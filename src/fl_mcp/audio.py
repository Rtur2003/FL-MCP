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


def capabilities() -> dict[str, Any]:
    """Return available audio/AI/DSP engines and downloaded ONNX models."""
    import importlib.util

    from fl_mcp import paths

    mdir = paths.models_dir()
    return {
        "dsp": available(),
        "onnxruntime": importlib.util.find_spec("onnxruntime") is not None,
        "soxr": importlib.util.find_spec("soxr") is not None,
        "pedalboard": importlib.util.find_spec("pedalboard") is not None,
        "pyloudnorm": importlib.util.find_spec("pyloudnorm") is not None,
        "pretty_midi": importlib.util.find_spec("pretty_midi") is not None,
        "basic_pitch_onnx": (mdir / "basic_pitch.onnx").is_file(),
        "silero_vad_onnx": (mdir / "silero_vad.onnx").is_file(),
    }


def _resample_1d(np: Any, signal: Any, mono: Any, src_fs: int, dst_fs: int) -> Any:
    """Resample 1-D mono audio using libsoxr SIMD when installed, falling back to scipy."""
    if src_fs == dst_fs or len(mono) < 16:
        return mono
    try:
        import soxr

        return soxr.resample(mono.astype("float32"), src_fs, dst_fs).astype("float64")
    except Exception:
        new_n = max(16, int(round(len(mono) * dst_fs / src_fs)))
        return signal.resample(mono, new_n)


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
    active_rms = float(np.sqrt(np.mean(body.astype("float64") ** 2))) if len(body) else 0.0
    return {
        "peak_db": _db(peak),
        "rms_db": _db(rms),
        "crest_db": round(_db(peak) - _db(rms), 1),
        "active_rms_db": _db(active_rms),
        "active_crest_db": round(_db(peak) - _db(active_rms), 1),
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


_KRUMHANSL_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
_KRUMHANSL_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _estimate_bpm(np: Any, mono: Any, fs: int) -> tuple[float, float]:
    """Autocorrelation-based tempo estimator in [60, 190] BPM from onset strength envelope."""
    hop = max(1, int(round(fs * 0.01)))  # 100 Hz envelope
    n_frames = len(mono) // hop
    if n_frames < 120:
        return 120.0, 0.0
    frames = mono[: n_frames * hop].reshape(n_frames, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    onset = np.maximum(0.0, np.diff(rms, prepend=rms[0]))
    onset = onset - float(onset.mean())
    env_fs = fs / hop
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1 :]
    lo_lag = max(1, int(round(env_fs * 60.0 / 190.0)))
    hi_lag = min(len(ac) - 1, int(round(env_fs * 60.0 / 60.0)))
    if hi_lag <= lo_lag or ac[0] <= 1e-12:
        return 120.0, 0.0
    window = ac[lo_lag : hi_lag + 1]
    peak_idx = int(np.argmax(window))
    lag = lo_lag + peak_idx
    if 0 < peak_idx < len(window) - 1:
        y0, y1, y2 = (
            float(window[peak_idx - 1]),
            float(window[peak_idx]),
            float(window[peak_idx + 1]),
        )
        denom = y0 - 2.0 * y1 + y2
        if abs(denom) > 1e-12:
            lag += max(-0.5, min(0.5, 0.5 * (y0 - y2) / denom))
    bpm = round(60.0 * env_fs / max(lag, 1e-6), 1)
    conf = max(0.0, min(1.0, float(window[peak_idx] / max(ac[0], 1e-12))))
    return bpm, round(conf, 3)


def _estimate_key(np: Any, mono: Any, fs: int) -> tuple[str, float, list[float]]:
    """Krumhansl-Schmuckler chroma key/mode classifier."""
    nfft = 8192
    if len(mono) < 512:
        return "C major", 0.0, [0.0] * 12
    step = nfft // 2
    n_win = max(1, (len(mono) - nfft) // step + 1) if len(mono) >= nfft else 1
    chroma = np.zeros(12, dtype="float64")
    hann = np.hanning(min(nfft, len(mono)))
    freqs = np.fft.rfftfreq(len(hann), 1.0 / fs)
    mask = (freqs >= 55.0) & (freqs <= 2500.0)
    f_sel = freqs[mask]
    midi_bins = np.round(69.0 + 12.0 * np.log2(f_sel / 440.0)).astype("int64") % 12
    for i in range(min(n_win, 80)):
        seg = mono[i * step : i * step + len(hann)]
        if len(seg) < len(hann):
            break
        mag = np.abs(np.fft.rfft(seg * hann))[mask]
        np.add.at(chroma, midi_bins, mag * mag)
    total = float(chroma.sum())
    if total <= 1e-12:
        return "C major", 0.0, [0.0] * 12
    chroma = chroma / total
    maj_t = np.array(_KRUMHANSL_MAJOR, dtype="float64")
    min_t = np.array(_KRUMHANSL_MINOR, dtype="float64")
    best_name, best_score = "C major", -2.0
    for root in range(12):
        rot = np.roll(chroma, -root)
        c_maj = float(np.corrcoef(rot, maj_t)[0, 1])
        c_min = float(np.corrcoef(rot, min_t)[0, 1])
        if c_maj > best_score:
            best_name, best_score = f"{_NOTE_NAMES[root]} major", c_maj
        if c_min > best_score:
            best_name, best_score = f"{_NOTE_NAMES[root]} minor", c_min
    return best_name, round(max(0.0, best_score), 3), [round(float(x), 3) for x in chroma]


def _basic_pitch_notes(
    np: Any, signal: Any, mono: Any, fs: int, bpm: float, max_notes: int = 128
) -> list[list[float]] | None:
    """Run Spotify Basic Pitch (nmp.onnx) polyphonic audio-to-MIDI inference when installed."""
    from fl_mcp import paths

    model_path = paths.models_dir() / "basic_pitch.onnx"
    if not model_path.is_file():
        return None
    try:
        import onnxruntime as ort
    except ImportError:
        return None
    try:
        target_sr = 22050
        win_samples = 43844
        hop_samples = 256
        y = _resample_1d(np, signal, mono, fs, target_sr).astype("float32")
        peak = float(np.max(np.abs(y))) if len(y) else 0.0
        if peak > 1e-6:
            y = y / peak
        if len(y) < win_samples:
            y = np.pad(y, (0, win_samples - len(y)))
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        in_name = sess.get_inputs()[0].name
        frames_list = []
        step = win_samples
        for start in range(0, len(y), step):
            chunk = y[start : start + win_samples]
            if len(chunk) < win_samples:
                chunk = np.pad(chunk, (0, win_samples - len(chunk)))
            outs = sess.run(None, {in_name: chunk.reshape(1, win_samples, 1)})
            # outs[1] is note activations (1, 172, 88), outs[0] is onsets (1, 172, 88)
            note_mat = outs[1][0] if len(outs) > 1 and outs[1].shape[-1] == 88 else outs[0][0]
            frames_list.append(note_mat)
        if not frames_list:
            return None
        full_mat = np.concatenate(frames_list, axis=0)
        frame_sec = hop_samples / float(target_sr)
        beats_per_sec = max(0.5, bpm / 60.0)
        notes: list[list[float]] = []
        for p_idx in range(88):
            col = full_mat[:, p_idx]
            active = col >= 0.45
            if not active.any():
                continue
            i = 0
            n_t = len(col)
            while i < n_t:
                if not active[i]:
                    i += 1
                    continue
                j = i + 1
                while j < n_t and active[j]:
                    j += 1
                dur_s = (j - i) * frame_sec
                if dur_s >= 0.045:
                    midi = 21 + p_idx
                    start_b = round((i * frame_sec * beats_per_sec) * 4.0) / 4.0
                    dur_b = max(0.25, round((dur_s * beats_per_sec) * 4.0) / 4.0)
                    pk_prob = float(np.max(col[i:j]))
                    vel = max(35, min(127, round(45 + 82 * min(1.0, pk_prob))))
                    notes.append([int(midi), float(start_b), float(dur_b), int(vel)])
                i = j
        notes.sort(key=lambda item: (item[1], item[0]))
        return notes[:max_notes]
    except Exception:
        return None


def _vad_active_mask(np: Any, signal: Any, mono: Any, fs: int) -> tuple[Any | None, float | None]:
    """Run Silero VAD (silero_vad.onnx) at 16 kHz to isolate voiced/vocal regions."""
    from fl_mcp import paths

    model_path = paths.models_dir() / "silero_vad.onnx"
    if not model_path.is_file():
        return None, None
    try:
        import onnxruntime as ort
    except ImportError:
        return None, None
    try:
        target_sr = 16000
        win = 512
        y16 = _resample_1d(np, signal, mono, fs, target_sr).astype("float32")
        n_win = len(y16) // win
        if n_win < 4:
            return None, None
        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        state = np.zeros((2, 1, 128), dtype="float32")
        sr_arr = np.array(target_sr, dtype="int64")
        probs: list[float] = []
        for idx in range(n_win):
            chunk = y16[idx * win : (idx + 1) * win].reshape(1, win)
            out, state = sess.run(None, {"input": chunk, "state": state, "sr": sr_arr})
            probs.append(float(out.squeeze()))
        prob_arr = np.array(probs, dtype="float32")
        active_wins = prob_arr >= 0.45
        ratio = round(float(active_wins.mean()), 3)
        if ratio < 0.08:
            return None, ratio
        hop_src = max(1, int(round(win * fs / target_sr)))
        chunks = [
            mono[idx * hop_src : (idx + 1) * hop_src]
            for idx, is_act in enumerate(active_wins)
            if is_act and (idx + 1) * hop_src <= len(mono)
        ]
        if not chunks:
            return None, ratio
        voiced = np.concatenate(chunks)
        return (voiced if len(voiced) >= 1024 else None), ratio
    except Exception:
        return None, None


def transcribe(path: str | Path, max_notes: int = 128) -> dict[str, Any]:
    """Estimate BPM, key/mode, and monophonic F0 melody tuples ([midi, start_beat, len, vel])."""
    np, signal, _ = _libs()
    x, fs = load(path)
    mono = x.mean(axis=1).astype("float64")
    bpm, bpm_conf = _estimate_bpm(np, mono, fs)
    key_str, key_conf, chroma = _estimate_key(np, mono, fs)

    # Frame-wise YIN-like normalized autocorrelation pitch tracking (65 Hz - 1200 Hz)
    win_n = max(256, int(round(fs * 0.04)))
    hop_n = max(128, int(round(fs * 0.01)))
    min_lag = max(2, int(fs / 1200.0))
    max_lag = min(win_n // 2, int(fs / 65.0))
    n_frames = max(0, (len(mono) - win_n) // hop_n + 1)
    pitches: list[int | None] = []
    amps: list[float] = []
    for i in range(n_frames):
        frame = mono[i * hop_n : i * hop_n + win_n]
        rms = float(np.sqrt(np.mean(frame * frame)))
        amps.append(rms)
        if rms < 0.012 or max_lag <= min_lag:
            pitches.append(None)
            continue
        f_win = frame - float(frame.mean())
        ac = np.correlate(f_win, f_win, mode="full")[win_n - 1 :]
        if ac[0] <= 1e-10:
            pitches.append(None)
            continue
        sub = ac[min_lag : max_lag + 1] / ac[0]
        pk = int(np.argmax(sub))
        if float(sub[pk]) < 0.55:
            pitches.append(None)
            continue
        lag = min_lag + pk
        if 0 < pk < len(sub) - 1:
            y0, y1, y2 = float(sub[pk - 1]), float(sub[pk]), float(sub[pk + 1])
            denom = y0 - 2.0 * y1 + y2
            if abs(denom) > 1e-12:
                lag += max(-0.5, min(0.5, 0.5 * (y0 - y2) / denom))
        hz = fs / max(lag, 1e-6)
        midi = int(round(69.0 + 12.0 * math.log2(hz / 440.0)))
        pitches.append(midi if 24 <= midi <= 108 else None)

    # Smooth isolated single-frame spikes and merge contiguous pitch runs into note events
    if len(pitches) >= 3:
        smoothed = list(pitches)
        for i in range(1, len(pitches) - 1):
            if pitches[i - 1] is not None and pitches[i - 1] == pitches[i + 1]:
                smoothed[i] = pitches[i - 1]
        pitches = smoothed

    notes: list[list[float]] = []
    frame_sec = hop_n / fs
    beats_per_sec = max(0.5, bpm / 60.0)
    peak_rms = max(amps, default=1.0) or 1.0
    i = 0
    while i < len(pitches) and len(notes) < max_notes:
        p = pitches[i]
        if p is None:
            i += 1
            continue
        j = i + 1
        while j < len(pitches) and pitches[j] == p:
            j += 1
        dur_s = (j - i) * frame_sec
        if dur_s >= 0.045:
            start_b = round((i * frame_sec * beats_per_sec) * 4.0) / 4.0
            dur_b = max(0.25, round((dur_s * beats_per_sec) * 4.0) / 4.0)
            seg_rms = max(amps[i:j]) / peak_rms
            vel = max(30, min(127, round(40 + 87 * min(1.0, seg_rms))))
            notes.append([int(p), float(start_b), float(dur_b), int(vel)])
        i = j

    bp_notes = _basic_pitch_notes(np, signal, mono, fs, bpm, max_notes=max_notes)
    engine = "yin"
    if bp_notes is not None:
        engine = "basic_pitch_onnx" if len(bp_notes) >= len(notes) else "hybrid_onnx_yin"
        if len(bp_notes) >= len(notes) and len(bp_notes) > 0:
            notes = bp_notes
    return {
        "file": str(Path(path)),
        "engine": engine,
        "bpm": bpm,
        "bpm_confidence": bpm_conf,
        "key": key_str,
        "key_confidence": key_conf,
        "chroma": chroma,
        "notes_count": len(notes),
        "notes": notes,
    }


def masking(path: str | Path, reference: str | Path) -> dict[str, Any]:
    """Compare spectral overlap (vocal/lead vs backing) and propose EQ cuts."""
    np, signal, _ = _libs()
    x_a, fs_a = load(path)
    x_b, fs_b = load(reference)
    m_a = x_a.mean(axis=1).astype("float64")
    m_b = x_b.mean(axis=1).astype("float64")
    if fs_b != fs_a and len(m_b) > 16:
        m_b = _resample_1d(np, signal, m_b, fs_b, fs_a)
    voiced_a, vad_ratio = _vad_active_mask(np, signal, m_a, fs_a)
    if voiced_a is not None:
        m_a = voiced_a
    nperseg = min(8192, len(m_a), len(m_b))
    freqs, p_a = signal.welch(m_a, fs_a, window="hann", nperseg=nperseg)
    _, p_b = signal.welch(m_b, fs_a, window="hann", nperseg=nperseg)
    p_a = np.maximum(p_a, 1e-18)
    p_b = np.maximum(p_b, 1e-18)

    zones = [
        ("low_warmth", 120, 250, 1.4, "clear mud from backing below vocal fundamentals"),
        ("mud", 250, 500, 1.5, "carve boxy 250-500 Hz overlap on backing track"),
        ("body", 500, 1200, 1.2, "pocket midrange body between foreground and bed"),
        (
            "clarity",
            1200,
            2800,
            1.4,
            "notch 1.2-2.8 kHz on backing so intelligibility cuts through",
        ),
        ("presence", 2800, 5200, 1.5, "dip 3-5 kHz on backing to unmask lead/vocal bite"),
        ("sibilance_air", 5200, 10000, 1.2, "tame backing upper harmonics competing with air"),
    ]
    collisions: list[dict[str, Any]] = []
    eq_moves: list[dict[str, Any]] = []
    overlap_sum = 0.0
    for name, lo, hi, q, why in zones:
        sel = (freqs >= lo) & (freqs < hi)
        if not sel.any():
            continue
        db_a = 10.0 * math.log10(float(p_a[sel].mean()))
        db_b = 10.0 * math.log10(float(p_b[sel].mean()))
        margin = round(db_a - db_b, 1)  # negative means backing is louder than foreground
        severity = max(0.0, min(1.0, (4.0 - margin) / 14.0))
        overlap_sum += severity
        if margin < 3.0:
            center_hz = round(math.sqrt(lo * hi))
            cut_db = round(-max(1.5, min(5.5, (3.0 - margin) * 0.65)), 1)
            collisions.append(
                {
                    "zone": name,
                    "range_hz": [lo, hi],
                    "lead_vs_bed_db": margin,
                    "severity": round(severity, 2),
                }
            )
            eq_moves.append(
                {
                    "target": "backing",
                    "zone": name,
                    "freq_hz": center_hz,
                    "gain_db": cut_db,
                    "q": q,
                    "why": why,
                }
            )
    masking_index = round(100.0 * overlap_sum / len(zones), 1)
    out: dict[str, Any] = {
        "foreground": str(Path(path)),
        "backing": str(Path(reference)),
        "masking_index": masking_index,
        "colliding_bands": collisions,
        "eq_moves": eq_moves,
    }
    if vad_ratio is not None:
        out["vad_engine"] = "silero_vad_onnx"
        out["vad_active_ratio"] = vad_ratio
    return out
