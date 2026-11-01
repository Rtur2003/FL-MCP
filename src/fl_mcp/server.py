"""MCP server for FL Studio. Tool schemas are kept flat and small so every model can use them."""

import contextlib
import functools
import inspect
import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from importlib import resources
from typing import Annotated, Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from fl_mcp import __version__, mixing, produce, units
from fl_mcp.bridge import Bridge, FLError
from fl_mcp.commerce import Gate, LicenseError, Spend
from fl_mcp.music import GENRES, compose
from fl_mcp.paths import data_dir

INSTRUCTIONS = (
    "Controls the FL Studio running on this computer through the FL MCP controller script "
    "(if calls time out, the user should run `fl-mcp doctor`). Indices: channels and mixer tracks "
    "0-based (mixer 0 = Master, 1-125 inserts), patterns and playlist tracks 1-based. Volume 0-1 "
    "(0.8 = 0 dB), pan -1..1, colors #RRGGBB. MIDI note 60 is C4 (FL's piano roll labels it C5). "
    "FL's API cannot create channels or plugins or place playlist clips. To make music fast use "
    "fl_produce: one call builds a genre-aware song plan, writes drums into the Channel Rack and "
    "exports a MIDI file with everything (and a ready .flp when given a template project). fl_mix "
    "measures and balances levels while the project plays. Melodies and chords reach the piano "
    "roll through fl_notes (the user runs Tools > Scripting > FL MCP once). Inspect with "
    "fl_status/fl_list, change with fl_set; use fl_api + fl_call only for anything else."
)
CREDITS_NOTE = (
    " fl_produce (new plans, .flp projects) and fl_mix balance use licence credits; fl_status "
    "shows the balance and prices. If credits run out, tell the user instead of retrying."
)

bridge = Bridge()
gate = Gate()
Beats = Annotated[float, Field(ge=0)]
Index = Annotated[int, Field(ge=0)]


async def _fl(req: dict[str, Any]) -> Any:
    try:
        return await anyio.to_thread.run_sync(bridge.call, req)
    except FLError as exc:
        raise ToolError(str(exc)) from None


