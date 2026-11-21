"""Lossless FL Studio project (.flp) reader plus a small patcher for generated songs.

A project is a header and a stream of events (id byte, then 1/2/4 bytes of data for ids below
64/128/192, or a varint length and bytes above). Every event the patcher does not touch is
written back byte for byte. Only tempo, pattern notes/names and the playlist are modified, and
only by cloning structure that FL Studio itself wrote into the template. Format facts come from
the public reverse-engineering work (see NOTICE); no code from it is included.
"""

import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fl_mcp.music.plan import Plan

PPQS = (24, 48, 72, 96, 120, 144, 168, 192, 384, 768, 960)
TEMPO, VERSION = 156, 199
NEW_CHAN, CHAN_TYPE, CHAN_NAME, PLUGIN_NAME, SAMPLE_PATH = 64, 21, 203, 201, 196
INSERT_NAME = 204
PLUGIN_FLAGS, PLUGIN_PARAMS = 212, 213
CHAN_ROUTE, CHAN_ROUTE_16, CHAN_COLOR, CHAN_LEVELS = 22, 104, 128, 219  # insert, RGB, pan/vol
NEW_PAT, PAT_NAME, PAT_COLOR, AUTOMATION_DATA, NOTES, INIT_EVENTS = 65, 193, 150, 223, 224, 225
NEW_ARR, PLAYLIST = 99, 233
TIME_MARKER_POS, TIME_MARKER_NAME, TIME_MARKER_NUM, TIME_MARKER_DEN = 148, 205, 33, 34
REC_TEMPO = 0x40000005
REEVERB2_FIELDS = {
    "highcut": 2,
    "lowcut": 3,
    "size": 4,
    "diffusion": 5,
    "decay": 6,
    "predelay": 7,
    "wet": 8,
    "mod": 12,
}
FSC_FORMAT = 16
FST_FORMAT = 48
_FL24_PL_TAIL = bytes.fromhex("00000000000000000000f03f0000000000000000")
_FL26_PL_TAIL = bytes.fromhex("00000000000000000000f03f00000000ffffffff0000000000000000")
PATTERN_EVENTS = {
    25,
    26,
    33,
    34,
    45,
    46,
    52,
    72,
    91,
    101,
    148,
    150,
    157,
    158,
    160,
    161,
    162,
    164,
    168,
    171,
    193,
    205,
    223,
    224,
    226,
    227,
    230,
    252,
    253,
}
NOTE = struct.Struct("<IHHIHH8B")  # position, flags, channel iid, length, key, group, 8 bytes
PL_BASE = 20480
CHANNEL_KINDS = {0: "sampler", 1: "hybrid", 2: "plugin", 3: "layer", 4: "audio", 5: "automation"}
OLD_ITEM = struct.Struct("<IHHIHH2sH4sff")  # 32 bytes (FL 20 and older)


class FlpError(ValueError):
    """The file is not a project we can safely patch; the message says why."""


@dataclass
class Event:
    id: int
    data: bytes
    raw: bytes | None = None  # the exact bytes read, kept so untouched events round-trip

    def encode(self) -> bytes:
        if self.raw is not None:
            return self.raw
        n = len(self.data)
        if (
            self.id < 64
            and n != 1
            or 64 <= self.id < 128
            and n != 2
            or 128 <= self.id < 192
            and n != (3 if self.id == 172 else 4)
        ):
            raise FlpError(f"event {self.id} has {n} data bytes")
        if self.id < 192:
            return bytes([self.id]) + self.data
        size, out = n, bytearray()
        while True:
            out.append((size & 0x7F) | (0x80 if size >> 7 else 0))
            size >>= 7
            if not size:
                break
        return bytes([self.id]) + bytes(out) + self.data


@dataclass
class Channel:
    iid: int
    name: str
    kind: str


def _text(data: bytes) -> str:
    if not data:
        return ""
    if len(data) >= 2 and len(data) % 2 == 0 and (data[-2:] == b"\x00\x00" or data[1] == 0):
        return data.decode("utf-16le", "replace").rstrip("\x00")
    return data.rstrip(b"\x00").decode("cp1252", "replace")


def _utf16(text: str) -> bytes:
    return text.encode("utf-16le") + b"\x00\x00"


def _encode_varint(n: int) -> bytes:
    out = bytearray()
    size = n
    while True:
        out.append((size & 0x7F) | (0x80 if size >> 7 else 0))
        size >>= 7
        if not size:
            return bytes(out)


def _decode_varint(raw: bytes, pos: int) -> tuple[int, int]:
    n = shift = 0
    while True:
        if pos >= len(raw) or shift > 28:
            raise FlpError(f"truncated varint at byte {pos}")
        b = raw[pos]
        pos += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, pos


def _parse_header_and_events(
    raw: bytes, *, expected_formats: tuple[int, ...] = (0,)
) -> tuple[bytes, int, int, list[Event]]:
    if len(raw) < 22 or raw[:4] != b"FLhd":
        kind = "project" if expected_formats == (0,) else "file"
        raise FlpError(f"not an FL Studio {kind} (missing FLhd header)")
    size, fmt, channels, ppq = struct.unpack("<IHHH", raw[4:14])
    if size != 6:
        raise FlpError("unexpected header size")
    if fmt not in expected_formats:
        raise FlpError("only project files (.flp) are supported, not scores or states")
    if expected_formats == (0,) and ppq not in PPQS:
        raise FlpError(f"unsupported timebase {ppq}")
    if raw[14:18] != b"FLdt":
        raise FlpError("missing FLdt data chunk")
    dlen = struct.unpack("<I", raw[18:22])[0]
    if dlen == 0 or dlen != len(raw) - 22:
        raise FlpError("data chunk size does not match the file (truncated or corrupted?)")
    events, pos = [], 22
    while pos < len(raw):
        start, eid = pos, raw[pos]
        pos += 1
        if eid == 172:
            n = 3
        elif eid < 192:
            n = 1 if eid < 64 else 2 if eid < 128 else 4
        else:
            try:
                n, pos = _decode_varint(raw, pos)
            except FlpError as exc:
                raise FlpError(f"truncated event at byte {start}") from exc
        if pos + n > len(raw):
            raise FlpError(f"event at byte {start} runs past the end of the file")
        events.append(Event(eid, raw[pos : pos + n], raw[start : pos + n]))
        pos += n
    return raw[:14], fmt, channels, events


_SF2_HEADER_BYTES = 56
_SF2_RELEASE_OFFSET = 9 * 4
_SF2_PATH_RE = re.compile(rb"(?:[A-Za-z]:\\|/)[^\x00\r\n]{1,500}\.sf2", re.IGNORECASE)


def _find_soundfont_path_span(blob: bytes) -> tuple[int, int, int] | None:
    """Locate (varint_pos, path_start, path_end) inside a SoundFont Player event 213 blob."""
    if len(blob) >= _SF2_HEADER_BYTES + 2:
        try:
            length, path_start = _decode_varint(blob, _SF2_HEADER_BYTES)
            path_end = path_start + length
            if length > 0 and path_end <= len(blob):
                sample = blob[path_start:path_end].decode("utf-8", "replace")
                if ".sf2" in sample.lower() or "\\" in sample or "/" in sample:
                    return _SF2_HEADER_BYTES, path_start, path_end
        except FlpError:
            pass
    # Fallback scan for FL builds where SoundFont Player header size differs from 56 bytes
    for m in _SF2_PATH_RE.finditer(blob):
        path_start, path_end = m.start(), m.end()
        length = path_end - path_start
        for back in (1, 2, 3):
            vpos = path_start - back
            if vpos < 0:
                continue
            try:
                dec_len, dec_start = _decode_varint(blob, vpos)
            except FlpError:
                continue
            if dec_start == path_start and dec_len == length:
                return vpos, path_start, path_end
    return None


def set_soundfont_release(blob: bytes, release_value: int) -> bytes:
    """Override the SoundFont Player release knob (event 213 int32 at byte offset 36)."""
    if len(blob) < _SF2_RELEASE_OFFSET + 4:
        raise FlpError("SoundFont Player plugin state blob is too short for release override")
    out = bytearray(blob)
    struct.pack_into("<i", out, _SF2_RELEASE_OFFSET, int(release_value))
    return bytes(out)


