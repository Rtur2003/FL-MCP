"""Stateful fake of the FL Studio scripting API, layered on the official stubs.

Each fake keeps the real stub's signature, so a wrong call in the controller script fails here
the way it would inside FL Studio. Any API function the script uses without a fake raises.
"""

import functools
import importlib.util
import inspect
import types
from pathlib import Path
from types import SimpleNamespace

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

from fl_mcp.bridge import REQ_CH, RES_CH, Bridge

SCRIPT = Path(__file__).parents[1] / "src" / "fl_mcp" / "fl" / "device_FLMCP.py"
MODULES = [
    transport,
    mixer,
    channels,
    patterns,
    playlist,
    plugins,
    ui,
    general,
    arrangement,
    device,
]


def _checked(stub, impl, label):
    sig = inspect.signature(stub)

    @functools.wraps(stub)
    def call(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        return impl(*bound.args, **bound.kwargs)

    call.__qualname__ = label
    return call


def _unfaked(label):
    def call(*args, **kwargs):
        raise AssertionError(f"{label} is called by the script but not faked")

    return call


class FakeFL:
    def __init__(self):
        self.bpm, self.ppq = 140.0, 96
        self.playing = self.recording = self.metro = False
        self.loop, self.ticks = 0, 0
        self.saves = self.undos = 0
        self.rec_events = []
        self.windows = []
        self.out_hook = None
        self.assigned = True
        self.chans = [
            self._chan("Kick", 0),
            self._chan("Snare", 0),
            self._chan("Lead", 2, "Sytrus"),
        ]
        self.sel_channel = 0
        self.tracks = [self._track("Master")]
        self.tracks += [self._track(f"Insert {i}", {0}) for i in range(1, 126)]
        self.tracks += [self._track("Current")]
        self.tracks[0]["routes"] = set()
        self.tracks[10]["name"] = "Drums"
        self.tracks[10]["slots"] = {0: "Fruity Reeverb 2"}
        self.cur_track = 1
        self.pat = 1
        self.pats = {1: {"name": "Pattern 1", "color": 0, "len": 4}}
        self.tlist = {
            i: {"name": f"Track {i}", "mute": 0, "solo": 0, "color": 0} for i in range(1, 501)
        }
        self.params = {
            ("c", 2, -1): [["Cutoff", 0.5], ["Resonance", 0.2], ["", 0.0], ["Master level", 0.8]],
            ("m", 10, 0): [["Wet", 0.3], ["Decay", 0.6]],
        }

    @staticmethod
    def _chan(name, kind, plugin=""):
        return {
            "name": name,
            "type": kind,
            "vol": 0.78125,
            "pan": 0.0,
            "mute": 0,
            "solo": 0,
            "fx": 0,
            "color": 0,
            "plugin": plugin,
            "steps": {},
            "sp": {},
        }

    @staticmethod
    def _track(name, routes=None):
        return {
            "name": name,
            "vol": 0.8,
            "pan": 0.0,
            "mute": 0,
            "solo": 0,
            "color": 0,
            "routes": set(routes or ()),
            "levels": {},
            "slots": {},
        }

    # ---- wiring

    def install(self, monkeypatch):
        for module in MODULES:
            prefix = module.__name__
            for name, stub in vars(module).items():
                if not isinstance(stub, types.FunctionType) or name.startswith("_"):
                    continue
                label = f"{prefix}.{name}"
                impl = getattr(self, f"{prefix}_{name}", None)
                fn = _checked(stub, impl, label) if impl else _unfaked(label)
                monkeypatch.setattr(module, name, fn)

    # ---- transport / general / ui / device

    def transport_start(self):
        self.playing = not self.playing

    def transport_stop(self):
        self.playing = False

    def transport_isPlaying(self):
        return self.playing

    def transport_record(self):
        self.recording = not self.recording

    def transport_isRecording(self):
        return self.recording

    def transport_getLoopMode(self):
        return self.loop

    def transport_setLoopMode(self):
        self.loop = 1 - self.loop

    def transport_globalTransport(self, command, value, pmeflags, flags):
        if command == midi.FPT_Metronome:
            self.metro = not self.metro
        elif command == midi.FPT_Save:
            self.saves += 1
        else:
            raise AssertionError(f"unexpected transport command {command}")
        return 0

    def transport_setSongPos(self, position, mode):
        assert mode == midi.SONGLENGTH_ABSTICKS
        self.ticks = position

    def transport_getSongPosHint(self):
        bar, rest = divmod(self.ticks, self.ppq * 4)
        return f"{bar + 1}:{rest // (self.ppq // 4) + 1:02d}:{rest % (self.ppq // 4):02d}"

    def general_getVersion(self):
        return 37

    def general_getUseMetronome(self):
        return self.metro

    def general_getRecPPQ(self):
        return self.ppq

    def general_getRecPPB(self):
        return self.ppq * 4

    def general_processRECEvent(self, eventId, value, flags):
        self.rec_events.append((eventId, value, flags))
        if eventId == midi.REC_Tempo:
            self.bpm = value / 1000
        return 0

    def general_undo(self):
        self.undos += 1
        return 0

    def ui_getVersion(self, mode):
        return "Producer Edition v25.1.6 [build 4000]"

    def ui_getProgTitle(self):
        return "My Song - FL Studio"

    def ui_showWindow(self, index):
        self.windows.append(index)

    def device_isAssigned(self):
        return self.assigned

    def device_getPortNumber(self):
        return 3

    def device_midiOutMsg(self, message, channel, data1, data2):
        if channel != -1:
            message = (0xB0 | channel) | (data1 << 8) | (data2 << 16)
        if self.out_hook:
            self.out_hook(message)

    # ---- mixer

    def mixer_getCurrentTempo(self, asInt):
        return self.bpm

    def mixer_trackCount(self):
        return len(self.tracks)

    def mixer_trackNumber(self):
        return self.cur_track

    def mixer_setTrackNumber(self, trackNumber, flags):
        self.cur_track = trackNumber

    def mixer_getTrackName(self, index):
        return self.tracks[index]["name"]

    def mixer_setTrackName(self, index, name):
        self.tracks[index]["name"] = name

    def mixer_setTrackColor(self, index, color):
        self.tracks[index]["color"] = color

    def mixer_getTrackVolume(self, index, mode):
        return self.tracks[index]["vol"]

    def mixer_setTrackVolume(self, index, volume, pickupMode):
        self.tracks[index]["vol"] = volume

    def mixer_getTrackPan(self, index):
        return self.tracks[index]["pan"]

    def mixer_setTrackPan(self, index, pan, pickupMode):
        self.tracks[index]["pan"] = pan

    def mixer_isTrackMuted(self, index):
        return bool(self.tracks[index]["mute"])

    def mixer_muteTrack(self, index, value):
        t = self.tracks[index]
        t["mute"] = 1 - t["mute"] if value == -1 else value

    def mixer_isTrackSolo(self, index):
        return bool(self.tracks[index]["solo"])

    def mixer_soloTrack(self, index, value, mode):
        t = self.tracks[index]
        t["solo"] = 1 - t["solo"] if value == -1 else value

    def mixer_isTrackPluginValid(self, index, plugIndex):
        return plugIndex in self.tracks[index]["slots"]

    def mixer_getRouteSendActive(self, index, destIndex):
        return destIndex in self.tracks[index]["routes"]

    def mixer_setRouteTo(self, index, destIndex, value, updateUI):
        routes = self.tracks[index]["routes"]
        (routes.add if value else routes.discard)(destIndex)

    def mixer_setRouteToLevel(self, index, destIndex, level):
        assert destIndex in self.tracks[index]["routes"], "route must exist before its level"
        self.tracks[index]["levels"][destIndex] = level

    # ---- channels

    def channels_channelCount(self, globalCount):
        assert globalCount, "script must use global channel indices"
        return len(self.chans)

    def _ch(self, index, glob):
        assert glob, "script must use global channel indices"
        return self.chans[index]

    def channels_getChannelName(self, index, useGlobalIndex):
        return self._ch(index, useGlobalIndex)["name"]

    def channels_setChannelName(self, index, name, useGlobalIndex):
        self._ch(index, useGlobalIndex)["name"] = name

    def channels_getChannelType(self, index, useGlobalIndex):
        return self._ch(index, useGlobalIndex)["type"]

    def channels_getChannelVolume(self, index, mode, useGlobalIndex):
        return self._ch(index, useGlobalIndex)["vol"]

    def channels_setChannelVolume(self, index, volume, pickupMode, useGlobalIndex):
        self._ch(index, useGlobalIndex)["vol"] = volume

    def channels_getChannelPan(self, index, useGlobalIndex):
        return self._ch(index, useGlobalIndex)["pan"]

    def channels_setChannelPan(self, index, pan, pickupMode, useGlobalIndex):
        self._ch(index, useGlobalIndex)["pan"] = pan

    def channels_setChannelColor(self, index, color, useGlobalIndex):
        self._ch(index, useGlobalIndex)["color"] = color

    def channels_isChannelMuted(self, index, useGlobalIndex):
        return bool(self._ch(index, useGlobalIndex)["mute"])

    def channels_muteChannel(self, index, value, useGlobalIndex):
        c = self._ch(index, useGlobalIndex)
        c["mute"] = 1 - c["mute"] if value == -1 else value

    def channels_isChannelSolo(self, index, useGlobalIndex):
        return bool(self._ch(index, useGlobalIndex)["solo"])

    def channels_soloChannel(self, index, useGlobalIndex):
        c = self._ch(index, useGlobalIndex)
        c["solo"] = 1 - c["solo"]

    def channels_getTargetFxTrack(self, index, useGlobalIndex):
        return self._ch(index, useGlobalIndex)["fx"]

    def channels_setTargetFxTrack(self, channelIndex, mixerIndex, useGlobalIndex):
        self._ch(channelIndex, useGlobalIndex)["fx"] = mixerIndex

    def channels_selectOneChannel(self, index, useGlobalIndex):
        self._ch(index, useGlobalIndex)
        self.sel_channel = index

    def channels_selectedChannel(self, canBeNone, offset, indexGlobal):
        assert indexGlobal
        return self.sel_channel

    def channels_getGridBit(self, index, position, useGlobalIndex):
        return position in self._ch(index, useGlobalIndex)["steps"].setdefault(self.pat, set())

    def channels_setGridBit(self, index, position, value, useGlobalIndex):
        steps = self._ch(index, useGlobalIndex)["steps"].setdefault(self.pat, set())
        (steps.add if value else steps.discard)(position)
        self.pats.setdefault(self.pat, self._pattern(self.pat))

    def channels_setStepParameterByIndex(self, index, patNum, step, param, value, useGlobalIndex):
        self._ch(index, useGlobalIndex)["sp"][(patNum, step, param)] = value

    # ---- patterns / playlist

    @staticmethod
    def _pattern(i):
        return {"name": f"Pattern {i}", "color": 0, "len": 4}

    def patterns_patternNumber(self):
        return self.pat

    def patterns_patternCount(self):
        return len(self.pats)

    def patterns_patternMax(self):
        return 999

    def patterns_isPatternDefault(self, index):
        return index not in self.pats

    def patterns_getPatternName(self, index):
        return self.pats.get(index, self._pattern(index))["name"]

    def patterns_setPatternName(self, index, name):
        self.pats.setdefault(index, self._pattern(index))["name"] = name

    def patterns_setPatternColor(self, index, color):
        self.pats.setdefault(index, self._pattern(index))["color"] = color

    def patterns_getPatternLength(self, index):
        return self.pats.get(index, self._pattern(index))["len"]

    def patterns_jumpToPattern(self, index):
        self.pats.setdefault(index, self._pattern(index))
        self.pat = index

    def playlist_trackCount(self):
        return len(self.tlist)

    def playlist_getTrackName(self, index):
        return self.tlist[index]["name"]

    def playlist_setTrackName(self, index, name):
        self.tlist[index]["name"] = name

    def playlist_setTrackColor(self, index, color):
        self.tlist[index]["color"] = color

    def playlist_isTrackMuted(self, index):
        return bool(self.tlist[index]["mute"])

    def playlist_muteTrack(self, index, value):
        t = self.tlist[index]
        t["mute"] = 1 - t["mute"] if value == -1 else value

    def playlist_isTrackSolo(self, index):
        return bool(self.tlist[index]["solo"])

    def playlist_soloTrack(self, index, value, inGroup):
        t = self.tlist[index]
        t["solo"] = 1 - t["solo"] if value == -1 else value

    def playlist_selectTrack(self, index):
        self.tlist[index]["selected"] = True

    # ---- plugins

    def _plug(self, index, slotIndex, useGlobalIndex):
        key = ("c", index, -1) if useGlobalIndex else ("m", index, slotIndex)
        return key, self.params.get(key)

    def plugins_isValid(self, index, slotIndex, useGlobalIndex):
        return self._plug(index, slotIndex, useGlobalIndex)[1] is not None

    def plugins_getPluginName(self, index, slotIndex, userName, useGlobalIndex):
        if useGlobalIndex:
            return self.chans[index]["plugin"]
        return self.tracks[index]["slots"][slotIndex]

    def plugins_getParamCount(self, index, slotIndex, useGlobalIndex):
        return len(self._plug(index, slotIndex, useGlobalIndex)[1])

    def plugins_getParamName(self, paramIndex, index, slotIndex, useGlobalIndex):
        return self._plug(index, slotIndex, useGlobalIndex)[1][paramIndex][0]

    def plugins_getParamValue(self, paramIndex, index, slotIndex, useGlobalIndex):
        return self._plug(index, slotIndex, useGlobalIndex)[1][paramIndex][1]

    def plugins_setParamValue(
        self, value, paramIndex, index, slotIndex, pickupMode, useGlobalIndex
    ):
        self._plug(index, slotIndex, useGlobalIndex)[1][paramIndex][1] = value

    def plugins_getParamValueString(self, paramIndex, index, slotIndex, pickupMode, useGlobalIndex):
        return f"{self.plugins_getParamValue(paramIndex, index, slotIndex, useGlobalIndex):.2f}"


def load_script():
    spec = importlib.util.spec_from_file_location("device_FLMCP_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(cc, value, channel=REQ_CH):
    return SimpleNamespace(status=0xB0 | channel, data1=cc, data2=value, handled=False)


class LoopbackBridge(Bridge):
    """Real Bridge logic, with the MIDI hop replaced by direct calls into the FL script."""

    def __init__(self, script, fake, timeout=0.3):
        super().__init__(port="loop", timeout=timeout)
        self.script, self.sent, self.drop_next = script, 0, 0
        self.tamper = None
        fake.out_hook = self._from_fl

    def _open(self):
        self._out = object()

    def close(self):
        self._out = None

    def _send(self, pairs):
        self.sent += 1
        if self.drop_next:
            self.drop_next -= 1
            return
        for cc, value in pairs:
            self.script.OnMidiMsg(event(cc, value))

    def _from_fl(self, packed):
        status, cc, value = packed & 0xFF, (packed >> 8) & 0xFF, (packed >> 16) & 0xFF
        if status == 0xB0 | RES_CH:
            if self.tamper:
                cc, value = self.tamper(cc, value)
            self._on_cc(cc, value)
