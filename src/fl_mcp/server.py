"""MCP server for FL Studio. Tool schemas are kept flat and small so every model can use them."""

import contextlib
import functools
import inspect
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools import Tool
from mcp.types import ToolAnnotations
from pydantic import Field

from fl_mcp import (
    __version__,
    audio,
    chain,
    desk,
    guides,
    library,
    mastering,
    mixing,
    popupkeys,
    produce,
    render,
    telemetry,
    uiload,
    units,
)
from fl_mcp.bridge import Bridge, FLError
from fl_mcp.commerce import Gate, LicenseError, Spend
from fl_mcp.music import GENRES, compose
from fl_mcp.paths import data_dir, notes_dir

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
DESK_NOTE = (
    " fl_desk is on: it sees FL's screen and presses keys and buttons in FL only. Use it for what "
    "the API cannot do: macro=script applies queued piano roll notes, macro=export bakes audio, "
    "macro=open loads a project. Take a shot first; click positions are read off the shot."
)
CREDITS_NOTE = (
    " fl_produce (new plans, .flp projects) and fl_mix balance use licence credits; fl_status "
    "shows the balance and prices. If credits run out, tell the user instead of retrying."
)

bridge = Bridge()
gate = Gate()
Beats = Annotated[float, Field(ge=0)]
Index = Annotated[int, Field(ge=0)]
Genre = Literal[tuple(GENRES)]  # type: ignore[valid-type]


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
    probe: Annotated[bool, Field(description="also list this FL build's API features")] = False,
    log: Annotated[
        bool, Field(description="include script errors and TX/RX traffic telemetry")
    ] = False,
) -> str:
    """Project snapshot: FL version, title, tempo, play/record state, loop mode, position,
    current pattern/channel/mixer track, counts (plus credits when licensed). probe lists API
    features; log shows script errors plus MIDI TX/RX telemetry."""
    status = await _fl({"o": "status"})
    telemetry.update_snapshot(status=status)
    if probe:
        status["probe"] = await _fl({"o": "probe"})
    if log:
        status["log"] = await _fl({"o": "diag"})
        status["traffic"] = telemetry.brief()
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
    """List channels, mixer tracks, patterns, playlist tracks or plugins in the project. Returns
    {cols,rows,next,max}; default and empty items are skipped unless include_unused=true."""
    req = {"o": "list", "k": kind, "s": start, "n": count, "a": int(include_unused)}
    data = await _fl(req)
    if start == 0:
        telemetry.update_snapshot(**{kind: data})
    return _out(data)


