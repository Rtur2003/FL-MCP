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
    return {
        "slots": [[slot, name, kind] for slot, name, kind in found],
        "free": 10 - len(found),
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
