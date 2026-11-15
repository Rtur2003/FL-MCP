"""Add a plugin to the project the way a person does: find it in FL's browser and load it.

FL's scripting API cannot create plugins, but its browser can be walked from inside FL and the
focused item loaded. Whether that worked is decided by what appeared in the project, not by the
browser's answer: the plugin inventory is read before and after."""

import inspect
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from fl_mcp import desk, popupkeys


def _call_popup(fn: Callable[..., bool], allow_cursor: bool) -> bool:
    """Invoke a popup confirm/dismiss callable, passing cursor preference if supported."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn()
    if "allow_cursor_fallback" in sig.parameters:
        return fn(allow_cursor_fallback=allow_cursor)
    return fn()


Call = Callable[[dict[str, Any]], Any]
ATTEMPTS = 6  # several browser items can share a name (the plugin, its preset folder...)
CHUNKS = 10  # the browser is walked in pieces of `steps` items, this many at most


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


def _settle(call: Call, before: list[list[Any]], wait: bool) -> list[dict[str, Any]]:
    """What the project gained. After a confirmed menu FL finishes the load a moment later."""
    for _ in range(8 if wait else 1):
        fresh = _new(before, inventory(call))
        if fresh:
            return fresh
        if wait:
            time.sleep(0.2)
    return []


def _select_tab(call: Call, want: str) -> None:
    for _ in range(8):
        call({"o": "ui", "a": "browser", "b": "tab", "d": "left"})
    cur = str(call({"o": "ui", "a": "browser", "b": "tab"}).get("tab", "")).lower()
    if want in cur:
        return
    for _ in range(8):
        nxt = str(
            call({"o": "ui", "a": "browser", "b": "tab", "d": "right"}).get("tab", "")
        ).lower()
        if want in nxt:
            return


def load(
    call: Call,
    name: str,
    track: int | None = None,
    steps: int = 80,
    confirm: Callable[[], bool] | None = None,
    dismiss: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Load the first browser item named like `name`; an effect goes to mixer `track`.

    FL opens a context menu for the focused item. confirm presses its first entry (an effect goes
    to the selected mixer track, an instrument to a new channel) and dismiss closes the menu."""
    if not name.strip():
        raise ValueError("load needs query: the plugin or sample name")
    confirm = confirm or popupkeys.confirm
    dismiss = dismiss or popupkeys.dismiss
    mode = getattr(desk, "mode", lambda: "foreground" if desk.enabled() else "off")()
    allow_cursor = mode == "foreground"
    _call_popup(dismiss, allow_cursor)  # a menu left open would block every other call
    if mode == "foreground":
        try:
            desk.get().focus_fl()
        except Exception:
            pass
    old_fx: tuple[int, int] | None = None
    if track is not None:
        call({"o": "ui", "a": "win", "w": "mixer", "v": "focus"})
        call({"o": "set", "k": "mixer", "i": track, "p": {"select": True}})
        try:
            (sel,) = call({"o": "call", "c": [["channels.selectedChannel", True, 0, True]]})
            if isinstance(sel, int) and sel >= 0:
                (fx,) = call({"o": "call", "c": [["channels.getTargetFxTrack", sel, True]]})
                if isinstance(fx, int) and fx != track:
                    old_fx = (sel, fx)
                    call({"o": "set", "k": "channel", "i": sel, "p": {"fx": track}})
        except Exception:
            old_fx = None
    before = inventory(call)
    call({"o": "ui", "a": "win", "w": "browser", "v": "show"})
    stops: list[str] = []
    try:
        for tab in ("plugins", "all"):
            _select_tab(call, tab)
            for _chunk in range(CHUNKS):  # to the top of the tree, however long it is
                if call({"o": "ui", "a": "browser", "b": "top", "n": steps}).get("done"):
                    break
            for _ in range(ATTEMPTS):
                hit = _find(call, name, steps)
                if not hit.get("found"):
                    break
                item = str(hit["item"][0])
                stops.append(item)
                call({"o": "ui", "a": "browser", "b": "select", "new": track is None})
                confirmed = _call_popup(confirm, allow_cursor)
                fresh = _settle(call, before, confirmed)
                if fresh:
                    return {"loaded": fresh, "via": item}
                _call_popup(dismiss, allow_cursor)
                call({"o": "ui", "a": "browser", "b": "nav", "d": "next", "sh": True})
            if stops:
                break
    finally:
        _call_popup(dismiss, allow_cursor)
        if old_fx is not None:
            try:
                call({"o": "set", "k": "channel", "i": old_fx[0], "p": {"fx": old_fx[1]}})
            except Exception:
                pass
    if not stops:
        raise ValueError(
            f"'{name}' is not in FL's browser (searched up to {CHUNKS * steps} items from the "
            "top): check the spelling, or that the plugin is installed and scanned"
        )
    raise ValueError(
        f"the browser stopped at {', '.join(repr(s) for s in stops)} but nothing was added to "
        "the project. Use a longer part of the name, or load it by hand / with fl_desk"
    )
