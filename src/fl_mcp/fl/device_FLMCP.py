# name=FL MCP
# supportedDevices=FL MCP
"""FL MCP controller script.

Answers requests from the fl-mcp server. Messages are JSON documents framed as MIDI CCs
(7 bits per message): BEGIN(id) DATA(byte)... SUM_A SUM_B. Requests use MIDI channel 15,
responses channel 16. Only an allowlisted set of FL API modules is reachable.
"""

import json

import arrangement
import channels
import device
import general
import midi
import mixer
import patterns
import playlist
import plugins
import transport
import ui

VERSION = 1
PROTO = 1

_IN, _OUT = 14, 15
_BEGIN, _DATA, _SUM_A, _SUM_B = 102, 103, 104, 105
_MAX_IN, _MAX_OUT = 8192, 8192

_rx = {"fid": -1, "buf": bytearray(), "sa": -1}
_last = {"key": None, "body": b""}


# ---------------------------------------------------------------- framing


def OnInit():
    _rx["fid"] = -1
    _last["key"] = None
    print(
        "FL MCP v%d | input port %s | output linked: %s"
        % (VERSION, device.getPortNumber(), bool(device.isAssigned()))
    )
    if not device.isAssigned():
        print("FL MCP: set the MIDI output port number equal to the input port number")


def OnMidiMsg(event):
    try:
        _on_midi(event)
    except Exception as exc:
        _rx["fid"] = -1
        print("FL MCP error: " + _err(exc))


def _on_midi(event):
    st = event.status
    if (st & 0xF0) != 0xB0 and getattr(event, "midiId", 0) != 0xB0:
        return
    # the channel is the low nibble of status; some FL builds report it in midiChan instead
    ch = (st & 0x0F) or (getattr(event, "midiChan", 0) & 0x0F)
    if ch == _OUT:
        event.handled = True
        return
    if ch != _IN:
        return
    event.handled = True
    cc = event.data1
    v = event.data2
    if cc == _BEGIN:
        _rx["fid"] = v
        _rx["buf"] = bytearray()
        _rx["sa"] = -1
    elif _rx["fid"] < 0:
        return
    elif cc == _DATA:
        _rx["buf"].append(v)
        if len(_rx["buf"]) > _MAX_IN:
            _rx["fid"] = -1
    elif cc == _SUM_A:
        _rx["sa"] = v
    elif cc == _SUM_B:
        fid, data, sa = _rx["fid"], bytes(_rx["buf"]), _rx["sa"]
        _rx["fid"] = -1
        _finish(fid, data, sa, v)


def _sums(data):
    a = b = 0
    for x in data:
        a = (a + x + 1) % 127
        b = (b + a) % 127
    return a, b


def _dump(obj):
    return json.dumps(obj, separators=(",", ":")).replace("\x7f", "\\u007f").encode("ascii")


def _err(exc):
    return ("%s: %s" % (type(exc).__name__, exc))[:240]


def _emit(fid, body):
    if not device.isAssigned():
        print("FL MCP: cannot answer, no MIDI output linked to this script")
        return
    a, b = _sums(body)
    base = 0xB0 | _OUT
    out = device.midiOutMsg
    out(base | (_BEGIN << 8) | (fid << 16))
    for x in body:
        out(base | (_DATA << 8) | (x << 16))
    out(base | (_SUM_A << 8) | (a << 16))
    out(base | (_SUM_B << 8) | (b << 16))


def _finish(fid, data, sa, sb):
    if (sa, sb) != _sums(data):
        _emit(fid, _dump({"ok": 0, "e": "bad frame"}))
        return
    if (fid, sa, sb) == _last["key"]:  # a retry of the request we just answered
        _emit(fid, _last["body"])
        return
    try:
        body = _dump({"ok": 1, "r": _run(json.loads(data.decode("ascii")))})
    except Exception as exc:
        body = _dump({"ok": 0, "e": _err(exc)})
    if len(body) > _MAX_OUT:
        body = _dump({"ok": 0, "e": "result too large, narrow the request"})
    _last["key"] = (fid, sa, sb)
    _last["body"] = body
    _emit(fid, body)


