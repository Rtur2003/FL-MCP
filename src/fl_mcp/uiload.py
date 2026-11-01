"""Add a plugin to the project the way a person does: find it in FL's browser and load it.

FL's scripting API cannot create plugins, but its browser can be walked from inside FL and the
focused item loaded. Whether that worked is decided by what appeared in the project, not by the
browser's answer: the plugin inventory is read before and after."""

from collections import Counter
from collections.abc import Callable
from typing import Any

Call = Callable[[dict[str, Any]], Any]
ATTEMPTS = 6  # several browser items can share a name (the plugin, its preset folder...)
CHUNKS = 8  # the browser is walked in pieces of `steps` items, this many at most


def inventory(call: Call) -> list[list[Any]]:
    """Every plugin in the project as [at, index, slot, plugin] (at: c channel rack, m mixer)."""
    out = call({"o": "list", "k": "plugins", "s": 0, "n": 200, "a": 1})
    return [[r[1], r[2], r[3], r[4]] for r in out["rows"]]


def _new(before: list[list[Any]], after: list[list[Any]]) -> list[dict[str, Any]]:
    seen = {tuple(r) for r in before}
    counts = Counter((r[0], r[3]) for r in before)
    found = []
    for at, plugin in sorted({(r[0], r[3]) for r in after}):
        grown = sum(1 for r in after if (r[0], r[3]) == (at, plugin)) - counts[(at, plugin)]
        if grown <= 0:
            continue
        rows = [r for r in after if (r[0], r[3]) == (at, plugin)]
        # mixer positions never shift; a channel inserted in the middle shifts the ones after it
        fresh = [r for r in rows if tuple(r) not in seen] if at == "m" else rows
        for r in fresh[-grown:]:
            found.append({"plugin": plugin, "at": "mixer" if at == "m" else "channel",
                          "index": r[1], "slot": r[2] if at == "m" else None})  # fmt: skip
    return found


def _find(call: Call, name: str, steps: int) -> dict[str, Any]:
    """Walk down from the focused item until one is named like `name` or the tree ends."""
    hit: dict[str, Any] = {}
    for _ in range(CHUNKS):
        hit = call(
            {
                "o": "ui",
                "a": "browser",
                "b": "goto",
                "q": name,
                "n": steps,
                "sh": True,
                "here": True,
            }
        )
        if hit.get("found") or hit.get("steps", 0) < steps:  # found, or the list ended
            break
    return hit


def load(call: Call, name: str, track: int | None = None, steps: int = 400) -> dict[str, Any]:
    """Load the first browser item named like `name`; an effect goes to mixer `track`."""
    if not name.strip():
        raise ValueError("load needs query: the plugin or sample name")
    if track is not None:
        call({"o": "set", "k": "mixer", "i": track, "p": {"select": True}})
    before = inventory(call)
    call({"o": "ui", "a": "win", "w": "browser", "v": "focus"})
    for _ in range(CHUNKS):  # to the top of the tree, however long it is
        if call({"o": "ui", "a": "browser", "b": "top", "n": steps}).get("done"):
            break
    stops: list[str] = []
    for _ in range(ATTEMPTS):
        hit = _find(call, name, steps)
        if not hit.get("found"):
            break
        item = str(hit["item"][0])
        stops.append(item)
        call({"o": "ui", "a": "browser", "b": "select"})
        fresh = _new(before, inventory(call))
        if fresh:
            return {"loaded": fresh, "via": item}
        call({"o": "ui", "a": "browser", "b": "nav", "d": "next", "sh": True})
    if not stops:
        raise ValueError(
            f"'{name}' is not in FL's browser (searched up to {CHUNKS * steps} items from the "
            "top): check the spelling, or that the plugin is installed and scanned"
        )
    raise ValueError(
        f"the browser stopped at {', '.join(repr(s) for s in stops)} but nothing was added to "
        "the project. Use a longer part of the name, or load it by hand / with fl_desk"
    )
