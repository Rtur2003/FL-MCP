"""Plan delivery: live into a running FL Studio, or as a MIDI file. Also plan storage."""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fl_mcp import flp, paths
from fl_mcp.music.genres import ROLE_ORDER
from fl_mcp.music.midi import plan_to_midi
from fl_mcp.music.plan import Part, Plan

Call = Callable[[dict[str, Any]], Any]
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
BATCH_BYTES = 12000  # stays well under the bridge request limit
GROUP_COLORS = {
    "Drums": "#e8573d",
    "Bass": "#8a5cf5",
    "Harmony": "#3da5e8",
    "Melody": "#f2c14e",
    "FX": "#6fcf97",
}
ALIASES = {
    "ohat": ("open hat", "openhat", "ohat", "oh", "open"),
    "hat": ("hihat", "hi hat", "hat", "hh", "chh", "closed"),
    "kick": ("kick", "bassdrum", "bass drum", "bd", "kck"),
    "snare": ("snare", "snr", "sd"),
    "clap": ("clap", "handclap", "clp"),
    "perc": ("perc", "percussion", "shaker", "rim", "tamb", "conga", "bongo", "tom"),
    "bass": ("bass", "808", "sub", "reese"),
    "chords": ("chord", "keys", "piano", "rhodes", "organ", "stab", "guitar"),
    "pad": ("pad", "strings", "atmos", "ambient"),
    "lead": ("lead", "melody", "hook", "pluck", "bell", "flute", "saz", "ney"),
    "arp": ("arp", "arpeggio", "sequence"),
    "fx": ("fx", "riser", "sweep", "impact", "effect", "noise", "uplifter", "downlifter"),
}
_ID = re.compile(r"^[a-z_]+-[0-9a-f]{1,8}$")


# ---------------------------------------------------------------- plan storage


def plans_dir() -> Path:
    return paths.data_dir() / "plans"


def save_plan(plan: Plan) -> Path:
    folder = plans_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{plan.id}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(plan.to_json(), encoding="utf-8")
    os.replace(tmp, path)
    for old in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)[:-30]:
        old.unlink(missing_ok=True)
        old.with_suffix(".live").unlink(missing_ok=True)
    return path


