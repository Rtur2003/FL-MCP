"""What sounds and plugins this computer has, as a searchable index.

The sample folders (FL's own packs, plus anything in FL_MCP_SAMPLES) are walked once and each
file gets a role from its name and folders (kick, snare, hat...). Audio features (length, peak,
decay, brightness, low-end share) are measured on demand for the files that matter and checked
against what the role should sound like, so a file called "kick" that is a 20 second loop does
not win. FL's plugin database folder tells which plugins are installed."""

import json
import os
import random
import threading
import time
import wave
from array import array
from pathlib import Path
from typing import Any

from fl_mcp import paths

SAMPLE_EXT = {".wav", ".flac", ".ogg", ".mp3", ".aif", ".aiff"}
MAX_FILES = 80_000

ROLE_TOKENS = {
    "kick": {"kick", "kicks", "bd", "bassdrum", "kik"},
    "snare": {"snare", "snares", "sd", "snr"},
    "clap": {"clap", "claps", "cp", "handclap"},
    "hat": {"hat", "hats", "hihat", "hihats", "hh", "chh", "closedhat"},
    "perc": {
        "perc",
        "percs",
        "percussion",
        "rim",
        "rimshot",
        "tom",
        "toms",
        "shaker",
        "tamb",
        "tambourine",
        "conga",
        "bongo",
        "cowbell",
        "clave",
        "snap",
    },
    "bass": {"808", "808s", "sub", "subbass", "bass", "basses"},
    "fx": {
        "fx",
        "riser",
        "risers",
        "impact",
        "impacts",
        "sweep",
        "downlifter",
        "uplifter",
        "crash",
        "reverse",
        "whoosh",
        "noise",
        "texture",
    },
    "chords": {"chord", "chords", "stab", "stabs"},
    "pad": {"pad", "pads", "atmosphere", "ambient", "drone"},
    "lead": {"lead", "leads", "pluck", "plucks", "arp"},
    "vocal": {"vocal", "vocals", "vox", "voice", "acapella", "chant"},
}
DRUMS = ("kick", "snare", "clap", "hat", "ohat", "perc")
LOOP_TOKENS = {"loop", "loops", "bpm", "full", "break", "breaks", "groove"}
GENRE_WORDS = {
    "house": ["house", "club", "disco", "funky"],
    "techno": ["techno", "industrial", "warehouse"],
    "trance": ["trance", "uplifting", "psy"],
    "dnb": ["dnb", "jungle", "drum and bass", "breakbeat", "amen"],
    "dubstep": ["dubstep", "riddim", "wobble", "bass music"],
    "trap": ["trap", "808", "dark", "drill", "hard"],
    "hicaz_trap": ["trap", "808", "oriental", "arabic", "dark"],
    "boom_bap": ["boom", "bap", "hiphop", "hip hop", "dusty", "vinyl", "90s"],
    "lofi": ["lofi", "lo-fi", "vinyl", "dusty", "chill", "mellow"],
    "pop": ["pop", "clean", "bright", "modern"],
    "synthwave": ["synthwave", "retro", "80s", "analog", "gated"],
    "reggaeton": ["reggaeton", "latin", "dembow", "perreo"],
}
# what a role sounds like: (min s, max s, low-end share, brightness Hz low, high), 0 = unbounded
SHAPE = {
    "kick": (0.06, 1.6, 0.45, 0, 1200),
    "snare": (0.08, 1.2, 0.0, 700, 7000),
    "clap": (0.05, 0.9, 0.0, 1200, 8000),
    "hat": (0.01, 0.6, 0.0, 3500, 0),
    "ohat": (0.15, 2.0, 0.0, 3000, 0),
    "perc": (0.02, 2.0, 0.0, 0, 0),
    "bass": (0.25, 5.0, 0.55, 0, 1500),
}


def tokens(text: str) -> list[str]:
    out, cur = [], ""
    for ch in text:
        if ch.isalnum():
            if cur and ch.isupper() and cur[-1].islower():  # camelCase: HiHat -> hi, hat
                out.append(cur.lower())
                cur = ""
            cur += ch
        elif cur:
            out.append(cur.lower())
            cur = ""
    if cur:
        out.append(cur.lower())
    return out


