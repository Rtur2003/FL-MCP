import sys
import threading
from types import SimpleNamespace

import pytest

from fl_mcp import bridge as b
from fl_mcp.bridge import Bridge, FLError


def test_lost_request_is_retried_with_the_same_id(fl):
    fl.call({"o": "status"})  # also performs the one-time handshake
    sent, fl.bridge.drop_next = fl.bridge.sent, 1
    assert fl.call({"o": "status"})["bpm"] == 140.0
    assert fl.bridge.sent - sent == 2


def test_silence_ends_in_one_actionable_error(fl):
    fl.bridge.drop_next = 99
    with pytest.raises(FLError) as info:
        fl.call({"o": "status"})
    message = str(info.value)
    assert fl.bridge.sent == 2 and "'loop'" in message
    for hint in ("controller type 'FL MCP'", "same port number", "fl-mcp install", "fl-mcp doctor"):
        assert hint in message


def test_corrupted_reply_is_retried_and_not_executed_twice(fl):
    seen = {"n": 0}

    def tamper(cc, value):
        seen["n"] += 1
        return (cc, (value + 1) % 128) if seen["n"] == 5 else (cc, value)

    fl.call({"o": "status"})
    fl.bridge.tamper = tamper
    fl.call({"o": "tr", "a": "undo"})
    assert fl.fake.undos == 1 and seen["n"] > 5


def test_request_corrupted_in_flight_is_retried(fl):
    fl.call({"o": "status"})
    fl.bridge.sent = 0
    original, calls = fl.bridge._send, []

    def bad_first(pairs):
        calls.append(1)
        if len(calls) == 1:
            pairs = [*pairs[:-1], (pairs[-1][0], (pairs[-1][1] + 1) % 127)]
        original(pairs)

    fl.bridge._send = bad_first
    assert fl.call({"o": "status"})["bpm"] == 140.0 and len(calls) == 2


def test_replies_for_other_requests_are_ignored(fl):
    fl.call({"o": "status"})
    original = fl.bridge._send

    def noisy(pairs):
        fl.bridge._frames.put((fl.bridge._fid % 127 + 1, b'{"ok":1,"r":"stale"}'))
        original(pairs)

    fl.bridge._send = noisy
    assert fl.call({"o": "status"})["bpm"] == 140.0


def test_outdated_script_is_detected(fl):
    fl.script.PROTO = 99
    with pytest.raises(FLError, match="outdated.*fl-mcp install"):
        fl.call({"o": "status"})
    assert fl.bridge.info is None


def test_old_script_revision_is_detected(fl):
    fl.script.VERSION = 1
    with pytest.raises(FLError, match="outdated"):
        fl.call({"o": "status"})


def test_fl_side_errors_become_flerrors(fl):
    with pytest.raises(FLError, match="channel 9 out of range"):
        fl.call({"o": "steps", "c": 9})


def test_oversized_request_is_rejected_locally(fl):
    with pytest.raises(FLError, match="request too large"):
        fl.call({"o": "call", "c": [["mixer.getTrackName", 1]] * 1000})
    assert fl.bridge.sent == 0


def test_concurrent_callers_are_serialised(fl):
    results, errors = {}, []

    def worker(i):
        try:
            for _ in range(5):
                results[i] = fl.call({"o": "tr", "a": "tempo", "v": 100 + i})["bpm"]
                assert results[i] == 100 + i
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and results == {i: 100 + i for i in range(6)}


# ---- real MIDI port handling, with mido replaced by a recording fake


class Port:
    def __init__(self, name, virtual, callback=None):
        self.name, self.virtual, self.callback = name, virtual, callback
        self.sent, self.closed, self.fail = [], False, False

    def send(self, msg):
        if self.fail:
            raise OSError("port vanished")
        self.sent.append(msg)

    def close(self):
        self.closed = True
        if self.fail:
            raise OSError("already gone")


class FakeMido:
    def __init__(self, outs=(), ins=()):
        self.outs, self.ins, self.opened = list(outs), list(ins), []
        self.fail_input = False

    def get_output_names(self):
        return self.outs

    def get_input_names(self):
        return self.ins

    def open_output(self, name, virtual=False):
        self.opened.append(("out", name, virtual))
        return Port(name, virtual)

    def open_input(self, name, callback=None, virtual=False):
        if self.fail_input:
            raise OSError("busy")
        self.opened.append(("in", name, virtual))
        return Port(name, virtual, callback)

    @staticmethod
    def Message(kind, **kwargs):  # noqa: N802 - mirrors mido's API
        return SimpleNamespace(type=kind, **kwargs)


