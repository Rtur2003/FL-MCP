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

VERSION = 6
PROTO = 1

_REC_PLUG_MUTE = getattr(midi, "REC_Plug_Mute", 536878848)
_REC_PLUG_MIX = getattr(midi, "REC_Plug_MixLevel", 536878849)

_IN, _OUT = 14, 15
_BEGIN, _DATA, _SUM_A, _SUM_B = 102, 103, 104, 105
_MAX_IN, _MAX_OUT = 20000, 8192

_rx = {"fid": -1, "buf": bytearray(), "sa": -1}
_last = {"key": None, "body": b""}
_diag = {"n": 0, "failed": 0, "errors": []}  # the last few failures, for fl_status(log=true)


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
    _diag["n"] += 1
    op = None
    try:
        req = json.loads(data.decode("ascii"))
        op = req.get("o")
        body = _dump({"ok": 1, "r": _run(req)})
    except Exception as exc:
        body = _dump({"ok": 0, "e": _err(exc)})
        _diag["failed"] += 1
        _diag["errors"] = (_diag["errors"] + [[_diag["n"], op, _err(exc)]])[-12:]
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


PG_COLS = ["i", "at", "idx", "slot", "plugin", "name", "params", "presets"]


def _mk_plugins(everything):
    where = []
    for c in range(channels.channelCount(True)):
        if channels.getChannelType(c, True) in (1, 2):
            where.append(("c", c, -1))
    for t in range(mixer.trackCount() - 1):
        for s in range(10):
            if mixer.isTrackPluginValid(t, s):
                where.append(("m", t, s))

    def make(i):
        at, idx, slot = where[i]
        glob = at == "c"
        return [
            i,
            at,
            idx,
            slot,
            plugins.getPluginName(idx, slot, False, glob),
            plugins.getPluginName(idx, slot, True, glob),
            _try(plugins.getParamCount, idx, slot, glob) or 0,
            _try(plugins.getPresetCount, idx, slot, glob) or 0,
        ]

    return PG_COLS, 0, len(where) - 1, make


