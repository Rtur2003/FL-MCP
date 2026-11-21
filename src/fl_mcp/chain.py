"""Plugin chains on mixer tracks: what is there, what the role wants, and applying settings."""

from collections.abc import Callable
from typing import Any

from fl_mcp import plugins_kb
from fl_mcp.produce import detect_roles, mapping_for, rows

Call = Callable[[dict[str, Any]], Any]


def inventory(call: Call) -> dict[int, list[tuple[int, str]]]:
    """Plugins on every mixer track: {track: [(slot, plugin name), ...]}."""
    out: dict[int, list[tuple[int, str]]] = {}
    for row in rows(call, "plugins"):
        _, at, idx, slot, plugin = row[:5]
        if at == "m":
            out.setdefault(idx, []).append((slot, plugin))
    return out


def targets(
    call: Call, plan_id: str | None, track: int | None, role: str | None
) -> list[tuple[int, str]]:
    """(mixer track, role) pairs to look at: one track, or every role of the plan, plus master."""
    mixer = rows(call, "mixer")
    names = {r[0]: r[1] for r in mixer}
    if track is not None:
        if track not in names:
            raise ValueError(f"mixer track {track} does not exist")
        if role is None:
            role = "master" if track == 0 else next(iter(detect_roles([names[track]])[0]), "")
        return [(track, role)]
    pairs: list[tuple[int, str]] = [(0, "master")]
    chans = rows(call, "channels")
    try:
        mapping = mapping_for(plan_id or "", [c[1] for c in chans])
    except ValueError:
        mapping = detect_roles([c[1] for c in chans])[0]
    seen_tracks: set[int] = {0}
    for r, ch in mapping.items():
        if role in (None, r) and ch < len(chans) and chans[ch][7] >= 1:
            trk = chans[ch][7]
            if trk not in seen_tracks:
                seen_tracks.add(trk)
                pairs.append((trk, r))
    # Also include secondary channels whose role matches even if mapping picked only the first
    for _ch_idx, c in enumerate(chans):
        trk = c[7]
        if trk < 1 or trk in seen_tracks:
            continue
        r = next(iter(detect_roles([c[1]])[0]), "")
        if r and role in (None, r):
            seen_tracks.add(trk)
            pairs.append((trk, r))
    return [p for p in pairs if role in (None, p[1])] or pairs[:1]


def analyze(call: Call, pairs: list[tuple[int, str]]) -> list[dict[str, Any]]:
    have = inventory(call)
    names = {r[0]: r[1] for r in rows(call, "mixer")}
    out = []
    for track, role in pairs:
        report = plugins_kb.analyze_chain(role, have.get(track, []))
        out.append({"track": track, "name": names.get(track, ""), "role": role, **report})
    return out


def _all_params(call: Call, track: int, slot: int) -> tuple[str, list[list[Any]]]:
    rows_out: list[list[Any]] = []
    plugin = ""
    start = 0
    for _ in range(6):
        page = call(
            {
                "o": "param",
                "t": "mixer",
                "i": track,
                "s": slot,
                "a": "list",
                "f": "",
                "st": start,
                "n": 120,
            }
        )
        plugin = page.get("plugin", plugin)
        batch = page.get("rows") or []
        rows_out.extend(batch)
        if not batch or batch[-1][0] + 1 >= page.get("count", 0):
            break
        start = batch[-1][0] + 1
    return plugin, rows_out