def _out(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


@contextlib.asynccontextmanager
async def _spend(*ops: str) -> AsyncIterator[Spend]:
    """Reserve credits for ops; bill.ok(op) marks each one delivered. No-op when not metered."""
    try:
        async with gate.spend(*ops) as bill:
            yield bill
    except LicenseError as exc:
        raise ToolError(str(exc)) from None


async def fl_status(
    probe: Annotated[bool, Field(description="also report what this FL build supports")] = False,
    log: Annotated[bool, Field(description="also report the script's recent failures")] = False,
) -> str:
    """Project snapshot: FL version, title, tempo, play/record state, loop mode, position,
    current pattern/channel/mixer track and counts (plus plan and credits when licensed).
    probe lists the API features this FL build has; log shows the controller script's last
    errors. Use both when something does not work."""
    status = await _fl({"o": "status"})
    if probe:
        status["probe"] = await _fl({"o": "probe"})
    if log:
        status["log"] = await _fl({"o": "diag"})
    if gate.metered:
        status["account"] = await anyio.to_thread.run_sync(gate.status)
    return _out(status)


async def fl_list(
    kind: Literal["channels", "mixer", "patterns", "playlist", "plugins"],
    start: Annotated[
        int, Field(ge=0, description="first index; use 'next' from the last page")
    ] = 0,
    count: Annotated[int, Field(ge=1, le=200)] = 50,
    include_unused: Annotated[bool, Field(description="also list default/empty items")] = False,
) -> str:
    """List channels, mixer tracks, patterns, playlist tracks or every plugin in the project.
    Returns {cols,rows,next,max}; default/empty items are skipped. channels rows:
    [i,name,type,vol,pan,mute,solo,fx,plugin]; mixer rows: [i,name,vol,pan,mute,solo,
    fx(plugin names),to(destination tracks)]; plugins rows: [n,at(c channel rack, m mixer),
    index,slot,plugin,name,params,presets]."""
    req = {"o": "list", "k": kind, "s": start, "n": count, "a": int(include_unused)}
    return _out(await _fl(req))


async def fl_transport(
    action: Literal[
        "play", "pause", "stop", "record", "loop", "metronome", "tempo", "position", "save", "undo"
    ],
    value: Annotated[
        float | None,
        Field(
            description="tempo: BPM 10-522; position: bar number (1 = start); loop: 1 song, "
            "0 pattern; record/metronome: 1 on, 0 off, omit to toggle"
        ),
    ] = None,
) -> str:
    """Playback and project control. Returns tempo, play/record state, loop mode and position."""
    if action in ("tempo", "position", "loop") and value is None:
        raise ToolError(f"{action} needs a value")
    return _out(await _fl({"o": "tr", "a": action, "v": value}))


async def fl_set(
    kind: Literal["channel", "mixer", "pattern", "playlist"],
    index: Annotated[Index, Field(description="channel/mixer 0-based; pattern/track 1-based")],
    name: str | None = None,
    color: Annotated[str | None, Field(description="#RRGGBB")] = None,
    volume: Annotated[float | None, Field(description="0-1, 0.8 = 0 dB")] = None,
    pan: Annotated[float | None, Field(description="-1 left .. 1 right")] = None,
    mute: bool | None = None,
    solo: bool | None = None,
    fx: Annotated[int | None, Field(description="channel: target mixer track")] = None,
    send: Annotated[
        int | None, Field(description="mixer: also route this track to track N")
    ] = None,
    level: Annotated[float | None, Field(description="send level 0-1; -1 removes the send")] = None,
    select: Annotated[bool | None, Field(description="make it the current item")] = None,
) -> str:
    """Change only the given properties of a channel, mixer track, pattern or playlist track.
    channel: name color volume pan mute solo fx select. mixer: name color volume pan mute solo
    send level select. pattern: name color select. playlist (track): name color mute solo select.
    Returns the updated row."""
    given = dict(
        name=name,
        color=color,
        volume=volume,
        pan=pan,
        mute=mute,
        solo=solo,
        fx=fx,
        send=send,
        level=level,
        select=select,
    )
    props = {k: v for k, v in given.items() if v is not None}
    if not props:
        raise ToolError("nothing to change: pass at least one property")
    return _out(await _fl({"o": "set", "k": kind, "i": index, "p": props}))


async def fl_steps(
    channel: Index,
    on: Annotated[
        list[int] | None, Field(description="steps to turn on, 0-based, 16 per bar")
    ] = None,
    off: Annotated[list[int] | None, Field(description="steps to turn off")] = None,
    clear: Annotated[bool, Field(description="turn all steps off first")] = False,
    pattern: Annotated[int, Field(ge=0, description="pattern to edit, 0 = current")] = 0,
    velocity: Annotated[int | None, Field(ge=0, le=127, description="for the 'on' steps")] = None,
    pitch: Annotated[
        int | None, Field(ge=0, le=127, description="MIDI note for 'on' steps")
    ] = None,
    length: Annotated[int, Field(ge=1, le=256, description="steps to clear/report")] = 16,
) -> str:
    """Step sequencer of one channel in a pattern. Order: clear, off, on. Returns the steps that
    are on. With no on/off/clear it only reads."""
    req = {"o": "steps", "c": channel, "p": pattern, "n": length, "clear": int(clear)}
    req.update({"on": on or [], "off": off or []})
    if velocity is not None:
        req["vel"] = velocity
    if pitch is not None:
        req["pit"] = pitch
    return _out(await _fl(req))


def _note(row: list[float]) -> list[float]:
    if len(row) not in (3, 4):
        raise ToolError("each note is [midi_note, start_beat, length_beats, velocity?]")
    pitch, start, length = row[:3]
    velocity = row[3] if len(row) == 4 else 100
    if not (0 <= pitch <= 127 and float(pitch).is_integer()):
        raise ToolError(f"midi_note must be an integer 0-127, got {pitch}")
    if start < 0 or length <= 0:
        raise ToolError(f"start must be >= 0 and length > 0, got {start}, {length}")
    if not 1 <= velocity <= 127:
        raise ToolError(f"velocity must be 1-127, got {velocity}")
    return [int(pitch), round(float(start), 4), round(float(length), 4), int(velocity)]


def _write_json(path: Any, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


async def fl_notes(
    notes: Annotated[
        list[list[float]] | None,
        Field(
            description="rows [midi_note, start_beat, length_beats, velocity 1-127 (100)]; "
            "a 4/4 bar is 4 beats"
        ),
    ] = None,
    replace: Annotated[bool, Field(description="clear the existing notes first")] = False,
    channel: Annotated[int, Field(ge=-1, description="channel to edit, -1 = current")] = -1,
    pattern: Annotated[int, Field(ge=0, description="pattern to edit, 0 = current")] = 0,
    read: Annotated[
        bool, Field(description="return notes exported by the last script run")
    ] = False,
) -> str:
    """Piano roll notes. FL's API cannot write notes directly: this queues them and opens the
    piano roll; the user must then run Tools > Scripting > FL MCP there and press Accept. Call
    with read=true afterwards to confirm ('applied') and to read the piano roll back."""
    folder = data_dir()
    if read:
        try:
            state = _read_json(folder / "state.json")
            if state is not None:
                state["notes"], state["count"], state["serial"], state["ts"]
        except (ValueError, KeyError, TypeError):
            raise ToolError(
                "state.json is unreadable: run Tools > Scripting > FL MCP again."
            ) from None
        if state is None:
            raise ToolError("No export yet: ask the user to run Tools > Scripting > FL MCP.")
        queued = _read_json(folder / "inbox.json") or {}
        shown = state["notes"][:300]
        return _out(
            {
                "count": state["count"],
                "applied": queued.get("serial") is not None
                and queued.get("serial") == state["serial"],
                "age_s": round(time.time() - state["ts"]),
                "truncated": len(shown) < state["count"],
                "notes": shown,
            }
        )
    if not notes:
        raise ToolError("pass notes to queue, or read=true")
    if len(notes) > 1000:
        raise ToolError("at most 1000 notes per call")
    rows = [_note(n) for n in notes]
    roll = await _fl({"o": "roll", "c": channel, "p": pattern})
    _write_json(
        folder / "inbox.json",
        {"serial": secrets.randbelow(2**31), "replace": replace, "notes": rows},
    )
    return _out(
        {
            "queued": len(rows),
            "channel": roll["ch"],
            "pattern": roll["pat"],
            "next": "In FL's piano roll run Tools > Scripting > FL MCP, then press Accept.",
        }
    )


async def fl_param(
    target: Literal["channel", "mixer"],
    index: Annotated[Index, Field(description="channel index or mixer track")],
    slot: Annotated[int, Field(ge=-1, le=9, description="mixer effect slot 0-9")] = -1,
    view: Annotated[Literal["params", "info", "presets"], Field(description="read")] = "params",
    find: Annotated[str, Field(description="list: names containing this")] = "",
    param: Annotated[str | None, Field(description="name (part) or index")] = None,
    value: Annotated[float | None, Field(description="0-1 position of 'param'")] = None,
    real: Annotated[str | None, Field(description="'-6 dB', '2.5 kHz', '120 ms', '4:1'")] = None,
    settings: Annotated[
        str | None, Field(description="'Threshold=-18 dB, Ratio=4:1, Gain=0.6' (bare = 0-1)")
    ] = None,
    preset: Annotated[str | None, Field(description="preset name, or +1 / -1")] = None,
    start: Annotated[int, Field(ge=0, description="first item to list")] = 0,
    limit: Annotated[int, Field(ge=1, le=120)] = 40,
) -> str:
    """Any plugin on a channel or mixer slot, stock or third party. Lists parameters
    [i,name,val,text]; view reads info or preset names. Sets one parameter (value 0-1, or real,
    solved against the plugin's own text so '-18 dB' really shows -18 dB) or many (settings),
    or loads a preset."""
    if target == "mixer" and slot < 0:
        raise ToolError("mixer needs slot 0-9")
    req: dict[str, Any] = {"o": "param", "t": target, "i": index, "s": slot}
    if settings:
        try:
            req.update(a="many", set=units.parse_settings(settings))
        except ValueError as exc:
            raise ToolError(str(exc)) from None
    elif value is not None or real is not None:
        if param is None:
            raise ToolError("value and real need param: a name or index (list them first)")
        if value is not None and real is not None:
            raise ToolError("give value or real, not both")
        req.update(a="set", p=int(param) if param.strip().isdigit() else param)
        req["v" if value is not None else "r"] = value if value is not None else real
    elif preset:
        req.update(a="preset", **({"d": int(preset)} if units.is_steps(preset) else {"q": preset}))
    elif view != "params":
        req.update(a=view, st=start, n=limit)
    else:
        req.update(a="list", f=find, st=start, n=limit)
    return _out(await _fl(req))


_WINDOWS = Literal["mixer", "channels", "playlist", "roll", "browser"]
_BROWSE = Literal[
    "caption", "next", "prev", "left_tab", "right_tab", "expand", "collapse", "select", "preview",
    "scan", "goto",
]  # fmt: skip


async def fl_ui(
    action: Literal["window", "key", "browser", "form"],
    window: _WINDOWS | None = None,
    mode: Literal["state", "show", "hide", "focus"] = "state",
    keys: Annotated[str | None, Field(description="comma list, e.g. 'down,enter,f8'")] = None,
    browse: _BROWSE = "caption",
    query: Annotated[str | None, Field(description="goto: part of an item name")] = None,
    count: Annotated[int, Field(ge=1, le=400, description="scan/goto: steps")] = 60,
    expand: Annotated[bool, Field(description="open folders while stepping")] = False,
) -> str:
    """Drive FL's own interface from inside FL, no mouse. window: show/hide/focus the mixer,
    channels, playlist, roll or browser. key: press up,down,left,right,enter,escape,yes,no,cut,
    copy,paste,insert,delete,next,previous,tab,menu,itemmenu,f1-f12 in the focused window (can
    change or delete things). browser: next/prev/expand/collapse/scan/goto query, then select
    loads the focused plugin or sample, preview plays it. form: the focused plugin window."""
    if action == "form":
        return _out(await _fl({"o": "ui", "a": "form"}))
    if action == "window":
        if window is None:
            raise ToolError("window needs which: mixer, channels, playlist, roll or browser")
        flag = {"state": "?"}.get(mode, mode)
        return _out(await _fl({"o": "ui", "a": "win", "w": window, "v": flag}))
    if action == "key":
        names = [k.strip() for k in (keys or "").split(",") if k.strip()]
        if not names:
            raise ToolError("key needs keys, for example 'down,down,enter'")
        return _out(await _fl({"o": "ui", "a": "key", "ks": names}))
    step = {
        "caption": {"b": "caption"},
        "next": {"b": "nav", "d": "next", "sh": expand},
        "prev": {"b": "nav", "d": "prev", "sh": expand},
        "left_tab": {"b": "tab", "d": "left"},
        "right_tab": {"b": "tab", "d": "right"},
        "expand": {"b": "toggle", "v": 1},
        "collapse": {"b": "toggle", "v": 0},
        "select": {"b": "select"},
        "preview": {"b": "preview"},
        "scan": {"b": "scan", "n": count, "sh": expand},
        "goto": {"b": "goto", "q": query, "n": count, "sh": expand},
    }[browse]
    if browse == "goto" and not query:
        raise ToolError("goto needs query")
    return _out(await _fl({"o": "ui", "a": "browser", **step}))


async def fl_call(
    calls: Annotated[
        str,
        Field(
            description='JSON array of calls [["module.function", arg...], ...], e.g. '
            '[["mixer.setTrackVolume",1,0.8]]. FL constants as "midi.NAME" ("midi.A|midi.B" ORs)'
        ),
    ],
) -> str:
    """Raw FL scripting API for anything the other tools do not cover (see fl_api). Positional
    arguments only. Modules: transport mixer channels patterns playlist plugins ui general
    arrangement. Runs the calls in order and returns their results."""
    try:
        batch = json.loads(calls)
    except ValueError as exc:
        raise ToolError(f"calls must be valid JSON: {exc}") from None
    if isinstance(batch, list) and batch and isinstance(batch[0], str):
        batch = [batch]
    ok = (
        isinstance(batch, list)
        and 0 < len(batch) <= 100
        and all(isinstance(c, list) and c and isinstance(c[0], str) for c in batch)
    )
    if not ok:
        raise ToolError('calls must be [["module.function", arg, ...], ...] (1-100 calls)')
    return _out(await _fl({"o": "call", "c": batch}))


async def _thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    except (FLError, ValueError) as exc:
        raise ToolError(str(exc)) from None


def _find_pattern(plan: Any, ref: str) -> int:
    if ref.isdigit() and int(ref) < len(plan.patterns):
        return int(ref)
    for i, pat in enumerate(plan.patterns):
        if pat.name.lower() == ref.lower():
            return i
    raise ToolError("pattern not found; use fl_produce action=show to see the names")


Genre = Literal[tuple(GENRES)]  # type: ignore[valid-type]


async def fl_produce(
    action: Annotated[
        Literal["make", "plan", "apply", "queue", "midi", "flp", "inspect", "show", "genres"],
        Field(description="make = plan + deliver; apply = deliver a stored plan"),
    ] = "make",
    genre: Genre | None = None,
    key: Annotated[str | None, Field(description="'A minor', 'F# dorian'; default random")] = None,
    bpm: Annotated[int | None, Field(ge=40, le=220, description="default: the genre's")] = None,
    length: Annotated[
        Literal["sketch", "short", "full"], Field(description="about 20, 60 or 90 bars")
    ] = "short",
    mood: Literal["dark", "bright", "chill", "aggressive", "epic"] | None = None,
    energy: Annotated[float, Field(ge=0, le=1, description="density and fills")] = 0.75,
    seed: Annotated[int | None, Field(ge=0, description="same seed, same song")] = None,
    roles: Annotated[str | None, Field(description="only these, e.g. 'kick,bass,lead'")] = None,
    plan: Annotated[str | None, Field(description="plan id; default the latest")] = None,
    channels: Annotated[
        str | None, Field(description="role to channel index, e.g. 'kick=0,bass=3'")
    ] = None,
    pattern: Annotated[str | None, Field(description="queue/show: pattern name or index")] = None,
    role: Annotated[str | None, Field(description="queue: role to send to the piano roll")] = None,
    template: Annotated[
        str | None, Field(description="path of a .flp project to build on; never modified")
    ] = None,
    replace_playlist: Annotated[bool, Field(description="flp: drop the template's clips")] = False,
) -> str:
    """Fast production. make builds a genre-aware plan (sections, drums, bass, chords, melody,
    mix targets) and delivers it: drums fill matching Channel Rack channels as named patterns
    with tempo, markers, EQ and pan, and a MIDI file carries everything. Channels are matched
    by name (kick, hat, bass, lead...). With template (a .flp) flp and make also write a full
    project: patterns with all notes plus the playlist. queue sends one part to the piano roll;
    inspect reads a template."""
    if action == "genres":
        return _out({k: [g.name, *g.bpm, g.default_scale] for k, g in GENRES.items()})
    if action == "inspect":
        if not template:
            raise ToolError("inspect needs template")
        return _out(await _thread(produce.inspect_flp, template))
    if action in ("make", "plan") and not genre:
        raise ToolError("genre is required; see action=genres")
    if action == "flp" and not template:
        raise ToolError("flp needs template: the path of a .flp project to build on")
    writes_flp = bool(template) and action in ("make", "flp")
    ops = [*(["produce.compose"] if genre else []), *(["produce.flp"] if writes_flp else [])]
    async with _spend(*ops) as bill:
        if genre:
            picked = [r.strip() for r in roles.split(",") if r.strip()] if roles else None
            built = await _thread(
                compose,
                genre,
                key=key,
                bpm=bpm,
                length=length,
                mood=mood,
                energy=energy,
                seed=seed,
                roles=picked,
            )
            await _thread(produce.save_plan, built)
            bill.ok("produce.compose")
        else:
            built = await _thread(produce.load_plan, plan or "")
        override = await _thread(produce.parse_map, channels) if channels else None
        out: dict[str, Any] = {"plan": built.summary()}
        if action == "show":
            if pattern:
                pat = built.patterns[_find_pattern(built, pattern)]
                out = {
                    "pattern": pat.name,
                    "bars": pat.bars,
                    "parts": {
                        r: [[round(e.t, 2), round(e.d, 2), e.p, e.v] for e in x.events[:24]]
                        for r, x in pat.parts.items()
                        if role in (None, r)
                    },
                }
            return _out(out)
        if action == "queue":
            if not (pattern and role):
                raise ToolError("queue needs pattern and role")
            idx = _find_pattern(built, pattern)
            part = built.patterns[idx].parts.get(role)
            if part is None:
                raise ToolError(f"pattern '{built.patterns[idx].name}' has no {role}")
            state = await _thread(produce.live_state, built.id)
            if role not in state["mapping"]:
                raise ToolError(
                    f"no channel for {role}; pass channels='{role}=<index>' when applying"
                )
            rows = [_note([e.p, e.t, e.d, e.v]) for e in part.events]
            roll = await _fl(
                {"o": "roll", "c": state["mapping"][role], "p": state["base"] + 1 + idx}
            )
            _write_json(
                data_dir() / "inbox.json",
                {"serial": secrets.randbelow(2**31), "replace": True, "notes": rows},
            )
            return _out(
                {
                    "queued": len(rows),
                    "channel": roll["ch"],
                    "pattern": roll["pat"],
                    "next": "In FL's piano roll run Tools > Scripting > FL MCP, then press Accept.",
                }
            )
        if action in ("make", "apply"):
            try:
                out["live"] = await _thread(produce.apply_live, bridge.call, built, override)
            except ToolError as exc:
                if action == "apply":
                    raise
                out["live"] = f"not applied: {exc}"
        if writes_flp:
            out["flp"] = await _thread(
                produce.export_flp, built, template, override, replace_playlist
            )
            bill.ok("produce.flp")
        if action in ("make", "midi"):
            out["midi"] = str(await _thread(produce.export_midi, built))
            out["midi_note"] = (
                "Drag it into FL Studio: every track becomes a channel, sections are markers."
            )
        return _out(out)


async def fl_mix(
    action: Annotated[
        Literal["meter", "balance"],
        Field(description="meter reads; balance also moves faders"),
    ] = "meter",
    seconds: Annotated[float, Field(ge=1, le=30, description="how long to play and measure")] = 6.0,
    bar: Annotated[float | None, Field(ge=1, description="start playing from this bar")] = None,
    plan: Annotated[
        str | None, Field(description="balance: plan for role targets; default latest")
    ] = None,
    targets: Annotated[
        str | None, Field(description="balance: explicit 'track=peak_dB' list, e.g. '1=-8,2=-9'")
    ] = None,
) -> str:
    """Measure the mix with the mixer's own peak meters while the project plays (stops again if it
    was stopped). meter returns per-track peak dBFS and activity. balance moves faders so each role
    lands on its target peak (kick -8, bass -9, hats -20...) and keeps the master below -3 dBFS.
    Play the loudest section: use bar. Needs the project to be playing audio from the playlist."""
    mixer_rows = await _thread(produce.rows, bridge.call, "mixer")
    used = [r[0] for r in mixer_rows]
    names = {r[0]: r[1] for r in mixer_rows}
    if action == "meter":
        result = await _thread(mixing.meter, bridge.call, used, seconds, bar)
        return _out(
            {
                "cols": ["track", "name", "peak_db", "active"],
                "rows": [[t, names[t], v["peak_db"], v["active"]] for t, v in result.items()],
                "clipping": [t for t, v in result.items() if v["clips"]],
            }
        )
    goals: dict[int, float] = {}
    if targets:
        try:
            goals = {int(k): float(v) for k, v in (p.split("=") for p in targets.split(","))}
        except ValueError:
            raise ToolError("targets look like '1=-8,2=-9'") from None
    else:
        built = await _thread(produce.load_plan, plan or "")
        chans = await _thread(produce.rows, bridge.call, "channels")
        mapping = await _thread(produce.mapping_for, built.id, [c[1] for c in chans])
        for r, ch in mapping.items():
            track = chans[ch][7] if ch < len(chans) else 0
            if track >= 1 and r in built.mix:
                goals[track] = max(goals.get(track, -99.0), built.mix[r].peak_db)
        if not goals:
            raise ToolError(
                "no channel is routed to its own mixer track; route channels to inserts first"
            )
    async with _spend("mix.balance") as bill:
        report = await _thread(mixing.balance, bridge.call, goals, seconds, bar)
        bill.ok("mix.balance")
    report["names"] = {t: names.get(t, "") for t in report["peaks_db"]}
    return _out(report)


@functools.cache
def _api() -> dict[str, list[str]]:
    return json.loads(resources.files("fl_mcp").joinpath("api.json").read_text(encoding="utf-8"))


async def fl_api(
    module: Annotated[
        str,
        Field(
            description="transport mixer channels patterns playlist "
            "plugins ui general arrangement midi; empty lists modules"
        ),
    ] = "",
    query: Annotated[str, Field(description="case-insensitive substring filter")] = "",
) -> str:
    """FL scripting API reference for fl_call: function signatures per module, or midi constants
    as NAME=value. Needs no connection to FL Studio."""
    api = _api()
    if not module:
        return "modules: " + ", ".join(f"{m}({len(v)})" for m, v in api.items())
    if module not in api:
        raise ToolError(f"unknown module '{module}'; use one of: {', '.join(api)}")
    q = query.lower()
    lines = [x for x in api[module] if q in x.lower()]
    shown = lines[:80]
    text = "\n".join(shown)
    if len(lines) > len(shown):
        text += f"\n... {len(lines) - len(shown)} more; narrow with query"
    return text or "no match"


def _flat(node: Any) -> Any:
    """Drop what small models and strict providers trip on: titles, null unions, null defaults."""
    if isinstance(node, list):
        return [_flat(x) for x in node]
    if not isinstance(node, dict):
        return node
    any_of = node.get("anyOf")
    if any_of:
        rest = [x for x in any_of if x.get("type") != "null"]
        if len(rest) == 1:
            node = {**{k: v for k, v in node.items() if k != "anyOf"}, **rest[0]}
    out = {}
    for key, val in node.items():
        if key == "title" or (key == "default" and val is None):
            continue
        out[key] = {n: _flat(s) for n, s in val.items()} if key == "properties" else _flat(val)
    return out


def _tool(fn: Any, **hints: bool) -> Tool:
    tool = Tool.from_function(
        fn,
        description=" ".join(inspect.getdoc(fn).split()),
        annotations=ToolAnnotations(openWorldHint=False, **hints),
        structured_output=False,
    )
    tool.parameters = _flat(tool.parameters)
    return tool


_READ = dict(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_EDIT = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_ACT = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=False)

mcp = MCPServer(
    "fl_mcp",
    version=__version__,
    instructions=INSTRUCTIONS + (CREDITS_NOTE if gate.metered else ""),
    tools=[
        _tool(fl_status, **_READ),
        _tool(fl_produce, readOnlyHint=False, destructiveHint=False, idempotentHint=False),
        _tool(fl_mix, readOnlyHint=False, destructiveHint=False, idempotentHint=False),
        _tool(fl_list, **_READ),
        _tool(fl_transport, **_ACT),
        _tool(fl_set, **_EDIT),
        _tool(fl_steps, **_EDIT),
        _tool(fl_notes, **_EDIT),
        _tool(fl_param, **_EDIT),
        _tool(fl_call, readOnlyHint=False, destructiveHint=True, idempotentHint=False),
        _tool(fl_ui, readOnlyHint=False, destructiveHint=True, idempotentHint=False),
        _tool(fl_api, **_READ),
    ],
)


def serve() -> None:
    mcp.run()
