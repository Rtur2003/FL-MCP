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
        raise ValueError("apply this plan to FL Studio first (fl_produce action=apply target=live)")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- files


def export_midi(plan: Plan, drums: str = "fl") -> Path:
    folder = paths.data_dir() / "out"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{plan.id}{'-gm' if drums == 'gm' else ''}.mid"
    path.write_bytes(plan_to_midi(plan, drums))
    return path


def _template(path: str) -> Path:
    f = Path(path).expanduser()
    if f.suffix.lower() != ".flp" or not f.is_file():
        raise ValueError("template must be the path of an existing .flp project")
    return f


def export_flp(
    plan: Plan, template: str, override: dict[str, int] | None = None, replace: bool = False
) -> dict[str, Any]:
    """Write template + plan (patterns, notes, playlist) as a new project; the template is kept."""
    out = paths.data_dir() / "out" / f"{plan.id}.flp"
    return flp.build(_template(template), plan, out, override, "replace" if replace else "append")


def inspect_flp(template: str) -> dict[str, Any]:
    project = flp.Flp.parse(_template(template).read_bytes())
    summary = project.summary()
    summary["roles"], summary["unmatched"] = detect_roles([c[1] for c in summary["channels"]])
    return summary