async def fl_transport(
    action: Literal[
        "play", "pause", "stop", "record", "loop", "metronome", "tempo", "position", "save", "undo"
    ],
    value: Annotated[
        float | None,
        Field(
            description="tempo: BPM; position: bar (1 = start); loop: 1 song, 0 pattern; "
            "record/metronome: 1 on, 0 off, omit toggles"
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
    send: Annotated[int | None, Field(description="mixer: route to track N")] = None,
    level: Annotated[float | None, Field(description="send level 0-1; -1 removes the send")] = None,
    select: bool | None = None,
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
    velocity: Annotated[int | None, Field(ge=0, le=127)] = None,
    pitch: Annotated[int | None, Field(ge=0, le=127, description="MIDI note for 'on'")] = None,
    length: Annotated[int, Field(ge=1, le=256, description="steps to clear/report")] = 16,
    grid: Annotated[str | None, Field(description="velocity grid (. 0-9 a-z; | ignored)")] = None,
    swing: Annotated[int, Field(ge=0, le=48, description="off-16th swing ticks")] = 0,
) -> str:
    """Step sequencer of one channel. Pass on/off/clear or a velocity grid ('z.o.|x.z.') with
    swing. Returns active steps and a bar-grouped visual strip."""
    if grid is not None:
        clean = re.sub(r"[\s|-]+", "", grid).lower()
        if (
            not clean
            or len(clean) > 512
            or any(ch not in ".0123456789abcdefghijklmnopqrstuvwxyz" for ch in clean)
        ):
            raise ToolError("grid must be 1..512 characters of . 0-9 a-z (spaces/| allowed)")
        pat = pattern or (await _fl({"o": "status"}))["pat"]
        res = await _fl({"o": "grid", "c": channel, "p": pat, "g": clean, "sw": swing})
        readback = await _fl(
            {"o": "steps", "c": channel, "p": pat, "n": min(256, max(length, len(clean)))}
        )
        readback["bad"] = res.get("bad", 0)
        readback["view"] = produce.step_view(readback["on"], len(clean), grid=clean)
        return _out(readback)
    req = {"o": "steps", "c": channel, "p": pattern, "n": length, "clear": int(clear)}
    req.update({"on": on or [], "off": off or []})
    if velocity is not None:
        req["vel"] = velocity
    if pitch is not None:
        req["pit"] = pitch
    out = await _fl(req)
    out["view"] = produce.step_view(out.get("on") or [], out.get("n") or length)
    return _out(out)


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
    read: Annotated[bool, Field(description="return the last script export")] = False,
    apply: Annotated[bool, Field(description="apply via desktop script immediately")] = False,
) -> str:
    """Piano roll notes: queues notes and opens the piano roll. Set apply=true when desktop
    control is on to apply immediately, or run Tools > Scripting > FL MCP. read=true confirms."""
    folder = notes_dir()
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
    if apply and not desk.enabled():
        raise ToolError("apply=true needs desktop control (`fl-mcp desktop on`)")
    rows = [_note(n) for n in notes]
    roll = await _fl({"o": "roll", "c": channel, "p": pattern})
    _write_json(
        folder / "inbox.json",
        {"serial": secrets.randbelow(2**31), "replace": replace, "notes": rows},
    )
    if apply:
        try:
            await _fl({"o": "ui", "a": "win", "w": "roll", "v": "focus"})
        except ToolError:
            pass
        macro_res = await _thread(desk.get().script, folder)
        return _out(
            {
                "queued": len(rows),
                "channel": roll["ch"],
                "pattern": roll["pat"],
                "applied": bool(macro_res.get("applied")),
                **macro_res,
            }
        )
    return _out(
        {
            "queued": len(rows),
            "channel": roll["ch"],
            "pattern": roll["pat"],
            "next": "Call fl_desk macro=script to apply them."
            if desk.enabled()
            else "In FL's piano roll run Tools > Scripting > FL MCP, then press Accept.",
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
    """Any plugin on a channel or mixer slot, stock or third party. Lists parameters; view reads
    info or preset names. Sets one parameter (value 0-1, or real, solved against the plugin's own
    text so '-18 dB' really shows -18 dB) or many (settings), or loads a preset."""
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


async def fl_library(
    action: Literal["scan", "find", "kit", "plugins", "info"],
    role: Annotated[
        str | None, Field(description="kick snare clap hat ohat perc bass fx chords pad lead")
    ] = None,
    genre: Genre | None = None,
    query: Annotated[str | None, Field(description="words in names and folders")] = None,
    path: Annotated[str | None, Field(description="info: a sample file")] = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    """Sounds and plugins on disk. scan indexes FL packs, FL_MCP_SAMPLES and plugin database.
    find ranks samples by name and measured sound; kit picks a genre drum kit (sounds=auto).
    plugins lists installed instruments/effects; info measures one sample."""
    if action == "scan":
        return _out(await _thread(library.scan, 45.0))
    if action == "plugins":
        rows = await _thread(library.list_plugins, query, role, limit)
        return _out(
            {
                "cols": ["name", "kind", "format"],
                "rows": [[p["name"], p["kind"], p["format"]] for p in rows],
            }
        )
    if action == "info":
        if not path:
            raise ToolError("info needs path")
        found = await _thread(library.analyze, path)
        return _out({"role": library.classify_name(Path(path)), "features": found})
    if action == "kit":
        picked = await _thread(library.pick_kit, genre, list(library.DRUMS), None)
        sounds = "; ".join(f"{r}={c['p']}" for r, c in picked.items())
        return _out(
            {"kit": {r: [c["p"], c["k"], c["score"]] for r, c in picked.items()}, "sounds": sounds}
        )
    found = await _thread(library.find, role, genre, query, limit)
    cols = ["path", "pack", "score", "dur", "decay_ms"]
    rows = [
        [c["p"], c["k"], c["score"], (c["f"] or {}).get("dur"), (c["f"] or {}).get("decay_ms")]
        for c in found
    ]
    return _out({"cols": cols, "rows": rows})


async def fl_audio(
    action: Literal["analyze", "render", "compare", "master", "status"] = "analyze",
    path: Annotated[str | None, Field(description="audio file; default latest export")] = None,
    project: Annotated[str | None, Field(description="render: .flp; default just saved")] = None,
    reference: Annotated[str | None, Field(description="compare: reference track")] = None,
    fmt: Literal["wav", "mp3", "ogg", "flac"] = "wav",
    genre: Genre | None = None,
    target: Annotated[
        float | None, Field(ge=-30, le=0, description="LUFS goal; default the genre's or -14")
    ] = None,
    ceiling: Annotated[float, Field(ge=-6, le=0, description="true peak limit, dBTP")] = -1.0,
) -> str:
    """Audio analysis and mastering (needs audio extra). analyze: LUFS, true peak, crest,
    clipping, DC, stereo width, 7-band spectrum and mix advice. render: export via CLI (.flp)
    or fl_desk (open project) then analyze. compare: vs reference. master: dial limiter."""
    if action == "status":
        return _out({"analysis": audio.available(), **await _thread(render.status)})
    goal = target if target is not None else (GENRES[genre].lufs if genre else None)
    if action == "compare":
        if not reference:
            raise ToolError("compare needs reference: the track to compare with")
        mine = path or await _thread(render.newest_audio)
        if not mine:
            raise ToolError("compare needs path: no audio was exported recently")
        return _out(await _thread(audio.compare, mine, reference))
    if action == "master":
        if not desk.enabled():
            raise ToolError(
                "master exports the open project on every pass, which needs fl_desk "
                "(fl-mcp desktop on). Without it set the limiter with fl_param, render with "
                "fl_audio and repeat (mix guide)"
            )

        def measure() -> dict[str, Any]:
            target_file = data_dir() / "exports" / f"master-{time.time_ns()}.wav"
            made_file = desk.get().export(target_file, "wav")
            return audio.report(made_file["audio"], goal, ceiling)

        aim = goal if goal is not None else audio.STREAMING_LUFS
        return _out(await _thread(mastering.run, bridge.call, measure, aim, ceiling))
    made: dict[str, Any] | None = None
    if action == "render":
        if not project and desk.enabled() and fmt in ("wav", "mp3"):  # the open project, as is
            target = data_dir() / "exports" / f"mix-{int(time.time())}.{fmt}"
            made = await _thread(lambda: desk.get().export(target, fmt))
        else:
            flp = project or await _thread(render.newest_project)
            if not flp:
                raise ToolError(
                    "no project to render: save it in FL (Ctrl+S) and call again, or pass project"
                )
            try:
                await _fl({"o": "ping"})
            except ToolError:
                pass  # FL is not answering: it is closed, which the batch renderer needs
            else:
                raise ToolError(
                    "FL Studio is open, and its command-line renderer starts its own copy: save "
                    "and close FL then call again, or turn on fl_desk to export the open project"
                )
            made = await _thread(render.render, flp, fmt)
        path = made["audio"]
    path = path or await _thread(render.newest_audio)
    if not path:
        raise ToolError("no audio file: pass path, or export one from FL (File > Export) first")
    out = await _thread(audio.report, path, goal, ceiling)
    out["target_lufs"] = goal if goal is not None else audio.STREAMING_LUFS
    if made:
        out["render"] = made
    telemetry.update_snapshot(audio=out)
    return _out(out)


_WINDOWS = Literal["mixer", "channels", "playlist", "roll", "browser"]
_BROWSE = Literal[
    "caption", "next", "prev", "left_tab", "right_tab", "expand", "collapse", "select", "preview",
    "scan", "goto", "top",
]  # fmt: skip


async def fl_ui(
    action: Literal["window", "key", "browser", "form", "load"],
    window: _WINDOWS | None = None,
    mode: Literal["state", "show", "hide", "focus"] = "state",
    keys: Annotated[str | None, Field(description="comma list: 'down,enter,f8'")] = None,
    browse: _BROWSE = "caption",
    query: Annotated[str | None, Field(description="goto/load: part of an item name")] = None,
    count: Annotated[int, Field(ge=1, le=400, description="scan/goto: steps")] = 60,
    expand: Annotated[bool, Field(description="open folders while stepping")] = False,
    index: Annotated[int | None, Field(ge=0, description="load: mixer track of an effect")] = None,
) -> str:
    """Drive FL Studio's UI without mouse: window (show/hide/focus mixer, channels, playlist,
    roll, browser), key (press navigation/editing keys), browser (step/scan/goto/select/preview),
    load (find query and add effect on mixer index or generator channel), form."""
    if action == "load":
        return _out(await _thread(uiload.load, bridge.call, query or "", index))
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
        "top": {"b": "top", "n": count},
        "scan": {"b": "scan", "n": count, "sh": expand},
        "goto": {"b": "goto", "q": query, "n": count, "sh": expand},
    }[browse]
    if browse == "goto" and not query:
        raise ToolError("goto needs query")
    out = await _fl({"o": "ui", "a": "browser", **step})
    if browse == "select" and isinstance(out, dict) and out.get("popup"):
        await anyio.sleep(0.15)  # the menu is drawn between two of FL's script calls
        out["confirmed"] = await _thread(popupkeys.confirm)
        if not out["confirmed"]:
            await _thread(popupkeys.dismiss)
    return _out(out)


async def fl_desk(
    action: Literal[
        "status",
        "shot",
        "click",
        "double",
        "right",
        "drag",
        "scroll",
        "type",
        "key",
        "wait",
        "macro",
    ],
    x: Annotated[int | None, Field(ge=0, description="position in the last shot")] = None,
    y: Annotated[int | None, Field(ge=0)] = None,
    to_x: Annotated[int | None, Field(ge=0, description="drag: end position")] = None,
    to_y: Annotated[int | None, Field(ge=0)] = None,
    text: Annotated[str | None, Field(description="type: the text")] = None,
    keys: Annotated[
        str | None, Field(description="key: 'mod+s' or 'down,enter'; mod = Ctrl/Cmd")
    ] = None,
    amount: Annotated[int, Field(ge=-20, le=20, description="scroll: clicks, up is +")] = 3,
    ms: Annotated[int, Field(ge=0, le=10000)] = 500,
    macro: Literal["script", "export", "open", "save"] | None = None,
    path: Annotated[
        str | None, Field(description="macro export: new audio file; open: .flp")
    ] = None,
    full: Annotated[bool, Field(description="shot: the whole screen, not only FL")] = False,
    look: Annotated[bool, Field(description="return a screenshot after acting")] = True,
) -> list[Any]:
    """See and drive FL Studio's screen, like a person: for what the scripting API cannot do. shot
    returns a picture; click, double, right, drag and scroll take positions from the last shot;
    type enters text, key presses combos (blocked: closing and system keys). Input only goes to FL
    and stops if the mouse is in the top-left corner. macro: script applies queued piano roll
    notes, export renders the open project to audio, open loads a .flp, save saves."""
    folder = data_dir()

    def work() -> tuple[dict[str, Any], bytes | None]:
        d = desk.get()
        if action == "status":
            return d.status(), None
        if action == "wait":
            d.wait(ms)
            return {"waited_ms": ms}, None
        if action == "shot":
            png, meta = d.shot(full)
            return meta, png
        info: dict[str, Any] = {"did": action}
        if action in ("click", "double", "right", "drag", "scroll"):
            if x is None or y is None:
                raise ToolError(f"{action} needs x and y, read off a shot")
            d.point(action, x, y, to_x, to_y, amount)
            info["at"] = [x, y]
        elif action == "type":
            info["typed"] = d.write(text or "")
        elif action == "key":
            info["keys"] = d.press(keys or "")
        elif macro == "script":
            info.update(d.script(notes_dir()))
        elif macro == "export":
            target = Path(path) if path else folder / "exports" / f"mix-{int(time.time())}.wav"
            info.update(d.export(target, target.suffix.lstrip(".").lower() or "wav"))
        elif macro == "open":
            info.update(d.open_project(Path(path or "")))
        elif macro == "save":
            info.update(d.save())
        else:
            raise ToolError("macro needs script, export, open or save")
        if not look:
            return info, None
        d.sleep(0.4)
        png, _ = d.shot(full)
        return info, png

    if action == "macro" and macro == "script":
        try:  # the shortcut only reaches the piano roll when that window has the focus
            await _fl({"o": "ui", "a": "win", "w": "roll", "v": "focus"})
        except ToolError:
            pass
    info, png = await _thread(work)
    out: list[Any] = [_out(info)]
    if png is not None:
        out.append(Image(data=png, format="png"))
    return out


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


async def fl_produce(
    action: Annotated[
        Literal["make", "plan", "apply", "queue", "midi", "flp", "inspect", "show", "genres"],
        Field(description="make = plan + deliver; apply = deliver a plan"),
    ] = "make",
    genre: Genre | None = None,
    key: Annotated[str | None, Field(description="'A minor', 'F# dorian'; default random")] = None,
    bpm: Annotated[int | None, Field(ge=40, le=220)] = None,
    length: Annotated[
        Literal["sketch", "short", "full"], Field(description="about 20, 60 or 90 bars")
    ] = "short",
    mood: Literal["dark", "bright", "chill", "aggressive", "epic"] | None = None,
    energy: Annotated[float, Field(ge=0, le=1)] = 0.75,
    seed: Annotated[int | None, Field(ge=0)] = None,
    roles: Annotated[str | None, Field(description="only these, e.g. 'kick,bass,lead'")] = None,
    plan: Annotated[str | None, Field(description="plan id; default latest")] = None,
    channels: Annotated[str | None, Field(description="'kick=0,bass=3'")] = None,
    pattern: Annotated[str | None, Field(description="queue/show: name or index")] = None,
    role: Annotated[str | None, Field(description="queue: part to send to the piano roll")] = None,
    template: Annotated[
        str | None, Field(description=".flp path or 'auto'; inspect 'list'")
    ] = None,
    replace_playlist: Annotated[bool, Field(description="flp: drop the template's clips")] = False,
    sounds: Annotated[
        str | None,
        Field(description="flp: 'auto' (a kit) or 'kick=path; snare=path'"),
    ] = None,
) -> str:
    """Fast production. make: builds a genre-aware plan (sections, drums, bass, chords, melody,
    mix targets) and delivers it: drums go into Channel Rack channels matched by name (kick,
    hat, bass...) as named patterns with tempo, markers, EQ and pan, and a MIDI file holds every
    part. With template (.flp), make and flp write a full project (notes, playlist; sounds adds
    sample channels). queue: one part to the piano roll. inspect: read a template."""
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
                notes_dir() / "inbox.json",
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
                produce.export_flp, built, template, override, replace_playlist, sounds
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
        Literal["meter", "balance", "doctor", "chain"],
        Field(description="meter/doctor read; balance moves faders; chain checks plugins"),
    ] = "meter",
    seconds: Annotated[float, Field(ge=1, le=30, description="how long to play and measure")] = 6.0,
    bar: Annotated[float | None, Field(ge=1, description="start playing from this bar")] = None,
    plan: Annotated[
        str | None, Field(description="role targets from this plan; default latest")
    ] = None,
    targets: Annotated[
        str | None, Field(description="balance: 'track=peak_dB', e.g. '1=-8,2=-9'")
    ] = None,
    track: Annotated[int | None, Field(ge=0, description="chain: one mixer track")] = None,
    role: Annotated[str | None, Field(description="chain: kick, bass, master...")] = None,
    apply: Annotated[bool, Field(description="chain: set the role's starting values")] = False,
) -> str:
    """Measure and balance levels using mixer peaks while FL plays. meter: dBFS per track.
    balance: move faders to role targets. doctor: sorted mix issues. chain: check/set
    track plugins (compressor, limiter, reverb, delay) in real units."""
    if action == "doctor":
        doc = await _thread(chain.doctor, bridge.call, seconds, bar, plan)
        telemetry.update_snapshot(doctor=doc)
        return _out(doc)
    if action == "chain":
        pairs = await _thread(chain.targets, bridge.call, plan, track, role)
        reports = await _thread(chain.analyze, bridge.call, pairs)
        out: dict[str, Any] = {"tracks": reports}
        if apply:
            out["applied"] = await _thread(chain.apply, bridge.call, reports)
        return _out(out)
    mixer_rows = await _thread(produce.rows, bridge.call, "mixer")
    used = [r[0] for r in mixer_rows]
    names = {r[0]: r[1] for r in mixer_rows}
    if action == "meter":
        result = await _thread(mixing.meter, bridge.call, used, seconds, bar)
        peaks_out = {
            "cols": ["track", "name", "peak_db", "active"],
            "rows": [[t, names[t], v["peak_db"], v["active"]] for t, v in result.items()],
            "clipping": [t for t, v in result.items() if v["clips"]],
        }
        telemetry.update_snapshot(peaks=peaks_out)
        return _out(peaks_out)
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


def _tracked(fn: Any) -> Any:
    """Wrap a tool handler so every MCP invocation logs incoming args and response size/errors."""
    name = fn.__name__

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            res = await fn(*args, **kwargs)
        except Exception as exc:
            telemetry.record_mcp(
                tool=name,
                args=kwargs,
                out_bytes=0,
                ms=(time.perf_counter() - started) * 1000,
                ok=False,
                error=str(exc),
            )
            raise
        if isinstance(res, str):
            out_bytes = len(res.encode("utf-8"))
        else:
            out_bytes = len(json.dumps(res, default=str))
        telemetry.record_mcp(
            tool=name,
            args=kwargs,
            out_bytes=out_bytes,
            ms=(time.perf_counter() - started) * 1000,
            ok=True,
        )
        return res

    return wrapper


def _tool(fn: Any, **hints: bool) -> Tool:
    tool = Tool.from_function(
        _tracked(fn),
        description=" ".join(inspect.getdoc(fn).split()),
        annotations=ToolAnnotations(openWorldHint=False, **hints),
        structured_output=False,
    )
    tool.parameters = _flat(tool.parameters)
    return tool


_READ = dict(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_EDIT = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_ACT = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


def _tools(with_desk: bool) -> list[Tool]:
    tools = [
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
        _tool(fl_library, **_EDIT),
        _tool(fl_ui, readOnlyHint=False, destructiveHint=True, idempotentHint=False),
        _tool(fl_audio, **_ACT),
        _tool(fl_api, **_READ),
    ]
    if with_desk:
        tools.append(_tool(fl_desk, readOnlyHint=False, destructiveHint=True, idempotentHint=False))
    return tools


mcp = MCPServer(
    "fl_mcp",
    version=__version__,
    instructions=INSTRUCTIONS
    + (CREDITS_NOTE if gate.metered else "")
    + (DESK_NOTE if desk.enabled() else ""),
    tools=_tools(desk.enabled()),
)


def _reader(topic: str) -> Any:
    def read() -> str:
        return guides.body(topic)

    return read


for _key, _topic in guides.TOPICS.items():
    mcp.resource(
        guides.uri(_key),
        name=f"{_key}_guide",
        title=_topic.title,
        description=f"{_topic.description} Read when {_topic.when}",
        mime_type="text/markdown",
    )(_reader(_key))


@mcp.resource(
    "fl://live/traffic",
    name="live_traffic",
    title="Live MIDI & MCP traffic",
    description="Recent incoming/outgoing MIDI bridge frames, byte counters, latencies and errors.",
    mime_type="application/json",
)
def _live_traffic() -> str:
    return _out(telemetry.read_traffic(limit=100))


@mcp.resource(
    "fl://live/studio",
    name="live_studio",
    title="Live studio snapshot",
    description="Latest cached FL Studio state: status, channels, mixer, peaks, audio, piano roll.",
    mime_type="application/json",
)
def _live_studio() -> str:
    return _out(telemetry.read_snapshot())


def _task(request: str, *topics: str) -> str:
    return " ".join(request.split()) + "\n\n" + "\n\n".join(guides.body(t) for t in topics)


@mcp.prompt(title="Make a track", description="Build a complete, mixed track in FL Studio")
def make_beat(genre: str = "", mood: str = "", length: str = "short") -> str:
    what = " ".join(x for x in (length, mood, genre, "track") if x)
    pick = "" if genre else " Pick the genre that fits the request, or ask if it is unclear."
    return _task(
        f"Make a {what} in the open FL Studio project with the FL Studio tools.{pick} Plan it "
        "with fl_produce, deliver it, mix it with fl_mix, then measure it with fl_audio and fix "
        "what the advice says. Finish by telling the user what was made, what was measured and "
        "what is left for them.",
        "produce",
        "mix",
    )


@mcp.prompt(title="Check the mix", description="Measure the open project and fix what is wrong")
def mix_check(genre: str = "") -> str:
    goal = f" Aim for the {genre} loudness target." if genre else ""
    return _task(
        "Check and improve the mix of the open FL Studio project: fl_mix doctor first, then "
        f"balance, chains, and a measured render with fl_audio.{goal} Change one thing at a "
        "time and report the before and after numbers.",
        "mix",
        "plugins",
    )


@mcp.prompt(title="Master", description="Bring the mix to a release-ready loudness and peak")
def master(genre: str = "", target_lufs: str = "") -> str:
    goal = f" Target {target_lufs} LUFS." if target_lufs else ""
    style = f" The genre is {genre}." if genre else ""
    return _task(
        "Master the open FL Studio project: measure with fl_audio, adjust only the master chain "
        f"(EQ, glue, limiter input gain and a -1.0 dB ceiling) and measure again.{style}{goal} "
        "Do not touch the balance unless fl_mix doctor finds a real fault; say so if it does.",
        "mix",
    )


@mcp.prompt(title="Fix the connection", description="Diagnose why FL Studio does not answer")
def fix_connection() -> str:
    return _task(
        "FL Studio is not answering or a feature fails. Call fl_status with probe and log, "
        "work through the table, and tell the user the exact step to take.",
        "setup",
    )


@mcp.prompt(title="Load a plugin", description="Add a plugin or sample to a channel or mixer slot")
def load_plugin(name: str, target: str = "") -> str:
    where = f" onto {target}" if target else ""
    return _task(
        f"Load '{name}'{where} in the open FL Studio project, checking after each step that it "
        "landed where intended, then set it up for its role.",
        "automation",
        "plugins",
    )


def serve() -> None:
    mcp.run()