GENERIC_FOLDERS = {
    "drums",
    "drum",
    "oneshots",
    "one",
    "shots",
    "shot",
    "samples",
    "sounds",
    "hits",
    "sfx",
}


def pack_of(path: Path, root: Path) -> str:
    """The pack a file belongs to: the first folder under the root that is not just a category
    such as Drums or Kicks, else the root's own name."""
    try:
        folders = path.relative_to(root).parts[:-1]
    except ValueError:
        return path.parent.name
    role_words = set().union(*ROLE_TOKENS.values()) | GENERIC_FOLDERS | {"open", "closed", "opened"}
    for name in folders:
        if not set(tokens(name)) <= role_words:
            return name
    return root.name


def classify_name(path: Path) -> str:
    """kick, snare, clap, hat, ohat, perc, bass, fx, chords, pad, lead, vocal, loop or other."""
    parts = [*path.parent.parts[-3:], path.stem]
    low = [t for part in parts for t in tokens(part)]
    stem = set(tokens(path.stem))
    folder = set(t for part in path.parent.parts[-2:] for t in tokens(part))
    if stem & LOOP_TOKENS or (
        folder & LOOP_TOKENS and not stem & set().union(*ROLE_TOKENS.values())
    ):
        return "loop"
    pool = stem or set(low)
    if pool & ROLE_TOKENS["hat"] or "hihat" in "".join(low):
        return "ohat" if pool & {"open", "opened", "ohat", "oh"} else "hat"
    for role in ("kick", "snare", "clap", "perc", "bass", "fx", "chords", "pad", "lead", "vocal"):
        if pool & ROLE_TOKENS[role]:
            return role
    for role in ("kick", "snare", "clap", "hat", "perc", "bass", "fx"):  # the folder says it
        if folder & ROLE_TOKENS[role]:
            return "ohat" if role == "hat" and folder & {"open", "opened"} else role
    return "other"


# ---------------------------------------------------------------- audio features


