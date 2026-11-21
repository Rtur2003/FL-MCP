"""What the stock and common third-party plugins are for, where they go in a chain and what a
sensible first setting is. Pure lookups: nothing here talks to FL Studio."""

import json
from functools import cache
from importlib import resources
from typing import Any

from fl_mcp.units import parse_value


@cache
def kb() -> dict[str, Any]:
    text = resources.files("fl_mcp").joinpath("data/plugins.json").read_text(encoding="utf-8")
    return json.loads(text)


def _key(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def classify(name: str) -> str:
    """The job of a plugin from its name: eq, comp, limiter, reverb, delay, drive..., or other."""
    low = f" {name.lower()} "
    for kind, words in kb()["kinds"]:
        if any(word in low for word in words):
            return kind
    return "other"


def instrument(name: str) -> dict[str, Any] | None:
    low = name.lower()
    for known, info in kb()["instruments"].items():
        if known in low:
            return {"name": known, **info}
    return None


def recipe(role: str) -> dict[str, Any] | None:
    return kb()["recipes"].get(role)


def analyze_chain(role: str, slots: list[tuple[int, str]]) -> dict[str, Any]:
    """Compare the plugins on a track (slot, name) with what its role usually wants."""
    found = [(slot, name, classify(name)) for slot, name in sorted(slots)]
    wanted = (recipe(role) or {}).get("want", [])
    have = {kind for _, _, kind in found}
    missing = [kind for kind in wanted if kind not in have]
    order = kb()["order"]
    ranks = [order.get(kind, 5) for _, _, kind in found if kind not in ("meter", "other")]
    max_slots = 25 if any(slot >= 10 for slot, _, _ in found) else 10
    return {
        "slots": [[slot, name, kind] for slot, name, kind in found],
        "free": max(0, max_slots - len(found)),
        "missing": missing,
        "add": [kb()["stock"].get(kind, kind) for kind in missing],
        "order_ok": all(a <= b for a, b in zip(ranks, ranks[1:], strict=False)),
        "why": (recipe(role) or {}).get("why", ""),
    }


def _score(name: str, words: list[str]) -> int:
    key = _key(name)
    best = 0
    for word in words:
        w = _key(word)
        if key == w:
            best = max(best, 3)
        elif key.startswith(w) or key.endswith(w):
            best = max(best, 2)
        elif w in key:
            best = max(best, 1)
    return best


def match_intents(
    rows: list[list[Any]], wants: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Find, for each wanted setting ('threshold': '-18 dB'), the plugin parameter that means it.

    rows are fl_param's [index, name, value, text]. The match is by meaning (name words and
    the unit in the value text), so it works on any plugin. Returns the settings to apply and
    the intents nothing matched, with the reason."""
    intents = kb()["intents"]
    items, missing, taken = [], {}, set()
    for intent, target in wants.items():
        spec = intents.get(intent)
        if spec is None:
            missing[intent] = "unknown setting"
            continue
        best: tuple[int, int, int, int] | None = None
        for index, name, _val, text in rows:
            score = _score(name, spec["words"])
            if not score or index in taken:
                continue
            got = parse_value(text)
            if got is None:  # the value cannot be solved against text that is not a number
                continue
            if spec["unit"] and got[1] and got[1] != spec["unit"]:
                continue
            rank = (score, -len(_key(name)), -index)
            if best is None or rank > best[:3]:
                best = (*rank, index)
        if best is None:
            missing[intent] = "no parameter with that meaning"
            continue
        taken.add(best[3])
        items.append({"p": best[3], "r": target})
    return items, missing


_PARAM_CATEGORIES: dict[str, tuple[str, ...]] = {
    "filter": ("cut", "freq", "res", "lp", "hp", "bp", "filter", "notch", "shelf", "slope", "q"),
    "env": ("attack", "decay", "sustain", "release", "hold", "adsr", "env", "amt", "punch"),
    "comp": ("thresh", "ratio", "knee", "attack", "release", "gain", "comp", "limit", "ceil"),
    "space": (
        "reverb",
        "verb",
        "delay",
        "time",
        "feedback",
        "damp",
        "decay",
        "size",
        "mix",
        "wet",
        "dry",
    ),
    "drive": ("drive", "dist", "sat", "gain", "fuzz", "clip", "crush", "bit", "warm", "tone"),
    "mod": ("rate", "depth", "lfo", "phase", "chorus", "flang", "width", "spread", "detune", "pan"),
    "osc": ("osc", "pitch", "tune", "fine", "coarse", "wave", "shape", "pwm", "sync", "sub", "fm"),
    "level": ("vol", "level", "gain", "trim", "out", "mix", "pan", "mute"),
}


def fx_prefs() -> dict[str, Any]:
    """Load optional ~/.fl-mcp/fx_prefs.json overrides/preferences cleanly."""
    import json

    from fl_mcp import paths

    pref_file = paths.data_dir() / "fx_prefs.json"
    try:
        raw = json.loads(pref_file.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def filter_params_by_category(rows: list[list[Any]], spec: str) -> list[list[Any]]:
    """Filter param rows when `spec` is 'cat:Filter' (or comma categories)."""
    raw_cats = [c.strip().lower() for c in spec[4:].split(",") if c.strip()]
    prefs = fx_prefs()
    custom = (
        prefs.get("param_categories") if isinstance(prefs.get("param_categories"), dict) else {}
    )
    keywords: list[str] = []
    for c in raw_cats:
        extra = custom.get(c, ()) if isinstance(custom.get(c), list) else ()
        keywords.extend([*(_PARAM_CATEGORIES.get(c, ())), *(str(x).lower() for x in extra), c])
    if not keywords:
        return rows
    return [r for r in rows if any(k in str(r[1]).lower() for k in keywords)]