def load_plan(plan_id: str = "") -> Plan:
    folder = plans_dir()
    if plan_id:
        if not _ID.match(plan_id):
            raise ValueError(f"bad plan id '{plan_id}'")
        path = folder / f"{plan_id}.json"
    else:
        found = (
            sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
            if folder.is_dir()
            else []
        )
        if not found:
            raise ValueError("no plan yet: create one with fl_produce action=plan or make")
        path = found[-1]
    if not path.is_file():
        raise ValueError(f"unknown plan '{plan_id}'")
    return Plan.from_dict(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- roles


def detect_roles(names: list[str]) -> tuple[dict[str, int], list[str]]:
    """Map instrument roles to channel indices by name. Returns (mapping, unmatched names)."""
    best: dict[str, tuple[int, int]] = {}
    unmatched = []
    for i, name in enumerate(names):
        text = " " + re.sub(r"[^a-z0-9]+", " ", name.lower()).strip() + " "
        hits = [
            (len(a), role)
            for role, aliases in ALIASES.items()
            for a in aliases
            if f" {a} " in text or (len(a) >= 4 and a in text)
        ]
        if not hits:
            unmatched.append(name)
            continue
        score, role = max(hits)
        if role not in best or score > best[role][0]:
            best[role] = (score, i)
    return {r: best[r][1] for r in ROLE_ORDER if r in best}, unmatched


def parse_map(text: str) -> dict[str, int]:
    """'kick=0, bass=3' -> {'kick': 0, 'bass': 3}"""
    out = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        role, _, idx = part.partition("=")
        if role.strip() not in ROLE_ORDER or not idx.strip().isdigit():
            raise ValueError(
                f"channel map entries look like 'kick=0' (roles: {', '.join(ROLE_ORDER)})"
            )
        out[role.strip()] = int(idx)
    return out


# ---------------------------------------------------------------- live delivery


def grid_string(part: Part, bars: int) -> str:
    """One char per 16th step: '.' off, otherwise base-36 velocity (1..35 -> 4..127)."""
    cells = [0] * (bars * 16)
    for e in part.events:
        s = round(e.t * 4)
        if 0 <= s < len(cells):
            cells[s] = max(cells[s], max(1, round(e.v * 35 / 127)))
    return "".join("." if c == 0 else B36[c] for c in cells)


def rows(call: Call, kind: str) -> list[list[Any]]:
    out, start = [], 0
    while start is not None:
        page = call({"o": "list", "k": kind, "s": start, "n": 200})
        out += page["rows"]
        start = page["next"]
    return out


def send(call: Call, ops: list[dict[str, Any]], label: str) -> int:
    """Run ops as size-limited batches; returns the number of steps FL could not keep."""
    chunks: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    size = 0
    for op in ops:
        n = len(json.dumps(op, separators=(",", ":")))
        if cur and (size + n > BATCH_BYTES or len(cur) >= 250):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(op)
        size += n
    if cur:
        chunks.append(cur)
    bad = 0
    for i, chunk in enumerate(chunks, 1):
        bad += call({"o": "batch", "u": f"{label} {i}/{len(chunks)}", "s": chunk})["bad"]
    return bad


def apply_live(
    call: Call, plan: Plan, override: dict[str, int] | None = None, tidy: bool = False
) -> dict[str, Any]:
    ppq = call({"o": "status"}).get("ppq") or 96
    chans = rows(call, "channels")
    mapping, unmatched = detect_roles([c[1] for c in chans])
    mapping.update(override or {})
    bad_idx = [r for r, c in mapping.items() if not 0 <= c < len(chans)]
    if bad_idx:
        raise ValueError(
            f"channel index out of range for {', '.join(bad_idx)} (0..{len(chans) - 1})"
        )
    used = [p[0] for p in rows(call, "patterns")]
    base = max(used, default=0)
    if base + len(plan.patterns) > 999:
        raise ValueError(
            "FL Studio allows 999 patterns; remove unused ones or pick a shorter length"
        )
    swing = round((plan.swing / 100 - 0.5) * 0.5 * 96)
    ops: list[dict[str, Any]] = [{"o": "tr", "a": "tempo", "v": plan.bpm}]
    delivered: dict[str, int] = {}
    for i, pat in enumerate(plan.patterns):
        num = base + 1 + i
        group = pat.name.rsplit(" - ", 1)[-1]
        ops.append(
            {
                "o": "set",
                "k": "pattern",
                "i": num,
                "p": {"name": pat.name, "color": GROUP_COLORS.get(group, "#999999")},
            }
        )
        for role, part in pat.parts.items():
            if part.grid and role in mapping:
                ops.append(
                    {
                        "o": "grid",
                        "c": mapping[role],
                        "p": num,
                        "g": grid_string(part, pat.bars),
                        "sw": swing,
                    }
                )
                delivered[role] = delivered.get(role, 0) + 1
    ops.append({"o": "markers", "m": [[s.bar * 4 * ppq, s.name] for s in plan.sections][:64]})
    for role, ch in mapping.items():
        mix = plan.mix.get(role)
        if mix is None or role not in plan.roles:
            continue
        track = chans[ch][7]
        if track >= 1:
            ops.append({"o": "set", "k": "mixer", "i": track, "p": {"pan": mix.pan}})
            ops += [{"o": "eq", "i": track, "b": int(b), "f": f, "g": g} for b, f, g in mix.eq]
            if tidy and re.fullmatch(r"Insert \d+", str(_track_name(call, track))):
                ops.append({"o": "set", "k": "mixer", "i": track, "p": {"name": role.capitalize()}})
        else:
            ops.append({"o": "set", "k": "channel", "i": ch, "p": {"pan": mix.pan}})
    bad = send(call, ops, f"produce {plan.genre}")
    live = {"base": base, "mapping": mapping, "ppq": ppq}
    plans_dir().mkdir(parents=True, exist_ok=True)
    (plans_dir() / f"{plan.id}.live").write_text(json.dumps(live), encoding="utf-8")
    manual = sorted(
        {r for p in plan.patterns for r, x in p.parts.items() if not x.grid and r in mapping}
    )
    return {
        "patterns": [base + 1, base + len(plan.patterns)],
        "bpm": plan.bpm,
        "channels": {r: c for r, c in mapping.items() if r in plan.roles},
        "steps_delivered": delivered,
        "no_channel": [r for r in plan.roles if r not in mapping],
        "needs_notes": manual,
        "unmatched_channels": unmatched,
        "not_kept_by_fl": bad,
    }


def _track_name(call: Call, track: int) -> str:
    page = call({"o": "list", "k": "mixer", "s": track, "n": 1, "a": 1})
    return page["rows"][0][1] if page["rows"] else ""


def mapping_for(plan_id: str, names: list[str]) -> dict[str, int]:
    """Role to channel map used when the plan was applied, else guessed from channel names."""
    try:
        return live_state(plan_id)["mapping"]
    except ValueError:
        return detect_roles(names)[0]


def live_state(plan_id: str) -> dict[str, Any]:
    path = plans_dir() / f"{plan_id}.live"
    if not path.is_file():
        raise ValueError("apply this plan to FL Studio first (fl_produce action=apply)")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- files


def export_midi(plan: Plan, drums: str = "fl") -> Path:
    folder = paths.data_dir() / "out"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{plan.id}{'-gm' if drums == 'gm' else ''}.mid"
    path.write_bytes(plan_to_midi(plan, drums))
    return path


def step_view(on: list[int], length: int, bar_steps: int = 16, grid: str | None = None) -> str:
    """Visual bar-grouped 16th-step strip, e.g. 'x...x...x...x...|....x.......x...'."""
    if grid:
        clean = re.sub(r"[\s|-]+", "", grid).lower()
        chunks = [clean[i : i + bar_steps] for i in range(0, len(clean), bar_steps)]
        return "|".join(chunks)
    active = set(on)
    chars = ["x" if i in active else "." for i in range(length)]
    return "|".join("".join(chars[i : i + bar_steps]) for i in range(0, length, bar_steps))


def _template_dirs() -> list[Path]:
    env = os.environ.get("FL_MCP_TEMPLATES", "").strip()
    dirs: list[Path] = []
    if env:
        dirs.extend(Path(p).expanduser() for p in env.split(os.pathsep) if p)
    else:
        for candidate in [
            paths.data_dir() / "templates",
            paths.fl_user_dir() / "Projects" / "Templates",
            *[d / "Data" / "Templates" for d in paths.fl_install_dirs()],
        ]:
            if candidate not in dirs:
                dirs.append(candidate)
    return [d for d in dirs if d.is_dir()]


def discover_templates(limit: int = 40) -> list[dict[str, Any]]:
    """Find valid .flp templates on disk and report which ones are ready for flp.build()."""
    found: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in _template_dirs():
        if len(found) >= limit:
            break
        for path in sorted(root.rglob("*.flp")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                summary = flp.Flp.parse(path.read_bytes()).summary()
            except (OSError, flp.FlpError):
                continue
            ready = bool(summary["named_pattern_with_notes"] and summary["playlist_items"] > 0)
            sampler_ready = bool(ready and summary["sampler_channel"])
            roles = detect_roles([c[1] for c in summary["channels"]])[0]
            found.append(
                {
                    "name": path.stem,
                    "rel": str(path.relative_to(root)),
                    "path": str(path),
                    "ready": ready,
                    "sampler_ready": sampler_ready,
                    "tempo": summary["tempo"],
                    "channels": len(summary["channels"]),
                    "patterns": summary["patterns"],
                    "playlist_items": summary["playlist_items"],
                    "roles": sorted(roles),
                }
            )
            if len(found) >= limit:
                break
    found.sort(
        key=lambda t: (
            not t["sampler_ready"],
            not t["ready"],
            0 if "4x4" in t["name"].lower() else 1,
            t["channels"],
            t["name"].lower(),
        )
    )
    return found


def _template(path: str) -> Path:
    raw = path.strip()
    low = raw.lower()
    if low == "auto":
        candidates = discover_templates()
        best = next((t for t in candidates if t["sampler_ready"]), None) or next(
            (t for t in candidates if t["ready"]), None
        )
        if not best:
            raise ValueError(
                "no ready FL Studio template found for template='auto'; pass an existing .flp "
                "with at least one named pattern containing a note and a playlist clip, or put "
                "one in ~/.fl-mcp/templates"
            )
        return Path(best["path"])
    f = Path(raw).expanduser()
    if f.suffix.lower() == ".flp" or "/" in raw or "\\" in raw:
        if f.suffix.lower() != ".flp" or not f.is_file():
            raise ValueError("template must be the path of an existing .flp project")
        return f
    candidates = discover_templates()
    matches = [t for t in candidates if low in t["name"].lower() or low in t["rel"].lower()]
    ready_match = next((t for t in matches if t["ready"]), None)
    picked = ready_match or (matches[0] if matches else None)
    if picked:
        return Path(picked["path"])
    raise ValueError("template must be the path of an existing .flp project (or 'auto')")


def _resolve_sound_spec(path: str, library: Any) -> str:
    if path.startswith("%"):
        return path
    expanded = Path(path).expanduser()
    if expanded.is_file():
        return str(expanded)
    low = path.lower()
    if low.startswith("fst:"):
        preset = library.find_preset(path[4:].strip())
        if preset is None:
            raise ValueError(f"preset not found: {path[4:].strip()}")
        return str(preset)
    if low.startswith("sf2:"):
        sf2 = library.find_soundfont(path[4:].strip())
        if sf2 is None:
            raise ValueError(f"soundfont not found: {path[4:].strip()}")
        return str(sf2)
    if "/" not in path and "\\" not in path and expanded.suffix.lower() not in library.SAMPLE_EXT:
        if low.endswith(".sf2"):
            sf2 = library.find_soundfont(path)
            if sf2 is not None:
                return str(sf2)
        else:
            preset = library.find_preset(path)
            if preset is not None:
                return str(preset)
            if not low.endswith(".fst"):
                sf2 = library.find_soundfont(path)
                if sf2 is not None:
                    return str(sf2)
    raise ValueError(f"sample not found: {path}")


def parse_sounds(
    plan: Plan, template: str, override: dict[str, int] | None, text: str
) -> dict[str, str]:
    """role -> path from 'auto' (kit for missing drums) or 'role=path/preset/sf2; ...'."""
    from fl_mcp import library

    if text.strip().lower() == "auto":
        project = flp.Flp.parse(_template(template).read_bytes())
        have = detect_roles([c.name for c in project.channels()])[0]
        have.update(override or {})
        missing = [r for r in plan.roles if r in library.DRUMS and r not in have]
        kit = library.pick_kit(plan.genre, missing, seed=plan.seed) if missing else {}
        return {r: c["p"] for r, c in kit.items()}
    out = {}
    for part in text.split(";"):
        role, eq, path = part.partition("=")
        role, path = role.strip().lower(), path.strip().strip("\"'")
        if not eq or not role or not path:
            raise ValueError(f"'{part.strip()}' should look like role=path")
        if role not in plan.roles:
            raise ValueError(f"the plan has no {role} part (roles: {', '.join(plan.roles)})")
        out[role] = _resolve_sound_spec(path, library)
    return out


def export_flp(
    plan: Plan,
    template: str,
    override: dict[str, int] | None = None,
    replace: bool = False,
    sounds: str | None = None,
) -> dict[str, Any]:
    """Write template + plan (patterns, notes, playlist) as a new project; the template is kept."""
    out = paths.data_dir() / "out" / f"{plan.id}.flp"
    tpl_path = _template(template)
    picked = parse_sounds(plan, str(tpl_path), override, sounds) if sounds else None
    report = flp.build(tpl_path, plan, out, override, "replace" if replace else "append", picked)
    report["template"] = str(tpl_path)
    report["verify"] = flp.Flp.parse(out.read_bytes()).verify()
    return report


def inspect_flp(template: str | Path) -> dict[str, Any]:
    raw = str(template)
    if raw.strip().lower() in ("list", "templates"):
        items = discover_templates()
        return {
            "count": len(items),
            "ready_count": sum(1 for t in items if t["ready"]),
            "templates": items,
        }
    cand = Path(raw).expanduser()
    if cand.suffix.lower() == ".fst":
        if not cand.is_file():
            if "/" not in raw and "\\" not in raw:
                from fl_mcp import library

                found = library.find_preset(cand.stem)
                if found is not None:
                    cand = found
            if not cand.is_file():
                raise ValueError("preset must be an existing .fst file")
        info = flp.read_fst(cand)
        return {
            "kind": "preset",
            "file": info["file"],
            "plugin": info["plugin"],
            "name": info["name"],
            "param_bytes": len(info["params"]),
            "sf2": info["sf2"],
        }
    if cand.suffix.lower() == ".fsc":
        if not cand.is_file():
            raise ValueError("score must be an existing .fsc file")
        score = flp.read_fsc(cand)
        return {
            "kind": "score",
            "file": str(cand),
            "ppq": score["ppq"],
            "count": len(score["notes"]),
            "notes": score["notes"][:80],
        }
    if cand.suffix.lower() == ".zip" and cand.is_file():
        project = flp.Flp.open(cand)
        summary = project.summary()
        summary["kind"] = "zip"
        summary["template"] = str(cand)
        summary["roles"], summary["unmatched"] = detect_roles([c[1] for c in summary["channels"]])
        summary["verify"] = project.verify()
        return summary
    path = _template(raw)
    project = flp.Flp.open(path)
    summary = project.summary()
    summary["template"] = str(path)
    summary["roles"], summary["unmatched"] = detect_roles([c[1] for c in summary["channels"]])
    summary["verify"] = project.verify()
    return summary


def arrange_flp(
    template: str | Path,
    length: str = "short",
    replace_playlist: bool = False,
) -> dict[str, Any]:
    """Expand an existing .flp/.zip loop into a multi-section arrangement, keeping patterns."""
    import struct
    import time

    arrangements = {
        "sketch": [("Intro", 4), ("Verse", 4), ("Chorus", 8), ("Outro", 4)],
        "short": [
            ("Intro", 4),
            ("Verse A", 8),
            ("Chorus A", 8),
            ("Breakdown", 4),
            ("Chorus B", 8),
            ("Outro", 4),
        ],
        "full": [
            ("Intro", 8),
            ("Verse A", 8),
            ("Pre", 4),
            ("Chorus A", 8),
            ("Verse B", 8),
            ("Breakdown", 8),
            ("Chorus B", 8),
            ("Outro", 8),
        ],
    }

    src = Path(str(template)).expanduser()
    if not src.is_file():
        src = _template(str(template))
    project = flp.Flp.open(src)
    ppq = project.ppq
    bar_ticks = 4 * ppq
    chans = project.channels()
    chan_roles, _ = detect_roles([c.name for c in chans])
    iid_to_role = {chans[idx].iid: role for role, idx in chan_roles.items()}

    pats = project.patterns()
    pat_blocks = project._pattern_blocks()
    non_empty = {pid: info for pid, info in pats.items() if info["notes"] > 0}
    if not non_empty:
        raise ValueError("source project has no pattern notes to arrange")

    pat_roles: dict[int, set[str]] = {}
    pat_lengths: dict[int, int] = {}
    for pid, info in non_empty.items():
        roles_here: set[str] = set()
        max_end = bar_ticks
        for s, e in pat_blocks[pid]["notes"]:
            for ev in project.events[s:e]:
                if ev.id != flp.NOTES:
                    continue
                for off in range(0, len(ev.data) - flp.NOTE.size + 1, flp.NOTE.size):
                    pos, _fl, ch_iid, nlen, _k, _g, *_ = flp.NOTE.unpack_from(ev.data, off)
                    max_end = max(max_end, pos + nlen)
                    if ch_iid in iid_to_role:
                        roles_here.add(iid_to_role[ch_iid])
        if not roles_here and info.get("name"):
            guessed, _ = detect_roles([info["name"]])
            roles_here = set(guessed)
        pat_roles[pid] = roles_here
        pat_lengths[pid] = max_end

    full_pids = sorted(non_empty)
    drum_pids = [
        p for p in full_pids if pat_roles[p] & {"kick", "snare", "clap", "hat", "ohat", "perc"}
    ]
    bass_pids = [p for p in full_pids if "bass" in pat_roles[p]]
    melodic_pids = [
        p
        for p in full_pids
        if (pat_roles[p] & {"chords", "pad", "lead", "arp", "vocal"})
        or (p not in drum_pids and p not in bass_pids)
    ]
    if not melodic_pids:
        melodic_pids = full_pids

    mode_key = length if length in arrangements else "short"
    form = arrangements[mode_key]
    clips: list[tuple[int, int, int, int]] = []
    markers: list[tuple[int, str]] = []
    bar_cursor = 0

    for sec_name, sec_bars in form:
        markers.append((bar_cursor * bar_ticks, sec_name))
        low = sec_name.lower()
        if "intro" in low or "outro" in low or "break" in low:
            use = melodic_pids
        elif "verse" in low or "pre" in low:
            use = list(dict.fromkeys([*melodic_pids, *bass_pids]))
        else:
            use = full_pids
        for lane, pid in enumerate(use[:12], start=1):
            step_bars = max(1, (pat_lengths[pid] + bar_ticks - 1) // bar_ticks)
            off = 0
            while off < sec_bars:
                span_bars = min(step_bars, sec_bars - off)
                clips.append(((bar_cursor + off) * bar_ticks, pid, span_bars * bar_ticks, lane))
                off += step_bars
        bar_cursor += sec_bars

    playlist_mode = "replace" if replace_playlist else "replace"
    written = project.set_playlist(clips, playlist_mode, synthesize=True)
    _, arr_hi = project._arrangement_bounds(0)
    insert_at = next(
        (i + 1 for i in range(arr_hi) if project.events[i].id == flp.PLAYLIST),
        arr_hi,
    )
    existing_marks = project.markers(0)
    if existing_marks:
        insert_at = max(m["idx_name"] or m["idx_pos"] for m in existing_marks) + 1
    for tick, title in markers:
        if tick == 0 and any(m["position"] == 0 for m in existing_marks):
            continue
        block = [
            flp.Event(flp.TIME_MARKER_POS, struct.pack("<I", tick)),
            flp.Event(33, b"\x04"),
            flp.Event(34, b"\x04"),
            flp.Event(flp.TIME_MARKER_NAME, flp._utf16(title)),
        ]
        project.events[insert_at:insert_at] = block
        insert_at += len(block)
    project.set_end_marker(bar_cursor * bar_ticks, create=True)
    slug = re.sub(r"[^a-z0-9_-]+", "-", src.stem.lower()).strip("-") or "project"
    out_path = paths.data_dir() / "out" / f"arranged-{slug}-{int(time.time())}.flp"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(project.dumps())
    ver = flp.Flp.parse(out_path.read_bytes()).verify()
    return {
        "file": str(out_path),
        "source": str(src),
        "bars": bar_cursor,
        "sections": [[name, bars] for name, bars in form],
        "preserved_patterns": full_pids,
        "clips": written,
        "verify": ver,
    }
