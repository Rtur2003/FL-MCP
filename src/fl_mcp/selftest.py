"""Checks every layer against the FL Studio that is running and says what works and what does not.

Writes are small and always put back (a mixer volume, the tempo, one plugin parameter), so it is
safe to run on a real project. The report is plain JSON: send it when something is off."""

import json
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fl_mcp import __version__, audio, desk, install, library, paths, render, uiload, units
from fl_mcp.bridge import SCRIPT_VERSION

OK, FAIL, SKIP = "ok", "fail", "skip"
WINDOWS = ("mixer", "channels", "playlist", "roll", "browser")
LISTS = ("channels", "mixer", "patterns", "playlist", "plugins")
Call = Callable[[dict[str, Any]], Any]


class Skip(Exception):
    pass


def _near(a: Any, b: float, tol: float) -> bool:
    return isinstance(a, int | float) and abs(a - b) <= tol


class Run:
    def __init__(self, call: Call) -> None:
        self.call = call
        self.results: list[dict[str, Any]] = []
        self.ctx: dict[str, Any] = {}

    def check(self, name: str, fn: Callable[[], str]) -> bool:
        started = time.perf_counter()
        try:
            status, detail = OK, fn()
        except Skip as exc:
            status, detail = SKIP, str(exc)
        except Exception as exc:  # a failing check is a result, not a crash
            status, detail = FAIL, f"{type(exc).__name__}: {exc}"
        self.results.append(
            {
                "check": name,
                "status": status,
                "detail": detail,
                "ms": round((time.perf_counter() - started) * 1000),
            }
        )
        return status != FAIL

    # -- the checks

    def deep_load(self, name: str) -> str:
        """Load a plugin through FL's browser, see it appear, undo it, see it gone."""
        rows = self.ctx.get("mixer", {}).get("rows", [])
        track = next((r[0] for r in rows if r[0] >= 1), 1)
        before = len(uiload.inventory(self.call))
        got = uiload.load(self.call, name, track)
        where = got["loaded"][0]
        self.call({"o": "tr", "a": "undo"})
        if len(uiload.inventory(self.call)) > before:
            try:
                self.call({"o": "call", "c": [["general.undoUp"]]})
            except Exception:
                pass
        if len(uiload.inventory(self.call)) > before:
            raise RuntimeError(
                f"{where['plugin']} was loaded (mixer {track}, slot {where['slot']}) but undo "
                "did not remove it: delete it by hand"
            )
        return f"{where['plugin']} loaded on mixer {track} through the browser, then undone"

    def link(self) -> str:
        info = self.call({"o": "ping"})
        self.ctx["ping"] = info
        if info.get("ver") != SCRIPT_VERSION:
            raise RuntimeError(
                f"the script in FL is v{info.get('ver')} but this server needs v{SCRIPT_VERSION}: "
                "run `fl-mcp install`, then restart FL Studio"
            )
        return f"FL {info.get('fl')} (API {info.get('api')}), script v{info['ver']}"

    def status(self) -> str:
        s = self.call({"o": "status"})
        self.ctx["status"] = s
        return (
            f"{s.get('bpm')} BPM, {s.get('n_ch')} channels, {s.get('n_pat')} patterns, "
            f"title '{s.get('title')}'"
        )

    def features(self) -> str:
        probe = self.call({"o": "probe"})
        has = probe.get("has", {})
        missing = sorted(k for k, v in has.items() if not v)
        if not probe.get("linked"):
            self.ctx["unlinked"] = True
        return f"{len(has) - len(missing)}/{len(has)} optional API functions" + (
            f"; missing: {', '.join(missing)}" if missing else ""
        )

    def lists(self) -> str:
        counts = []
        for kind in LISTS:
            out = self.call({"o": "list", "k": kind, "s": 0, "n": 200, "a": 0})
            counts.append(f"{kind} {len(out['rows'])}")
            self.ctx[kind] = out
        return ", ".join(counts)

    def mixer_write(self) -> str:
        rows = self.ctx["mixer"]["rows"]
        row = next((r for r in rows if r[0] >= 1), None)
        if row is None:
            all_rows = self.call({"o": "list", "k": "mixer", "s": 1, "n": 1, "a": 1}).get(
                "rows", []
            )
            row = all_rows[0] if all_rows else None
        if row is None:
            raise Skip("no used mixer track besides the master")
        index, before = row[0], row[2]
        probe = 0.5 if abs(before - 0.5) > 0.05 else 0.6
        try:
            now = self.call({"o": "set", "k": "mixer", "i": index, "p": {"volume": probe}})
            if not _near(now["vol"], probe, 0.02):
                # FL Studio 2025 commits active/routed mixer edits on the next UI/mixer tick
                # (~15-50 ms); re-read briefly before declaring a write mismatch.
                for _ in range(6):
                    time.sleep(0.05)
                    rows = self.call({"o": "list", "k": "mixer", "s": index, "n": 1, "a": 1}).get(
                        "rows", []
                    )
                    if rows and _near(rows[0][2], probe, 0.02):
                        now = {"vol": rows[0][2]}
                        break
            if not _near(now["vol"], probe, 0.02):
                raise RuntimeError(
                    f"wrote {probe}, FL reports {now['vol']} (track may be bound to automation)"
                )
        finally:
            self.call({"o": "set", "k": "mixer", "i": index, "p": {"volume": before}})
        return f"track {index} volume {before} -> {probe} -> {before}"

    def tempo_write(self) -> str:
        before = self.call({"o": "status"})["bpm"]
        probe = before + 1 if before < 521 else before - 1
        try:
            now = self.call({"o": "tr", "a": "tempo", "v": probe})
            if not _near(now["bpm"], probe, 0.01):
                raise RuntimeError(f"wrote {probe}, FL reports {now['bpm']}")
        finally:
            self.call({"o": "tr", "a": "tempo", "v": before})
        return f"tempo {before} -> {probe} -> {before}"

    def plugin_params(self) -> str:
        out = self.ctx["plugins"]
        cols = out["cols"]
        rows = [dict(zip(cols, r, strict=False)) for r in out["rows"]]
        pick = next((r for r in rows if r.get("params")), None)
        if pick is None:
            raise Skip("no plugin with parameters in the project: add one and run again")
        where = "channel" if pick["at"] == "c" else "mixer"
        self.ctx["plugin"] = (where, pick["idx"], pick["slot"])
        listing = self.call(
            {
                "o": "param",
                "t": where,
                "i": pick["idx"],
                "s": pick["slot"],
                "a": "list",
                "f": "",
                "st": 0,
                "n": 60,
            }  # fmt: skip
        )
        self.ctx["params"] = listing
        return f"{pick['plugin']} ({where} {pick['idx']}): {len(listing['rows'])} parameters shown"

    def plugin_units(self) -> str:
        where, index, slot = self.ctx.get("plugin", (None, None, None))
        if where is None or "params" not in self.ctx:
            raise Skip("needs a plugin")
        for i, name, val, text, *_ in self.ctx["params"]["rows"]:
            parsed = units.parse_value(text)
            if parsed is None or not parsed[1]:
                continue
            req = {"o": "param", "t": where, "i": index, "s": slot, "a": "set", "p": i}
            try:
                got = self.call({**req, "r": text})["param"]  # [i, name, val, text, ok]
            finally:
                self.call({**req, "v": val})
            if not got[4]:
                raise RuntimeError(f"could not solve '{name}' to its own text '{text}'")
            return f"'{name}' solved to {text} (FL shows {got[3]}) and restored"
        raise Skip("no parameter of this plugin shows a value with a unit (dB, Hz, ms...)")

    def presets(self) -> str:
        where, index, slot = self.ctx.get("plugin", (None, None, None))
        if where is None:
            raise Skip("needs a plugin")
        out = self.call({"o": "param", "t": where, "i": index, "s": slot, "a": "presets",
                         "st": 0, "n": 5})  # fmt: skip
        if not out.get("count"):
            raise Skip("this plugin reports no presets to the scripting API")
        names = ", ".join(str(r[1]) for r in out["rows"][:3])
        return f"{out['count']} presets, e.g. {names}"

    def windows(self) -> str:
        shown = []
        for w in WINDOWS:
            state = self.call({"o": "ui", "a": "win", "w": w, "v": "?"})
            shown.append(f"{w} {'open' if state.get('visible') else 'closed'}")
        return ", ".join(shown)

    def browser(self) -> str:
        out = self.call({"o": "ui", "a": "browser", "b": "caption"})
        return f"focused browser item: {out.get('item')!r}"

    def piano_roll(self) -> str:
        device, script = install.targets()
        if not paths.fl_user_dir().is_dir():
            raise Skip("FL Studio's user data folder was not found")
        if not script.is_file():
            raise RuntimeError(f"{script.name} is not installed: run `fl-mcp install`")
        state = paths.notes_dir() / "state.json"
        if not state.is_file():
            return "installed; never run: in the piano roll use Tools > Scripting > FL MCP once"
        age = (time.time() - state.stat().st_mtime) / 3600
        return f"installed; last run {age:.1f} h ago"

    def library_index(self) -> str:
        try:
            index = library.load()
        except ValueError as exc:
            raise Skip(str(exc)) from None
        return f"{len(index['samples'])} samples, {len(index['plugins'])} plugins indexed"

    def audio_tools(self) -> str:
        if not audio.available():
            raise Skip("not installed: pip install 'fl-mcp[audio]' (analysis and fl_audio)")
        return "numpy, scipy and soundfile are available"

    def renderer(self) -> str:
        info = render.status()
        if not info["command_line_render"]:
            raise Skip(f"FL's command-line render needs FL64.exe on Windows ({info['platform']})")
        return f"{info['fl_exe']}"

    def desktop(self) -> str:
        if not desk.enabled():
            raise Skip("off: `fl-mcp desktop on` lets the model see and press FL's screen")
        d = desk.get()
        s = d.status()
        if s["fl"] is None:
            raise RuntimeError(f"{s['backend']} backend works, but no FL window was found")
        return f"{s['backend']} backend; FL window '{s['fl']['title']}'"