def _pcm(path: Path, seconds: float = 2.0) -> tuple[list[float], int, float] | None:
    """Mono samples (first `seconds`), the sample rate and the full length in seconds."""
    if path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as w:
            sr, width, ch, total = (
                w.getframerate(),
                w.getsampwidth(),
                w.getnchannels(),
                w.getnframes(),
            )
            raw = w.readframes(min(total, int(sr * seconds)))
    except (wave.Error, EOFError, OSError):
        return None
    if width == 2:
        data = array("h")
        data.frombytes(raw[: len(raw) // 2 * 2])
        vals = [x / 32768.0 for x in data]
    elif width == 3:
        vals = [
            int.from_bytes(raw[i : i + 3], "little", signed=True) / 8388608.0
            for i in range(0, len(raw) - 2, 3)
        ]
    elif width == 4:
        data = array("i")
        data.frombytes(raw[: len(raw) // 4 * 4])
        vals = [x / 2147483648.0 for x in data]
    elif width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
    else:
        return None
    if ch > 1:
        vals = [sum(vals[i : i + ch]) / ch for i in range(0, len(vals) - ch + 1, ch)]
    return vals, sr, total / sr if sr else 0.0


def _db(x: float) -> float:
    import math

    return round(20 * math.log10(max(x, 1e-6)), 1)


def analyze(path: str | Path) -> dict[str, Any] | None:
    """Length, peak, decay time, brightness and low-end share of a WAV file, or None."""
    got = _pcm(Path(path))
    if got is None or not got[0]:
        return None
    x, sr, dur = got
    peak = max(abs(v) for v in x)
    rms = (sum(v * v for v in x) / len(x)) ** 0.5
    win = max(1, sr // 200)  # 5 ms envelope
    env = [
        (sum(v * v for v in x[i : i + win]) / len(x[i : i + win])) ** 0.5
        for i in range(0, len(x), win)
    ]
    top = max(range(len(env)), key=env.__getitem__)
    floor = env[top] * 0.01  # 40 dB below the peak
    end = next((k for k in range(top, len(env)) if env[k] < floor), len(env))
    decay_ms = round((end - top) * 5.0)
    seg = x[top * win : top * win + sr // 5] or x
    crossings = sum(1 for a, b in zip(seg, seg[1:], strict=False) if (a < 0) != (b < 0))
    zcr_hz = round(crossings * sr / max(len(seg), 1) / 2)
    alpha = min(1.0, 6.2832 * 200 / sr)  # one-pole low-pass at 200 Hz
    y = low = total = 0.0
    for v in x:
        y += alpha * (v - y)
        low += y * y
        total += v * v
    return {
        "dur": round(dur, 3),
        "peak_db": _db(peak),
        "rms_db": _db(rms),
        "decay_ms": decay_ms,
        "zcr_hz": zcr_hz,
        "low": round(low / total, 2) if total else 0.0,
    }


def fit(role: str, f: dict[str, Any] | None) -> float:
    """0..1: how well measured features match what the role should sound like."""
    shape = SHAPE.get(role)
    if f is None or shape is None:
        return 0.5
    lo_d, hi_d, low, bright_lo, bright_hi = shape
    score = 1.0
    if not lo_d <= f["dur"] <= hi_d:
        score -= 0.5 if f["dur"] > hi_d * 3 or f["dur"] < lo_d / 3 else 0.25
    if low and f["low"] < low:
        score -= 0.35
    if bright_lo and f["zcr_hz"] < bright_lo:
        score -= 0.45 if f["zcr_hz"] < bright_lo * 0.4 else 0.25
    if role in ("hat", "ohat", "clap") and f["low"] > 0.4:
        score -= 0.3  # a high sound with a heavy low end is something else
    if bright_hi and f["zcr_hz"] > bright_hi:
        score -= 0.25
    if f["peak_db"] > -0.2:
        score -= 0.05  # clipped at the source
    if f["peak_db"] < -24:
        score -= 0.1  # very quiet
    return max(0.0, score)


# ---------------------------------------------------------------- the index


def sample_roots() -> list[Path]:
    roots = [
        Path(p).expanduser() for p in os.environ.get("FL_MCP_SAMPLES", "").split(os.pathsep) if p
    ]
    for install in paths.fl_install_dirs():
        for rel in (
            "Data/Patches/Packs",
            "Contents/Resources/FL/Data/Patches/Packs",
            "Contents/Resources/Data/Patches/Packs",
        ):
            roots.append(install / rel)
    roots.append(paths.fl_user_dir() / "Presets" / "Samples")
    seen, out = set(), []
    for r in roots:
        if r.is_dir() and r.resolve() not in seen:
            seen.add(r.resolve())
            out.append(r)
    return out


def plugin_dirs() -> list[Path]:
    base = paths.fl_user_dir() / "Presets" / "Plugin database"
    return [base] if base.is_dir() else []


def _classify_plugin_path(f: Path, base: Path, default_kind: str = "other") -> dict[str, Any]:
    rel = [p.lower() for p in f.relative_to(base).parts[:-1]]
    kind = (
        "effect"
        if any("effect" in p for p in rel)
        else "generator"
        if any("generator" in p for p in rel)
        else default_kind
    )
    fmt = next((p.upper() for p in rel if p in ("vst", "vst3", "au", "fruity", "clap")), "")
    return {"name": f.stem, "kind": kind, "format": fmt, "installed": int("installed" in rel)}


def _scan_plugins_and_presets() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    out: list[dict[str, Any]] = []
    presets: list[dict[str, Any]] = []
    seen_presets: set[str] = set()
    for base in plugin_dirs():
        for f in base.rglob("*"):
            suf = f.suffix.lower()
            if suf not in (".nfo", ".fst"):
                continue
            entry = _classify_plugin_path(f, base)
            out.append(entry)
            if suf == ".fst":
                fp = str(f)
                key = fp.lower()
                if key not in seen_presets:
                    seen_presets.add(key)
                    presets.append({**entry, "path": fp})
    user_presets = paths.fl_user_dir() / "Presets" / "Plugin presets"
    if user_presets.is_dir():
        for f in user_presets.rglob("*.fst"):
            fp = str(f)
            key = fp.lower()
            if key not in seen_presets:
                seen_presets.add(key)
                presets.append({**_classify_plugin_path(f, user_presets, "preset"), "path": fp})
    seen, uniq = set(), []
    for p in out:
        key = (p["name"].lower(), p["kind"])
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return (
        sorted(uniq, key=lambda p: p["name"].lower()),
        sorted(presets, key=lambda p: (-p["installed"], p["name"].lower())),
    )


def _plugins() -> list[dict[str, Any]]:
    return _scan_plugins_and_presets()[0]


def _soundfont_roots(samples: list[Path]) -> list[Path]:
    extra = paths.fl_user_dir() / "Presets" / "SoundFonts"
    seen = {r.resolve() for r in samples}
    out = list(samples)
    if extra.is_dir() and extra.resolve() not in seen:
        out.append(extra)
    return out


def _scan_soundfonts_on_disk(
    roots: list[Path] | None = None,
) -> list[dict[str, str]]:
    sf2_map: dict[str, dict[str, str]] = {}
    for root in _soundfont_roots(roots if roots is not None else sample_roots()):
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                p = Path(dirpath) / n
                if p.suffix.lower() == ".sf2":
                    sf2_map.setdefault(
                        str(p).lower(), {"p": str(p), "name": p.stem, "k": pack_of(p, root)}
                    )
    return sorted(sf2_map.values(), key=lambda s: s["name"].lower())


def index_path() -> Path:
    return paths.data_dir() / "library.json"


def scan(budget: float = 60.0) -> dict[str, Any]:
    """Walk the sample folders and the plugin database; save and summarise the index."""
    started, files, sf2_map, truncated = time.monotonic(), [], {}, False
    roots = sample_roots()
    for idx, root in enumerate(_soundfont_roots(roots)):
        collect_samples = idx < len(roots)
        for dirpath, _dirs, names in os.walk(root):
            for n in names:
                p = Path(dirpath) / n
                suf = p.suffix.lower()
                if collect_samples and suf in SAMPLE_EXT:
                    files.append({"p": str(p), "r": classify_name(p), "k": pack_of(p, root)})
                elif suf == ".sf2":
                    sf2_map.setdefault(
                        str(p).lower(), {"p": str(p), "name": p.stem, "k": pack_of(p, root)}
                    )
            if len(files) >= MAX_FILES or time.monotonic() - started > budget:
                truncated = True
                break
        if truncated:
            break
    plugins, presets = _scan_plugins_and_presets()
    index = {
        "v": 1,
        "at": int(time.time()),
        "roots": [str(r) for r in roots],
        "samples": files,
        "soundfonts": sorted(sf2_map.values(), key=lambda s: s["name"].lower()),
        "plugins": plugins,
        "presets": presets,
        "truncated": truncated,
    }
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return summary(index)


def load() -> dict[str, Any]:
    try:
        return json.loads(index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("no library index yet: run fl_library action=scan first") from None


def summary(index: dict[str, Any]) -> dict[str, Any]:
    roles: dict[str, int] = {}
    for s in index["samples"]:
        roles[s["r"]] = roles.get(s["r"], 0) + 1
    kinds: dict[str, int] = {}
    for p in index["plugins"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    return {
        "roots": index["roots"],
        "samples": len(index["samples"]),
        "soundfonts": len(index.get("soundfonts", [])),
        "by_role": dict(sorted(roles.items(), key=lambda kv: -kv[1])),
        "plugins": kinds,
        "presets": len(index.get("presets", [])),
        "truncated": index.get("truncated", False),
        "hint": "" if index["roots"] else "no sample folder found: set FL_MCP_SAMPLES to yours",
    }


def _features_cache() -> dict[str, Any]:
    try:
        raw = json.loads((paths.data_dir() / "library_features.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def features(path: str, cache: dict[str, Any]) -> dict[str, Any] | None:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = f"{st.st_size}:{int(st.st_mtime)}"
    hit = cache.get(path)
    if isinstance(hit, dict) and hit.get("k") == key:
        return hit.get("f")
    f = analyze(p)
    cache[path] = {"k": key, "f": f}
    return f


def _save_cache(cache: dict[str, Any]) -> None:
    target = paths.data_dir() / "library_features.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(cache, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def find(
    role: str | None = None,
    genre: str | None = None,
    query: str | None = None,
    limit: int = 10,
    measure: int = 40,
) -> list[dict[str, Any]]:
    """Best-matching samples: name first, then measured features of the top candidates."""
    index = load()
    words = [w for q in (query or "").lower().split() for w in [q]] + GENRE_WORDS.get(
        genre or "", []
    )
    scored = []
    for s in index["samples"]:
        if role and s["r"] != role:
            continue
        if not role and s["r"] in ("loop", "other"):
            continue
        hay = " ".join(tokens(s["p"]))
        hits = sum(1 for w in words if w in hay)
        scored.append((hits, s))
    scored.sort(key=lambda t: -t[0])
    cache = _features_cache()
    out = []
    for hits, s in scored[: max(measure, limit)]:
        f = features(s["p"], cache) if s["r"] in SHAPE or s["r"] == "hat" else None
        out.append({**s, "score": round(0.4 * min(hits, 3) / 3 + 0.6 * fit(s["r"], f), 3), "f": f})
    _save_cache(cache)
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def pick_kit(
    genre: str | None, roles: list[str], seed: int | None = None
) -> dict[str, dict[str, Any]]:
    """One sample per role, preferring a single pack so the kit sounds like it belongs together."""
    best = {r: find(r, genre, None, limit=25, measure=25) for r in roles}
    packs: dict[str, float] = {}
    for found in best.values():  # a pack scores by its best file for each role it covers
        top_of = {}
        for c in found:
            top_of[c["k"]] = max(top_of.get(c["k"], 0.0), c["score"])
        for pack, score in top_of.items():
            packs[pack] = packs.get(pack, 0.0) + score
    order = sorted(packs, key=lambda k: -packs[k])
    rng = random.Random(seed if seed is not None else 0)
    top = [k for k in order[:3] if packs[k] >= 0.85 * packs[order[0]]]  # only near-equals vary
    rng.shuffle(top)
    order = top + [k for k in order if k not in top]
    kit: dict[str, dict[str, Any]] = {}
    for r in roles:
        pool = best[r]
        pick = next(
            (c for pack in order for c in pool if c["k"] == pack), pool[0] if pool else None
        )
        if pick:
            kit[r] = pick
    return kit


def list_plugins(query: str | None = None, kind: str | None = None, limit: int = 40) -> list[dict]:
    index = load()
    q = (query or "").lower()
    rows = [
        p
        for p in index["plugins"]
        if (not q or q in p["name"].lower()) and (not kind or p["kind"] == kind)
    ]
    return rows[:limit]


def list_presets(query: str | None = None, kind: str | None = None, limit: int = 40) -> list[dict]:
    """List indexed `.fst` presets (scanning plugin folders on demand if index lacks them)."""
    try:
        rows = load().get("presets")
    except ValueError:
        rows = None
    if rows is None:
        rows = _scan_plugins_and_presets()[1]
    q = (query or "").lower()
    out = [p for p in rows if (not q or q in p["name"].lower()) and (not kind or p["kind"] == kind)]
    return out[:limit]


def find_preset(query: str, kind: str | None = None) -> Path | None:
    """Resolve a plugin or preset name to a `.fst` file path (exact stem match first)."""
    q = query.strip().lower()
    if q.endswith(".fst"):
        q = q[:-4]
    if not q:
        return None
    candidates = [
        p for p in list_presets(query=None, kind=kind, limit=5000) if Path(p["path"]).is_file()
    ]
    exact = [p for p in candidates if p["name"].lower() == q]
    pool = exact or [p for p in candidates if q in p["name"].lower()]
    if not pool:
        return None
    pool.sort(key=lambda p: (p["kind"] != "generator", -int(p.get("installed", 0)), len(p["name"])))
    return Path(pool[0]["path"])


def list_soundfonts(query: str | None = None, limit: int = 40) -> list[dict[str, str]]:
    """List `.sf2` SoundFonts (from the index, or scanning disk if not indexed yet)."""
    try:
        rows = load().get("soundfonts")
    except ValueError:
        rows = None
    if rows is None:
        rows = _scan_soundfonts_on_disk()
    q = (query or "").lower()
    out = [s for s in rows if not q or q in s["name"].lower() or q in s["k"].lower()]
    return out[:limit]


def find_soundfont(query: str) -> Path | None:
    """Resolve a SoundFont name or `.sf2` filename to a file path on disk."""
    q = query.strip().lower()
    if q.endswith(".sf2"):
        q = q[:-4]
    if not q:
        return None
    candidates = [s for s in list_soundfonts(limit=5000) if Path(s["p"]).is_file()]
    exact = [s for s in candidates if s["name"].lower() == q]
    pool = exact or [s for s in candidates if q in s["name"].lower()]
    return Path(pool[0]["p"]) if pool else None
