"""Just enough image code to hand a screenshot to a model without any imaging library."""

import struct
import zlib
from operator import itemgetter


def bgra_to_rgb(bgra: bytes | bytearray, width: int, height: int) -> bytes:
    """Windows bitmaps are B,G,R,A per pixel; PNG wants R,G,B."""
    pixels = width * height
    if len(bgra) < pixels * 4:
        raise ValueError("bitmap is smaller than its size says")
    rgb = bytearray(pixels * 3)
    rgb[0::3] = bgra[2 : pixels * 4 : 4]
    rgb[1::3] = bgra[1 : pixels * 4 : 4]
    rgb[2::3] = bgra[0 : pixels * 4 : 4]
    return bytes(rgb)


def resize(rgb: bytes, width: int, height: int, max_width: int) -> tuple[bytes, int, int]:
    """Nearest-neighbour shrink to at most max_width wide (no growing); returns rgb, w, h."""
    if width <= max_width:
        return rgb, width, height
    new_w = max_width
    new_h = max(1, round(height * new_w / width))
    cols = [min(width - 1, int((i + 0.5) * width / new_w)) for i in range(new_w)]
    pick = itemgetter(*cols)
    out = bytearray(new_w * new_h * 3)
    stride = width * 3
    for j in range(new_h):
        src = min(height - 1, int((j + 0.5) * height / new_h))
        row = rgb[src * stride : (src + 1) * stride]
        base = j * new_w * 3
        for c in range(3):
            out[base + c : base + new_w * 3 : 3] = bytes(pick(row[c::3]))
    return bytes(out), new_w, new_h


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(rgb: bytes, width: int, height: int) -> bytes:
    """An RGB8 PNG (no filtering, fast zlib): screenshots are mostly flat colour, so it is small."""
    if width < 1 or height < 1 or len(rgb) != width * height * 3:
        raise ValueError("rgb data does not match the size")
    stride = width * 3
    raw = b"".join(b"\x00" + rgb[y * stride : (y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 3))
        + _chunk(b"IEND", b"")
    )
