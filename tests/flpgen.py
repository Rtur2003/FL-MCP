"""Builds small FL project files with the same event structure FL Studio writes (own data)."""

import struct

from fl_mcp.flp import Event, Flp

PAT_DEFAULTS = [
    bytes.fromhex("01000000000000000190ff0f04000000d5010000"),
    bytes.fromhex("fd000000000000008090ff0f04000000d5010000"),
    bytes.fromhex("ff000000ff0000000400ff0f0400000000feffff"),
]


def txt(s: str) -> bytes:
    return s.encode("utf-16le") + b"\x00\x00"


def note(pos, length, key, ch, vel=100, flags=0x4000):
    return struct.pack(
        "<IHHIHH8B", pos, flags, ch, length, key, 0, 120, 0, 64, 0, 64, vel, 128, 128
    )


def item(pos, pat, length, track, extra=b""):
    base = struct.pack(
        "<IHHIHH2sH4sff",
        pos,
        20480,
        20480 + pat,
        length,
        500 - track,
        0,
        bytes([120, 0]),
        64,
        bytes([64, 100, 128, 128]),
        -1.0,
        -1.0,
    )
    return base + extra


def template(
    version="20.8.4.2576",
    ppq=96,
    clips=1,
    seed=True,
    wide=False,
    channels=("Kick", "808 Bass", "Lead"),
):
    E = Event
    ev = [
        E(199, version.encode() + b"\x00"),
        E(156, struct.pack("<I", 120000)),
        E(250, bytes(range(256)) * 2),
    ]  # an event we know nothing about must survive
    if seed:
        ev += [E(65, struct.pack("<H", 1)), E(224, note(0, 24, 60, 0))]
        ev += [E(226, d) for d in PAT_DEFAULTS]
    for iid, name in enumerate(channels):
        ev += [
            E(64, struct.pack("<H", iid)),
            E(21, bytes([2])),
            E(201, txt(name)),
            E(203, txt(name)),
        ]
        if iid == 0 and seed:  # FL writes the pattern list after the first channel
            ev += [
                E(65, struct.pack("<H", 1)),
                E(193, txt("Seed")),
                E(150, bytes([72, 81, 86, 0])),
                E(157, b"\xff\xff\xff\xff"),
                E(158, b"\xff\xff\xff\xff"),
                E(164, bytes(4)),
            ]
    ev += [
        E(99, struct.pack("<H", 1)),
        E(
            233,
            b"".join(
                item(i * ppq * 4, 1, ppq * 4, 1, bytes(28) if wide else b"") for i in range(clips)
            ),
        ),
    ]
    ev += [E(99, struct.pack("<H", 2)), E(233, b""), E(203, txt("mixer slot name")), E(11, b"\x01")]
    body = b"".join(e.encode() for e in ev)
    header = b"FLhd" + struct.pack("<IHHH", 6, 0, len(channels), ppq)
    return header + b"FLdt" + struct.pack("<I", len(body)) + body


def parse(raw: bytes) -> Flp:
    return Flp.parse(raw)