def patch_soundfont_blob(
    blob: bytes, sf2_path: str, *, release: int | None = None
) -> bytes:
    """Replace the `.sf2` path (and optional release override) inside SoundFont Player event 213."""
    span = _find_soundfont_path_span(blob)
    if span is None:
        if len(blob) < _SF2_HEADER_BYTES + 2:
            raise FlpError("SoundFont Player plugin state blob is too short")
        old_len, path_start = _decode_varint(blob, _SF2_HEADER_BYTES)
        path_end = path_start + old_len
        if path_end > len(blob):
            raise FlpError("SoundFont Player plugin state path runs past end of blob")
        vpos = _SF2_HEADER_BYTES
    else:
        vpos, _path_start, path_end = span
    encoded = sf2_path.encode("utf-8")
    patched = blob[:vpos] + _encode_varint(len(encoded)) + encoded + blob[path_end:]
    if release is not None:
        patched = set_soundfont_release(patched, release)
    return patched


def read_soundfont_blob_path(blob: bytes) -> str | None:
    """Extract the `.sf2` path stored inside a SoundFont Player event 213 blob, if readable."""
    span = _find_soundfont_path_span(blob)
    if span is None:
        return None
    _, path_start, path_end = span
    return blob[path_start:path_end].decode("utf-8", "replace")


def read_fst(path: str | Path) -> dict[str, Any]:
    """Read an FL Studio `.fst` plugin preset into structured fields for offline injection."""
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) < 22 or raw[:4] != b"FLhd":
        raise FlpError(f"not an FL Studio preset (.fst): {p.name}")
    fmt = struct.unpack("<H", raw[8:10])[0]
    if fmt != FST_FORMAT:
        raise FlpError(f"expected .fst format {FST_FORMAT}, got {fmt}")
    _, _, _, events = _parse_header_and_events(raw, expected_formats=(FST_FORMAT,))
    by_id: dict[int, bytes] = {}
    for ev in events:
        by_id.setdefault(ev.id, ev.data)
    plugin = _text(by_id[PLUGIN_NAME]) if PLUGIN_NAME in by_id else ""
    if not plugin:
        raise FlpError(f".fst preset {p.name} has no plugin identifier (event {PLUGIN_NAME})")
    name = _text(by_id[CHAN_NAME]) if CHAN_NAME in by_id else p.stem
    params = by_id.get(PLUGIN_PARAMS, b"")
    sf2 = read_soundfont_blob_path(params) if "soundfont" in plugin.lower() else None
    return {
        "file": str(p),
        "plugin": plugin,
        "name": name or plugin,
        "flags": by_id.get(PLUGIN_FLAGS),
        "params": params,
        "color": by_id.get(CHAN_COLOR),
        "sf2": sf2,
    }


def read_fsc(path: str | Path) -> dict[str, Any]:
    """Read an FL `.fsc` Piano Roll score into `[pitch, start_beat, length_beat, vel]` rows."""
    p = Path(path)
    raw = p.read_bytes()
    header, _, _, events = _parse_header_and_events(raw, expected_formats=(FSC_FORMAT,))
    ppq = struct.unpack("<H", header[12:14])[0]
    notes: list[list[Any]] = []
    for ev in events:
        if ev.id != NOTES:
            continue
        for k in range(0, len(ev.data) - (len(ev.data) % NOTE.size), NOTE.size):
            pos, _flags, _ch, length, key, _grp, _fp, _u1, _rel, _mc, _pan, vel, _mx, _my = (
                NOTE.unpack_from(ev.data, k)
            )
            notes.append([key, round(pos / ppq, 4), round(length / ppq, 4), vel])
    return {"file": str(p), "ppq": ppq, "count": len(notes), "notes": notes}


def write_fsc(path: str | Path, notes: list[list[Any]], ppq: int = 96) -> Path:
    """Write notes `[pitch, start_beat, length_beat, vel]` into an FL Studio `.fsc` score file."""
    if ppq not in PPQS:
        raise FlpError(f"unsupported PPQ {ppq}")
    records = [
        note_record(
            round(float(n[1]) * ppq),
            max(1, round(float(n[2]) * ppq)),
            int(n[0]),
            0,
            int(n[3]) if len(n) > 3 else 100,
        )
        for n in notes
    ]
    events = [Event(NOTES, b"".join(sorted(records, key=lambda r: struct.unpack_from("<I", r)[0])))]
    body = b"".join(e.encode() for e in events)
    header = b"FLhd" + struct.pack("<IHHH", 6, FSC_FORMAT, 0, ppq)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + b"FLdt" + struct.pack("<I", len(body)) + body)
    return out