def _run(req):
    fn = _OPS.get(req.get("o"))
    if fn is None:
        raise ValueError("unknown op")
    return fn(req)


# ---------------------------------------------------------------- helpers


def _try(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def _r(x):
    return round(float(x), 3)


def _idx(v, lo, hi, what):
    v = int(v)
    if v < lo or v > hi:
        raise ValueError("%s %d out of range %d..%d" % (what, v, lo, hi))
    return v


def _num(v, lo, hi, what):
    v = float(v)
    if v < lo or v > hi:
        raise ValueError("%s %s out of range %s..%s" % (what, v, lo, hi))
    return v


def _col(s):
    s = str(s).strip().lstrip("#")
    if len(s) != 6:
        raise ValueError("color must be #RRGGBB")
    return int(s[0:2], 16) | (int(s[2:4], 16) << 8) | (int(s[4:6], 16) << 16)


def _flip(current, want, toggle):
    if bool(current) != bool(want):
        toggle()


def _plain(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return dict((str(k), _plain(x)) for k, x in v.items())
    return str(v)


# ---------------------------------------------------------------- rows

_CT = ("sampler", "hybrid", "plugin", "layer", "audio", "auto")
CH_COLS = ["i", "name", "type", "vol", "pan", "mute", "solo", "fx", "plugin"]
MX_COLS = ["i", "name", "vol", "pan", "mute", "solo", "fx", "to"]
PT_COLS = ["i", "name", "beats"]
PL_COLS = ["i", "name", "mute", "solo"]


def _ch_row(i):
    t = channels.getChannelType(i, True)
    plug = plugins.getPluginName(i, -1, False, True) if t in (1, 2) else ""
    return [
        i,
        channels.getChannelName(i, True),
        _CT[t] if 0 <= t < len(_CT) else t,
        _r(channels.getChannelVolume(i, False, True)),
        _r(channels.getChannelPan(i, True)),
        int(channels.isChannelMuted(i, True)),
        int(channels.isChannelSolo(i, True)),
        channels.getTargetFxTrack(i, True),
        plug,
    ]


def _mx_row(i, n=None):
    n = n or mixer.trackCount() - 1
    fx = [
        plugins.getPluginName(i, s, False, False)
        for s in range(10)
        if mixer.isTrackPluginValid(i, s)
    ]
    to = [d for d in range(n) if d != i and mixer.getRouteSendActive(i, d)]
    return [
        i,
        mixer.getTrackName(i),
        _r(mixer.getTrackVolume(i)),
        _r(mixer.getTrackPan(i)),
        int(mixer.isTrackMuted(i)),
        int(mixer.isTrackSolo(i)),
        ",".join(fx),
        to,
    ]


def _mx_used(i, row):
    return (
        i == 0
        or row[1] != "Insert %d" % i
        or row[4]
        or row[5]
        or row[6]
        or abs(row[2] - 0.8) > 0.006
        or abs(row[3]) > 0.006
        or row[7] != [0]
    )


def _pt_row(i):
    return [i, patterns.getPatternName(i), patterns.getPatternLength(i)]


def _pl_row(i):
    return [
        i,
        playlist.getTrackName(i),
        int(playlist.isTrackMuted(i)),
        int(playlist.isTrackSolo(i)),
    ]


def _mk_channels(everything):
    return CH_COLS, 0, channels.channelCount(True) - 1, _ch_row


def _mk_mixer(everything):
    count = mixer.trackCount() - 1

    def make(i):
        row = _mx_row(i, count)
        return row if everything or _mx_used(i, row) else None

    return MX_COLS, 0, count - 1, make


def _mk_patterns(everything):
    def make(i):
        return None if not everything and patterns.isPatternDefault(i) else _pt_row(i)

    return PT_COLS, 1, patterns.patternMax(), make


def _mk_playlist(everything):
    def make(i):
        row = _pl_row(i)
        used = row[1] != "Track %d" % i or row[2] or row[3]
        return row if everything or used else None

    return PL_COLS, 1, playlist.trackCount(), make


_LISTS = {
    "channels": _mk_channels,
    "mixer": _mk_mixer,
    "patterns": _mk_patterns,
    "playlist": _mk_playlist,
}


def _op_list(r):
    maker = _LISTS.get(r.get("k"))
    if maker is None:
        raise ValueError("kind must be one of " + ", ".join(sorted(_LISTS)))
    cols, lo, hi, make = maker(bool(r.get("a")))
    count = _idx(r.get("n") or 50, 1, 200, "count")
    rows, nxt = [], None
    for i in range(max(int(r.get("s") or 0), lo), hi + 1):
        if len(rows) >= count:
            nxt = i
            break
        row = make(i)
        if row is not None:
            rows.append(row)
    return {"cols": cols, "rows": rows, "next": nxt, "max": hi}


# ---------------------------------------------------------------- set


def _route(i, send, level):
    if send is None:
        if level is not None:
            raise ValueError("level needs send")
        return
    dest = _idx(send, 0, mixer.trackCount() - 2, "send")
    if level is not None and float(level) < 0:
        mixer.setRouteTo(i, dest, False, True)
        return
    mixer.setRouteTo(i, dest, True, True)
    if level is not None:
        mixer.setRouteToLevel(i, dest, _num(level, 0, 1, "level"))


_SETTERS = {
    "channel": {
        "name": lambda i, v: channels.setChannelName(i, str(v), True),
        "color": lambda i, v: channels.setChannelColor(i, _col(v), True),
        "volume": lambda i, v: channels.setChannelVolume(
            i, _num(v, 0, 1, "volume"), midi.PIM_None, True
        ),
        "pan": lambda i, v: channels.setChannelPan(i, _num(v, -1, 1, "pan"), midi.PIM_None, True),
        "mute": lambda i, v: channels.muteChannel(i, 1 if v else 0, True),
        "solo": lambda i, v: _flip(
            channels.isChannelSolo(i, True), v, lambda: channels.soloChannel(i, True)
        ),
        "fx": lambda i, v: channels.setTargetFxTrack(i, _idx(v, 0, 125, "fx"), True),
        "select": lambda i, v: channels.selectOneChannel(i, True) if v else None,
    },
    "mixer": {
        "name": lambda i, v: mixer.setTrackName(i, str(v)),
        "color": lambda i, v: mixer.setTrackColor(i, _col(v)),
        "volume": lambda i, v: mixer.setTrackVolume(i, _num(v, 0, 1, "volume")),
        "pan": lambda i, v: mixer.setTrackPan(i, _num(v, -1, 1, "pan")),
        "mute": lambda i, v: mixer.muteTrack(i, 1 if v else 0),
        "solo": lambda i, v: _flip(mixer.isTrackSolo(i), v, lambda: mixer.soloTrack(i)),
        "select": lambda i, v: (
            mixer.setTrackNumber(i, midi.curfxScrollToMakeVisible) if v else None
        ),
    },
    "pattern": {
        "name": lambda i, v: patterns.setPatternName(i, str(v)),
        "color": lambda i, v: patterns.setPatternColor(i, _col(v)),
        "select": lambda i, v: patterns.jumpToPattern(i) if v else None,
    },
    "playlist": {
        "name": lambda i, v: playlist.setTrackName(i, str(v)),
        "color": lambda i, v: playlist.setTrackColor(i, _col(v)),
        "mute": lambda i, v: playlist.muteTrack(i, 1 if v else 0),
        "solo": lambda i, v: _flip(playlist.isTrackSolo(i), v, lambda: playlist.soloTrack(i)),
        "select": lambda i, v: playlist.selectTrack(i) if v else None,
    },
}

_BOUNDS = {
    "channel": lambda: (0, channels.channelCount(True) - 1),
    "mixer": lambda: (0, mixer.trackCount() - 2),
    "pattern": lambda: (1, patterns.patternMax()),
    "playlist": lambda: (1, playlist.trackCount()),
}

_ROWS = {
    "channel": (CH_COLS, _ch_row),
    "mixer": (MX_COLS, _mx_row),
    "pattern": (PT_COLS, _pt_row),
    "playlist": (PL_COLS, _pl_row),
}


def _op_set(r):
    k = r.get("k")
    spec = _SETTERS.get(k)
    if spec is None:
        raise ValueError("kind must be one of " + ", ".join(sorted(_SETTERS)))
    lo, hi = _BOUNDS[k]()
    i = _idx(r["i"], lo, hi, k)
    props = dict(r.get("p") or {})
    send, level = None, None
    if k == "mixer":
        send, level = props.pop("send", None), props.pop("level", None)
    bad = [x for x in props if x not in spec]
    if bad:
        raise ValueError(
            "not settable on %s: %s (allowed: %s)" % (k, ", ".join(bad), ", ".join(sorted(spec)))
        )
    _route(i, send, level)
    for name, value in props.items():
        spec[name](i, value)
    cols, row = _ROWS[k]
    return dict(zip(cols, row(i)))


# ---------------------------------------------------------------- transport / status


def _brief():
    return {
        "bpm": _try(lambda: _r(mixer.getCurrentTempo())),
        "play": bool(_try(transport.isPlaying)),
        "rec": bool(_try(transport.isRecording)),
        "mode": "song" if _try(transport.getLoopMode) == 1 else "pattern",
        "pos": _try(transport.getSongPosHint),
    }


def _op_ping(r):
    return {"proto": PROTO, "ver": VERSION, "api": general.getVersion(), "fl": ui.getVersion()}


def _op_status(r):
    d = _brief()
    d.update(
        {
            "fl": _try(ui.getVersion),
            "api": _try(general.getVersion),
            "title": _try(ui.getProgTitle),
            "metro": bool(_try(general.getUseMetronome)),
            "pat": _try(patterns.patternNumber),
            "ch": _try(channels.selectedChannel, False, 0, True),
            "trk": _try(mixer.trackNumber),
            "n_ch": _try(channels.channelCount, True),
            "n_pat": _try(patterns.patternCount),
            "ppq": _try(general.getRecPPQ),
        }
    )
    return d


def _op_tr(r):
    a, v = r.get("a"), r.get("v")
    if a == "play":
        if not transport.isPlaying():
            transport.start()
    elif a == "pause":
        if transport.isPlaying():
            transport.start()
    elif a == "stop":
        transport.stop()
    elif a == "record":
        if v is None or bool(transport.isRecording()) != bool(v):
            transport.record()
    elif a == "loop":
        want = 1 if v in (1, "song") else 0
        if transport.getLoopMode() != want:
            transport.setLoopMode()
    elif a == "metronome":
        if v is None or bool(general.getUseMetronome()) != bool(v):
            transport.globalTransport(midi.FPT_Metronome, 1)
    elif a == "tempo":
        bpm = _num(v, 10, 522, "tempo")
        general.processRECEvent(
            midi.REC_Tempo, int(round(bpm * 1000)), midi.REC_Control | midi.REC_UpdateControl
        )
    elif a == "position":
        bar = _num(v, 1, 100000, "bar")
        transport.setSongPos(int(round((bar - 1) * general.getRecPPB())), midi.SONGLENGTH_ABSTICKS)
    elif a == "save":
        transport.globalTransport(midi.FPT_Save, 1)
    elif a == "undo":
        general.undo()
    else:
        raise ValueError("unknown action")
    return _brief()


# ---------------------------------------------------------------- steps / params / roll


def _op_steps(r):
    c = _idx(r["c"], 0, channels.channelCount(True) - 1, "channel")
    p = int(r.get("p") or 0)
    if p > 0 and p != patterns.patternNumber():
        patterns.jumpToPattern(p)
    pat = patterns.patternNumber()
    n = _idx(r.get("n") or 16, 1, 256, "length")
    if r.get("clear"):
        for s in range(n):
            if channels.getGridBit(c, s, True):
                channels.setGridBit(c, s, False, True)
    for s in r.get("off") or ():
        channels.setGridBit(c, _idx(s, 0, 511, "step"), False, True)
    vel, pit = r.get("vel"), r.get("pit")
    for s in r.get("on") or ():
        s = _idx(s, 0, 511, "step")
        channels.setGridBit(c, s, True, True)
        if vel is not None:
            channels.setStepParameterByIndex(
                c, pat, s, midi.pVelocity, _idx(vel, 0, 127, "velocity"), True
            )
        if pit is not None:
            channels.setStepParameterByIndex(
                c, pat, s, midi.pPitch, _idx(pit, 0, 127, "pitch"), True
            )
    return {
        "pat": pat,
        "ch": c,
        "n": n,
        "on": [s for s in range(n) if channels.getGridBit(c, s, True)],
    }


def _op_param(r):
    t = r.get("t")
    if t == "channel":
        i = _idx(r["i"], 0, channels.channelCount(True) - 1, "channel")
        slot, glob = -1, True
    elif t == "mixer":
        i = _idx(r["i"], 0, mixer.trackCount() - 2, "track")
        if r.get("s") is None:
            raise ValueError("mixer needs slot 0-9")
        slot, glob = _idx(r["s"], 0, 9, "slot"), False
    else:
        raise ValueError("target must be channel or mixer")
    if not plugins.isValid(i, slot, glob):
        raise ValueError("no plugin there")
    out = {"plugin": plugins.getPluginName(i, slot, False, glob)}
    p, v = r.get("p"), r.get("v")
    if p is not None and v is not None:
        p = _idx(p, 0, plugins.getParamCount(i, slot, glob) - 1, "param")
        plugins.setParamValue(_num(v, 0, 1, "value"), p, i, slot, midi.PIM_None, glob)
        out["param"] = [
            p,
            plugins.getParamName(p, i, slot, glob),
            _r(plugins.getParamValue(p, i, slot, glob)),
            plugins.getParamValueString(p, i, slot, midi.PIM_None, glob),
        ]
        return out
    find = str(r.get("f") or "").lower()
    limit = _idx(r.get("n") or 40, 1, 200, "limit")
    count = plugins.getParamCount(i, slot, glob)
    rows = []
    for k in range(count):
        name = plugins.getParamName(k, i, slot, glob)
        if name and (not find or find in name.lower()):
            rows.append([k, name, _r(plugins.getParamValue(k, i, slot, glob))])
            if len(rows) >= limit:
                break
    out.update({"count": count, "cols": ["i", "name", "val"], "rows": rows})
    return out


def _op_roll(r):
    c, p = int(r.get("c", -1)), int(r.get("p") or 0)
    if p > 0:
        patterns.jumpToPattern(p)
    if c >= 0:
        channels.selectOneChannel(_idx(c, 0, channels.channelCount(True) - 1, "channel"), True)
    ui.showWindow(midi.widPianoRoll)
    return {
        "pat": patterns.patternNumber(),
        "ch": channels.selectedChannel(False, 0, True),
        "ppq": general.getRecPPQ(),
    }


# ---------------------------------------------------------------- raw API access

_MODS = {
    "transport": transport,
    "mixer": mixer,
    "channels": channels,
    "patterns": patterns,
    "playlist": playlist,
    "plugins": plugins,
    "ui": ui,
    "general": general,
    "arrangement": arrangement,
}


def _arg(a):
    if isinstance(a, str) and a.startswith("midi."):
        v = 0
        for part in a.split("|"):
            part = part.strip()
            if not part.startswith("midi."):
                raise ValueError("bad constant " + part)
            v |= int(getattr(midi, part[5:]))
        return v
    return a


def _op_call(r):
    out = []
    for n, c in enumerate(r["c"]):
        name = str(c[0])
        mod, _, fn = name.partition(".")
        if mod not in _MODS or not fn or fn.startswith("_"):
            raise ValueError("call %d: %s is not allowed" % (n, name))
        try:
            out.append(_plain(getattr(_MODS[mod], fn)(*[_arg(a) for a in c[1:]])))
        except Exception as exc:
            raise ValueError(
                "call %d (%s) failed after %d ok: %s" % (n, name, n, _err(exc))
            ) from None
    return out


_OPS = {
    "ping": _op_ping,
    "status": _op_status,
    "list": _op_list,
    "set": _op_set,
    "tr": _op_tr,
    "steps": _op_steps,
    "param": _op_param,
    "roll": _op_roll,
    "call": _op_call,
}
