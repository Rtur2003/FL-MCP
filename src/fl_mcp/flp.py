"""Lossless FL Studio project (.flp) reader plus a small patcher for generated songs.

A project is a header and a stream of events (id byte, then 1/2/4 bytes of data for ids below
64/128/192, or a varint length and bytes above). Every event the patcher does not touch is
written back byte for byte. Only tempo, pattern notes/names and the playlist are modified, and
only by cloning structure that FL Studio itself wrote into the template. Format facts come from
the public reverse-engineering work (see NOTICE); no code from it is included.
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fl_mcp.music.plan import Plan

PPQS = (24, 48, 72, 96, 120, 144, 168, 192, 384, 768, 960)
TEMPO, VERSION = 156, 199
NEW_CHAN, CHAN_TYPE, CHAN_NAME, PLUGIN_NAME, SAMPLE_PATH = 64, 21, 203, 201, 196
PLUGIN_FLAGS, PLUGIN_PARAMS = 212, 213
CHAN_ROUTE, CHAN_ROUTE_16, CHAN_COLOR, CHAN_LEVELS = 22, 104, 128, 219  # insert, RGB, pan/vol
NEW_PAT, PAT_NAME, PAT_COLOR, NOTES = 65, 193, 150, 224
NEW_ARR, PLAYLIST = 99, 233
TIME_MARKER_POS, TIME_MARKER_NAME = 148, 205
FST_FORMAT = 48
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
    return data.decode("utf-16le", "replace").rstrip("\x00")


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
        raise FlpError("not an FL Studio file (missing FLhd header)")
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


def patch_soundfont_blob(blob: bytes, sf2_path: str) -> bytes:
    """Replace the `.sf2` path inside an FL SoundFont Player event 213 state blob."""
    if len(blob) < _SF2_HEADER_BYTES + 2:
        raise FlpError("SoundFont Player plugin state blob is too short")
    old_len, path_start = _decode_varint(blob, _SF2_HEADER_BYTES)
    path_end = path_start + old_len
    if path_end > len(blob):
        raise FlpError("SoundFont Player plugin state path runs past end of blob")
    encoded = sf2_path.encode("utf-8")
    return blob[:_SF2_HEADER_BYTES] + _encode_varint(len(encoded)) + encoded + blob[path_end:]


def read_soundfont_blob_path(blob: bytes) -> str | None:
    """Extract the `.sf2` path stored inside a SoundFont Player event 213 blob, if readable."""
    if len(blob) < _SF2_HEADER_BYTES + 2:
        return None
    try:
        length, path_start = _decode_varint(blob, _SF2_HEADER_BYTES)
        if path_start + length > len(blob) or length == 0:
            return None
        text = blob[path_start : path_start + length].decode("utf-8", "replace")
        return text if ".sf2" in text.lower() or "\\" in text or "/" in text else None
    except FlpError:
        return None


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


class Flp:
    def __init__(self, header: bytes, events: list[Event]) -> None:
        self.header, self.events = header, events

    # ---- reading / writing

    @classmethod
    def parse(cls, raw: bytes) -> "Flp":
        header, _, _, events = _parse_header_and_events(raw, expected_formats=(0,))
        return cls(header, events)

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
        starts = [i for i, e in enumerate(self.events) if e.id == NEW_CHAN]
        stop = self._first_arrangement()
        out = []
        for k, s in enumerate(starts):
            span = self.events[s : (starts[k + 1] if k + 1 < len(starts) else stop)]
            get = lambda i, span=span: next((e.data for e in span if e.id == i), None)  # noqa: E731
            name = _text(get(CHAN_NAME) or b"")
            if not name and get(SAMPLE_PATH):
                name = (
                    _text(get(SAMPLE_PATH)).replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
                )
            name = name or _text(get(PLUGIN_NAME) or b"")
            kind = get(CHAN_TYPE)
            out.append(
                Channel(
                    struct.unpack("<H", self.events[s].data)[0],
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
        if not 0 <= insert <= 125:
            raise FlpError("insert must be 0-125")
        spans = self._channel_spans()
        start, stop = spans[seed_iid]
        block = [
            Event(e.id, e.data)
            for e in self.events[start:stop]
            if not drop_ids or e.id not in drop_ids
        ]
        iid = max(spans, default=-1) + 1
        block[0] = Event(NEW_CHAN, struct.pack("<H", iid))

        def put(eid: int, data: bytes, before_id: int | None = None, after_id: int | None = None) -> None:
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
        if any(e.id == CHAN_ROUTE_16 for e in block):
            put(CHAN_ROUTE_16, struct.pack("<H", insert))
        else:
            put(CHAN_ROUTE, bytes([insert]))
        if color is not None:
            put(CHAN_COLOR, bytes([*color, 0]))
        levels = next((e for e in block if e.id == CHAN_LEVELS), None)
        if levels is not None and (volume is not None or pan is not None):
            data = bytearray(levels.data)
            if pan is not None:
                struct.pack_into("<I", data, 0, round((max(-1.0, min(1.0, pan)) + 1) * 6400))
            if volume is not None:
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
            raise FlpError(f"channel {channel_iid} uses {pname or 'no plugin'}, not SoundFont Player")
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
            for s, e in b["meta"]:
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
        """Playlist records are 32 bytes up to FL 20, 60 in FL 21-24, and 80 from FL 25;
        verify PL_BASE at +4 when non-empty, falling back to version-ordered divisibility."""
        vm = self._version_major()
        order = (
            (80, 60, 32)
            if (vm == 25 or self.version.startswith("24.2.99"))
            else (60, 80, 32)
            if vm >= 21
            else (32, 60, 80)
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
            out.append(
                {"position": pos, "pattern": idx - PL_BASE, "length": length, "track": 500 - rv}
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
        self, iid: int, notes: list[bytes], name: str, color: int | None = None
    ) -> None:
        """Set a pattern's notes and name; patterns that do not exist yet are cloned from a seed."""
        blob = b"".join(sorted(notes, key=lambda n: struct.unpack_from("<I", n)[0]))
        blocks = self._pattern_blocks()
        if iid not in blocks:
            seed = self._seed_pattern()
            if seed is None:
                raise FlpError(
                    "the template needs one named pattern that contains a note to build on"
                )
            self._clone_pattern(seed, iid)
            blocks = self._pattern_blocks()
        for s, e in blocks[iid]["notes"]:
            for x in self.events[s:e]:
                if x.id == NOTES:
                    x.data, x.raw = blob, None
        for s, e in blocks[iid]["meta"]:
            for x in self.events[s:e]:
                if x.id == PAT_NAME:
                    x.data, x.raw = _utf16(name), None
                elif x.id == PAT_COLOR and color is not None:
                    x.data, x.raw = bytes([*color, 0]), None

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
        self, items: list[tuple[int, int, int, int]], mode: str = "append", arrangement: int = 0
    ) -> int:
        """items: (position ticks, pattern iid, length ticks, track 1-500); returns the count."""
        ev = self._playlist_event(arrangement)
        size = self._record_size(ev.data)
        old = [ev.data[k : k + size] for k in range(0, len(ev.data), size)]
        if not old and size in (60, 80):
            raise FlpError("the template playlist needs one clip to build on")
        seed = (
            old[0]
            if old
            else OLD_ITEM.pack(
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
        for pos, iid, length, track in items:
            if not 1 <= track <= 500:
                raise FlpError("playlist track must be 1-500")
            rec = bytearray(seed)
            struct.pack_into("<IHHI", rec, 0, start + pos, PL_BASE, PL_BASE + iid, length)
            struct.pack_into("<HH", rec, 12, 500 - track, 0)
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
        """Move the arrangement 'end' marker to `ticks`, or insert one if requested."""
        if ticks < 0:
            raise FlpError("end marker ticks must be >= 0")
        marks = self.markers(arrangement)
        existing = next(
            (m for m in marks if m["flags"] == 0 and m["name"].strip().lower() == "end"),
            None,
        )
        if existing is not None:
            ev = self.events[existing["idx_pos"]]
            ev.data, ev.raw = struct.pack("<I", ticks), None
            return {"action": "updated", "position": ticks}
        if not create:
            return None
        if marks:
            anchor = max(m["idx_name"] or m["idx_pos"] for m in marks) + 1
        else:
            start, stop = self._arrangement_bounds(arrangement)
            anchor = next(
                (i + 1 for i in range(start, stop) if self.events[i].id == PLAYLIST),
                stop,
            )
        block = [
            Event(TIME_MARKER_POS, struct.pack("<I", ticks)),
            Event(33, b"\x04"),
            Event(34, b"\x04"),
            Event(TIME_MARKER_NAME, _utf16("end")),
        ]
        self.events[anchor:anchor] = block
        return {"action": "created", "position": ticks}

    def verify(self) -> dict[str, Any]:
        """Run an offline structural & musical readiness check on this `.flp`."""
        chans = self.channels()
        spans = self._channel_spans()
        pats = self.patterns()
        has_playlist = any(e.id == PLAYLIST for e in self.events)
        items = self.playlist_items() if has_playlist else []
        marks = self.markers() if has_playlist else []
        max_tick = max((it["position"] + it["length"] for it in items), default=0)
        end_m = next((m for m in marks if m["flags"] == 0 and m["name"].lower() == "end"), None)
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
                info: dict[str, Any] = {"iid": c.iid, "name": c.name, "plugin": plugin, "insert": route}
                if sf2:
                    info["sf2"] = sf2
                channel_plugins.append(info)

        warnings: list[str] = []
        if not items:
            warnings.append("playlist has no arrangement clips")
        if end_m is not None and max_tick > 0 and end_m["position"] < max_tick:
            warnings.append(
                f"'end' marker at tick {end_m['position']} cuts off arrangement ending at {max_tick}"
            )
        empty_pats = [pid for pid, pinfo in pats.items() if pinfo["notes"] == 0 and pinfo["name"]]
        if len(pats) > 0 and len(empty_pats) == len(pats):
            warnings.append("all patterns have 0 notes")
        unrouted = [c.name for c in chans if c.kind != "automation" and channel_routes.get(c.iid, 0) == 0]
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

    flp = Flp.parse(Path(template).read_bytes())
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
            if insert > 125:
                raise FlpError("the mixer has no free insert left for another channel")
        target_insert = insert if inserts else 0
        low = sample.lower()
        if low.endswith(".fst"):
            iids[role] = flp.add_plugin_from_fst(
                sample, name=role.capitalize(), insert=target_insert, color=rgb, pan=mix.pan if mix else None
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
            base + 1 + i, notes, pat.name, tuple(int(hexcolor[k : k + 2], 16) for k in (0, 2, 4))
        )
    flp.set_tempo(plan.bpm)
    written = flp.set_playlist(
        [
            (round(c.bar * 4 * ppq), base + 1 + c.pattern, round(c.bars * 4 * ppq), c.track)
            for c in plan.clips
        ],
        playlist,
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