class Flp:
    def __init__(self, header: bytes, events: list[Event]) -> None:
        self.header, self.events = header, events

    # ---- reading / writing

    @classmethod
    def parse(cls, raw: bytes) -> "Flp":
        header, _, _, events = _parse_header_and_events(raw, expected_formats=(0,))
        return cls(header, events)

    @classmethod
    def open(cls, path: str | Path) -> "Flp":
        """Open a `.flp` file or a Zipped Loop Package (`.zip`) containing a `.flp`."""
        p = Path(path)
        raw = p.read_bytes()
        if p.suffix.lower() == ".zip" or raw[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(p) as zf:
                flps = [n for n in zf.namelist() if n.lower().endswith(".flp")]
                if not flps:
                    raise FlpError(f"zipped loop package {p.name} contains no .flp project")
                raw = zf.read(flps[0])
        return cls.parse(raw)

    def dumps(self) -> bytes:
        body = b"".join(e.encode() for e in self.events)
        return self.header + b"FLdt" + struct.pack("<I", len(body)) + body

    @property
    def ppq(self) -> int:
        return struct.unpack("<H", self.header[12:14])[0]

    # ---- reading the project

    def first(self, eid: int) -> Event | None:
        return next((e for e in self.events if e.id == eid), None)

    @property
    def version(self) -> str:
        e = self.first(VERSION)
        return e.data.rstrip(b"\x00").decode("ascii", "replace") if e else ""

    @property
    def tempo(self) -> float:
        e = self.first(TEMPO)
        if e is None:
            raise FlpError("project has no tempo event")
        return struct.unpack("<I", e.data)[0] / 1000

    def _first_arrangement(self) -> int:
        return next((i for i, e in enumerate(self.events) if e.id == NEW_ARR), len(self.events))

    def channels(self) -> list[Channel]:
        spans = self._channel_spans()
        out = []
        for iid, (s, e) in sorted(spans.items(), key=lambda kv: kv[1][0]):
            span = self.events[s:e]
            get = lambda i, span=span: next((ev.data for ev in span if ev.id == i), None)  # noqa: E731
            name = _text(get(CHAN_NAME) or b"")
            if not name and get(SAMPLE_PATH):
                name = (
                    _text(get(SAMPLE_PATH)).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                )
            name = name or _text(get(PLUGIN_NAME) or b"")
            kind = get(CHAN_TYPE)
            out.append(
                Channel(
                    iid,
                    name,
                    CHANNEL_KINDS.get(kind[0], "?") if kind else "?",
                )
            )
        return out

    def _channel_spans(self) -> dict[int, tuple[int, int]]:
        """channel iid -> (first event, one past the last): up to the next channel, pattern list
        or arrangement, because FL writes the pattern list in the middle of the first channel."""
        spans = {}
        for i, e in enumerate(self.events):
            if e.id != NEW_CHAN:
                continue
            j = i + 1
            while j < len(self.events) and self.events[j].id not in (NEW_CHAN, NEW_PAT, NEW_ARR):
                j += 1
            spans[struct.unpack("<H", e.data)[0]] = (i, j)
        return spans

    def inserts_in_use(self) -> set[int]:
        used = set()
        for s, e in self._channel_spans().values():
            for x in self.events[s:e]:
                if x.id == CHAN_ROUTE:
                    used.add(x.data[0])
                elif x.id == CHAN_ROUTE_16:
                    used.add(struct.unpack("<H", x.data)[0])
        return used

    def _max_insert(self) -> int:
        return 500 if (self._version_major() >= 25 or self.version.startswith("24.2.99")) else 125

    def _clone_channel_block(
        self,
        seed_iid: int,
        name: str,
        insert: int = 0,
        color: tuple[int, int, int] | None = None,
        volume: float | None = None,
        pan: float | None = None,
        *,
        updates: list[tuple[int, bytes, int | None]] | None = None,
        drop_ids: set[int] | None = None,
    ) -> int:
        limit = self._max_insert()
        if not 0 <= insert <= limit:
            raise FlpError(f"insert must be 0-{limit}")
        spans = self._channel_spans()
        start, stop = spans[seed_iid]
        block = [
            Event(e.id, e.data)
            for e in self.events[start:stop]
            if not drop_ids or e.id not in drop_ids
        ]
        iid = max(spans, default=-1) + 1
        block[0] = Event(NEW_CHAN, struct.pack("<H", iid))

        def put(
            eid: int, data: bytes, before_id: int | None = None, after_id: int | None = None
        ) -> None:
            for k, e in enumerate(block):
                if e.id == eid:
                    block[k] = Event(eid, data)
                    return
            if before_id is not None:
                pos = next((k for k, e in enumerate(block) if e.id == before_id), None)
                if pos is not None:
                    block.insert(pos, Event(eid, data))
                    return
            if after_id is not None:
                pos = next((k for k, e in enumerate(block) if e.id == after_id), None)
                if pos is not None:
                    block.insert(pos + 1, Event(eid, data))
                    return
            block.append(Event(eid, data))

        for eid, data, before_id in updates or ():
            after_id = (
                CHAN_TYPE
                if eid in (PLUGIN_NAME, PLUGIN_FLAGS)
                else CHAN_COLOR
                if eid == PLUGIN_PARAMS
                else None
            )
            put(eid, data, before_id=before_id, after_id=after_id)
        put(CHAN_NAME, _utf16(name))
        if insert > 255 or any(e.id == CHAN_ROUTE_16 for e in block):
            block[:] = [e for e in block if e.id != CHAN_ROUTE]
            put(CHAN_ROUTE_16, struct.pack("<H", insert))
        else:
            put(CHAN_ROUTE, bytes([insert]))
        if color is not None:
            put(CHAN_COLOR, bytes([*color, 0]))
        levels = next((e for e in block if e.id == CHAN_LEVELS), None)
        if levels is not None and (volume is not None or pan is not None):
            data = bytearray(levels.data)
            if pan is not None and len(data) >= 4:
                struct.pack_into("<I", data, 0, round((max(-1.0, min(1.0, pan)) + 1) * 6400))
            if volume is not None and len(data) >= 8:
                struct.pack_into("<I", data, 4, round(max(0.0, min(1.0, volume)) * 12800))
            put(CHAN_LEVELS, bytes(data))
        last_end = max(stop_ for _, stop_ in spans.values())
        self.events[last_end:last_end] = block
        count = len(spans) + 1
        self.header = self.header[:10] + struct.pack("<H", count) + self.header[12:]
        return iid

    def add_sampler(
        self,
        name: str,
        sample: str,
        insert: int = 0,
        color: tuple[int, int, int] | None = None,
        volume: float | None = None,
        pan: float | None = None,
    ) -> int:
        """Add a Sampler channel playing `sample`, cloned from a sampler channel FL wrote."""
        kinds = {c.iid: c.kind for c in self.channels()}
        seeds = [iid for iid, kind in kinds.items() if kind == "sampler"]
        if not seeds:
            raise FlpError(
                "the template needs one Sampler channel (it can be empty) to build sample channels"
            )
        return self._clone_channel_block(
            seeds[0],
            name,
            insert,
            color,
            volume,
            pan,
            updates=[(SAMPLE_PATH, _utf16(sample), 170)],
        )

    def add_plugin_from_fst(
        self,
        fst_path: str | Path,
        name: str | None = None,
        insert: int = 0,
        sf2_path: str | None = None,
        color: tuple[int, int, int] | None = None,
        volume: float | None = None,
        pan: float | None = None,
    ) -> int:
        """Clone a new generator plugin channel offline from an FL Studio `.fst` preset."""
        preset = read_fst(fst_path)
        chans = self.channels()
        if not chans:
            raise FlpError("the template needs at least one channel to build plugin channels")
        seed = next((c for c in chans if c.kind == "plugin"), chans[0])
        params = bytes(preset["params"])
        if sf2_path is not None:
            if "soundfont" not in preset["plugin"].lower():
                raise FlpError(f"preset plugin {preset['plugin']!r} is not SoundFont Player")
            params = patch_soundfont_blob(params, str(sf2_path))
        if color is None and preset["color"] and len(preset["color"]) >= 3:
            c = preset["color"]
            color = (c[0], c[1], c[2])
        updates: list[tuple[int, bytes, int | None]] = [
            (CHAN_TYPE, b"\x02", None),
            (PLUGIN_NAME, _utf16(preset["plugin"]), None),
        ]
        if preset["flags"]:
            updates.append((PLUGIN_FLAGS, preset["flags"], None))
        if params:
            updates.append((PLUGIN_PARAMS, params, None))
        return self._clone_channel_block(
            seed.iid,
            name or preset["name"],
            insert,
            color,
            volume,
            pan,
            updates=updates,
            drop_ids={SAMPLE_PATH},
        )

    def set_soundfont(self, channel_iid: int, sf2_path: str, name: str | None = None) -> None:
        """Swap the `.sf2` bank path on an existing SoundFont Player channel in this `.flp`."""
        spans = self._channel_spans()
        if channel_iid not in spans:
            raise FlpError(f"channel id {channel_iid} not found")
        s, e = spans[channel_iid]
        pname = next((_text(x.data) for x in self.events[s:e] if x.id == PLUGIN_NAME), "")
        if "soundfont" not in pname.lower():
            raise FlpError(
                f"channel {channel_iid} uses {pname or 'no plugin'}, not SoundFont Player"
            )
        ev = next((x for x in self.events[s:e] if x.id == PLUGIN_PARAMS), None)
        if ev is None:
            raise FlpError(f"channel {channel_iid} has no plugin state event ({PLUGIN_PARAMS})")
        ev.data, ev.raw = patch_soundfont_blob(ev.data, str(sf2_path)), None
        if name:
            cev = next((x for x in self.events[s:e] if x.id == CHAN_NAME), None)
            if cev is not None:
                cev.data, cev.raw = _utf16(name), None

    def _pattern_blocks(self) -> dict[int, dict[str, list[tuple[int, int]]]]:
        """pattern iid -> {'notes': [(start, end)], 'meta': [(start, end)]} event index ranges."""
        blocks: dict[int, dict[str, list[tuple[int, int]]]] = {}
        i = 0
        while i < len(self.events):
            if self.events[i].id != NEW_PAT:
                i += 1
                continue
            j = i + 1
            while j < len(self.events) and self.events[j].id in PATTERN_EVENTS:
                j += 1
            iid = struct.unpack("<H", self.events[i].data)[0]
            has_notes = any(e.id == NOTES for e in self.events[i:j])
            blocks.setdefault(iid, {"notes": [], "meta": []})[
                "notes" if has_notes else "meta"
            ].append((i, j))
            i = j
        return blocks

    def patterns(self) -> dict[int, dict]:
        out = {}
        for iid, b in self._pattern_blocks().items():
            name = ""
            for s, e in (*b["meta"], *b["notes"]):
                name = next((_text(x.data) for x in self.events[s:e] if x.id == PAT_NAME), name)
            n = sum(
                len(x.data) // NOTE.size
                for s, e in b["notes"]
                for x in self.events[s:e]
                if x.id == NOTES
            )
            out[iid] = {"name": name, "notes": n}
        return out

    def _record_size(self, data: bytes) -> int:
        """Playlist records are 32 bytes up to FL 20, 60 in FL 21-24, 80 in FL 25, and 88 in FL 26+;
        verify PL_BASE at +4 when non-empty, falling back to version-ordered divisibility."""
        vm = self._version_major()
        order = (
            (88, 80, 60, 32)
            if vm >= 26
            else (80, 88, 60, 32)
            if (vm == 25 or self.version.startswith("24.2.99"))
            else (60, 88, 80, 32)
            if vm >= 21
            else (32, 60, 80, 88)
        )
        if data:
            for size in order:
                if len(data) % size == 0 and all(
                    struct.unpack_from("<H", data, k + 4)[0] == PL_BASE
                    for k in range(0, len(data), size)
                ):
                    return size
        for size in order:
            if len(data) % size == 0:
                return size
        raise FlpError("unrecognised playlist layout; this project version is not supported")

    def playlist_items(self, arrangement: int = 0) -> list[dict[str, int]]:
        ev = self._playlist_event(arrangement)
        size = self._record_size(ev.data)
        out = []
        for k in range(0, len(ev.data), size):
            pos, _, idx, length, rv = struct.unpack("<IHHIH", ev.data[k : k + 14])
            if idx >= PL_BASE:
                out.append(
                    {"position": pos, "pattern": idx - PL_BASE, "length": length, "track": 500 - rv}
                )
            else:
                out.append(
                    {
                        "position": pos,
                        "channel": idx,
                        "kind": "channel",
                        "length": length,
                        "track": 500 - rv,
                    }
                )
        return out

    def _playlist_event(self, arrangement: int) -> Event:
        found = [e for e in self.events if e.id == PLAYLIST]
        if arrangement >= len(found):
            raise FlpError("project has no playlist data")
        return found[arrangement]

    def summary(self) -> dict:
        pats = self.patterns()
        has_playlist = any(e.id == PLAYLIST for e in self.events)
        return {
            "version": self.version,
            "ppq": self.ppq,
            "tempo": self.tempo,
            "channels": [[c.iid, c.name, c.kind] for c in self.channels()],
            "patterns": len(pats),
            "named_pattern_with_notes": self._seed_pattern() is not None,
            "sampler_channel": any(c.kind == "sampler" for c in self.channels()),
            "playlist_items": len(self.playlist_items()) if has_playlist else 0,
        }

    # ---- patching

    def set_tempo(self, bpm: float) -> None:
        e = self.first(TEMPO)
        if e is None:
            raise FlpError("project has no tempo event")
        if not 10 <= bpm <= 522:
            raise FlpError("tempo must be 10-522 BPM")
        e.data, e.raw = struct.pack("<I", round(bpm * 1000)), None

    def _seed_pattern(self) -> int | None:
        for iid, b in self._pattern_blocks().items():
            if (
                b["notes"]
                and b["meta"]
                and any(x.id == PAT_NAME for s, e in b["meta"] for x in self.events[s:e])
            ):
                return iid
        return None

    def put_pattern(
        self,
        iid: int,
        notes: list[bytes],
        name: str,
        color: tuple[int, int, int] | None = None,
        *,
        synthesize: bool = False,
        force_synthesize: bool = False,
    ) -> None:
        """Set a pattern's notes and name; patterns that do not exist yet are cloned from a seed."""
        blob = b"".join(sorted(notes, key=lambda n: struct.unpack_from("<I", n)[0]))
        blocks = self._pattern_blocks()
        if iid not in blocks:
            seed = self._seed_pattern()
            if seed is None:
                if force_synthesize or (synthesize and not blocks):
                    anchor = next(
                        (idx for idx, ev in enumerate(self.events) if ev.id in (NEW_ARR, PLAYLIST)),
                        len(self.events),
                    )
                    col = color or (153, 153, 153)
                    self.events[anchor:anchor] = [
                        Event(NEW_PAT, struct.pack("<H", iid)),
                        Event(PAT_COLOR, bytes([*col, 0])),
                        Event(PAT_NAME, _utf16(name)),
                        Event(NOTES, blob),
                    ]
                    return
                raise FlpError(
                    "the template needs one named pattern that contains a note to build on"
                )
            self._clone_pattern(seed, iid)
            blocks = self._pattern_blocks()
        wrote_notes = False
        for s, e in blocks[iid]["notes"]:
            for x in self.events[s:e]:
                if x.id == NOTES:
                    x.data, x.raw = blob, None
                    wrote_notes = True
        if not wrote_notes and blob:
            meta_spans = blocks[iid]["meta"]
            if meta_spans:
                ins_at = meta_spans[-1][1]
                self.events.insert(ins_at, Event(NOTES, blob))
                blocks = self._pattern_blocks()
        wrote_name = False
        for s, e in blocks[iid]["meta"]:
            for x in self.events[s:e]:
                if x.id == PAT_NAME:
                    x.data, x.raw = _utf16(name), None
                    wrote_name = True
                elif x.id == PAT_COLOR and color is not None:
                    x.data, x.raw = bytes([*color, 0]), None
        if not wrote_name and name:
            target_spans = blocks[iid]["meta"] or blocks[iid]["notes"]
            if target_spans:
                self.events.insert(target_spans[0][0] + 1, Event(PAT_NAME, _utf16(name)))

    def _clone_pattern(self, seed: int, iid: int) -> None:
        b = self._pattern_blocks()
        copy = lambda s, e: [Event(x.id, x.data) for x in self.events[s:e]]  # noqa: E731
        for kind in ("meta", "notes"):  # insert the later block first so indices stay valid
            last = max((e for blk in b.values() for s, e in blk[kind]), default=None)
            if last is None:
                continue
            s, e = b[seed][kind][0]
            cloned = copy(s, e)
            cloned[0] = Event(NEW_PAT, struct.pack("<H", iid))
            self.events[last:last] = cloned

    def set_playlist(
        self,
        items: list[tuple[int, int, int, int]],
        mode: str = "append",
        arrangement: int = 0,
        *,
        synthesize: bool = False,
    ) -> int:
        """items: (position ticks, pattern iid, length ticks, track 1-500); returns the count."""
        ev = self._playlist_event(arrangement)
        size = self._record_size(ev.data)
        old = [ev.data[k : k + size] for k in range(0, len(ev.data), size)]
        if not old and size in (60, 80, 88) and not synthesize:
            raise FlpError("the template playlist needs one clip to build on")
        if old:
            seed = old[0]
        elif size in (60, 80, 88):
            head = struct.pack(
                "<IHHIHH2sH4s",
                0,
                PL_BASE,
                PL_BASE,
                0,
                0,
                0,
                bytes([120, 0]),
                64,
                bytes([64, 100, 128, 128]),
            )
            tail = _FL24_PL_TAIL if size == 80 else (_FL26_PL_TAIL if size == 88 else b"")
            seed = head + b"\xff" * 8 + struct.pack("<I", 1) + b"\x00" * 24 + tail
        else:
            seed = OLD_ITEM.pack(
                0,
                PL_BASE,
                PL_BASE,
                0,
                0,
                0,
                bytes([120, 0]),
                64,
                bytes([64, 100, 128, 128]),
                -1.0,
                -1.0,
            )
        start = 0
        if mode == "append" and old:
            start = max(
                struct.unpack_from("<I", r)[0] + struct.unpack_from("<I", r, 8)[0] for r in old
            )
            start = -(-start // (4 * self.ppq)) * 4 * self.ppq  # next bar line
        elif mode not in ("append", "replace"):
            raise FlpError("playlist mode must be append or replace")
        new = []
        for idx, (pos, iid, length, track) in enumerate(items, start=1):
            if not 1 <= track <= 500:
                raise FlpError("playlist track must be 1-500")
            rec = bytearray(seed)
            struct.pack_into("<IHHI", rec, 0, start + pos, PL_BASE, PL_BASE + iid, length)
            struct.pack_into("<HH", rec, 12, 500 - track, 0)
            if not old and size >= 60:
                struct.pack_into("<I", rec, 32, idx)
            new.append(bytes(rec))
        merged = (old if mode == "append" else []) + new
        merged.sort(
            key=lambda r: (struct.unpack_from("<I", r)[0], -struct.unpack_from("<H", r, 12)[0])
        )
        ev.data, ev.raw = b"".join(merged), None
        return len(new)

    def _version_major(self) -> int:
        head = self.version.split(".")[0]
        return int(head) if head.isdigit() else 0

    def _arrangement_bounds(self, arrangement: int = 0) -> tuple[int, int]:
        arrs = [i for i, e in enumerate(self.events) if e.id == NEW_ARR]
        if not arrs:
            ev = self._playlist_event(arrangement)
            idx = self.events.index(ev)
            return idx, len(self.events)
        if arrangement >= len(arrs):
            raise FlpError("project has no playlist data")
        start = arrs[arrangement]
        stop = arrs[arrangement + 1] if arrangement + 1 < len(arrs) else len(self.events)
        return start, stop

    def markers(self, arrangement: int = 0) -> list[dict[str, Any]]:
        """Return arrangement time markers (position ticks, flags, name, event indices)."""
        start, stop = self._arrangement_bounds(arrangement)
        pl_idx = next(
            (i for i in range(start, stop) if self.events[i].id == PLAYLIST),
            start,
        )
        out: list[dict[str, Any]] = []
        i = pl_idx + 1
        while i < stop:
            if self.events[i].id != TIME_MARKER_POS:
                i += 1
                continue
            raw_val = struct.unpack("<I", self.events[i].data)[0]
            flags, pos = raw_val >> 24, raw_val & 0x00FFFFFF
            name, name_idx = "", None
            j = i + 1
            while j < stop and self.events[j].id not in (TIME_MARKER_POS, NEW_ARR, PLAYLIST, 238):
                if self.events[j].id == TIME_MARKER_NAME:
                    name = _text(self.events[j].data)
                    name_idx = j
                    break
                j += 1
            out.append(
                {
                    "position": pos,
                    "flags": flags,
                    "name": name,
                    "idx_pos": i,
                    "idx_name": name_idx,
                }
            )
            i = max(i + 1, j + 1)
        return out

    def set_end_marker(
        self, ticks: int, arrangement: int = 0, *, create: bool = True
    ) -> dict[str, Any] | None:
        """Move the arrangement 'end' marker to `ticks` (preserving upper flag bits), or insert."""
        if ticks < 0:
            raise FlpError("end marker ticks must be >= 0")
        marks = self.markers(arrangement)
        existing = next(
            (m for m in marks if m["flags"] == 0 and m["name"].strip().lower() == "end"),
            None,
        ) or next((m for m in marks if m["name"].strip().lower() == "end"), None)
        if existing is not None:
            ev = self.events[existing["idx_pos"]]
            old_raw = struct.unpack("<I", ev.data)[0]
            new_raw = (old_raw & 0xFF000000) | (ticks & 0x00FFFFFF)
            ev.data, ev.raw = struct.pack("<I", new_raw), None
            return {"action": "updated", "position": ticks}
        if not create:
            return None
        return self.add_marker(ticks, "end", arrangement=arrangement)

    def add_marker(
        self,
        ticks: int,
        name: str,
        *,
        mode: int = 0,
        ts_num: int = 4,
        ts_den: int = 4,
        arrangement: int = 0,
    ) -> dict[str, Any]:
        """Insert a playlist marker (mode=8 for time-signature change) into `arrangement`."""
        if ticks < 0:
            raise FlpError("marker ticks must be >= 0")
        marks = self.markers(arrangement)
        if marks:
            anchor = max(m["idx_name"] or m["idx_pos"] for m in marks) + 1
        else:
            start, stop = self._arrangement_bounds(arrangement)
            anchor = next(
                (i + 1 for i in range(start, stop) if self.events[i].id == PLAYLIST),
                stop,
            )
        raw_pos = ((int(mode) & 0xFF) << 24) | (int(ticks) & 0x00FFFFFF)
        block = [
            Event(TIME_MARKER_POS, struct.pack("<I", raw_pos)),
            Event(TIME_MARKER_NUM, bytes([max(1, min(16, int(ts_num)))])),
            Event(TIME_MARKER_DEN, bytes([max(1, min(16, int(ts_den)))])),
            Event(TIME_MARKER_NAME, _utf16(name)),
        ]
        self.events[anchor:anchor] = block
        return {
            "action": "created",
            "position": ticks,
            "name": name,
            "mode": int(mode),
            "ts": [int(ts_num), int(ts_den)],
        }

    def set_time_signature(
        self, numerator: int, denominator: int, *, ticks: int = 0, arrangement: int = 0
    ) -> dict[str, Any]:
        """Insert a time-signature marker (`mode=8`) at `ticks` in `arrangement`."""
        if numerator < 1 or numerator > 16 or denominator not in (1, 2, 4, 8, 16):
            raise FlpError(f"invalid time signature {numerator}/{denominator}")
        return self.add_marker(
            ticks,
            f"{numerator}/{denominator}",
            mode=8,
            ts_num=numerator,
            ts_den=denominator,
            arrangement=arrangement,
        )

    def rename_insert(self, insert: int, name: str) -> bool:
        """Rename mixer insert (0 = Master, 1..N = Insert 1..N) via INSERT_NAME (event 204)."""
        names = [ev for ev in self.events if ev.id == INSERT_NAME]
        if not 0 <= insert < len(names):
            return False
        names[insert].data, names[insert].raw = _utf16(name), None
        return True

    def patch_init_events(
        self, updates: dict[tuple[int, int], int], *, append_missing: bool = True
    ) -> int:
        """Patch/append FLP_InitEvents (id=225, 12-byte <IIi) records keyed by (insert, param_lo).

        param_lo: 0x1fc0=fader (12800=0 dB), 0x1fc1=pan (-6400..6400), 0x1fc2=stereo sep,
        0x2000+dst=send level to insert dst (0..12800).
        """
        ev = self.first(INIT_EVENTS)
        if ev is None:
            if not append_missing:
                return 0
            buf = bytearray()
            ev = Event(INIT_EVENTS, b"")
            anchor = self._first_arrangement()
            self.events.insert(anchor, ev)
        else:
            buf = bytearray(ev.data)
        remaining = {
            ((0x7000 + int(ins) * 0x40), int(lo)): int(v) for (ins, lo), v in updates.items()
        }
        changed = 0
        for off in range(0, len(buf) - 11, 12):
            _a, b, _val = struct.unpack_from("<IIi", buf, off)
            key = (b >> 16, b & 0xFFFF)
            if key in remaining:
                struct.pack_into("<i", buf, off + 8, remaining.pop(key))
                changed += 1
        if append_missing and remaining:
            for (hi, lo), val in remaining.items():
                buf.extend(struct.pack("<IIi", 0, (hi << 16) | (lo & 0xFFFF), val))
                changed += 1
        ev.data, ev.raw = bytes(buf), None
        return changed

    def set_mixer_fader(
        self, insert: int, gain_db: float | None = None, *, raw: int | None = None
    ) -> int:
        """Set offline mixer insert fader in InitEvents (12800 = 0 dB unity)."""
        if raw is None:
            if gain_db is None:
                raise FlpError("set_mixer_fader needs gain_db or raw")
            raw = max(0, min(16000, int(round(12800 * (10.0 ** (float(gain_db) / 20.0))))))
        return self.patch_init_events({(insert, 0x1FC0): int(raw)})

    def set_mixer_pan(self, insert: int, pan: float) -> int:
        """Set offline mixer insert pan (-1.0 left .. +1.0 right) in InitEvents."""
        raw = max(-6400, min(6400, int(round(float(pan) * 6400))))
        return self.patch_init_events({(insert, 0x1FC1): raw})

    def set_mixer_stereo(self, insert: int, separation: float) -> int:
        """Set offline mixer stereo separation (-1.0 merged .. +1.0 separated) in InitEvents."""
        raw = max(-6400, min(6400, int(round(float(separation) * 6400))))
        return self.patch_init_events({(insert, 0x1FC2): raw})

    def set_mixer_send(self, src_insert: int, dst_insert: int, level: float) -> int:
        """Set offline mixer send level (0.0..1.0 -> 0..12800) from src_insert to dst_insert."""
        raw = max(0, min(12800, int(round(max(0.0, min(1.0, float(level))) * 12800))))
        return self.patch_init_events({(src_insert, 0x2000 + int(dst_insert)): raw})

    def set_insert(
        self,
        insert: int,
        *,
        name: str | None = None,
        gain_db: float | None = None,
        pan: float | None = None,
        stereo: float | None = None,
        sends: dict[int, float] | None = None,
    ) -> dict[str, Any]:
        """Configure a mixer insert offline (name, fader dB, pan, stereo sep, sends) in 1 call."""
        updates: dict[tuple[int, int], int] = {}
        if gain_db is not None:
            raw_v = max(0, min(16000, int(round(12800 * (10.0 ** (float(gain_db) / 20.0))))))
            updates[(insert, 0x1FC0)] = raw_v
        if pan is not None:
            updates[(insert, 0x1FC1)] = max(-6400, min(6400, int(round(float(pan) * 6400))))
        if stereo is not None:
            updates[(insert, 0x1FC2)] = max(-6400, min(6400, int(round(float(stereo) * 6400))))
        for dst, lvl in (sends or {}).items():
            raw_s = max(0, min(12800, int(round(max(0.0, min(1.0, float(lvl))) * 12800))))
            updates[(insert, 0x2000 + int(dst))] = raw_s
        renamed = self.rename_insert(insert, name) if name is not None else False
        patched = self.patch_init_events(updates) if updates else 0
        return {"insert": insert, "renamed": renamed, "patched_init_events": patched}

    def patch_fruity_reeverb2(
        self,
        *,
        occurrence: int = 0,
        highcut_hz: float | None = None,
        lowcut_hz: float | None = None,
        size: float | None = None,
        diffusion: int | None = None,
        decay_s: float | None = None,
        predelay_ms: int | None = None,
        wet: int | None = None,
        mod: int | None = None,
        send_bus: bool = False,
    ) -> list[int] | None:
        """Patch Fruity Reeverb 2 binary state (event 213, 16x int32 + tail) offline.

        When `send_bus=True`, `wet` is clamped to `100..125` (default `115`) so a dedicated
        reverb send bus never loses its wet return.
        """
        seen = 0
        for i, ev in enumerate(self.events):
            if ev.id == PLUGIN_NAME and "reeverb 2" in _text(ev.data).lower():
                for j in range(i, min(len(self.events), i + 10)):
                    pe = self.events[j]
                    if pe.id == PLUGIN_PARAMS and len(pe.data) >= 64:
                        if seen < occurrence:
                            seen += 1
                            break
                        tail = pe.data[64:]
                        vals = list(struct.unpack("<16i", pe.data[:64]))
                        eff_wet = wet
                        if send_bus:
                            eff_wet = max(100, min(125, 115 if eff_wet is None else int(eff_wet)))
                        elif eff_wet is not None:
                            eff_wet = max(0, min(125, int(eff_wet)))
                        updates = {
                            "highcut": (
                                None if highcut_hz is None else int(round(highcut_hz / 100.0))
                            ),
                            "lowcut": None if lowcut_hz is None else int(round(lowcut_hz / 10.0)),
                            "size": None if size is None else int(round(size * 100)),
                            "diffusion": None if diffusion is None else int(diffusion),
                            "decay": None if decay_s is None else int(round(decay_s * 10)),
                            "predelay": None if predelay_ms is None else int(predelay_ms),
                            "wet": eff_wet,
                            "mod": None if mod is None else int(mod),
                        }
                        for key, v in updates.items():
                            if v is not None:
                                vals[REEVERB2_FIELDS[key]] = v
                        pe.data, pe.raw = struct.pack("<16i", *vals) + tail, None
                        return vals
        return None

    def add_sampler_from_donor(
        self,
        donor: "Flp | str | Path",
        name: str,
        sample: str,
        insert: int = 0,
        color: tuple[int, int, int] | None = None,
        volume: float | None = None,
        pan: float | None = None,
    ) -> int:
        """Clone a Sampler channel from `donor` `.flp` when this project lacks a Sampler seed."""
        src = donor if isinstance(donor, Flp) else Flp.open(donor)
        src_kinds = {c.iid: c.kind for c in src.channels()}
        seeds = [iid for iid, kind in src_kinds.items() if kind == "sampler"]
        if not seeds:
            raise FlpError("donor .flp has no Sampler channel to clone")
        s_start, s_stop = src._channel_spans()[seeds[0]]
        spans = self._channel_spans()
        iid = max(spans, default=-1) + 1
        block = [Event(e.id, e.data) for e in src.events[s_start:s_stop]]
        block[0] = Event(NEW_CHAN, struct.pack("<H", iid))
        limit = self._max_insert()
        if not 0 <= insert <= limit:
            raise FlpError(f"insert must be 0-{limit}")

        def _put(eid: int, data: bytes) -> None:
            for k, e in enumerate(block):
                if e.id == eid:
                    block[k] = Event(eid, data)
                    return
            block.append(Event(eid, data))

        _put(CHAN_NAME, _utf16(name))
        _put(SAMPLE_PATH, _utf16(str(Path(sample))))
        if insert > 255 or any(e.id == CHAN_ROUTE_16 for e in block):
            block[:] = [e for e in block if e.id != CHAN_ROUTE]
            _put(CHAN_ROUTE_16, struct.pack("<H", insert))
        else:
            _put(CHAN_ROUTE, bytes([insert]))
        if color is not None:
            _put(CHAN_COLOR, bytes([*color, 0]))
        levels = next((e for e in block if e.id == CHAN_LEVELS), None)
        if levels is not None and (volume is not None or pan is not None):
            data = bytearray(levels.data)
            if pan is not None and len(data) >= 4:
                struct.pack_into("<I", data, 0, round((max(-1.0, min(1.0, pan)) + 1) * 6400))
            if volume is not None and len(data) >= 8:
                struct.pack_into("<I", data, 4, round(max(0.0, min(1.0, volume)) * 12800))
            _put(CHAN_LEVELS, bytes(data))
        last_end = max((stop_ for _, stop_ in spans.values()), default=self._first_arrangement())
        self.events[last_end:last_end] = block
        self.header = self.header[:10] + struct.pack("<H", len(spans) + 1) + self.header[12:]
        return iid

    def set_tempo_map(
        self,
        points: list[tuple[int, float]],
        *,
        pattern_iid: int | None = None,
        arrangement: int = 0,
    ) -> dict[str, Any]:
        """Write a multi-point tempo map: base TEMPO + event-223 REC_Tempo automation + markers."""
        if not points:
            raise FlpError("tempo map needs at least one (ticks, bpm) point")
        ordered = sorted((int(t), float(b)) for t, b in points)
        if ordered[0][0] < 0 or any(not 10.0 <= b <= 522.0 for _, b in ordered):
            raise FlpError("tempo points need ticks >= 0 and bpm in 10..522")
        self.set_tempo(ordered[0][1])
        auto_blob = b"".join(
            struct.pack("<IIi", tick, REC_TEMPO, int(round(bpm * 1000))) for tick, bpm in ordered
        )
        blocks = self._pattern_blocks()
        target_pat = pattern_iid or (min(blocks) if blocks else 1)
        if target_pat in blocks and blocks[target_pat]["notes"]:
            s, e = blocks[target_pat]["notes"][0]
            existing_auto = next((x for x in self.events[s:e] if x.id == AUTOMATION_DATA), None)
            if existing_auto is not None:
                existing_auto.data, existing_auto.raw = auto_blob, None
            else:
                self.events.insert(e, Event(AUTOMATION_DATA, auto_blob))
        else:
            anchor = self._first_arrangement()
            self.events[anchor:anchor] = [
                Event(NEW_PAT, struct.pack("<H", target_pat)),
                Event(PAT_NAME, _utf16("Tempo Map")),
                Event(AUTOMATION_DATA, auto_blob),
            ]
        return {"base_bpm": ordered[0][1], "points": len(ordered), "pattern": target_pat}

    def verify(self) -> dict[str, Any]:
        """Run an offline structural & musical readiness check on this `.flp`."""
        chans = self.channels()
        spans = self._channel_spans()
        pats = self.patterns()
        has_playlist = any(e.id == PLAYLIST for e in self.events)
        items = self.playlist_items() if has_playlist else []
        marks = self.markers() if has_playlist else []
        max_tick = max((it["position"] + it["length"] for it in items), default=0)
        end_m = next(
            (m for m in marks if m["flags"] == 0 and m["name"].lower() == "end"), None
        ) or next((m for m in marks if m["name"].strip().lower() == "end"), None)
        bars = round(max_tick / (4 * self.ppq), 2) if self.ppq else 0.0
        duration_s = round((max_tick / max(1, self.ppq)) * (60.0 / max(1.0, self.tempo)), 2)

        channel_routes: dict[int, int] = {}
        channel_plugins: list[dict[str, Any]] = []
        for c in chans:
            s, e = spans.get(c.iid, (0, 0))
            route = 0
            plugin = ""
            sf2 = None
            for ev in self.events[s:e]:
                if ev.id == CHAN_ROUTE:
                    route = ev.data[0]
                elif ev.id == CHAN_ROUTE_16:
                    route = struct.unpack("<H", ev.data)[0]
                elif ev.id == PLUGIN_NAME:
                    plugin = _text(ev.data)
                elif ev.id == PLUGIN_PARAMS and "soundfont" in plugin.lower():
                    sf2 = read_soundfont_blob_path(ev.data)
            channel_routes[c.iid] = route
            if plugin or sf2:
                info: dict[str, Any] = {
                    "iid": c.iid,
                    "name": c.name,
                    "plugin": plugin,
                    "insert": route,
                }
                if sf2:
                    info["sf2"] = sf2
                channel_plugins.append(info)

        warnings: list[str] = []
        if not items:
            warnings.append("playlist has no arrangement clips")
        if end_m is not None and max_tick > 0 and end_m["position"] < max_tick:
            warnings.append(
                f"'end' marker at tick {end_m['position']} cuts off arrangement ending at "
                f"{max_tick}"
            )
        if end_m is not None and max_tick > 0 and end_m["position"] > max_tick + 16 * self.ppq:
            warnings.append(
                f"'end' marker at tick {end_m['position']} extends past arrangement end {max_tick}"
            )
        empty_pats = [pid for pid, pinfo in pats.items() if pinfo["notes"] == 0 and pinfo["name"]]
        if len(pats) > 0 and len(empty_pats) == len(pats):
            warnings.append("all patterns have 0 notes")
        unrouted = [
            c.name for c in chans if c.kind != "automation" and channel_routes.get(c.iid, 0) == 0
        ]
        if chans and len(unrouted) == len([c for c in chans if c.kind != "automation"]):
            warnings.append("all channels route to Master (insert 0); separate inserts help mixing")

        return {
            "ok": not warnings,
            "version": self.version,
            "ppq": self.ppq,
            "tempo": self.tempo,
            "bars": bars,
            "duration_s": duration_s,
            "channels": len(chans),
            "plugins": channel_plugins,
            "patterns": len(pats),
            "empty_patterns": len(empty_pats),
            "playlist_items": len(items),
            "end_marker_ticks": end_m["position"] if end_m else None,
            "warnings": warnings,
        }


# ---------------------------------------------------------------- plan -> project


def note_record(
    position: int, length: int, key: int, channel: int, velocity: int, slide: bool = False
) -> bytes:
    flags = 0x4000 | (0x08 if slide else 0)
    return NOTE.pack(
        position, flags, channel, length, key, 0, 120, 0, 64, 0, 64, velocity, 128, 128
    )


def _find_soundfont_fst() -> Path | None:
    from fl_mcp import paths

    roots = [*paths.fl_install_dirs(), paths.fl_user_dir()]
    for base in roots:
        for pat in ("*Soundfont*.fst", "*SoundFont*.fst"):
            hits = sorted(base.rglob(pat)) if base.is_dir() else []
            if hits:
                return hits[0]
    return None


def _add_soundfont_channel(
    flp: Flp,
    role: str,
    sf2_path: str,
    insert: int,
    color: tuple[int, int, int],
    pan: float | None,
) -> int:
    spans = flp._channel_spans()
    for ch in flp.channels():
        s, e = spans.get(ch.iid, (0, 0))
        pname = next((_text(x.data) for x in flp.events[s:e] if x.id == PLUGIN_NAME), "")
        if "soundfont" in pname.lower():
            blob = next((x.data for x in flp.events[s:e] if x.id == PLUGIN_PARAMS), None)
            if blob is not None:
                patched = patch_soundfont_blob(blob, sf2_path)
                return flp._clone_channel_block(
                    ch.iid,
                    role.capitalize(),
                    insert,
                    color,
                    pan=pan,
                    updates=[(PLUGIN_PARAMS, patched, None)],
                )
    fst = _find_soundfont_fst()
    if fst is None:
        raise FlpError(
            "adding an .sf2 instrument needs a SoundFont Player channel in the template or "
            "a SoundFont Player.fst preset in FL Studio's plugin database"
        )
    return flp.add_plugin_from_fst(
        fst,
        name=role.capitalize(),
        insert=insert,
        sf2_path=sf2_path,
        color=color,
        pan=pan,
    )


def build(
    template: Path,
    plan: Plan,
    out: Path,
    mapping: dict[str, int] | None = None,
    playlist: str = "append",
    sounds: dict[str, str] | None = None,
    inserts: bool = True,
) -> dict:
    """Write `out` = template + the plan's patterns and playlist. The template is never changed.

    sounds maps a role to a sample (.wav/.ogg), plugin preset (.fst), or SoundFont (.sf2):
    that role gets its own channel on a free mixer insert (unless inserts is False)."""
    from fl_mcp.produce import GROUP_COLORS, detect_roles

    flp = Flp.open(template)
    chans = flp.channels()
    found, unmatched = detect_roles([c.name for c in chans])
    iids = {r: chans[i].iid for r, i in found.items()}
    for r, i in (mapping or {}).items():
        if not 0 <= i < len(chans):
            raise FlpError(f"channel index {i} for {r} is out of range 0..{len(chans) - 1}")
        iids[r] = chans[i].iid
    created: dict[str, dict] = {}
    insert = max(flp.inserts_in_use(), default=0)
    for role in plan.roles:
        sample = (sounds or {}).get(role)
        if not sample:
            continue
        mix = plan.mix.get(role)
        color = GROUP_COLORS.get(_group_of(role), "#999999").lstrip("#")
        rgb = (int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16))
        if inserts:
            insert += 1
            if insert > flp._max_insert():
                raise FlpError("the mixer has no free insert left for another channel")
        target_insert = insert if inserts else 0
        low = sample.lower()
        if low.endswith(".fst"):
            iids[role] = flp.add_plugin_from_fst(
                sample,
                name=role.capitalize(),
                insert=target_insert,
                color=rgb,
                pan=mix.pan if mix else None,
            )
        elif low.endswith(".sf2"):
            iids[role] = _add_soundfont_channel(
                flp, role, sample, target_insert, rgb, mix.pan if mix else None
            )
        else:
            iids[role] = flp.add_sampler(
                role.capitalize(), sample, target_insert, rgb, pan=mix.pan if mix else None
            )
        created[role] = {"iid": iids[role], "insert": target_insert, "sample": sample}
    existing = flp.patterns()
    base = max(existing, default=0)
    if base + len(plan.patterns) > 999:
        raise FlpError("FL Studio allows 999 patterns")
    ppq = flp.ppq
    skipped: set[str] = set()
    for i, pat in enumerate(plan.patterns):
        notes = []
        for role, part in pat.parts.items():
            if role not in iids:
                skipped.add(role)
                continue
            for e in part.events:
                notes.append(
                    note_record(
                        round(e.t * ppq), max(1, round(e.d * ppq)), e.p, iids[role], e.v, e.slide
                    )
                )
        group = pat.name.rsplit(" - ", 1)[-1]
        hexcolor = GROUP_COLORS.get(group, "#999999").lstrip("#")
        flp.put_pattern(
            base + 1 + i,
            notes,
            pat.name,
            tuple(int(hexcolor[k : k + 2], 16) for k in (0, 2, 4)),
            synthesize=True,
        )
    flp.set_tempo(plan.bpm)
    written = flp.set_playlist(
        [
            (round(c.bar * 4 * ppq), base + 1 + c.pattern, round(c.bars * 4 * ppq), c.track)
            for c in plan.clips
        ],
        playlist,
        synthesize=True,
    )
    end_tick = max((it["position"] + it["length"] for it in flp.playlist_items()), default=0)
    if end_tick > 0:
        flp.set_end_marker(end_tick, create=False)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(flp.dumps())
    return {
        "file": str(out),
        "patterns": [base + 1, base + len(plan.patterns)],
        "clips": written,
        "channels": {r: c for r, c in iids.items() if r in plan.roles},
        "no_channel": sorted(skipped),
        "unmatched_channels": unmatched,
        "created": created,
        "tempo": plan.bpm,
    }


def _group_of(role: str) -> str:
    """The colour group a role belongs to, as the composer groups them."""
    from fl_mcp.music.compose import GROUPS

    return next((name for name, roles in GROUPS if role in roles), "FX")


def build_from_score(
    template: str | Path,
    score: dict[str, Any],
    out: str | Path,
    *,
    donor_flp: str | Path | None = None,
    resolve_harmony: bool = True,
) -> dict[str, Any]:
    """Build a hybrid multi-channel, multi-section `.flp` from a structured score dict (D1/D2).

    `score` supports:
      - `tempo`: float (default 120.0)
      - `tempo_map`: list of `[ticks_or_sec, bpm]` (uses seconds when `time_unit == 'seconds'`)
      - `time_signatures`: list of `[pos, num, den]`
      - `sections`: list of `[pos, name]`
      - `time_unit`: `'seconds'` (default) or `'beats'`
      - `channels`: list of channel dicts (`name`, `kind`: `'sf2'|'wav'|'sampler'`, `sf2`/`wav`,
        `insert`, `level_db`, `pan`, `color`, `sf2_release`, `events`: `[[start, dur, midi, vel]]`,
        optional `mode` (an `articulation.MODES` name; applies acoustic physics), `pre_ms`, `lo`,
        `hi`, `min_ring`, `audit_mel` (melody-vs-chord audit))
      - `chords`: optional `[[t0_sec, t1_sec, "Am7"], ...]` (pedal / let-ring boundaries + audit)
      - `chord_bounds`: optional extra chord-change times in seconds
      - `inserts`: optional dict `{insert_idx: {name, gain_db, pan, stereo, sends}}`
      - `hall`: optional dict for `patch_fruity_reeverb2(..., send_bus=True)`
    """
    from fl_mcp.music import articulation as artic
    from fl_mcp.music.theory import (
        _DEFAULT_SKIP_CHANNELS,
        audit_ensemble_harmony,
        resolve_ensemble_harmony,
    )

    flp = Flp.open(template)
    bpm = float(score.get("tempo") or 120.0)
    flp.set_tempo(bpm)
    ppq = flp.ppq
    unit = str(score.get("time_unit") or "seconds").lower()
    ticks_per_unit = (ppq * (bpm / 60.0)) if unit == "seconds" else float(ppq)

    ch_specs: list[dict[str, Any]] = list(score.get("channels") or [])
    if not ch_specs:
        raise FlpError("score needs at least one channel in 'channels'")

    # Optional ensemble harmony solver (E20) when working in seconds
    raw_by_ch = {
        str(c.get("id") or c.get("name") or f"ch{idx}"): [
            [float(e[0]), float(e[1]), int(e[2]), int(e[3]) if len(e) > 3 else 100]
            for e in (c.get("events") or [])
        ]
        for idx, c in enumerate(ch_specs)
    }
    harmony_audit: dict[str, Any] | None = None
    physics: dict[str, str] = {}
    melody_audit: dict[str, list[Any]] = {}
    chord_slots = [(float(a), float(b), str(s)) for a, b, s in (score.get("chords") or [])]
    if unit == "seconds":
        extra_bounds = {float(x) for x in (score.get("chord_bounds") or [])}
        bounds = sorted(extra_bounds | set(artic.slot_boundaries(chord_slots)))
        for idx, c in enumerate(ch_specs):
            key = str(c.get("id") or c.get("name") or f"ch{idx}")
            mode = c.get("mode")
            if not mode or mode == "wav" or c.get("kind") in ("wav", "sampler"):
                continue
            raw_by_ch[key] = artic.apply_instrument_physics(
                raw_by_ch[key],
                str(mode),
                bounds,
                attack_pre_ms=float(c.get("pre_ms", 0.0)),
                pitch_lo=int(c.get("lo", 21)),
                pitch_hi=int(c.get("hi", 108)),
                min_ring=float(c.get("min_ring", 2.8)),
            )
            physics[key] = str(mode)
    if resolve_harmony and unit == "seconds":
        quiet = {
            str(c.get("id") or c.get("name") or f"ch{idx}")
            for idx, c in enumerate(ch_specs)
            if c.get("kind") in ("wav", "sampler") or c.get("mode") in ("wav", "perc")
        }
        skip = frozenset({*_DEFAULT_SKIP_CHANNELS, *(k.lower() for k in quiet)})
        raw_by_ch = resolve_ensemble_harmony(raw_by_ch, skip_channels=skip)
        harmony_audit = audit_ensemble_harmony(raw_by_ch, skip_channels=skip)
        if chord_slots:
            for idx, c in enumerate(ch_specs):
                key = str(c.get("id") or c.get("name") or f"ch{idx}")
                if c.get("audit_mel") and raw_by_ch.get(key):
                    melody_audit[key] = [
                        list(x)
                        for x in artic.audit_melody_clashes(
                            raw_by_ch[key], chord_slots, pre_ms=float(c.get("pre_ms", 0.0))
                        )
                    ]

    donor = Flp.open(donor_flp) if donor_flp else None
    has_local_sampler = any(c.kind == "sampler" for c in flp.channels())
    ch_iids: dict[str, int] = {}

    for idx, spec in enumerate(ch_specs):
        key = str(spec.get("id") or spec.get("name") or f"ch{idx}")
        label = str(spec.get("name") or key)
        kind = str(spec.get("kind") or ("wav" if spec.get("wav") else "sf2")).lower()
        insert = int(spec.get("insert", idx + 1))
        col_raw = spec.get("color")
        rgb = (
            (int(col_raw[0]), int(col_raw[1]), int(col_raw[2]))
            if isinstance(col_raw, (list, tuple)) and len(col_raw) >= 3
            else (120, 160, 210)
        )
        pan = float(spec["pan"]) if spec.get("pan") is not None else None
        vol: float | None = None
        if spec.get("level_db") is not None:
            # Map level_db relative to 0 dB = 10000/12800 (~0.78125)
            gain = 10.0 ** (float(spec["level_db"]) / 20.0)
            vol = max(0.0, min(1.0, (10000.0 / 12800.0) * gain))
        if kind in ("wav", "sampler"):
            sample_path = str(spec.get("wav") or spec.get("sample") or "")
            if not sample_path:
                raise FlpError(f"channel '{label}' (kind={kind}) needs 'wav' or 'sample' path")
            if has_local_sampler:
                iid = flp.add_sampler(label, sample_path, insert, rgb, volume=vol, pan=pan)
            elif donor is not None:
                iid = flp.add_sampler_from_donor(
                    donor, label, sample_path, insert, rgb, volume=vol, pan=pan
                )
            else:
                raise FlpError(
                    f"channel '{label}' needs a Sampler channel in template or donor_flp="
                )
        else:
            sf2_path = str(spec.get("sf2") or "")
            if not sf2_path:
                raise FlpError(f"channel '{label}' (kind=sf2) needs 'sf2' path")
            iid = _add_soundfont_channel(flp, label, sf2_path, insert, rgb, pan)
            if spec.get("sf2_release") is not None or vol is not None:
                s0, e0 = flp._channel_spans()[iid]
                for ev in flp.events[s0:e0]:
                    if ev.id == PLUGIN_PARAMS and spec.get("sf2_release") is not None:
                        ev.data = set_soundfont_release(ev.data, int(spec["sf2_release"]))
                        ev.raw = None
                    elif ev.id == CHAN_LEVELS and vol is not None and len(ev.data) >= 8:
                        buf = bytearray(ev.data)
                        struct.pack_into("<I", buf, 4, round(vol * 12800))
                        ev.data, ev.raw = bytes(buf), None
        ch_iids[key] = iid
        flp.rename_insert(insert, label)

    # Apply offline mixer inserts & optional send-bus Reeverb 2
    for ins_k, ins_cfg in (score.get("inserts") or {}).items():
        if isinstance(ins_cfg, dict):
            flp.set_insert(
                int(ins_k),
                name=ins_cfg.get("name"),
                gain_db=ins_cfg.get("gain_db"),
                pan=ins_cfg.get("pan"),
                stereo=ins_cfg.get("stereo"),
                sends=ins_cfg.get("sends"),
            )
    if isinstance(score.get("hall"), dict):
        h = score["hall"]
        flp.patch_fruity_reeverb2(
            highcut_hz=h.get("highcut_hz", h.get("highcut")),
            lowcut_hz=h.get("lowcut_hz", h.get("lowcut")),
            size=h.get("size"),
            diffusion=h.get("diffusion"),
            decay_s=h.get("decay_s", h.get("decay")),
            predelay_ms=h.get("predelay_ms", h.get("predelay")),
            wet=h.get("wet"),
            mod=h.get("mod"),
            send_bus=bool(h.get("send_bus", True)),
        )

    # Slice notes by sections (or build one master section if none provided)
    raw_secs = score.get("sections") or [[0.0, "Main"]]
    sec_list = sorted((max(0.0, float(s[0])), str(s[1])) for s in raw_secs)
    max_end_unit = max(
        (float(e[0]) + float(e[1]) for evs in raw_by_ch.values() for e in evs),
        default=4.0,
    )
    existing_pats = flp.patterns()
    pat_iid = max(existing_pats, default=0)
    clips: list[tuple[int, int, int, int]] = []

    for s_idx, (s_start, s_name) in enumerate(sec_list):
        s_end = sec_list[s_idx + 1][0] if s_idx + 1 < len(sec_list) else max_end_unit
        s_start_tk = int(round(s_start * ticks_per_unit))
        s_len_tk = max(ppq, int(round((s_end - s_start) * ticks_per_unit)))
        sec_notes: list[bytes] = []
        for key, iid in ch_iids.items():
            for t0, dur, pitch, vel in raw_by_ch.get(key, ()):
                if s_start - 1e-4 <= t0 < s_end - 1e-4 or (
                    s_idx + 1 == len(sec_list) and t0 >= s_start - 1e-4
                ):
                    rel_tk = max(0, int(round((t0 - s_start) * ticks_per_unit)))
                    d_tk = max(1, int(round(dur * ticks_per_unit)))
                    sec_notes.append(note_record(rel_tk, d_tk, int(pitch), iid, int(vel)))
        if sec_notes:
            pat_iid += 1
            flp.put_pattern(pat_iid, sec_notes, s_name, force_synthesize=True)
            clips.append((s_start_tk, pat_iid, s_len_tk, 1))
        flp.add_marker(s_start_tk, s_name)

    if clips:
        flp.set_playlist(clips, mode="replace", synthesize=True)

    for ts_item in score.get("time_signatures") or []:
        t_pos, num, den = ts_item[:3]
        flp.set_time_signature(int(num), int(den), ticks=int(round(float(t_pos) * ticks_per_unit)))

    if score.get("tempo_map"):
        pts = [
            (int(round(float(p[0]) * ticks_per_unit)), float(p[1])) for p in score["tempo_map"]
        ]
        flp.set_tempo_map(pts)

    end_tk = max((c[0] + c[2] for c in clips), default=int(round(max_end_unit * ticks_per_unit)))
    flp.set_end_marker(end_tk, create=True)

    out_path = Path(out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(flp.dumps())
    return {
        "file": str(out_path),
        "tempo": bpm,
        "ppq": ppq,
        "channels": ch_iids,
        "sections": len(sec_list),
        "clips": len(clips),
        "end_ticks": end_tk,
        "harmony_audit": harmony_audit,
        "articulation": physics,
        "melody_audit": melody_audit,
    }
