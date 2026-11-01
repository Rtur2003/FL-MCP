"""Standard MIDI File (type 1) export of a Plan, unrolled along the playlist."""

import re
import struct

from fl_mcp.music.plan import Plan

TICKS = 480
GM_DRUMS = {"kick": 36, "snare": 38, "clap": 39, "hat": 42, "ohat": 46, "perc": 37}
DRUMS = tuple(GM_DRUMS)


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    while n >> 7:
        n >>= 7
        out.append((n & 0x7F) | 0x80)
    return bytes(reversed(out))


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _track(events: list[tuple[int, int, bytes]]) -> bytes:
    """events: (tick, order, message). order 0 sorts before 1 on the same tick."""
    body, last = b"", 0
    for tick, _, msg in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - last) + msg
        last = tick
    body += _vlq(0) + _meta(0x2F, b"")
    return b"MTrk" + struct.pack(">I", len(body)) + body


def _ascii(text: str) -> bytes:
    return re.sub(r"[^\x20-\x7e]", "?", text).encode("ascii")


def plan_to_midi(plan: Plan, drums: str = "fl") -> bytes:
    """drums='fl': one track per drum role, all notes at C5 (60) so FL samplers play them
    unpitched. drums='gm': a single General MIDI drum track on channel 10."""
    if drums not in ("fl", "gm"):
        raise ValueError("drums must be 'fl' or 'gm'")
    tracks: dict[str, list[tuple[int, int, bytes]]] = {}
    for clip in plan.clips:
        pat = plan.patterns[clip.pattern]
        end = (clip.bar + clip.bars) * 4.0
        for role, part in pat.parts.items():
            name = "drums" if drums == "gm" and role in DRUMS else role
            ev_list = tracks.setdefault(name, [])
            for e in part.events:
                start = clip.bar * 4.0 + e.t
                stop = min(start + e.d, end)
                if stop <= start:
                    continue
                pitch = GM_DRUMS[role] if name == "drums" else e.p
                a, b = round(start * TICKS), max(round(stop * TICKS), round(start * TICKS) + 1)
                ev_list.append((a, 1, bytes([0x90, pitch, e.v])))  # channel patched below
                ev_list.append((b, 0, bytes([0x80, pitch, 0])))
    order = (
        ["drums"] + [r for r in plan.roles if r not in DRUMS] if drums == "gm" else list(plan.roles)
    )
    order = [r for r in order if r in tracks]
    free = [c for c in range(16) if c != 9]
    out = []
    for i, role in enumerate(order):
        ch = 9 if role == "drums" else free[i % len(free)]
        events = [(t, o, bytes([m[0] | ch]) + m[1:]) for t, o, m in tracks[role]]
        events.append((0, 0, _meta(0x03, _ascii(role))))
        out.append(_track(events))
    tempo = round(60_000_000 / plan.bpm)
    conductor = [
        (0, 0, _meta(0x03, _ascii(f"FL MCP {plan.genre} {plan.key}"))),
        (0, 0, _meta(0x58, bytes([4, 2, 24, 8]))),
        (0, 0, _meta(0x51, tempo.to_bytes(3, "big"))),
    ]
    conductor += [(round(s.bar * 4 * TICKS), 0, _meta(0x06, _ascii(s.name))) for s in plan.sections]
    conductor.append((round(plan.bars * 4 * TICKS), 0, _meta(0x06, b"End")))
    header = b"MThd" + struct.pack(">IHHH", 6, 1, 1 + len(out), TICKS)
    return header + _track(conductor) + b"".join(out)