@pytest.fixture
def mido(monkeypatch):
    fake = FakeMido(["Synth", "FL MCP 1"], ["FL MCP 1"])
    monkeypatch.setitem(sys.modules, "mido", fake)
    return fake


def test_opens_the_matching_ports(mido):
    bridge = Bridge(port="FL MCP")
    bridge._open()
    assert mido.opened == [("out", "FL MCP 1", False), ("in", "FL MCP 1", False)]
    assert bridge._in.callback == bridge._on_msg
    bridge._open()
    assert len(mido.opened) == 2  # idempotent


def test_windows_cannot_create_ports_and_says_how_to_fix_it(mido, monkeypatch):
    mido.outs, mido.ins = ["Synth"], ["Keys"]
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(FLError) as info:
        Bridge(port="FL MCP")._open()
    assert "loopMIDI" in str(info.value) and "'Keys'" in str(info.value) and not mido.opened


def test_virtual_ports_are_created_where_supported(mido, monkeypatch):
    mido.outs, mido.ins = [], []
    monkeypatch.setattr(sys, "platform", "linux")
    Bridge(port="FL MCP")._open()
    assert mido.opened == [("out", "FL MCP", True), ("in", "FL MCP", True)]


def test_macos_falls_back_to_the_iac_bus(mido, monkeypatch):
    mido.outs, mido.ins = ["IAC Driver Bus 1"], ["IAC Driver Bus 1"]
    monkeypatch.setattr(sys, "platform", "darwin")
    Bridge(port="FL MCP")._open()
    assert mido.opened == [("out", "IAC Driver Bus 1", False), ("in", "IAC Driver Bus 1", False)]


def test_open_failure_cleans_up_and_explains(mido):
    mido.fail_input = True
    bridge = Bridge(port="FL MCP")
    with pytest.raises(FLError, match="cannot open MIDI port 'FL MCP': busy"):
        bridge._open()
    assert bridge._out is None and bridge._in is None


def test_backend_failure_is_reported(monkeypatch):
    class Broken(FakeMido):
        def get_output_names(self):
            raise RuntimeError("no ALSA")

    monkeypatch.setitem(sys.modules, "mido", Broken())
    with pytest.raises(FLError, match="MIDI backend unavailable: no ALSA"):
        Bridge()._open()


def test_frames_leave_as_control_changes_on_the_request_channel(mido):
    bridge = Bridge(port="FL MCP")
    bridge._open()
    bridge._send(b.encode(9, b"hi"))
    sent = [(m.type, m.channel, m.control, m.value) for m in bridge._out.sent]
    assert sent[0] == ("control_change", b.REQ_CH, b.BEGIN, 9)
    assert sent[1:3] == [
        ("control_change", b.REQ_CH, b.DATA, ord("h")),
        ("control_change", b.REQ_CH, b.DATA, ord("i")),
    ]
    assert [s[2] for s in sent[-2:]] == [b.SUM_A, b.SUM_B]


def test_send_failure_drops_the_connection_for_a_clean_reconnect(mido):
    bridge = Bridge(port="FL MCP")
    bridge._open()
    bridge._out.fail = True
    with pytest.raises(FLError, match="MIDI send failed.*reconnecting"):
        bridge._send(b.encode(1, b"x"))
    assert bridge._out is None  # next call reopens


def test_incoming_messages_are_filtered_and_decoded(mido):
    bridge = Bridge(port="FL MCP")
    echo = SimpleNamespace(type="control_change", channel=b.REQ_CH, control=b.BEGIN, value=4)
    note = SimpleNamespace(type="note_on", channel=b.RES_CH, note=60, velocity=1)
    for msg in (echo, note):
        bridge._on_msg(msg)
    for cc, value in b.encode(4, b'{"ok":1}'):
        bridge._on_msg(
            SimpleNamespace(type="control_change", channel=b.RES_CH, control=cc, value=value)
        )
    assert bridge._frames.get_nowait() == (4, b'{"ok":1}') and bridge._frames.empty()


def test_close_never_raises(mido):
    bridge = Bridge(port="FL MCP")
    bridge._open()
    bridge._in.fail = bridge._out.fail = True
    bridge.close()
    bridge.close()


def test_environment_configuration(monkeypatch):
    monkeypatch.setenv("FL_MCP_PORT", "My Port")
    monkeypatch.setenv("FL_MCP_TIMEOUT", "7")
    bridge = Bridge()
    assert (bridge.port, bridge.timeout) == ("My Port", 7.0)
    assert Bridge(port="x", timeout=1).port == "x"


def test_invalid_timeout_does_not_crash_startup(monkeypatch):
    monkeypatch.setenv("FL_MCP_TIMEOUT", "soon")
    assert Bridge().timeout == 2.5