_LISTS = {
    "channels": _mk_channels,
    "mixer": _mk_mixer,
    "patterns": _mk_patterns,
    "playlist": _mk_playlist,
    "plugins": _mk_plugins,
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
        "stereo": lambda i, v: mixer.setTrackStereoSep(i, _num(v, -1, 1, "stereo")),
        "arm": lambda i, v: _flip(mixer.isTrackArmed(i), v, lambda: mixer.armTrack(i)),
        "polarity": lambda i, v: mixer.revTrackPolarity(i, bool(v)),
        "swap": lambda i, v: mixer.swapTrackChannels(i, bool(v)),
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
    out = dict(zip(cols, row(i)))
    if k == "mixer":
        out["stereo"] = _r(_try(mixer.getTrackStereoSep, i))
        out["arm"] = bool(_try(mixer.isTrackArmed, i))
    return out


# ---------------------------------------------------------------- transport / status


def _tempo():
    v = _try(mixer.getCurrentTempo, 1)
    if v is None:
        v = _try(mixer.getCurrentTempo)
    if v is None:
        return None
    return _r(v / 1000.0 if v > 1000 else v)


def _brief():
    return {
        "bpm": _tempo(),
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
            "len_bars": _try(transport.getSongLength, midi.SONGLENGTH_BARS),
            "len_ms": _try(transport.getSongLength, midi.SONGLENGTH_MS),
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
    elif a == "savenew":
        transport.globalTransport(midi.FPT_SaveNew, 1)
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


# ---------------------------------------------------------------- plugin parameters

_UNITS = {
    "db": ("db", 1.0),
    "hz": ("hz", 1.0),
    "khz": ("hz", 1000.0),
    "ms": ("ms", 1.0),
    "sec": ("ms", 1000.0),
    "s": ("ms", 1000.0),
    "%": ("%", 1.0),
    "st": ("st", 1.0),
    "semitones": ("st", 1.0),
    "semitone": ("st", 1.0),
    "ct": ("ct", 1.0),
    "cents": ("ct", 1.0),
    "cent": ("ct", 1.0),
    "x": ("x", 1.0),
    ":1": ("ratio", 1.0),
    "oct": ("oct", 1.0),
    "bpm": ("bpm", 1.0),
}
_UNIT_KEYS = sorted(_UNITS, key=len, reverse=True)
_TOL = {"db": 0.15, "hz": 1.0, "ms": 0.5, "%": 0.6, "st": 0.2, "ct": 1.0, "ratio": 0.1, "x": 0.05}


def _scan_number(s):
    n = len(s)
    for i in range(n):
        c = s[i]
        digit_next = i + 1 < n and (
            s[i + 1].isdigit() or (s[i + 1] == "." and i + 2 < n and s[i + 2].isdigit())
        )
        if (
            c.isdigit()
            or (c == "." and i + 1 < n and s[i + 1].isdigit())
            or (c in "+-" and digit_next)
        ):
            j, dot = i + 1, c == "."
            while j < n and (s[j].isdigit() or (s[j] == "." and not dot)):
                dot = dot or s[j] == "."
                j += 1
            try:
                return float(s[i:j]), s[j:]
            except ValueError:
                return None
    return None


def _parse_text(text):
    """A plugin's value text as (number, unit): '2.5 kHz' -> (2500.0, 'hz')."""
    s = str(text).strip()
    if not s:
        return None
    low = s.lower()
    if "inf" in low:
        return (-120.0 if "-" in low else 120.0), "db"
    got = _scan_number(s)
    if got is None:
        return None
    value, rest = got
    if s[:1] in "LlRr" and s[1:2] in " 0123456789":  # pan readouts: L 50% / R 30%
        value = -abs(value) if s[0] in "Ll" else abs(value)
    rest = rest.lower().replace(" ", "")
    for key in _UNIT_KEYS:
        if rest.startswith(key):
            unit, scale = _UNITS[key]
            return value * scale, unit
    return value, ""


def _key(text):
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _plug_ref(r):
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
    return i, slot, glob


def _tokens(text):
    out, buf = [], []
    for ch in str(text or "").lower():
        if ch.isalnum():
            buf.append(ch)
        elif buf:
            out.append("".join(buf))
            buf = []
    if buf:
        out.append("".join(buf))
    return out


def _find_param(i, slot, glob, ref):
    """A parameter index from an index or a name (exact, prefix, substring, then tokens)."""
    count = plugins.getParamCount(i, slot, glob)
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return _idx(int(ref), 0, count - 1, "param")
    want = _key(ref)
    toks = _tokens(ref)
    if not want:
        raise ValueError("param needs an index or a name")
    exact, starts, within, tok_all = [], [], [], []
    named = []
    for k in range(count):
        name = plugins.getParamName(k, i, slot, glob)
        key = _key(name)
        if not key:
            continue
        named.append((k, name, key))
        if key == want:
            exact.append(k)
        elif key.startswith(want):
            starts.append(k)
        elif want in key:
            within.append(k)
        elif len(toks) > 1 and all(t in key for t in toks):
            tok_all.append(k)
    for group in (exact, starts, within, tok_all):
        if len(group) == 1:
            return group[0]
        if group:
            names = ", ".join(
                "%d %s" % (k, plugins.getParamName(k, i, slot, glob)) for k in group[:8]
            )
            raise ValueError("'%s' matches several parameters: %s" % (ref, names))
    close = ["%d %s" % (k, name) for k, name, key in named if toks and any(t in key for t in toks)][
        :6
    ]
    if not close:
        close = ["%d %s" % (k, name) for k, name, _ in named[:6]]
    if close:
        raise ValueError("no parameter named '%s'; try: %s" % (ref, ", ".join(close)))
    raise ValueError("no parameter named '%s'; list them with find" % ref)


def _shown(i, slot, glob, p):
    return plugins.getParamValueString(p, i, slot, glob)


def _param_row(i, slot, glob, p):
    return [
        p,
        plugins.getParamName(p, i, slot, glob),
        _r(plugins.getParamValue(p, i, slot, glob)),
        _shown(i, slot, glob, p),
    ]


def _target(value, unit):
    """The wanted real value as (number, canonical unit) from a number or a text like '-6 dB'."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        got = (float(value), "")
    else:
        got = _parse_text(value)
        if got is None:
            raise ValueError("cannot read '%s' as a value with a unit" % value)
    unit = str(unit or "").lower()
    if unit:
        key = unit if unit in _UNITS else None
        if key is None:
            raise ValueError("unknown unit " + unit)
        canon = _UNITS[key]
        return (got[0] * canon[1], canon[0]) if not got[1] else got
    return got


def _solve_param(i, slot, glob, p, want, unit):
    """Set parameter p so that its own value text shows `want`: sample, bracket, bisect, verify."""
    start = plugins.getParamValue(p, i, slot, glob)

    def put(x):
        plugins.setParamValue(x, p, i, slot, midi.PIM_None, glob)
        return _parse_text(_shown(i, slot, glob, p))

    try:
        first = _parse_text(_shown(i, slot, glob, p))
        if first is None:
            raise ValueError("this plugin does not show the value as text; use value 0-1")
        if unit and first[1] and unit != first[1]:
            raise ValueError("the parameter is in %s, not %s" % (first[1], unit))
        samples = []
        for k in range(17):
            got = put(k / 16.0)
            if got is not None:
                samples.append((k / 16.0, got[0]))
        if len(samples) < 3:
            raise ValueError("the value text is not numeric")
    except Exception:
        plugins.setParamValue(start, p, i, slot, midi.PIM_None, glob)
        raise
    tol = max(abs(want) * 0.02, _TOL.get(unit or first[1], 0.01))
    best = min(samples, key=lambda sv: abs(sv[1] - want))
    x_best, v_best = best
    for (x1, v1), (x2, v2) in zip(samples, samples[1:]):
        if (v1 - want) * (v2 - want) <= 0 and v1 != v2:
            a, b, rising = x1, x2, v2 > v1
            for _ in range(20):
                mid = (a + b) / 2
                got = put(mid)
                if got is None:
                    break
                if abs(got[0] - want) < abs(v_best - want):
                    x_best, v_best = mid, got[0]
                if abs(got[0] - want) <= tol / 8:  # as close as the plugin can show
                    break
                if (got[0] < want) == rising:
                    a = mid
                else:
                    b = mid
            break
    plugins.setParamValue(x_best, p, i, slot, midi.PIM_None, glob)
    return abs(v_best - want) <= tol * 2


def _plug_eid(i, slot, offset):
    pid = _try(mixer.getTrackPluginId, i, slot)
    return None if pid is None or pid < 0 else pid + offset


def _slot_mix_get(i, slot):
    eid = _plug_eid(i, slot, _REC_PLUG_MIX)
    if eid is None:
        return 1.0
    v = _try(device.getLinkedValue, eid)
    if v is not None and 0.0 <= v <= 1.0:
        return float(v)
    raw = _try(general.processRECEvent, eid, 0, midi.REC_GetValue)
    if raw is None or raw < 0:
        return 1.0
    if raw > 12800:
        return max(0.0, min(1.0, raw / float(midi.FromMIDI_Max)))
    return max(0.0, min(1.0, raw / 12800.0))


def _slot_mix_set(i, slot, val):
    eid = _plug_eid(i, slot, _REC_PLUG_MIX)
    if eid is None:
        raise ValueError("cannot address mixer slot mix")
    general.processRECEvent(
        eid,
        int(round(val * midi.FromMIDI_Max)),
        midi.REC_MIDIController | midi.REC_UpdateControl,
    )


def _slot_mute_get(i, slot):
    eid = _plug_eid(i, slot, _REC_PLUG_MUTE)
    if eid is None:
        return False
    v = _try(device.getLinkedValue, eid)
    if v is not None and 0.0 <= v <= 1.0:
        return v < 0.5
    raw = _try(general.processRECEvent, eid, 0, midi.REC_GetValue)
    return False if raw is None or raw < 0 else not bool(raw)


def _slot_mute_set(i, slot, muted):
    eid = _plug_eid(i, slot, _REC_PLUG_MUTE)
    if eid is None:
        raise ValueError("cannot address mixer slot mute")
    general.processRECEvent(eid, 0 if muted else 1, midi.REC_SetAll)


def _parse_toggle(item, on_words, off_words, default=None):
    if item.get("v") is not None:
        return bool(_num(item["v"], 0, 1, "value") >= 0.5)
    if item.get("r") is not None:
        s = str(item["r"]).strip().lower()
        if s in on_words:
            return True
        if s in off_words:
            return False
        raise ValueError("expected one of %s / %s, got '%s'" % (on_words[0], off_words[0], s))
    if default is not None:
        return default
    raise ValueError("give value (0 or 1) or real")


def _set_wrapper(i, slot, glob, key, item):
    if key == "fl:focus":
        on = _parse_toggle(
            item, ("1", "true", "on", "yes", "focus"), ("0", "false", "off", "no"), True
        )
        if on:
            if glob:
                channels.focusEditor(i, True)
            else:
                mixer.focusEditor(i, slot)
        return [-1, "fl:focus", 1.0 if on else 0.0, "Focused" if on else "Off", 1]
    if glob:
        raise ValueError("%s applies to mixer effect slots only" % key)
    if key == "fl:mix":
        if item.get("r") is not None:
            want, unit = _target(item["r"], item.get("u"))
            if unit not in ("", "%"):
                raise ValueError("fl:mix expects 0-1 or %")
            val = _num(want / 100.0 if unit == "%" else want, 0, 1, "fl:mix")
        elif item.get("v") is not None:
            val = _num(item["v"], 0, 1, "fl:mix")
        else:
            raise ValueError("give value (0-1) or real ('50%%') for fl:mix")
        _slot_mix_set(i, slot, val)
        got = _slot_mix_get(i, slot)
        return [-1, "fl:mix", _r(got), "%d%%" % round(got * 100), 1]
    if key == "fl:mute":
        mute = _parse_toggle(
            item,
            ("1", "true", "on", "yes", "mute", "muted", "bypass"),
            ("0", "false", "off", "no", "unmute", "active"),
        )
        _slot_mute_set(i, slot, mute)
        got = _slot_mute_get(i, slot)
        return [-2, "fl:mute", 1.0 if got else 0.0, "Muted" if got else "Active", 1]
    if key == "fl:slots":
        enable = _parse_toggle(
            item,
            ("1", "true", "on", "yes", "enable", "enabled"),
            ("0", "false", "off", "no", "disable", "disabled"),
        )
        mixer.enableTrackSlots(i, enable)
        got = bool(mixer.isTrackSlotsEnabled(i))
        return [-3, "fl:slots", 1.0 if got else 0.0, "On" if got else "Off", 1]
    raise ValueError("unknown wrapper control '%s'; use fl:mix, fl:mute, fl:slots, fl:focus" % key)


def _set_one(i, slot, glob, item):
    ref = item.get("p")
    if isinstance(ref, str) and ref.strip().lower().startswith("fl:"):
        return _set_wrapper(i, slot, glob, ref.strip().lower(), item)
    p = _find_param(i, slot, glob, ref)
    ok = True
    if item.get("r") is not None:
        want, unit = _target(item["r"], item.get("u"))
        ok = _solve_param(i, slot, glob, p, want, unit)
    elif item.get("v") is not None:
        plugins.setParamValue(_num(item["v"], 0, 1, "value"), p, i, slot, midi.PIM_None, glob)
    else:
        raise ValueError("give value (0-1) or real (a number with a unit)")
    return _param_row(i, slot, glob, p) + [int(ok)]


def _preset_names(i, slot, glob, start, count):
    total = plugins.getPresetCount(i, slot, glob)
    return total, [
        [k, plugins.getName(i, slot, midi.FPN_Preset, k, glob)]
        for k in range(start, min(total, start + count))
    ]


def _current_preset(i, slot, glob):
    return plugins.getName(i, slot, midi.FPN_Preset, midi.GPN_GetCurrentPreset, glob)


def _op_preset(i, slot, glob, r):
    total = plugins.getPresetCount(i, slot, glob)
    if total <= 0:
        raise ValueError("this plugin has no internal presets")
    steps = r.get("d")
    if r.get("q"):
        want = _key(r["q"])
        names = [(k, plugins.getName(i, slot, midi.FPN_Preset, k, glob)) for k in range(total)]
        hits = [k for k, n in names if _key(n) == want] or [k for k, n in names if want in _key(n)]
        if not hits:
            raise ValueError("no preset matches '%s' (list them with a=presets)" % r["q"])
        here = _current_preset(i, slot, glob)
        now = [k for k, n in names if n == here]
        if not now:
            raise ValueError("cannot tell which preset is loaded; step with d instead")
        steps = hits[0] - now[0]
    steps = int(steps or 0)
    if abs(steps) > total:
        raise ValueError("at most %d steps" % total)
    for _ in range(abs(steps)):
        if steps > 0:
            plugins.nextPreset(i, slot, glob)
        else:
            plugins.prevPreset(i, slot, glob)
    return {"preset": _current_preset(i, slot, glob), "count": total, "moved": steps}


def _named_series(i, slot, glob, flag, cap):
    rows = []
    for k in range(cap):
        name = _try(plugins.getName, i, slot, flag, k, glob)
        if not name:
            break
        rows.append([k, name])
    return rows


def _note_names(i, slot, glob, limit):
    rows = []
    for note in range(128):
        name = _try(plugins.getName, i, slot, midi.FPN_Semitone, note, glob)
        if name:
            rows.append([note, name])
            if len(rows) >= limit:
                break
    return rows


def _track_slots_io(trk):
    rows = []
    for s in range(10):
        if plugins.isValid(trk, s, False):
            rows.append(
                [
                    s,
                    plugins.getPluginName(trk, s, True, False),
                    int(_slot_mute_get(trk, s)),
                    _r(_slot_mix_get(trk, s)),
                ]
            )
    return rows


def _track_sends(trk, n_trk):
    out = []
    for d in range(n_trk):
        if d != trk and _try(mixer.getRouteSendActive, trk, d):
            lvl = _try(mixer.getRouteToLevel, trk, d)
            out.append([d, mixer.getTrackName(d), _r(1.0 if lvl is None else lvl)])
            if len(out) >= 32:
                break
    return out


def _plug_io(i, slot, glob):
    n_trk = mixer.trackCount() - 1
    out = {
        "kind": "channel" if glob else "mixer",
        "params": plugins.getParamCount(i, slot, glob),
        "presets": plugins.getPresetCount(i, slot, glob),
        "preset": _try(_current_preset, i, slot, glob),
        "out_ctrls": _named_series(i, slot, glob, midi.FPN_OutCtrl, 32),
        "out_voices": _named_series(i, slot, glob, midi.FPN_OutVoice, 16),
        "notes": _note_names(i, slot, glob, 48),
    }
    if glob:
        trk = _try(channels.getTargetFxTrack, i, True)
        if trk is None:
            trk = 0
        out.update(
            {
                "channel": i,
                "channel_name": channels.getChannelName(i, True),
                "channel_type": channels.getChannelType(i, True),
                "midi_in_port": _try(channels.getChannelMidiInPort, i, True),
                "activity": _r(_try(channels.getActivityLevel, i, True) or 0.0),
                "target_track": trk,
                "target_track_name": _try(mixer.getTrackName, trk),
                "track_slots_enabled": _try(mixer.isTrackSlotsEnabled, trk),
                "fx_slots": _track_slots_io(trk),
                "sends": _track_sends(trk, n_trk),
            }
        )
    else:
        in_chans = [
            [c, channels.getChannelName(c, True)]
            for c in range(channels.channelCount(True))
            if _try(channels.getTargetFxTrack, c, True) == i
        ][:32]
        in_trks = []
        for s in range(n_trk):
            if s != i and _try(mixer.getRouteSendActive, s, i):
                lvl = _try(mixer.getRouteToLevel, s, i)
                in_trks.append([s, mixer.getTrackName(s), _r(1.0 if lvl is None else lvl)])
                if len(in_trks) >= 32:
                    break
        out.update(
            {
                "track": i,
                "track_name": mixer.getTrackName(i),
                "slot": slot,
                "muted": _slot_mute_get(i, slot),
                "mix": _r(_slot_mix_get(i, slot)),
                "track_slots_enabled": _try(mixer.isTrackSlotsEnabled, i),
                "automation_enabled": _try(mixer.isTrackAutomationEnabled, i, slot),
                "in_channels": in_chans,
                "in_tracks": in_trks,
                "fx_slots": _track_slots_io(i),
                "sends": _track_sends(i, n_trk),
            }
        )
    return out


def _op_param(r):
    i, slot, glob = _plug_ref(r)
    act = r.get("a") or ("many" if r.get("set") else "set" if r.get("p") is not None else "list")
    out = {
        "plugin": plugins.getPluginName(i, slot, False, glob),
        "name": plugins.getPluginName(i, slot, True, glob),
    }
    if act == "info":
        out.update(
            {
                "params": plugins.getParamCount(i, slot, glob),
                "presets": plugins.getPresetCount(i, slot, glob),
                "preset": _try(_current_preset, i, slot, glob),
            }
        )
    elif act == "io":
        out.update(_plug_io(i, slot, glob))
    elif act == "set":
        out["param"] = _set_one(i, slot, glob, r)
    elif act == "many":
        items = r.get("set") or []
        if len(items) > 40:
            raise ValueError("at most 40 parameters per call")
        done = []
        for n, item in enumerate(items):
            try:
                done.append(_set_one(i, slot, glob, item))
            except Exception as exc:
                raise ValueError("item %d (%s): %s" % (n, item.get("p"), exc)) from None
        out["params"] = done
        out["cols"] = ["i", "name", "val", "text", "ok"]
    elif act == "presets":
        total, rows = _preset_names(
            i, slot, glob, int(r.get("st") or 0), _idx(r.get("n") or 40, 1, 120, "limit")
        )
        out.update(
            {
                "count": total,
                "cols": ["i", "name"],
                "rows": rows,
                "cur": _try(_current_preset, i, slot, glob),
            }
        )
    elif act == "preset":
        out.update(_op_preset(i, slot, glob, r))
    elif act == "list":
        find = _key(r.get("f"))
        limit = _idx(r.get("n") or 40, 1, 120, "limit")
        count = plugins.getParamCount(i, slot, glob)
        rows, nxt = [], None
        for k in range(int(r.get("st") or 0), count):
            name = plugins.getParamName(k, i, slot, glob)
            if name and (not find or find in _key(name)):
                if len(rows) >= limit:
                    nxt = k
                    break
                rows.append(_param_row(i, slot, glob, k))
        out.update(
            {"count": count, "cols": ["i", "name", "val", "text"], "rows": rows, "next": nxt}
        )
    else:
        raise ValueError("unknown action")
    return out


def _op_roll(r):
    c, p = int(r.get("c", -1)), int(r.get("p") or 0)
    if p > 0:
        patterns.jumpToPattern(p)
    if c >= 0:
        channels.selectOneChannel(_idx(c, 0, channels.channelCount(True) - 1, "channel"), True)
    if not ui.getVisible(midi.widPianoRoll):
        ui.showWindow(midi.widPianoRoll)
    return {
        "pat": patterns.patternNumber(),
        "ch": channels.selectedChannel(False, 0, True),
        "ppq": general.getRecPPQ(),
    }


# ---------------------------------------------------------------- real units, metering, batches


def _db(x):
    return round(max(-120.0, float(x)), 2)


def _solve(setter, getter, target, tol, lo=0.0, hi=1.0):
    """Find the normalised value whose real-unit read-back matches `target` (monotonic rising)."""
    a, b = lo, hi
    mid, val = a, None
    for _ in range(24):
        mid = (a + b) / 2
        setter(mid)
        val = getter()
        if abs(val - target) <= tol:
            break
        if val < target:
            a = mid
        else:
            b = mid
    return mid, val


def _op_gain(r):
    """Set a fader to a level in dB; the exact curve is found by reading the dB value back."""
    k = r.get("t")
    if k == "mixer":
        i = _idx(r["i"], 0, mixer.trackCount() - 2, "track")
        setter, getter = lambda x: mixer.setTrackVolume(i, x), lambda: mixer.getTrackVolume(i, 1)
    elif k == "channel":
        i = _idx(r["i"], 0, channels.channelCount(True) - 1, "channel")
        setter, getter = (
            lambda x: channels.setChannelVolume(i, x, midi.PIM_None, True),
            lambda: channels.getChannelVolume(i, True, True),
        )
    else:
        raise ValueError("target must be mixer or channel")
    # "db" is absolute; "d" changes the current level by that many dB
    db = (
        _num(r["db"], -90, 12, "db")
        if r.get("db") is not None
        else max(-90.0, min(12.0, getter() + _num(r["d"], -60, 60, "d")))
    )
    mid, got = _solve(setter, getter, db, 0.05)
    return {"v": _r(mid), "db": _db(got), "clamped": abs(got - db) > 0.25}


def _op_eq(r):
    """Built-in mixer EQ: set frequency (Hz) and gain (dB) of one band, verified by read-back."""
    i = _idx(r["i"], 0, mixer.trackCount() - 2, "track")
    b = _idx(r["b"], 0, mixer.getEqBandCount() - 1, "band")
    out = {"b": b}
    if r.get("f") is not None:
        hz = _num(r["f"], 20, 20000, "frequency")
        _, got = _solve(
            lambda x: mixer.setEqFrequency(i, b, x),
            lambda: mixer.getEqFrequency(i, b, 1),
            hz,
            hz * 0.01,
        )
        out["f"] = round(got, 1)
    if r.get("g") is not None:
        db = _num(r["g"], -24, 24, "gain")
        _, got = _solve(
            lambda x: mixer.setEqGain(i, b, x), lambda: mixer.getEqGain(i, b, 1), db, 0.05
        )
        out["g"] = _db(got)
    if r.get("w") is not None:
        mixer.setEqBandwidth(i, b, _num(r["w"], 0, 1, "bandwidth"))
    out["w"] = _r(mixer.getEqBandwidth(i, b))
    return out


def _op_peaks(r):
    ts = [_idx(t, 0, mixer.trackCount() - 2, "track") for t in r["t"][:64]]
    return {"p": [_r(mixer.getTrackPeaks(t, 2)) for t in ts]}


def _op_markers(r):
    for ticks, name in r["m"][:64]:
        arrangement.addAutoTimeMarker(_idx(ticks, 0, 10**9, "time"), str(name)[:40])
    return {"n": len(r["m"][:64])}


def _op_note(r):
    c = _idx(r["c"], 0, channels.channelCount(True) - 1, "channel")
    channels.midiNoteOn(c, _idx(r["n"], 0, 127, "note"), _idx(r.get("v", 100), 0, 127, "velocity"))
    return {"c": c}


def _op_grid(r):
    """Replace one channel's steps in a pattern from a string: '.' = off, 1-9/a-z = velocity."""
    c = _idx(r["c"], 0, channels.channelCount(True) - 1, "channel")
    pat = _idx(r["p"], 1, patterns.patternMax(), "pattern")
    g = str(r["g"])
    if len(g) > 512 or any(ch not in ".0123456789abcdefghijklmnopqrstuvwxyz" for ch in g):
        raise ValueError("grid must be at most 512 characters of . 0-9 a-z")
    if pat != patterns.patternNumber():
        patterns.jumpToPattern(pat)
    sw = _idx(r.get("sw") or 0, 0, 48, "swing")
    on = bad = 0
    for s, ch in enumerate(g):
        want = ch not in ".0"
        if want != bool(channels.getGridBit(c, s, True)):
            channels.setGridBit(c, s, want, True)
        if want:
            on += 1
            vel = round(int(ch, 36) * 127 / 35)
            channels.setStepParameterByIndex(c, pat, s, midi.pVelocity, vel, True)
            if sw and s % 2:
                channels.setStepParameterByIndex(c, pat, s, midi.pShift, sw, True)
    for s in range(len(g)):  # verify what FL actually kept
        if (g[s] not in ".0") != bool(channels.getGridBit(c, s, True)):
            bad += 1
    return {"on": on, "bad": bad}


def _op_batch(r):
    """Run many ops in one request; one undo step covers them all."""
    steps = r["s"]
    if len(steps) > 300:
        raise ValueError("at most 300 steps per batch")
    bad = 0
    for n, sub in enumerate(steps):
        if sub.get("o") in ("batch", "ping"):
            raise ValueError("step %d: %s cannot run inside a batch" % (n, sub.get("o")))
        try:
            res = _run(sub)
        except Exception as exc:
            raise ValueError("step %d (%s): %s" % (n, sub.get("o"), _err(exc))) from None
        if isinstance(res, dict):
            bad += int(res.get("bad") or 0)
    if r.get("u"):
        general.saveUndo(
            "FL MCP: " + str(r["u"])[:40], midi.UF_EE | midi.UF_PR | midi.UF_PL | midi.UF_Knob
        )
    return {"n": len(steps), "bad": bad}


# ---------------------------------------------------------------- windows, keys, browser

_WIN = {
    "mixer": midi.widMixer,
    "channels": midi.widChannelRack,
    "playlist": midi.widPlaylist,
    "roll": midi.widPianoRoll,
    "browser": midi.widBrowser,
}

_KEYS = {
    "up": lambda: ui.up(1),
    "down": lambda: ui.down(1),
    "left": lambda: ui.left(1),
    "right": lambda: ui.right(1),
    "enter": lambda: ui.enter(),
    "escape": lambda: ui.escape(),
    "yes": lambda: ui.yes(),
    "no": lambda: ui.no(),
    "cut": lambda: ui.cut(),
    "copy": lambda: ui.copy(),
    "paste": lambda: ui.paste(),
    "insert": lambda: ui.insert(),
    "delete": lambda: ui.delete(),
    "next": lambda: ui.next(),
    "previous": lambda: ui.previous(),
    "tab": lambda: ui.selectWindow(False),
    "shift_tab": lambda: ui.selectWindow(True),
    "nextwindow": lambda: ui.nextWindow(),
    "menu": lambda: transport.globalTransport(midi.FPT_Menu, 1),
    "itemmenu": lambda: transport.globalTransport(midi.FPT_ItemMenu, 1),
}


def _press(name):
    name = str(name).lower()
    if name in _KEYS:
        _KEYS[name]()
    elif name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 12:
        transport.globalTransport(midi.FPT_F1 + int(name[1:]) - 1, 1)
    else:
        raise ValueError("unknown key %s (%s, f1-f12)" % (name, ", ".join(sorted(_KEYS))))


def _win_state(name):
    w = _WIN[name]
    return {"visible": bool(ui.getVisible(w)), "focused": bool(ui.getFocused(w))}


_SKIP_EXPAND = (
    "currentproject",
    "recentfiles",
    "pluginpresets",
    "channelpresets",
    "mixerpresets",
    "scores",
    "backup",
    "channelenvelopes",
    "clipboardfiles",
)


def _browser_item(name=None):
    cap = name or ui.getFocusedNodeCaption()
    if not cap:
        _try(ui.navigateBrowserTabs, 0)
        cap = ui.getFocusedNodeCaption()
    return [cap, ui.getFocusedNodeFileType()]


def _browser_walk(count, expand, want, here=False):
    """Step down the browser tree reading each item; stop at a match or at the end of the list.
    With here, the item that has the focus now is checked before the first step."""
    rows, same, last = [], 0, None
    cur = _browser_item()
    if here and want and want in _key(cur[0]):
        return [cur], 0, True
    for step in range(count):
        sh = bool(expand) and cur[1] == -100 and _key(cur[0]) not in _SKIP_EXPAND
        cur = _browser_item(ui.navigateBrowser(41, sh))
        same = same + 1 if cur[0] == last else 0
        if same >= 2:
            break
        last = cur[0]
        rows.append(cur)
        if want and want in _key(cur[0]):
            return rows, step + 1, True
    return rows, len(rows), False


def _close_popup():
    if _try(ui.isInPopupMenu):
        _try(ui.left, 1)
        _try(ui.escape)
        _try(ui.closeActivePopupMenu)


def _op_browser(r):
    b = r.get("b")
    if b == "caption":
        return {"item": _browser_item()}
    if b == "nav":
        _close_popup()
        down = r.get("d") not in ("prev", "up", 40)
        return {"item": _browser_item(ui.navigateBrowser(41 if down else 40, bool(r.get("sh"))))}
    if b == "tab":
        way = {"left": midi.FPT_Left, "right": midi.FPT_Right}.get(r.get("d"), 0)
        return {"tab": ui.navigateBrowserTabs(way)}
    if b == "toggle":
        ui.toggleBrowserNode(int(r.get("v", -1)))
        return {"item": _browser_item()}
    if b == "select":
        # A plugin's context menu opens and stays open: its first entry (Send to selected
        # channel / Open in new channel) is confirmed from outside FL, because the menu takes
        # no keys through the scripting API. Other entries (favourite, delete file) are never
        # reached: no key but the first-entry confirm is ever sent to it.
        if not ui.getVisible(midi.widBrowser):
            ui.showWindow(midi.widBrowser)
        if _try(ui.isBrowserAutoHide):
            _try(ui.setBrowserAutoHide, False)
        ui.selectBrowserMenuItem()
        return {"item": _browser_item(), "popup": True}
    if b == "preview":
        ui.previewBrowserMenuItem()
        return {"item": _browser_item()}
    if b == "top":
        _close_popup()
        want_tab = _key(r.get("tab"))
        if want_tab:
            for _ in range(8):
                _try(ui.navigateBrowserTabs, midi.FPT_Left)
            for _ in range(8):
                t = _key(_try(ui.navigateBrowserTabs, 0))
                if not t or want_tab in t:
                    break
                nxt = _key(_try(ui.navigateBrowserTabs, midi.FPT_Right))
                if want_tab in nxt:
                    break
        _browser_item()
        last, same = None, 0
        for _ in range(_idx(r.get("n") or 80, 1, 600, "count")):
            name = _browser_item(ui.navigateBrowser(40, False))[0]
            same = same + 1 if name == last else 0
            if same >= 2:
                break
            last = name
        return {"item": _browser_item(), "done": same >= 2}
    if b in ("scan", "goto"):
        want = _key(r.get("q")) if b == "goto" else ""
        if b == "goto" and not want:
            raise ValueError("goto needs q (part of the item name)")
        rows, steps, found = _browser_walk(
            _idx(r.get("n") or 60, 1, 400, "count"), r.get("sh"), want, bool(r.get("here"))
        )
        if b == "goto":
            return {"found": found, "steps": steps, "item": _browser_item()}
        return {"cols": ["name", "type"], "rows": rows}
    raise ValueError(
        "browser action must be caption, nav, tab, toggle, select, preview, scan, goto or top"
    )


def _op_ui(r):
    a = r.get("a")
    if a == "win":
        name = r.get("w")
        if name not in _WIN:
            raise ValueError("window must be one of " + ", ".join(sorted(_WIN)))
        w = _WIN[name]
        v = r.get("v") or "?"
        if v in ("show", "focus"):
            if not ui.getVisible(w):
                ui.showWindow(w)
            if w == midi.widBrowser and _try(ui.isBrowserAutoHide):
                _try(ui.setBrowserAutoHide, False)
            if v == "focus" and w != midi.widBrowser and ui.getVisible(w) and not ui.getFocused(w):
                ui.setFocused(w)
        elif v == "hide":
            if ui.getVisible(w):
                ui.hideWindow(w)
        elif v != "?":
            raise ValueError("v must be show, hide, focus or ?")
        return _win_state(name)
    if a == "key":
        keys = r.get("ks") or [r.get("k")]
        if len(keys) > 20:
            raise ValueError("at most 20 keys per call")
        for k in keys:
            _press(k)
        return {"n": len(keys), "hint": _try(ui.getHintMsg)}
    if a == "hint":
        return {"hint": _try(ui.getHintMsg)}
    if a == "form":
        return {
            "caption": _try(ui.getFocusedFormCaption),
            "plugin": _try(ui.getFocusedPluginName),
            "fx": _try(mixer.getActiveEffectIndex),
            "ch": _try(channels.selectedChannel, False, 0, True),
            "windows": dict((n, _win_state(n)) for n in sorted(_WIN)),
        }
    if a == "browser":
        return _op_browser(r)
    raise ValueError("ui action must be win, key, hint, form or browser")


# ---------------------------------------------------------------- capability probe, diagnostics

_OPTIONAL = (
    ("ui", "navigateBrowser"),
    ("ui", "toggleBrowserNode"),
    ("ui", "getFocusedNodeCaption"),
    ("plugins", "getName"),
    ("plugins", "getPresetCount"),
    ("plugins", "nextPreset"),
    ("plugins", "getParamValueString"),
    ("mixer", "getActiveEffectIndex"),
    ("mixer", "linkChannelToTrack"),
    ("general", "getChangedFlag"),
    ("general", "saveUndo"),
    ("channels", "getChannelType"),
    ("arrangement", "addAutoTimeMarker"),
)


def _op_probe(r):
    """What this FL Studio build offers, so the server can pick a method that will work."""
    return {
        "ver": VERSION,
        "fl": _try(ui.getVersion),
        "api": _try(general.getVersion),
        "title": _try(ui.getProgTitle),
        "has": dict(
            ("%s.%s" % (m, n), getattr(_MODS[m], n, None) is not None) for m, n in _OPTIONAL
        ),
        "n_ch": _try(channels.channelCount, True),
        "n_trk": _try(mixer.trackCount),
        "n_pat": _try(patterns.patternCount),
        "changed": _try(general.getChangedFlag),
        "port": _try(device.getPortNumber),
        "linked": bool(_try(device.isAssigned)),
    }


def _op_diag(r):
    return {"n": _diag["n"], "failed": _diag["failed"], "errors": _diag["errors"]}


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
    "gain": _op_gain,
    "eq": _op_eq,
    "peaks": _op_peaks,
    "markers": _op_markers,
    "note": _op_note,
    "grid": _op_grid,
    "batch": _op_batch,
    "ui": _op_ui,
    "probe": _op_probe,
    "diag": _op_diag,
}
