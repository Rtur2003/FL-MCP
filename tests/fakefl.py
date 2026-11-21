"""Stateful fake of the FL Studio scripting API, layered on the official stubs.

Each fake keeps the real stub's signature, so a wrong call in the controller script fails here
the way it would inside FL Studio. Any API function the script uses without a fake raises.
"""

import functools
import importlib.util
import inspect
import math
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


# fl-studio-api-stubs mistakenly copied pickupMode onto getParamValueString;
# real FL Studio (verified on FL 2025 API 40) takes at most 4 arguments:
# (paramIndex, index, slotIndex=-1, useGlobalIndex=False).
plugins.getParamValueString.__signature__ = inspect.signature(plugins.getParamValue)

# fl-studio-api-stubs v37.0.1 omits the middle `value: int = -1` arg on soloChannel;
# real FL Studio (verified on API v30+ through v45) is (index, value=-1, useGlobalIndex=False).
channels.soloChannel.__signature__ = inspect.Signature(
    [
        inspect.Parameter("index", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int),
        inspect.Parameter(
            "value", inspect.Parameter.POSITIONAL_OR_KEYWORD, default=-1, annotation=int
        ),
        inspect.Parameter(
            "useGlobalIndex",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=False,
            annotation=bool,
        ),
    ],
    return_annotation=None,
)


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
        self.peaks, self.markers, self.auditioned, self.undo_points = {}, [], [], []
        self.eq = {}
        self.step_limit = 1024
        self.params = {
            ("c", 2, -1): [["Cutoff", 0.5], ["Resonance", 0.2], ["", 0.0], ["Master level", 0.8]],
            ("m", 10, 0): [["Wet", 0.3], ["Decay", 0.6]],
        }
        self.keys, self.visible, self.focused = [], {0, 1}, 1
        self.changed = 0
        self.browser = [
            ["Plugin database", -100],
            ["Generators", -100],
            ["3x Osc", 5],
            ["Sytrus", 5],
            ["Effects", -100],
            ["Fruity Limiter", 5],
            ["Kick 01.wav", 1],
            ["Snare 01.wav", 1],
        ]
        self.browser_at, self.browser_log, self.loads = 0, [], []
        self.in_popup, self.popup_mode, self.pending = False, False, 0
        self.presets = {("c", 2, -1): ["Init", "Dystopian lead", "Soft pad", "Sub bass"]}
        self.preset_at = {("c", 2, -1): 0}
        self.slot_ctrl = {}
        self.out_ctrls = {}
        self.out_voices = {}
        self.semitones = {}
        self.focused_editor = None
        self.forwarded_midi = []
        self.linked_params = {}
        self.last_tweaked_eid = midi.REC_Chan_Mute
        self.selection_range = None
        self.clipboard_range = None
        self.pastes = []

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
            "sep": 0.0,
            "arm": False,
            "polarity": False,
            "swap": False,
            "routes": set(routes or ()),
            "levels": {},
            "slots": {},
            "slots_enabled": True,
        }

    def add_master_limiter(self, gain=0.5, ceiling=0.9, name="Fruity Limiter"):
        """A limiter in master slot 9 whose Gain and Ceiling show dB (-60..0)."""
        self.tracks[0]["slots"][9] = name
        self.params[("m", 0, 9)] = [
            ["Gain", gain, "db"],
            ["Ceiling", ceiling, "db"],
            ["Attack", 0.2, "ms"],
        ]

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
        elif command == midi.FPT_SaveNew:
            self.saves += 10
        elif command in (
            midi.FPT_Cut,
            midi.FPT_Copy,
            midi.FPT_Paste,
            midi.FPT_Insert,
            midi.FPT_Delete,
        ):
            self.keys.append(command)
            if command == midi.FPT_Copy and self.selection_range is not None:
                self.clipboard_range = self.selection_range
            elif command == midi.FPT_Cut and self.selection_range is not None:
                self.clipboard_range = self.selection_range
            elif command == midi.FPT_Paste and self.clipboard_range is not None:
                self.pastes.append((self.ticks, self.clipboard_range))
        elif (
            command in (midi.FPT_Menu, midi.FPT_ItemMenu) or midi.FPT_F1 <= command <= midi.FPT_F12
        ):
            self.keys.append(command)
        else:
            raise AssertionError(f"unexpected transport command {command}")
        return 0

    def transport_setSongPos(self, position, mode):
        assert mode == midi.SONGLENGTH_ABSTICKS
        self.ticks = position

    def transport_getSongPosHint(self):
        bar, rest = divmod(self.ticks, self.ppq * 4)
        return f"{bar + 1}:{rest // (self.ppq // 4) + 1:02d}:{rest % (self.ppq // 4):02d}"

    def transport_getSongLength(self, mode):
        if mode == midi.SONGLENGTH_BARS:
            return 16
        if mode == midi.SONGLENGTH_MS:
            return 32000
        return 16 * self.ppq * 4

    def general_getVersion(self):
        return 37

    def general_getUseMetronome(self):
        return self.metro

    def general_getRecPPQ(self):
        return self.ppq

    def general_getRecPPB(self):
        return self.ppq * 4

    @staticmethod
    def _rec_plug_slot(eventId):
        off = eventId - 536878848  # REC_Plug_Mute (536878848), REC_Plug_MixLevel (536878849)
        prop = off & 0xFFFF
        if off >= 0 and prop in (0, 1):
            slot_base = off >> 16
            return slot_base >> 6, slot_base & 0x3F, prop
        return None

    def general_processRECEvent(self, eventId, value, flags):
        self.rec_events.append((eventId, value, flags))
        if eventId == midi.REC_Tempo:
            self.bpm = value / 1000
            return 0
        slot_ref = self._rec_plug_slot(eventId)
        if slot_ref is not None:
            trk, slt, prop = slot_ref
            st = self.slot_ctrl.setdefault((trk, slt), {"muted": False, "mix": 1.0, "auto": True})
            if flags == midi.REC_GetValue:
                if prop == 0:
                    return 0 if st["muted"] else 1
                return int(round(st["mix"] * midi.FromMIDI_Max))
            if prop == 0:
                val = (
                    (value >= midi.FromMIDI_Max / 2) if (flags & midi.REC_FromMIDI) else bool(value)
                )
                st["muted"] = not val
            else:
                v = (
                    (value / float(midi.FromMIDI_Max))
                    if (flags & midi.REC_FromMIDI)
                    else float(value)
                )
                st["mix"] = max(0.0, min(1.0, v))
            return 0
        return 0

    def general_undo(self):
        self.undos += 1
        if self.loads:  # undo takes back the last thing the browser loaded
            where = self.loads.pop()
            if where[0] == "m":
                del self.tracks[where[1]]["slots"][where[2]]
            else:
                self.chans.pop(where[1])
        return 0

    def ui_getVersion(self, mode):
        return "Producer Edition v25.1.6 [build 4000]"

    def ui_getProgTitle(self):
        return "My Song - FL Studio"

    def ui_showWindow(self, index):
        self.windows.append(index)
        self.visible.add(index)

    def ui_hideWindow(self, index):
        self.visible.discard(index)

    def ui_getVisible(self, index):
        return index in self.visible

    def ui_getFocused(self, index):
        return index == self.focused

    def ui_setFocused(self, index):
        self.focused = index

    def _key(self, name):
        self.keys.append(name)
        return 0

    def ui_up(self, value):
        return self._key("up")

    def ui_down(self, value):
        return self._key("down")

    def ui_left(self, value):
        return self._key("left")

    def ui_right(self, value):
        return self._key("right")

    def ui_enter(self):
        return self._key("enter")

    def ui_escape(self):
        return self._key("escape")

    def ui_yes(self):
        return self._key("yes")

    def ui_no(self):
        return self._key("no")

    def ui_cut(self):
        return self._key("cut")

    def ui_copy(self):
        return self._key("copy")

    def ui_paste(self):
        return self._key("paste")

    def ui_insert(self):
        return self._key("insert")

    def ui_delete(self):
        return self._key("delete")

    def ui_next(self):
        return self._key("next")

    def ui_previous(self):
        return self._key("previous")

    def ui_selectWindow(self, shift):
        return self._key("shift_tab" if shift else "tab")

    def ui_nextWindow(self):
        return self._key("nextwindow")

    def ui_getHintMsg(self):
        return "Hint: " + self.browser[self.browser_at][0]

    def ui_getFocusedFormCaption(self):
        return "Fruity Limiter"

    def ui_getFocusedPluginName(self):
        return "Fruity Limiter"

    def ui_navigateBrowser(self, direction, shiftHeld):
        step = 1 if direction == 41 else -1
        self.browser_at = max(0, min(len(self.browser) - 1, self.browser_at + step))
        self.browser_log.append(("nav", direction, shiftHeld))
        return self.browser[self.browser_at][0]

    def ui_navigateBrowserTabs(self, direction):
        self.browser_log.append(("tab", direction))
        return "Tab"

    def ui_toggleBrowserNode(self, value):
        self.browser_log.append(("toggle", value))

    def ui_selectBrowserMenuItem(self):
        name, kind = self.browser[self.browser_at]
        self.browser_log.append(("select", name))
        if kind != 5:
            return
        if self.popup_mode:  # the real thing: a menu opens and waits for its first entry
            self.in_popup, self.pending = True, self.browser_at
            return
        self._load_item(self.browser_at)

    def confirm_menu(self):
        """What pressing Return in the open menu does: its first entry loads the item."""
        if not self.in_popup:
            return False
        self.in_popup = False
        self._load_item(self.pending)
        return True

    def dismiss_menu(self):
        self.in_popup = False
        return True

    def _load_item(self, at):
        name = self.browser[at][0]
        above = [n for n, k in self.browser[:at] if k == -100]
        if above[-1:] == ["Effects"]:  # an effect goes to the first free slot of the selected track
            slots = self.tracks[self.cur_track]["slots"]
            free = next((i for i in range(10) if i not in slots), None)
            if free is not None:
                slots[free] = name
                self.loads.append(("m", self.cur_track, free))
        elif above[-1:] == ["Generators"]:  # an instrument becomes a new channel
            self.chans.append(self._chan(name, 2, name))
            self.loads.append(("c", len(self.chans) - 1))

    def ui_previewBrowserMenuItem(self):
        self.browser_log.append(("preview", self.browser[self.browser_at][0]))

    def ui_getFocusedNodeCaption(self):
        return self.browser[self.browser_at][0]

    def ui_getFocusedNodeFileType(self):
        return self.browser[self.browser_at][1]

    def ui_isInPopupMenu(self):
        return self.in_popup

    def ui_closeActivePopupMenu(self):
        self.in_popup = False

    def ui_isBrowserAutoHide(self):
        return False

    def ui_setBrowserAutoHide(self, value):
        self.browser_log.append(("autohide", bool(value)))

    def general_getChangedFlag(self):
        return self.changed

    def mixer_getActiveEffectIndex(self):
        return (10, 0)

    def mixer_afterRoutingChanged(self):
        self.routing_commits = getattr(self, "routing_commits", 0) + 1

    def device_isAssigned(self):
        return self.assigned

    def device_getPortNumber(self):
        return 3

    def device_midiOutMsg(self, message, channel, data1, data2):
        if channel != -1:
            message = (0xB0 | channel) | (data1 << 8) | (data2 << 16)
        if self.out_hook:
            self.out_hook(message)

    def device_getLinkedValue(self, eventID):
        slot_ref = self._rec_plug_slot(eventID)
        if slot_ref is None:
            return -1.0
        trk, slt, prop = slot_ref
        st = self.slot_ctrl.get((trk, slt), {"muted": False, "mix": 1.0})
        return (0.0 if st["muted"] else 1.0) if prop == 0 else float(st["mix"])

    def device_getLinkedValueString(self, eventID):
        slot_ref = self._rec_plug_slot(eventID)
        if slot_ref is None:
            return ""
        trk, slt, prop = slot_ref
        st = self.slot_ctrl.get((trk, slt), {"muted": False, "mix": 1.0})
        if prop == 0:
            return "Off" if st["muted"] else "On"
        return f"{round(st['mix'] * 100)}%"

    def device_findEventID(self, controlId, flags=0):
        return self.linked_params.get((controlId, flags), -1)

    def device_linkToLastTweaked(
        self, controlIndex, channel, global_link=False, eventId=2147483647
    ):
        eid = self.last_tweaked_eid if eventId == 2147483647 else eventId
        self.linked_params[(controlIndex, 1 if global_link else 0)] = eid
        return 0

    def device_getLinkedParamName(self, eventID):
        return f"Event {eventID}"

    def device_forwardMIDICC(self, message, mode=1):
        self.forwarded_midi.append((message, mode))

    # ---- mixer

    def mixer_getCurrentTempo(self, asInt):
        return self.bpm if asInt else round(self.bpm * 1000)

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
        v = self.tracks[index]["vol"]
        return (20 * math.log10(v / 0.8) if v > 0 else -120.0) if mode == 1 else v

    def mixer_setTrackVolume(self, index, volume, pickupMode):
        self.tracks[index]["vol"] = volume

    def mixer_getTrackPan(self, index):
        return self.tracks[index]["pan"]

    def mixer_setTrackPan(self, index, pan, pickupMode):
        self.tracks[index]["pan"] = pan

    def mixer_getTrackStereoSep(self, index):
        return self.tracks[index]["sep"]

    def mixer_setTrackStereoSep(self, index, pan, pickupMode=0):
        self.tracks[index]["sep"] = max(-1.0, min(1.0, float(pan)))

    def mixer_isTrackArmed(self, index):
        return bool(self.tracks[index]["arm"])

    def mixer_armTrack(self, index):
        self.tracks[index]["arm"] = not self.tracks[index]["arm"]

    def mixer_isTrackRevPolarity(self, index):
        return bool(self.tracks[index]["polarity"])

    def mixer_revTrackPolarity(self, index, value=False):
        self.tracks[index]["polarity"] = bool(value)

    def mixer_isTrackSwapChannels(self, index):
        return bool(self.tracks[index]["swap"])

    def mixer_swapTrackChannels(self, index, value=False):
        self.tracks[index]["swap"] = bool(value)

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

    def mixer_getRouteToLevel(self, index, destIndex):
        return float(
            self.tracks[index]["levels"].get(
                destIndex, 1.0 if destIndex in self.tracks[index]["routes"] else 0.0
            )
        )

    def mixer_getTrackPluginId(self, index, plugIndex):
        return ((index << 6) + plugIndex) << 16

    def mixer_isTrackSlotsEnabled(self, index):
        return bool(self.tracks[index]["slots_enabled"])

    def mixer_enableTrackSlots(self, index, value):
        self.tracks[index]["slots_enabled"] = bool(value)

    def mixer_isTrackAutomationEnabled(self, index, plugIndex):
        if plugIndex not in self.tracks[index]["slots"]:
            return False
        return bool(self.slot_ctrl.get((index, plugIndex), {}).get("auto", True))

    def mixer_focusEditor(self, index, plugIndex):
        assert plugIndex in self.tracks[index]["slots"], "plugin slot empty"
        self.focused_editor = ("m", index, plugIndex)

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
        v = self._ch(index, useGlobalIndex)["vol"]
        return (20 * math.log10(v / 0.78125) if v > 0 else -120.0) if mode else v

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

    def channels_soloChannel(self, index, value=-1, useGlobalIndex=False):
        c = self._ch(index, useGlobalIndex)
        c["solo"] = 1 - c["solo"] if value == -1 else int(bool(value))

    def channels_getTargetFxTrack(self, index, useGlobalIndex):
        return self._ch(index, useGlobalIndex)["fx"]

    def channels_setTargetFxTrack(self, channelIndex, mixerIndex, useGlobalIndex):
        self._ch(channelIndex, useGlobalIndex)["fx"] = mixerIndex

    def channels_getChannelMidiInPort(self, index, useGlobalIndex):
        return int(self._ch(index, useGlobalIndex).get("midi_in", -2))

    def channels_getActivityLevel(self, index, useGlobalIndex):
        return float(self._ch(index, useGlobalIndex).get("activity", 0.0))

    def channels_focusEditor(self, index, useGlobalIndex):
        self._ch(index, useGlobalIndex)
        self.focused_editor = ("c", index, -1)

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
        if value and position >= self.step_limit:
            return  # FL ignores steps beyond the pattern
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

    def channels_midiNoteOn(self, indexGlobal, note, velocity, channel):
        self.auditioned.append((indexGlobal, note, velocity))

    def arrangement_addAutoTimeMarker(self, time, name):
        self.markers.append((time, name))

    def general_saveUndo(self, undoName, flags, update):
        self.undo_points.append((undoName, flags))

    def mixer_getTrackPeaks(self, index, mode):
        assert mode == 2
        fn = getattr(self, "peak_fn", None)
        return fn(index) if fn else self.peaks.get(index, 0.0)

    def mixer_getEqBandCount(self):
        return 3

    def _eq(self, index, band):
        return self.eq.setdefault((index, band), {"g": 0.5, "f": 0.5, "w": 0.5})

    def mixer_getEqGain(self, index, band, mode):
        v = self._eq(index, band)["g"]
        return (v - 0.5) * 36 if mode == 1 else v

    def mixer_setEqGain(self, index, band, value):
        self._eq(index, band)["g"] = value

    def mixer_getEqFrequency(self, index, band, mode):
        v = self._eq(index, band)["f"]
        return 20 * 1000**v if mode == 1 else v

    def mixer_setEqFrequency(self, index, band, value):
        self._eq(index, band)["f"] = value

    def mixer_getEqBandwidth(self, index, band):
        return self._eq(index, band)["w"]

    def mixer_setEqBandwidth(self, index, band, value):
        self._eq(index, band)["w"] = value

    # ---- plugins

    def _plug(self, index, slotIndex, useGlobalIndex):
        key = ("c", index, -1) if useGlobalIndex else ("m", index, slotIndex)
        return key, self.params.get(key)

    def plugins_isValid(self, index, slotIndex, useGlobalIndex):
        return self._plug(index, slotIndex, useGlobalIndex)[1] is not None

    def plugins_getPluginName(self, index, slotIndex, userName, useGlobalIndex):
        if useGlobalIndex:
            return self.chans[index]["plugin"]
        name = self.tracks[index]["slots"][slotIndex]
        return name.upper() if userName else name

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

    @staticmethod
    def param_text(kind, x):
        """How a plugin shows normalised value x (a few families seen in FL's own plugins)."""
        if kind == "db":
            return f"{-60 + 60 * x:.1f} dB"
        if kind == "hz":
            hz = 20 * (1000**x)
            return f"{hz / 1000:.2f} kHz" if hz >= 1000 else f"{hz:.0f} Hz"
        if kind == "ms":
            return f"{1 + 999 * x:.0f} ms"
        if kind == "pct":
            return f"{100 * x:.0f}%"
        if kind == "ratio":
            return f"{1 + round(x * 19):.1f}:1"
        if kind == "pan":
            side = "L" if x < 0.5 else "R"
            return f"{side} {abs(x - 0.5) * 200:.0f}%"
        if kind == "word":
            return "On" if x >= 0.5 else "Off"
        if kind == "silent":
            return ""
        return f"{x:.2f}"

    def plugins_getParamValueString(self, paramIndex, index, slotIndex, useGlobalIndex):
        entry = self._plug(index, slotIndex, useGlobalIndex)[1][paramIndex]
        return self.param_text(entry[2] if len(entry) > 2 else "", entry[1])

    def plugins_getPresetCount(self, index, slotIndex, useGlobalIndex):
        return len(self.presets.get(self._plug(index, slotIndex, useGlobalIndex)[0], ()))

    def plugins_getName(self, index, slotIndex, flag, paramIndex, useGlobalIndex):
        key = self._plug(index, slotIndex, useGlobalIndex)[0]
        if flag == midi.FPN_Preset:
            names = self.presets.get(key, [])
            if not names:
                return ""
            k = self.preset_at[key] if paramIndex == midi.GPN_GetCurrentPreset else paramIndex
            return names[k] if 0 <= k < len(names) else ""
        if flag == midi.FPN_Param:
            return self.plugins_getParamName(paramIndex, index, slotIndex, useGlobalIndex)
        if flag == midi.FPN_ParamValue:
            return self.plugins_getParamValueString(paramIndex, index, slotIndex, useGlobalIndex)
        if flag == midi.FPN_OutCtrl:
            items = self.out_ctrls.get(key, ())
            return items[paramIndex] if 0 <= paramIndex < len(items) else ""
        if flag == midi.FPN_OutVoice:
            items = self.out_voices.get(key, ())
            return items[paramIndex] if 0 <= paramIndex < len(items) else ""
        if flag == midi.FPN_Semitone:
            return self.semitones.get(key, {}).get(paramIndex, "")
        return ""

    def plugins_nextPreset(self, index, slotIndex, useGlobalIndex):
        key = self._plug(index, slotIndex, useGlobalIndex)[0]
        self.preset_at[key] = (self.preset_at[key] + 1) % len(self.presets[key])

    def plugins_prevPreset(self, index, slotIndex, useGlobalIndex):
        key = self._plug(index, slotIndex, useGlobalIndex)[0]
        self.preset_at[key] = (self.preset_at[key] - 1) % len(self.presets[key])


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
