"""Offline AI Models, Audio Packs, UI Typography/Icons & Rulebook Vault Manager.

Manages the local `~/.fl-mcp/` asset and model vault (`models/`, `assets/soundfonts/`,
`assets/samples/`, `assets/irs/`, `assets/midi/`), bundled self-hosted UI fonts/icons,
installed DSP/AI Python frameworks, and autonomous production/mixing/UI rulebooks.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.resources
import importlib.util
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fl_mcp import paths

RULE_BEGIN = "<!-- BEGIN FL-MCP RULES -->"
RULE_END = "<!-- END FL-MCP RULES -->"
_UA = {"User-Agent": "fl-mcp-vault/0.3"}

_PKG_DIST_MAP = {
    "PIL": "pillow",
    "pretty_midi": "pretty-midi",
    "webview": "pywebview",
}


def _data_path(*parts: str) -> Path:
    local = Path(__file__).with_name("data").joinpath(*parts)
    if local.exists():
        return local
    return Path(str(importlib.resources.files("fl_mcp").joinpath("data", *parts)))


def load_catalog() -> dict[str, Any]:
    """Load the structured catalog of downloadable models, audio packs, frameworks, and rules."""
    p = _data_path("catalog.json")
    return json.loads(p.read_text(encoding="utf-8"))


def rule_topics() -> list[dict[str, str]]:
    """Return all available autonomous rulebook topics."""
    cat = load_catalog()
    out: list[dict[str, str]] = []
    for item in cat.get("rules", []):
        p = _data_path("rules", item["file"])
        out.append(
            {
                "id": item["id"],
                "title": item["title"],
                "file": item["file"],
                "uri": f"fl://rules/{item['id']}",
                "bytes": p.stat().st_size if p.is_file() else 0,
            }
        )
    return out


def read_rule(topic: str) -> str:
    """Read a single rulebook markdown document by topic id (e.g. ``production``, ``mixing``)."""
    clean = topic.strip().lower().replace("-", "_")
    for item in load_catalog().get("rules", []):
        if item["id"] == clean:
            return _data_path("rules", item["file"]).read_text(encoding="utf-8")
    valid = ", ".join(r["id"] for r in load_catalog().get("rules", []))
    raise ValueError(f"unknown rulebook '{topic}' (choose from: {valid})")


def all_rules_markdown() -> str:
    """Concatenate all FL-MCP rulebooks into a single autonomous agent specification."""
    sections = [
        RULE_BEGIN,
        "# FL-MCP Autonomous Studio & Production Rules",
        "",
    ]
    for item in load_catalog().get("rules", []):
        body = _data_path("rules", item["file"]).read_text(encoding="utf-8").strip()
        sections.append(body)
        sections.append("")
    sections.append(RULE_END)
    return "\n".join(sections)


def frameworks_status() -> list[dict[str, Any]]:
    """Inspect installed status and version of optional AI, DSP, MIDI, and Desktop frameworks."""
    out: list[dict[str, Any]] = []
    for spec in load_catalog().get("frameworks", []):
        mod = spec["module"]
        dist_name = _PKG_DIST_MAP.get(mod, mod)
        installed = False
        version = ""
        try:
            if importlib.util.find_spec(mod) is not None:
                installed = True
                try:
                    version = importlib.metadata.version(dist_name)
                except importlib.metadata.PackageNotFoundError:
                    version = "installed"
        except Exception:
            installed = False
        out.append(
            {
                "module": mod,
                "package": dist_name,
                "extra": spec.get("extra", ""),
                "role": spec.get("role", ""),
                "installed": installed,
                "version": version,
            }
        )
    return out


def _dir_files(folder: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in suffixes)


def status() -> dict[str, Any]:
    """Return full inventory of downloaded models, audio packs, UI assets, frameworks & rules."""
    cat = load_catalog()
    mdir = paths.models_dir()
    adir = paths.assets_dir()
    sf2_dir = adir / "soundfonts"
    smp_dir = adir / "samples"
    ir_dir = adir / "irs"
    wf_dir = adir / "waveforms"
    midi_dir = adir / "midi"
    py_dir = adir / "pyscripts"
    pst_dir = adir / "presets"
    scr_dir = adir / "scores"
    scl_dir = adir / "scales"
    tpl_dir = paths.data_dir() / "templates"
    ui_dir = Path(__file__).with_name("assets")

    models: list[dict[str, Any]] = []
    for m in cat.get("models", []):
        fp = mdir / m["filename"]
        exists = fp.is_file()
        sz = fp.stat().st_size if exists else 0
        models.append(
            {
                "id": m["id"],
                "filename": m["filename"],
                "title": m["title"],
                "role": m["role"],
                "license": m["license"],
                "installed": exists and sz >= 50_000,
                "bytes": sz,
                "expected_bytes": m.get("bytes", 0),
                "path": str(fp),
            }
        )

    sf2_files = _dir_files(sf2_dir, (".sf2",))
    smp_files = _dir_files(smp_dir, (".wav", ".flac", ".ogg", ".aif", ".aiff"))
    ir_files = _dir_files(ir_dir, (".wav", ".flac", ".ogg"))
    wf_files = _dir_files(wf_dir, (".wav",))
    midi_files = _dir_files(midi_dir, (".mid", ".midi", ".fsc"))
    py_files = _dir_files(py_dir, (".pyscript",))
    pst_files = _dir_files(pst_dir, (".fst",))
    scr_files = _dir_files(scr_dir, (".fsc",))
    scl_files = _dir_files(scl_dir, (".scl",))
    tpl_files = _dir_files(tpl_dir, (".flp",))
    font_files = [
        ui_dir / f for f in cat.get("ui_assets", {}).get("fonts", []) if (ui_dir / f).is_file()
    ]
    sprite_path = ui_dir / cat.get("ui_assets", {}).get("icons_sprite", "lucide-sprite.svg")

    fws = frameworks_status()
    rules = rule_topics()

    models_bytes = sum(m["bytes"] for m in models)
    sf2_bytes = sum(f.stat().st_size for f in sf2_files)
    smp_bytes = sum(f.stat().st_size for f in smp_files)
    ir_bytes = sum(f.stat().st_size for f in ir_files)
    wf_bytes = sum(f.stat().st_size for f in wf_files)
    midi_bytes = sum(f.stat().st_size for f in midi_files)
    py_bytes = sum(f.stat().st_size for f in py_files)
    pst_bytes = sum(f.stat().st_size for f in pst_files)
    scr_bytes = sum(f.stat().st_size for f in scr_files)
    scl_bytes = sum(f.stat().st_size for f in scl_files)
    tpl_bytes = sum(f.stat().st_size for f in tpl_files)
    ui_bytes = sum(f.stat().st_size for f in font_files) + (
        sprite_path.stat().st_size if sprite_path.is_file() else 0
    )

    models_ok = sum(1 for m in models if m["installed"])
    fw_ok = sum(1 for f in fws if f["installed"])

    return {
        "models_dir": str(mdir),
        "assets_dir": str(adir),
        "models": models,
        "audio": {
            "soundfonts": {
                "count": len(sf2_files),
                "bytes": sf2_bytes,
                "files": [f.name for f in sf2_files[:12]],
                "dir": str(sf2_dir),
            },
            "samples": {
                "count": len(smp_files),
                "bytes": smp_bytes,
                "packs": sorted({f.parent.name for f in smp_files}),
                "dir": str(smp_dir),
            },
            "irs": {
                "count": len(ir_files),
                "bytes": ir_bytes,
                "files": [f.name for f in ir_files[:18]],
                "dir": str(ir_dir),
            },
            "waveforms": {
                "count": len(wf_files),
                "bytes": wf_bytes,
                "packs": sorted({f.parent.name for f in wf_files}),
                "dir": str(wf_dir),
            },
            "midi": {
                "count": len(midi_files),
                "bytes": midi_bytes,
                "files": [f.name for f in midi_files[:16]],
                "dir": str(midi_dir),
            },
        },
        "fl_studio": {
            "pyscripts": {
                "count": len(py_files),
                "bytes": py_bytes,
                "files": [f.name for f in py_files[:20]],
                "dir": str(py_dir),
            },
            "presets": {
                "count": len(pst_files),
                "bytes": pst_bytes,
                "files": [f.name for f in pst_files[:20]],
                "dir": str(pst_dir),
            },
            "scores": {
                "count": len(scr_files),
                "bytes": scr_bytes,
                "files": [f.name for f in scr_files[:24]],
                "dir": str(scr_dir),
            },
            "templates": {
                "count": len(tpl_files),
                "bytes": tpl_bytes,
                "files": [f.name for f in tpl_files[:16]],
                "dir": str(tpl_dir),
            },
            "scales": {
                "count": len(scl_files),
                "bytes": scl_bytes,
                "dir": str(scl_dir),
            },
        },
        "ui": {
            "fonts_count": len(font_files),
            "fonts": [f.name for f in font_files],
            "sprite_installed": sprite_path.is_file(),
            "sprite_bytes": sprite_path.stat().st_size if sprite_path.is_file() else 0,
            "bytes": ui_bytes,
        },
        "frameworks": fws,
        "rules": rules,
        "summary": {
            "models_installed": models_ok,
            "models_total": len(models),
            "soundfonts_count": len(sf2_files),
            "samples_count": len(smp_files),
            "irs_count": len(ir_files),
            "waveforms_count": len(wf_files),
            "midi_count": len(midi_files),
            "pyscripts_count": len(py_files),
            "presets_count": len(pst_files),
            "scores_count": len(scr_files),
            "templates_count": len(tpl_files),
            "scales_count": len(scl_files),
            "ui_fonts_count": len(font_files),
            "rules_count": len(rules),
            "frameworks_installed": fw_ok,
            "frameworks_total": len(fws),
            "total_vault_bytes": models_bytes
            + sf2_bytes
            + smp_bytes
            + ir_bytes
            + wf_bytes
            + midi_bytes
            + py_bytes
            + pst_bytes
            + scr_bytes
            + scl_bytes
            + tpl_bytes
            + ui_bytes,
            "ready": models_ok == len(models) and len(sf2_files) >= 1 and len(smp_files) >= 10,
        },
    }


def _default_fetch(url: str, timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _generate_default_midi(midi_dir: Path) -> int:
    """Create 12 genre starter MIDI files using mido (zero extra binary dependency required)."""
    import mido

    specs = [
        ("trap_minor_140.mid", 140, [(57, 60, 64), (53, 57, 60), (55, 59, 62), (52, 55, 59)]),
        ("drill_dark_142.mid", 142, [(50, 53, 57), (50, 53, 58), (48, 51, 55), (49, 53, 56)]),
        (
            "house_classic_124.mid",
            124,
            [(53, 57, 60, 64), (55, 59, 62, 65), (57, 60, 64, 67), (52, 55, 59, 62)],
        ),
        ("techno_hypnotic_132.mid", 132, [(45, 52, 57), (45, 52, 58), (43, 50, 55), (45, 52, 57)]),
        (
            "lofi_jazzy_82.mid",
            82,
            [(62, 65, 69, 72), (55, 59, 62, 65), (60, 64, 67, 71), (57, 60, 64, 67)],
        ),
        ("synthwave_80s_112.mid", 112, [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]),
        (
            "boombap_soul_90.mid",
            90,
            [(58, 61, 65, 68), (56, 60, 63, 67), (54, 58, 61, 65), (53, 56, 60, 63)],
        ),
        (
            "dnb_liquid_174.mid",
            174,
            [(50, 53, 57, 60), (58, 62, 65, 69), (53, 57, 60, 64), (55, 58, 62, 65)],
        ),
        (
            "rnb_velvet_96.mid",
            96,
            [(60, 63, 67, 70), (58, 62, 65, 69), (56, 60, 63, 67), (55, 59, 62, 67)],
        ),
        ("hyperpop_glitch_160.mid", 160, [(60, 64, 67), (67, 71, 74), (69, 72, 76), (65, 69, 72)]),
        (
            "ambient_drone_70.mid",
            70,
            [(48, 55, 62, 64), (53, 60, 65, 69), (50, 57, 62, 65), (55, 62, 67, 71)],
        ),
        ("cinematic_epic_100.mid", 100, [(50, 53, 57), (58, 62, 65), (55, 58, 62), (57, 61, 64)]),
    ]
    midi_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for fname, bpm, chords in specs:
        dest = midi_dir / fname
        if dest.is_file() and dest.stat().st_size > 80:
            created += 1
            continue
        mid = mido.MidiFile(ticks_per_beat=480)
        trk = mido.MidiTrack()
        mid.tracks.append(trk)
        trk.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))
        bar_ticks = 480 * 4
        for chord in chords:
            for note in chord:
                trk.append(mido.Message("note_on", note=note, velocity=84, time=0))
            for idx, note in enumerate(chord):
                trk.append(
                    mido.Message(
                        "note_off",
                        note=note,
                        velocity=0,
                        time=bar_ticks if idx == 0 else 0,
                    )
                )
        mid.save(str(dest))
        created += 1
    return created


def _generate_default_fl_resources(adir: Path, data_dir: Path) -> dict[str, int]:
    """Generate native FL Studio binary .fsc scores, 12-channel .flp templates & .pyscript tools."""
    import struct

    from fl_mcp import flp
    from fl_mcp.flp import Event

    scr_dir = adir / "scores"
    scr_dir.mkdir(parents=True, exist_ok=True)
    score_specs = {
        "Trap_Minor_140.fsc": [[57, 0.0, 2.0, 92], [60, 0.0, 2.0, 88], [64, 0.0, 2.0, 86]],
        "Drill_Dark_Sliding_142.fsc": [[50, 0.0, 1.5, 96], [53, 0.0, 1.5, 88], [58, 1.5, 0.5, 102]],
        "House_Piano_Stabs_124.fsc": [
            [53, 0.0, 0.75, 98],
            [57, 0.0, 0.75, 94],
            [60, 0.0, 0.75, 92],
        ],
        "MPC60_Swing_58pct.fsc": [
            [60, (i // 2) * 0.5 + (0.29 if i % 2 else 0.0), 0.2, 96 if i % 2 == 0 else 82]
            for i in range(16)
        ],
        "SP1200_BoomBap_Pocket.fsc": [
            [60, (i // 2) * 0.5 + (0.305 if i % 2 else 0.0), 0.22, 95 if i % 4 == 0 else 80]
            for i in range(16)
        ],
        "Trap_HiHat_Roll_32nd_Stutter.fsc": [
            [60, i * 0.125, 0.11, 65 + (i % 4) * 10] for i in range(16)
        ],
    }
    for fname, notes in score_specs.items():
        dest = scr_dir / fname
        if not dest.is_file():
            flp.write_fsc(dest, notes, ppq=96)

    py_dir = adir / "pyscripts"
    py_dir.mkdir(parents=True, exist_ok=True)
    euclid = py_dir / "FL-MCP Euclidean Rhythm.pyscript"
    if not euclid.is_file():
        euclid.write_text(
            '"""FL-MCP Euclidean Rhythm Generator."""\nimport flpianoroll as flp\n',
            encoding="utf-8",
        )

    tpl_dir = data_dir / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    tpl_file = tpl_dir / "FL-MCP_House_124.flp"
    if not tpl_file.is_file():
        txt = lambda s: s.encode("utf-16le") + b"\x00\x00"  # noqa: E731
        levels = struct.pack("<IIiIII", 6400, 10000, 0, 256, 256, 0)
        item_blob = struct.pack(
            "<IHHIHH2sH4sff",
            0,
            20480,
            20481,
            384,
            499,
            0,
            bytes([120, 0]),
            64,
            bytes([64, 100, 128, 128]),
            -1.0,
            -1.0,
        )
        ev = [
            Event(199, b"20.8.4.2576\x00"),
            Event(156, struct.pack("<I", 124000)),
            Event(65, struct.pack("<H", 1)),
            Event(224, flp.note_record(0, 24, 60, 0, 100)),
            Event(64, struct.pack("<H", 0)),
            Event(21, bytes([0])),
            Event(201, b"\x00\x00"),
            Event(212, bytes(52)),
            Event(203, txt("Kick")),
            Event(155, bytes(4)),
            Event(128, bytes([0x5C, 0x65, 0x6A, 0])),
            Event(0, b"\x01"),
            Event(22, bytes([1])),
            Event(219, levels),
            Event(215, bytes(158)),
            Event(143, struct.pack("<I", 10)),
            Event(20, b"\x00"),
            Event(65, struct.pack("<H", 1)),
            Event(193, txt("Seed")),
            Event(150, bytes([72, 81, 86, 0])),
            Event(157, b"\xff\xff\xff\xff"),
            Event(158, b"\xff\xff\xff\xff"),
            Event(164, bytes(4)),
            Event(99, struct.pack("<H", 1)),
            Event(203, txt("Arrangement")),
            Event(36, b"\x00"),
            Event(233, item_blob),
        ]
        body = b"".join(e.encode() for e in ev)
        tpl_file.write_bytes(
            b"FLhd"
            + struct.pack("<IHHH", 6, 0, 1, 96)
            + b"FLdt"
            + struct.pack("<I", len(body))
            + body
        )

    return {
        "scores": len(_dir_files(scr_dir, (".fsc",))),
        "pyscripts": len(_dir_files(py_dir, (".pyscript",))),
        "templates": len(_dir_files(tpl_dir, (".flp",))),
    }


def download(
    target: str = "all",
    *,
    force: bool = False,
    fetcher: Any = None,
    rescan_library: bool = True,
) -> dict[str, Any]:
    """Download and verify vault categories (all, models, soundfonts, samples, irs, midi)."""
    get_bytes = fetcher or _default_fetch
    cat = load_catalog()
    want = (
        {target.lower()}
        if target.lower() != "all"
        else {"models", "soundfonts", "samples", "irs", "midi", "fl_studio"}
    )
    mdir = paths.models_dir()
    adir = paths.assets_dir()
    downloaded: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    if "models" in want:
        mdir.mkdir(parents=True, exist_ok=True)
        for m in cat.get("models", []):
            dest = mdir / m["filename"]
            if dest.is_file() and not force and dest.stat().st_size >= 50_000:
                skipped.append(m["filename"])
                continue
            ok = False
            for url in m.get("urls", []):
                try:
                    data = get_bytes(url)
                    if m.get("sha256") and _sha256(data) != m["sha256"]:
                        raise ValueError(f"SHA-256 mismatch for {m['filename']}")
                    dest.write_bytes(data)
                    downloaded.append(m["filename"])
                    ok = True
                    break
                except Exception as exc:
                    errors.append(f"{m['filename']}: {exc}")
            if ok:
                errors = [e for e in errors if not e.startswith(f"{m['filename']}:")]

    if "soundfonts" in want:
        sf2_dir = adir / "soundfonts"
        sf2_dir.mkdir(parents=True, exist_ok=True)
        for sf in cat.get("soundfonts", []):
            dest = sf2_dir / sf["filename"]
            if dest.is_file() and not force and dest.stat().st_size >= 1_000_000:
                skipped.append(sf["filename"])
                continue
            for url in sf.get("urls", []):
                try:
                    data = get_bytes(url)
                    if data[:4] != b"RIFF":
                        raise ValueError(f"{sf['filename']} missing RIFF header")
                    dest.write_bytes(data)
                    downloaded.append(sf["filename"])
                    break
                except Exception as exc:
                    errors.append(f"{sf['filename']}: {exc}")

    if "samples" in want:
        smp_dir = adir / "samples"
        smp_dir.mkdir(parents=True, exist_ok=True)
        existing_wavs = _dir_files(smp_dir, (".wav",))
        if len(existing_wavs) >= 24 and not force:
            skipped.append(f"samples ({len(existing_wavs)} wavs)")
        else:
            for pack in cat.get("sample_packs", []):
                base_api = pack.get("repo_api", "")
                if not base_api:
                    continue
                for folder in pack.get("folders", []):
                    fdir = smp_dir / f"Dirt-{folder}"
                    fdir.mkdir(parents=True, exist_ok=True)
                    try:
                        listing = json.loads(get_bytes(f"{base_api}/{folder}").decode("utf-8"))
                    except Exception as exc:
                        errors.append(f"Dirt-{folder}: {exc}")
                        continue
                    tasks = []
                    for it in (listing if isinstance(listing, list) else [])[:6]:
                        name = it.get("name", "")
                        dl = it.get("download_url")
                        if dl and name.lower().endswith(".wav"):
                            tasks.append((folder, name, dl, fdir / name))

                    def _dl_wav(t: tuple[str, str, str, Path]) -> str | None:
                        fld, nm, u, d = t
                        if d.is_file() and not force and d.stat().st_size > 200:
                            return None
                        try:
                            blob = get_bytes(u)
                            if blob[:4] == b"RIFF":
                                d.write_bytes(blob)
                                return f"Dirt-{fld}/{nm}"
                        except Exception:
                            return None
                        return None

                    with ThreadPoolExecutor(max_workers=6) as ex:
                        for res in ex.map(_dl_wav, tasks):
                            if res:
                                downloaded.append(res)

    if "irs" in want:
        ir_dir = adir / "irs"
        ir_dir.mkdir(parents=True, exist_ok=True)
        existing_irs = _dir_files(ir_dir, (".wav",))
        if len(existing_irs) >= 6 and not force:
            skipped.append(f"irs ({len(existing_irs)} wavs)")
        else:
            for pack in cat.get("ir_packs", []):
                if not pack.get("repo_api"):
                    continue
                try:
                    listing = json.loads(get_bytes(pack["repo_api"]).decode("utf-8"))
                    for it in (listing if isinstance(listing, list) else [])[:18]:
                        name = it.get("name", "").replace(" ", "_")
                        dl = it.get("download_url")
                        if dl and name.lower().endswith(".wav"):
                            dest = ir_dir / name
                            if dest.is_file() and not force:
                                continue
                            blob = get_bytes(dl)
                            if blob[:4] == b"RIFF":
                                dest.write_bytes(blob)
                                downloaded.append(name)
                except Exception as exc:
                    errors.append(f"irs: {exc}")

    if "midi" in want:
        midi_dir = adir / "midi"
        count = _generate_default_midi(midi_dir)
        downloaded.append(f"midi_pack ({count} files)")

    if want & {"fl_studio", "scores", "templates", "pyscripts"}:
        fl_counts = _generate_default_fl_resources(adir, paths.data_dir())
        downloaded.append(
            f"fl_resources (scores={fl_counts['scores']}, "
            f"templates={fl_counts['templates']}, pyscripts={fl_counts['pyscripts']})"
        )

    lib_summary = None
    if rescan_library:
        try:
            from fl_mcp import library

            idx = library.scan()
            s_val = idx.get("samples", 0)
            sf_val = idx.get("soundfonts", 0)
            lib_summary = {
                "samples": s_val if isinstance(s_val, int) else len(s_val),
                "soundfonts": sf_val if isinstance(sf_val, int) else len(sf_val),
            }
        except Exception:
            lib_summary = None

    return {
        "ok": len(errors) == 0,
        "target": target,
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "library": lib_summary,
        "status": status()["summary"],
    }


def _upsert_rule_block(path: Path, block: str) -> str:
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if RULE_BEGIN in existing and RULE_END in existing:
            pre = existing.split(RULE_BEGIN, 1)[0]
            post = existing.split(RULE_END, 1)[1]
            updated = pre.rstrip() + "\n\n" + block.strip() + "\n" + post.lstrip()
            path.write_text(updated, encoding="utf-8")
            return "updated"
        updated = existing.rstrip() + "\n\n" + block.strip() + "\n"
        path.write_text(updated, encoding="utf-8")
        return "appended"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block.strip() + "\n", encoding="utf-8")
    return "created"


def install_rules(
    workspace: str | Path | None = None,
    targets: tuple[str, ...] = ("agents", "claude", "cursor"),
) -> dict[str, Any]:
    """Write or update FL-MCP autonomous production/mixing/UI rulebooks into agent rule files."""
    root = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()
    block = all_rules_markdown()
    written: list[dict[str, str]] = []
    want = {t.lower() for t in targets}
    if "all" in want:
        want = {"agents", "claude", "cursor"}

    if "agents" in want:
        p = root / "AGENTS.md"
        act = _upsert_rule_block(p, block)
        written.append({"target": "agents", "path": str(p), "action": act})
    if "claude" in want:
        p = root / "CLAUDE.md"
        act = _upsert_rule_block(p, block)
        written.append({"target": "claude", "path": str(p), "action": act})
    if "cursor" in want:
        p = root / ".cursor" / "rules" / "fl-mcp.mdc"
        frontmatter = (
            "---\n"
            "description: FL-MCP Autonomous FL Studio Production, Mixing, "
            "Mastering & Studio UI Rules\n"
            "globs: *\n"
            "alwaysApply: true\n"
            "---\n\n"
        )
        act = _upsert_rule_block(p, frontmatter + block)
        written.append({"target": "cursor", "path": str(p), "action": act})

    return {"ok": True, "workspace": str(root), "written": written}