def apply(call: Call, reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Set the role's starting values on the plugins that are already there."""
    done = []
    for rep in reports:
        recipe = plugins_kb.recipe(rep["role"]) or {}
        for kind, wants in recipe.get("set", {}).items():
            slot = next((s for s, _, k in rep["slots"] if k == kind), None)
            if slot is None:
                continue
            plugin_name, param_rows = _all_params(call, rep["track"], slot)
            items, missing = plugins_kb.match_intents(param_rows, wants)
            entry: dict[str, Any] = {"track": rep["track"], "slot": slot, "plugin": plugin_name}
            if items:
                res = call(
                    {
                        "o": "param",
                        "t": "mixer",
                        "i": rep["track"],
                        "s": slot,
                        "a": "many",
                        "set": items,
                    }
                )
                entry["set"] = [[row[1], row[3], bool(row[4])] for row in res["params"]]
            if missing:
                entry["not_found"] = missing
            done.append(entry)
    return done


SEV = {"high": 0, "med": 1, "low": 2, "info": 3}


_ACOUSTIC_STYLES = frozenset(
    {"acoustic", "piano", "classical", "orchestral", "ambient", "ost", "solo"}
)
_ACOUSTIC_HINTS = (
    "piano", "grand", "upright", "felted", "salamander", "violin", "viola", "cello",
    "contrabass", "strings", "harp", "flute", "oboe", "clarinet", "horn", "brass",
    "guitar", "nylon", "acoustic", "glock", "marimba", "choir", "orchestra",
)
_EDM_DRUM_HINTS = ("kick", "808", "snare", "clap", "hihat", "hat", "trap", "sub", "reese")


def _is_acoustic_project(chans: list[list[Any]], style: str | None = None) -> bool:
    if style and style.strip().lower() in _ACOUSTIC_STYLES:
        return True
    names = [str(c[1]).lower() for c in chans if c[2] != "automation"]
    if not names:
        return False
    has_acoustic = any(any(h in n for h in _ACOUSTIC_HINTS) for n in names)
    has_edm_drums = any(any(h in n for h in _EDM_DRUM_HINTS) for n in names)
    return has_acoustic and not has_edm_drums


def doctor(
    call: Call,
    seconds: float = 6.0,
    bar: float | None = None,
    plan_id: str | None = None,
    style: str | None = None,
) -> dict[str, Any]:
    """Play the project, measure every used mixer track, and list what to fix, worst first."""
    from fl_mcp import mixing

    mixer = rows(call, "mixer")
    names = {r[0]: r[1] for r in mixer}
    pans = {r[0]: r[3] for r in mixer}
    seen = mixing.meter(call, [r[0] for r in mixer], seconds, bar)
    found: list[dict[str, Any]] = []

    def add(sev: str, track: int | None, what: str, fix: str) -> None:
        found.append({"sev": sev, "track": track, "what": what, "fix": fix})

    chans = rows(call, "channels")
    acoustic = _is_acoustic_project(chans, style)
    master = seen.get(0, {"peak_db": -120.0, "active": 0.0, "clips": 0})
    if master["active"] == 0:
        add(
            "high",
            0,
            "no signal at the master: nothing is playing, or the playhead is in an empty part",
            "put clips in the playlist and use bar to start in a busy section; findings below "
            "are not reliable until then",
        )
    for t, v in seen.items():
        label = names.get(t, "") or f"track {t}"
        if v["clips"] or v["peak_db"] >= -0.3:
            add(
                "high",
                t,
                f"{label} clips (peak {v['peak_db']} dB)",
                "lower its fader (fl_mix balance or fl_set) and the sources feeding it",
            )
        elif t == 0 and v["peak_db"] > -3.0:
            add(
                "med",
                0,
                f"master peaks at {v['peak_db']} dB: no headroom for a limiter to work gently",
                "bring the loudest tracks down so the master stays below -3 dB before limiting",
            )
        elif t and v["active"] > 0.2 and v["peak_db"] < (-42 if acoustic else -30):
            add(
                "low",
                t,
                f"{label} is very quiet (peak {v['peak_db']} dB)",
                "raise it, or check whether it is the right part to hear",
            )
        if t and v["active"] > 0.05 and names.get(t) == f"Insert {t}":
            add(
                "info",
                t,
                f"{label} carries signal but has the default name",
                "name it: fl_set kind=mixer name",
            )
    straight = [c[1] for c in chans if c[7] == 0 and c[2] != "automation"]
    if len(straight) > 1:
        add(
            "med",
            None,
            f"{len(straight)} channels go straight to the master: {', '.join(straight[:6])}",
            "give each part its own insert (fl_set kind=channel fx) to balance and process it",
        )
    live = [t for t, v in seen.items() if t and v["active"] > 0.05]
    if not acoustic and len(live) >= 4 and all(abs(pans.get(t, 0)) < 0.05 for t in live):
        add(
            "low",
            None,
            "every active track is panned centre",
            "pan hats, percussion, arps and pads off-centre; keep kick, snare, bass centred",
        )
    for rep in analyze(call, targets(call, plan_id, None, None)):
        missing = rep["missing"]
        if acoustic and rep["role"] != "master":
            # Solo/acoustic/orchestral instruments do not require pop/EDM delay/drive/comp chains.
            missing = [m for m in missing if m not in ("delay", "drive", "comp")]
        if missing:
            stock = plugins_kb.kb()["stock"]
            add_names = " and ".join(stock.get(k, k) for k in missing)
            add(
                "low" if rep["role"] != "master" else "med",
                rep["track"],
                f"{rep['name'] or rep['role']} has no {', '.join(missing)} plugin",
                f"add {add_names} ({rep['why']})",
            )
        elif not rep["order_ok"]:
            add(
                "low",
                rep["track"],
                f"{rep['name'] or rep['role']} has its plugins in an unusual order",
                "usual order: eq, compressor, drive, modulation, delay, reverb, limiter last",
            )
    found.sort(key=lambda f: SEV[f["sev"]])
    return {
        "playing": master["active"] > 0,
        "tracks": len(seen),
        "acoustic": acoustic,
        "findings": found,
    }
