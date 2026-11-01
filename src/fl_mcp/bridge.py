"""JSON-over-MIDI bridge to the FL MCP controller script running inside FL Studio.

Messages are framed as 7-bit MIDI CC values (no SysEx, so no buffer limits):
BEGIN(id) DATA(byte)... SUM_A SUM_B, with a Fletcher-style checksum. Requests use MIDI
channel 15, responses channel 16, so a loopback port can be shared by both directions.
"""

import atexit
import json
import logging
import os
import queue
import random
import re
import sys
import threading
import time
from typing import Any

log = logging.getLogger("fl_mcp")

PROTO = 1
SCRIPT_VERSION = 2  # FL script revision this server needs (new ops)
REQ_CH, RES_CH = 14, 15
BEGIN, DATA, SUM_A, SUM_B = 102, 103, 104, 105
MAX_REQ = 16384
DEFAULT_PORT = "FL MCP"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        log.warning("ignoring invalid %s", name)
        return default


class FLError(Exception):
    """Expected failure; the message is meant for the model and the user."""


def dumps(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":")).replace("\x7f", "\\u007f").encode("ascii")


def sums(data: bytes) -> tuple[int, int]:
    a = b = 0
    for x in data:
        a = (a + x + 1) % 127
        b = (b + a) % 127
    return a, b


def encode(fid: int, payload: bytes) -> list[tuple[int, int]]:
    a, b = sums(payload)
    return [(BEGIN, fid), *((DATA, x) for x in payload), (SUM_A, a), (SUM_B, b)]


class Decoder:
    """Rebuilds frames from (cc, value) pairs; feed() returns (id, payload|None) at frame end."""

    def __init__(self) -> None:
        self.fid, self.buf, self.sa = -1, bytearray(), -1

    def feed(self, cc: int, value: int) -> tuple[int, bytes | None] | None:
        if cc == BEGIN:
            self.fid, self.buf, self.sa = value, bytearray(), -1
        elif self.fid < 0:
            pass
        elif cc == DATA:
            self.buf.append(value)
        elif cc == SUM_A:
            self.sa = value
        elif cc == SUM_B:
            fid, data, sa = self.fid, bytes(self.buf), self.sa
            self.fid = -1
            return fid, (data if (sa, value) == sums(data) else None)
        return None


def match_port(names: list[str], wanted: str) -> str | None:
    """Find a MIDI port: exact, then name + OS-appended index ('FL MCP 1'), then substring."""
    w = wanted.lower()
    for n in names:
        if n.lower() == w:
            return n
    indexed = re.compile(rf"^{re.escape(w)}\s+\d+$")
    for n in names:
        if indexed.match(n.lower()):
            return n
    return next((n for n in names if w in n.lower()), None)


class Bridge:
    """Thread-safe request/response channel to FL Studio. One request in flight at a time."""

    def __init__(self, port: str | None = None, timeout: float | None = None) -> None:
        self.port = port or os.environ.get("FL_MCP_PORT") or DEFAULT_PORT
        self.timeout = timeout or _env_float("FL_MCP_TIMEOUT", 2.5)
        self.info: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._frames: queue.Queue[tuple[int, bytes | None]] = queue.Queue()
        self._dec = Decoder()
        self._fid = random.randint(1, 127)
        self._in: Any = None
        self._out: Any = None
        atexit.register(self.close)

    # -- MIDI I/O (replaced by an in-memory loopback in tests)

    def _open(self) -> None:
        if self._out is not None:
            return
        try:
            import mido

            outs, ins = mido.get_output_names(), mido.get_input_names()
        except Exception as exc:
            raise FLError(f"MIDI backend unavailable: {exc}") from exc
        out_name, in_name = match_port(outs, self.port), match_port(ins, self.port)
        if sys.platform == "darwin":
            out_name = out_name or match_port(outs, "iac")
            in_name = in_name or match_port(ins, "iac")
        try:
            if out_name and in_name:
                self._out = mido.open_output(out_name)
                self._in = mido.open_input(in_name, callback=self._on_msg)
            elif sys.platform == "win32":
                raise FLError(
                    f"MIDI port '{self.port}' not found (inputs: {ins}, outputs: {outs}). "
                    f"Create a loopMIDI port named '{self.port}', then restart FL Studio."
                )
            else:
                log.warning("no '%s' port; creating a virtual one (FL must rescan MIDI)", self.port)
                self._out = mido.open_output(self.port, virtual=True)
                self._in = mido.open_input(self.port, virtual=True, callback=self._on_msg)
        except FLError:
            raise
        except Exception as exc:
            self.close()
            raise FLError(f"cannot open MIDI port '{self.port}': {exc}") from exc

    def _send(self, pairs: list[tuple[int, int]]) -> None:
        import mido

        try:
            for cc, value in pairs:
                self._out.send(
                    mido.Message("control_change", channel=REQ_CH, control=cc, value=value)
                )
        except Exception as exc:
            self.close()
            raise FLError(f"MIDI send failed ({exc}); reconnecting on the next call") from exc

    def close(self) -> None:
        for port in (self._in, self._out):
            try:
                if port is not None:
                    port.close()
            except Exception:
                pass
        self._in = self._out = None

    def _on_msg(self, msg: Any) -> None:
        if msg.type == "control_change" and msg.channel == RES_CH:
            self._on_cc(msg.control, msg.value)

    def _on_cc(self, cc: int, value: int) -> None:
        done = self._dec.feed(cc, value)
        if done:
            self._frames.put(done)

    # -- requests

    def call(self, req: dict[str, Any], timeout: float | None = None) -> Any:
        payload = dumps(req)
        if len(payload) > MAX_REQ:
            raise FLError(f"request too large ({len(payload)} bytes, max {MAX_REQ}); split it")
        with self._lock:
            self._open()
            if self.info is None and req.get("o") != "ping":
                self.info = self._roundtrip(dumps({"o": "ping"}), timeout)
                if self.info.get("proto") != PROTO or self.info.get("ver", 0) < SCRIPT_VERSION:
                    self.info = None
                    raise FLError(
                        "FL MCP script in FL Studio is outdated: run `fl-mcp install`, "
                        "then restart FL Studio."
                    )
            return self._roundtrip(payload, timeout)

    def _roundtrip(self, payload: bytes, timeout: float | None) -> Any:
        wait = timeout or self.timeout
        self._fid = self._fid % 127 + 1
        fid = self._fid
        for _ in range(2):
            while not self._frames.empty():
                self._frames.get_nowait()
            self._send(encode(fid, payload))
            end = time.monotonic() + wait
            while (left := end - time.monotonic()) > 0:
                try:
                    got, data = self._frames.get(timeout=left)
                except queue.Empty:
                    break
                if got != fid:
                    continue
                if data is None:
                    break
                res = json.loads(data)
                if res.get("ok"):
                    return res.get("r")
                if res.get("e") == "bad frame":
                    break
                raise FLError(str(res.get("e") or "unknown FL Studio error"))
        raise FLError(
            f"No answer from FL Studio on MIDI port '{self.port}'. Check: FL Studio is running "
            "and not blocked by a dialog; Options > MIDI settings has the port enabled as input "
            "with controller type 'FL MCP' and as output with the same port number; the script is "
            "installed (`fl-mcp install`). Run `fl-mcp doctor` for details."
        )
