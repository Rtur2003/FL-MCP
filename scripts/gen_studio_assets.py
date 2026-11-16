"""Precision pro-audio asset generator for FL-MCP Studio.

Renders crisp, hardware-calibrated studio icons and technical grid backdrops using a restrained
industrial palette (neutral dark graphite #141619, machined titanium #2E3239, studio amber
#FF7A2F, and crisp silver #E8ECF2 — no neon rainbow gradients or blurry glow):
- src/fl_mcp/assets/logo.svg
- src/fl_mcp/assets/icon-256.png, icon-128.png, icon-64.png, favicon.png
- src/fl_mcp/assets/icon.ico (16, 32, 48, 64, 128, 256)
- src/fl_mcp/assets/icon.icns (ic07 128, ic08 256)
- src/fl_mcp/assets/studio-backdrop.webp (1440x320 technical dB/frequency calibration grid)
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np
from scipy.ndimage import zoom

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "fl_mcp" / "assets"


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _sd_rounded_box(
    px: np.ndarray,
    py: np.ndarray,
    cx: float,
    cy: float,
    half_w: float,
    half_h: float,
    rad: float,
) -> np.ndarray:
    qx = np.abs(px - cx) - (half_w - rad)
    qy = np.abs(py - cy) - (half_h - rad)
    out_d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    in_d = np.minimum(np.maximum(qx, qy), 0.0)
    return out_d + in_d - rad


def _encode_png(rgba: np.ndarray) -> bytes:
    h, w, c = rgba.shape
    color_type = 6 if c == 4 else 2
    raw_rows = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    compressed = zlib.compress(raw_rows, level=9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _downsample(rgba: np.ndarray, target_size: int) -> np.ndarray:
    h, w, c = rgba.shape
    if h % target_size == 0:
        factor = h // target_size
        if factor <= 1:
            return rgba
        reshaped = rgba.astype(np.float32).reshape(target_size, factor, target_size, factor, c)
        return np.clip(np.round(reshaped.mean(axis=(1, 3))), 0, 255).astype(np.uint8)
    scale = target_size / float(h)
    return np.clip(
        np.round(zoom(rgba.astype(np.float32), (scale, scale, 1.0), order=1)), 0, 255
    ).astype(np.uint8)


def render_master_icon(size: int = 512) -> np.ndarray:
    """Render a 512x512 industrial studio fader-console emblem (neutral graphite + amber)."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    nx = (xx - cx) / (size / 2.0)
    ny = (yy - cy) / (size / 2.0)
    px_w = 2.0 / size

    # 1. Outer Squircle Chassis (superellipse n=4.8, radius 0.92)
    pad = 0.92
    sq = (np.abs(nx / pad) ** 4.8 + np.abs(ny / pad) ** 4.8) ** (1.0 / 4.8)
    alpha = 1.0 - _smoothstep(1.0 - px_w * 1.1, 1.0 + px_w * 1.1, sq)

    # Neutral dark anodized graphite surface (#1C1E22 top -> #121316 bottom)
    v_shade = np.clip(0.5 - 0.45 * ny - 0.15 * nx, 0.0, 1.0)
    red = 18.0 + 11.0 * v_shade
    grn = 19.0 + 12.0 * v_shade
    blu = 22.0 + 13.0 * v_shade

    # Crisp 1.5px metallic outer bevel (top highlight #424750, bottom shadow #24272C)
    rim = np.exp(-(((sq - 0.978) / 0.014) ** 2))
    rim_light = np.clip(-0.35 * nx - 0.85 * ny + 0.25, -0.4, 1.0)
    red += rim * (28.0 * rim_light + 14.0)
    grn += rim * (30.0 * rim_light + 15.0)
    blu += rim * (34.0 * rim_light + 17.0)

    # 2. Inner Recessed Console Bay (rounded square, half_w=0.68, rad=0.16)
    sd_bay = _sd_rounded_box(nx, ny, 0.0, 0.0, 0.68, 0.68, 0.16)
    bay_mask = 1.0 - _smoothstep(-px_w, px_w, sd_bay)
    bay_border = np.exp(-((sd_bay / 0.010) ** 2))
    red = red * (1.0 - bay_mask) + 13.0 * bay_mask + bay_border * 18.0
    grn = grn * (1.0 - bay_mask) + 14.0 * bay_mask + bay_border * 19.0
    blu = blu * (1.0 - bay_mask) + 16.0 * bay_mask + bay_border * 22.0

    # Subtle horizontal dB calibration ticks across the console bay
    for ty in (-0.42, -0.21, 0.0, 0.21, 0.42):
        tick_line = np.exp(-(((ny - ty) / 0.005) ** 2)) * bay_mask * (np.abs(nx) < 0.58)
        red += tick_line * 9.0
        grn += tick_line * 10.0
        blu += tick_line * 11.0

    # 3. Four Precision Motorized Studio Fader Channels
    # Symmetrically spaced at x = -0.39, -0.13, +0.13, +0.39
    fader_x = (-0.39, -0.13, 0.13, 0.39)
    fader_levels = (0.08, -0.26, -0.06, -0.34)  # y-coordinate of fader cap (negative is higher)
    slot_bot = 0.48
    slot_cy = 0.0
    slot_hh = 0.48

    amber = (255.0, 122.0, 47.0)
    amber_hi = (255.0, 158.0, 92.0)
    cap_light = (234.0, 237.0, 242.0)
    cap_dark = (42.0, 46.0, 52.0)

    for idx, (fx, fy) in enumerate(zip(fader_x, fader_levels, strict=True)):
        # Recessed dark fader track slot
        sd_slot = _sd_rounded_box(nx, ny, fx, slot_cy, 0.048, slot_hh, 0.024)
        slot_cov = 1.0 - _smoothstep(-px_w, px_w, sd_slot)
        red = red * (1.0 - slot_cov) + 7.0 * slot_cov
        grn = grn * (1.0 - slot_cov) + 8.0 * slot_cov
        blu = blu * (1.0 - slot_cov) + 9.0 * slot_cov

        # Illuminated active signal bar from slot_bot up to fy
        bar_cy = (slot_bot + fy) * 0.5
        bar_hh = max(0.03, (slot_bot - fy) * 0.5)
        sd_bar = _sd_rounded_box(nx, ny, fx, bar_cy, 0.036, bar_hh, 0.018)
        bar_cov = 1.0 - _smoothstep(-px_w, px_w, sd_bar)
        v_t = np.clip((slot_bot - ny) / max(0.01, slot_bot - fy), 0.0, 1.0)
        br = amber[0] + (amber_hi[0] - amber[0]) * v_t
        bg = amber[1] + (amber_hi[1] - amber[1]) * v_t
        bb = amber[2] + (amber_hi[2] - amber[2]) * v_t
        red = red * (1.0 - bar_cov) + br * bar_cov
        grn = grn * (1.0 - bar_cov) + bg * bar_cov
        blu = blu * (1.0 - bar_cov) + bb * bar_cov

        # Precision anodized fader cap at fy (with dark center index line)
        sd_cap = _sd_rounded_box(nx, ny, fx, fy, 0.088, 0.042, 0.016)
        cap_cov = 1.0 - _smoothstep(-px_w, px_w, sd_cap)
        is_accent_cap = idx == 3
        c_col = amber_hi if is_accent_cap else cap_light
        red = red * (1.0 - cap_cov) + c_col[0] * cap_cov
        grn = grn * (1.0 - cap_cov) + c_col[1] * cap_cov
        blu = blu * (1.0 - cap_cov) + c_col[2] * cap_cov

        # Center engraved line inside the fader cap
        sd_notch = _sd_rounded_box(nx, ny, fx, fy, 0.062, 0.009, 0.004)
        notch_cov = (1.0 - _smoothstep(-px_w, px_w, sd_notch)) * cap_cov
        red = red * (1.0 - notch_cov) + cap_dark[0] * notch_cov
        grn = grn * (1.0 - notch_cov) + cap_dark[1] * notch_cov
        blu = blu * (1.0 - notch_cov) + cap_dark[2] * notch_cov

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(np.round(red), 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(np.round(grn), 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(np.round(blu), 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(np.round(alpha * 255.0), 0, 255).astype(np.uint8)
    return rgba


def render_studio_backdrop(width: int = 1440, height: int = 320) -> np.ndarray:
    """Render a subtle, neutral charcoal dB/frequency calibration grid backdrop."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = xx / float(width)
    ny = yy / float(height)

    # Neutral dark graphite surface (#141518 -> #181A1E)
    c = 20.0 + 4.0 * (1.0 - ny)
    r = c.copy()
    g = c + 1.0
    b = c + 3.0

    # Subtle logarithmic vertical & horizontal calibration hairlines
    for lp in (0.08, 0.18, 0.30, 0.44, 0.58, 0.72, 0.85, 0.94):
        gv = np.exp(-(((nx - lp) / 0.0007) ** 2))
        r += gv * 10.0
        g += gv * 11.0
        b += gv * 12.0
    for db_y in (0.25, 0.50, 0.75):
        gh = np.exp(-(((ny - db_y) / 0.003) ** 2))
        r += gh * 8.0
        g += gh * 9.0
        b += gh * 10.0

    # Understated studio amber reference response curve
    curve = (
        0.58
        - 0.16 * np.exp(-(((nx - 0.18) / 0.06) ** 2))
        - 0.20 * np.exp(-(((nx - 0.48) / 0.09) ** 2))
        - 0.14 * np.exp(-(((nx - 0.78) / 0.07) ** 2))
    )
    fill = np.clip((ny - curve) / 0.42, 0.0, 1.0)
    under = (ny >= curve).astype(np.float32) * ((1.0 - fill) ** 2.0)
    r += under * 22.0
    g += under * 11.0
    b += under * 4.0

    trace = np.exp(-(((ny - curve) / 0.006) ** 2))
    r += trace * 145.0
    g += trace * 72.0
    b += trace * 26.0

    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = np.clip(np.round(r), 0, 255).astype(np.uint8)
    rgb[..., 1] = np.clip(np.round(g), 0, 255).astype(np.uint8)
    rgb[..., 2] = np.clip(np.round(b), 0, 255).astype(np.uint8)
    return rgb


def write_ico(out_path: Path, png_map: dict[int, bytes]) -> None:
    sizes = [16, 32, 48, 64, 128, 256]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = []
    blobs = []
    for sz in sizes:
        data = png_map[sz]
        w_byte = 0 if sz == 256 else sz
        h_byte = 0 if sz == 256 else sz
        entries.append(struct.pack("<BBBBHHII", w_byte, h_byte, 0, 0, 1, 32, len(data), offset))
        blobs.append(data)
        offset += len(data)
    out_path.write_bytes(header + b"".join(entries) + b"".join(blobs))


def write_icns(out_path: Path, png_128: bytes, png_256: bytes) -> None:
    chunks = [
        b"ic07" + struct.pack(">I", 8 + len(png_128)) + png_128,
        b"ic08" + struct.pack(">I", 8 + len(png_256)) + png_256,
    ]
    body = b"".join(chunks)
    out_path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


def write_logo_svg(out_path: Path) -> None:
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">',
        '  <rect x="2" y="2" width="60" height="60" rx="14" fill="#181A1E"'
        ' stroke="#343840" stroke-width="1.5"/>',
        '  <rect x="10" y="10" width="44" height="44" rx="8" fill="#0E1013"'
        ' stroke="#24272D" stroke-width="1"/>',
        '  <rect x="17" y="16" width="4" height="32" rx="2" fill="#08090B"/>',
        '  <rect x="25.6" y="16" width="4" height="32" rx="2" fill="#08090B"/>',
        '  <rect x="34.4" y="16" width="4" height="32" rx="2" fill="#08090B"/>',
        '  <rect x="43" y="16" width="4" height="32" rx="2" fill="#08090B"/>',
        '  <rect x="17.5" y="34" width="3" height="14" rx="1.5" fill="#FF7A2F"/>',
        '  <rect x="26.1" y="23" width="3" height="25" rx="1.5" fill="#FF7A2F"/>',
        '  <rect x="34.9" y="29" width="3" height="19" rx="1.5" fill="#FF7A2F"/>',
        '  <rect x="43.5" y="20" width="3" height="28" rx="1.5" fill="#FF7A2F"/>',
        '  <rect x="15.5" y="32" width="7" height="3.2" rx="1" fill="#E8ECF2"/>',
        '  <rect x="24.1" y="21" width="7" height="3.2" rx="1" fill="#E8ECF2"/>',
        '  <rect x="32.9" y="27" width="7" height="3.2" rx="1" fill="#E8ECF2"/>',
        '  <rect x="41.5" y="18" width="7" height="3.2" rx="1" fill="#FF9E5E"/>',
        "</svg>",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = render_master_icon(512)

    png_map: dict[int, bytes] = {}
    for sz in (256, 128, 64, 48, 32, 16):
        png_map[sz] = _encode_png(_downsample(master, sz))

    (ASSETS / "icon-256.png").write_bytes(png_map[256])
    (ASSETS / "icon-128.png").write_bytes(png_map[128])
    (ASSETS / "icon-64.png").write_bytes(png_map[64])
    (ASSETS / "favicon.png").write_bytes(png_map[32])

    write_ico(ASSETS / "icon.ico", png_map)
    write_icns(ASSETS / "icon.icns", png_map[128], png_map[256])
    write_logo_svg(ASSETS / "logo.svg")

    backdrop_rgb = render_studio_backdrop(1440, 320)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        with tempfile.TemporaryDirectory() as td:
            tmp_png = Path(td) / "backdrop.png"
            tmp_png.write_bytes(_encode_png(backdrop_rgb))
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(tmp_png),
                    "-c:v",
                    "libwebp",
                    "-quality",
                    "92",
                    str(ASSETS / "studio-backdrop.webp"),
                ],
                check=True,
            )
    print("Generated all studio assets in", ASSETS)


if __name__ == "__main__":
    main()