def run(call: Call, deep: str | None = None) -> dict[str, Any]:
    """All checks. deep names a plugin to load through the browser and undo again (it changes
    the project for a moment, so it is opt-in)."""
    t = Run(call)
    if not t.check("link", t.link):
        for name in ("status", "features", "lists", "mixer write", "tempo write", "plugin params"):
            t.results.append({"check": name, "status": SKIP, "detail": "no link to FL", "ms": 0})
    else:
        t.check("status", t.status)
        t.check("features", t.features)
        t.check("lists", t.lists)
        t.check("mixer write", t.mixer_write if "mixer" in t.ctx else _skip("lists failed"))
        t.check("tempo write", t.tempo_write)
        t.check("plugin params", t.plugin_params if "plugins" in t.ctx else _skip("lists failed"))
        t.check("plugin real units", t.plugin_units)
        t.check("plugin presets", t.presets)
        t.check("windows", t.windows)
        t.check("browser", t.browser)
        if deep:
            t.check("browser load + undo", lambda: t.deep_load(deep))
    t.check("piano roll script", t.piano_roll)
    t.check("library", t.library_index)
    t.check("audio analysis", t.audio_tools)
    t.check("command-line render", t.renderer)
    t.check("desktop", t.desktop)
    count = {s: sum(1 for r in t.results if r["status"] == s) for s in (OK, FAIL, SKIP)}
    return {
        "fl_mcp": __version__,
        "script": SCRIPT_VERSION,
        "python": platform.python_version(),
        "platform": sys.platform,
        "fl": t.ctx.get("ping"),
        "summary": count,
        "checks": t.results,
    }


def _skip(why: str) -> Callable[[], str]:
    def raiser() -> str:
        raise Skip(why)

    return raiser


def render_text(report: dict[str, Any]) -> str:
    mark = {OK: " ok ", FAIL: "FAIL", SKIP: "skip"}
    lines = [f"[{mark[c['status']]}] {c['check']:<22} {c['detail']}" for c in report["checks"]]
    s = report["summary"]
    lines.append(f"\n{s[OK]} ok, {s[FAIL]} failed, {s[SKIP]} skipped")
    return "\n".join(lines)


def save(report: dict[str, Any], folder: Path | None = None) -> Path:
    path = (folder or paths.data_dir()) / "selftest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path
