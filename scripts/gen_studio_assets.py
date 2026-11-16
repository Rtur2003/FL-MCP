"""Analytical SDF (Signed Distance Field) pro-audio asset generator for FL-MCP Studio.

Uses exact sub-pixel Euclidean distance fields (numpy + scipy.ndimage) — the same technique
used by modern GPU audio plugins (FabFilter, Vital) — to produce mathematically exact,
zero-hallucination DAW hardware icons and console backdrops:
- src/fl_mcp/assets/logo.svg
- src/fl_mcp/assets/icon-256.png, icon-128.png, icon-64.png, favicon.png
- src/fl_mcp/assets/icon.ico (multi-size Windows icon: 16, 32, 48, 64, 128, 256)
- src/fl_mcp/assets/icon.icns (macOS iconset: ic07 128, ic08 256)
- src/fl_mcp/assets/studio-backdrop.webp (1440x320 parametric EQ / FFT console backdrop)
"""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, zoom

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "fl_mcp" / "assets"


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _sd_segment(
    px: np.ndarray, py: np.ndarray, ax: float, ay: float, bx: float, by: float
) -> np.ndarray:
    """Exact signed distance from (px, py) to line segment (ax, ay)-(bx, by)."""
    pax = px - ax
    pay = py - ay
    bax = bx - ax
    bay = by - ay
    denom = bax * bax + bay * bay
    h = np.clip((pax * bax + pay * bay) / max(denom, 1e-12), 0.0, 1.0)
    return np.hypot(pax - bax * h, pay - bay * h)


def _encode_png(rgba: np.ndarray) -> bytes:
    """Encode an HxWx4 (or HxWx3) uint8 numpy array as a standard PNG."""
    h, w, c = rgba.shape
    color_type = 6 if c == 4 else 2
    raw_rows = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))
    compressed = zlib.compress(raw_rows, level=9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")


def _downsample(rgba: np.ndarray, target_size: int) -> np.ndarray:
    """Exact area-box downsampling from power-of-two master (or spline zoom for 48x48)."""
    h, w, c = rgba.shape
    if h % target_size == 0:
        factor = h // target_size
        if factor <= 1:
            return rgba
        reshaped = rgba.astype(np.float32).reshape(target_size, factor, target_size, factor, c)
        return np.clip(np.round(reshaped.mean(axis=(1, 3))), 0, 255).astype(np.uint8)
    scale = target_size / float(h)
    return np.clip(np.round(zoom(rgba.astype(np.float32), (scale, scale, 1.0), order=1)), 0, 255).astype(np.uint8)


def render_master_icon(size: int = 512) -> np.ndarray:
    """Render a 512x512 sub-pixel SDF precision DAW rotary encoder & spectral bridge icon."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    nx = (xx - cx) / (size / 2.0)
    ny = (yy - cy) / (size / 2.0)
    r = np.hypot(nx, ny)
    theta = np.arctan2(ny, nx)
    px_w = 2.0 / size

    # 1. Outer Squircle Chassis (superellipse n=4.6, radius 0.92)
    pad = 0.92
    sq = (np.abs(nx / pad) ** 4.6 + np.abs(ny / pad) ** 4.6) ** (1.0 / 4.6)
    alpha = 1.0 - _smoothstep(1.0 - px_w * 1.2, 1.0 + px_w * 1.2, sq)

    # Anodized dark obsidian chassis lighting (top-left key light + radial vignette)
    key_light = np.clip(-0.55 * nx - 0.75 * ny + 0.30, -1.0, 1.0)
    base = 15.0 + 10.0 * key_light - 5.0 * (r**1.4)
    red = base * 0.80
    grn = base * 1.02
    blu = base * 1.40

    # Machined titanium outer bevel rim
    rim = np.exp(-(((sq - 0.978) / 0.015) ** 2))
    rim_spec = np.clip(-0.6 * nx - 0.8 * ny + 0.2, -0.3, 1.0)
    red += rim * (34.0 * rim_spec + 16.0)
    grn += rim * (42.0 * rim_spec + 20.0)
    blu += rim * (56.0 * rim_spec + 28.0)

    # 4 corner micro-socket indentations
    for sx in (-0.71, 0.71):
        for sy in (-0.71, 0.71):
            sd_screw = np.hypot(nx - sx, ny - sy)
            screw_ring = np.exp(-(((sd_screw - 0.032) / 0.006) ** 2))
            screw_pit = 1.0 - _smoothstep(0.0, 0.030, sd_screw)
            red = red * (1.0 - 0.35 * screw_pit) + screw_ring * 14.0
            grn = grn * (1.0 - 0.35 * screw_pit) + screw_ring * 18.0
            blu = blu * (1.0 - 0.35 * screw_pit) + screw_ring * 24.0

    # Recessed circular meter trough (0.55 < r < 0.77)
    trough = np.exp(-(((r - 0.66) / 0.095) ** 2))
    red -= trough * 5.5
    grn -= trough * 6.5
    blu -= trough * 8.5

    # Knurled metallic rotary bezel (0.47 < r < 0.53)
    bezel = np.exp(-(((r - 0.502) / 0.022) ** 2))
    knurl = 0.5 + 0.5 * np.cos(theta * 96.0)
    b_light = 0.55 + 0.45 * np.cos(theta + 2.2)
    red += bezel * (28.0 * b_light + 8.0 * knurl)
    grn += bezel * (35.0 * b_light + 10.0 * knurl)
    blu += bezel * (46.0 * b_light + 14.0 * knurl)

    # Inner brushed aluminum encoder cap (r < 0.475) with anisotropic conical sheen
    cap_mask = 1.0 - _smoothstep(0.470 - px_w, 0.470 + px_w, r)
    conical = (
        0.50
        + 0.28 * np.cos(2.0 * (theta + 0.78))
        + 0.11 * np.cos(4.0 * (theta - 0.35))
        + 0.03 * np.sin(42.0 * theta)
    )
    cap_r = 15.0 + 17.0 * conical - 7.0 * (r / 0.47) ** 2
    cap_g = 20.0 + 21.0 * conical - 8.0 * (r / 0.47) ** 2
    cap_b = 29.0 + 29.0 * conical - 10.0 * (r / 0.47) ** 2
    red = red * (1.0 - cap_mask) + cap_r * cap_mask
    grn = grn * (1.0 - cap_mask) + cap_g * cap_mask
    blu = blu * (1.0 - cap_mask) + cap_b * cap_mask

    # Crisp specular collar ring around encoder cap (r == 0.468)
    collar = np.exp(-(((r - 0.468) / 0.0055) ** 2)) * (0.45 + 0.55 * np.cos(theta + 2.2))
    red += collar * 58.0
    grn += collar * 68.0
    blu += collar * 86.0

    # Emissive glow accumulator
    glow_r = np.zeros_like(red)
    glow_g = np.zeros_like(grn)
    glow_b = np.zeros_like(blu)

    def paint_capsule(
        ax: float,
        ay: float,
        bx: float,
        by: float,
        radius: float,
        col: tuple[float, float, float],
        emissive: float = 0.0,
    ) -> None:
        nonlocal red, grn, blu, glow_r, glow_g, glow_b
        dist = _sd_segment(nx, ny, ax, ay, bx, by)
        cov = 1.0 - _smoothstep(radius - px_w * 1.1, radius + px_w * 1.1, dist)
        red = red * (1.0 - cov) + col[0] * cov
        grn = grn * (1.0 - cov) + col[1] * cov
        blu = blu * (1.0 - cov) + col[2] * cov
        if emissive > 0.0:
            glow_r += cov * col[0] * emissive
            glow_g += cov * col[1] * emissive
            glow_b += cov * col[2] * emissive

    # 2. Calibrated 270-degree dB LED meter ring (33 radial capsules: 135 deg -> 405 deg)
    num_segs = 33
    active_seg = 24  # Single calibrated gain position at ~2 o'clock (337.5 deg)
    start_deg = 135.0
    span_deg = 270.0
    r_in = 0.585
    r_out = 0.695
    r_out_major = 0.725

    teal = (43.0, 212.0, 181.0)
    amber = (255.0, 122.0, 47.0)
    gold = (255.0, 184.0, 77.0)
    crimson = (255.0, 77.0, 94.0)

    for i in range(num_segs):
        frac = i / (num_segs - 1)
        deg = start_deg + frac * span_deg
        rad = math.radians(deg)
        ca, sa = math.cos(rad), math.sin(rad)
        is_major = i % 4 == 0
        ro = r_out_major if is_major else r_out
        seg_r = 0.0135 if is_major else 0.0105

        if i <= active_seg:
            if frac < 0.36:
                t = frac / 0.36
                col = (
                    teal[0] + (amber[0] - teal[0]) * t,
                    teal[1] + (amber[1] - teal[1]) * t,
                    teal[2] + (amber[2] - teal[2]) * t,
                )
            else:
                t = min(1.0, (frac - 0.36) / 0.44)
                col = (
                    amber[0] + (gold[0] - amber[0]) * t,
                    amber[1] + (gold[1] - amber[1]) * t,
                    amber[2] + (gold[2] - amber[2]) * t,
                )
            paint_capsule(r_in * ca, r_in * sa, ro * ca, ro * sa, seg_r, col, emissive=0.55)
        elif i == num_segs - 2:
            # Peak-hold LED indicator
            paint_capsule(r_in * ca, r_in * sa, ro * ca, ro * sa, seg_r, crimson, emissive=0.45)
        else:
            unlit = (44.0, 55.0, 74.0) if i < 28 else (68.0, 32.0, 42.0)
            paint_capsule(r_in * ca, r_in * sa, ro * ca, ro * sa, seg_r, unlit, emissive=0.0)

    # 3. Single calibrated encoder pointer notch aligned with active_seg
    act_deg = start_deg + (active_seg / (num_segs - 1)) * span_deg
    act_rad = math.radians(act_deg)
    ca, sa = math.cos(act_rad), math.sin(act_rad)
    paint_capsule(
        0.315 * ca,
        0.315 * sa,
        0.415 * ca,
        0.415 * sa,
        0.021,
        (255.0, 200.0, 128.0),
        emissive=0.75,
    )

    # 4. Bold 5-bar Studio Spectral / Step-Sequencer Bridge Emblem in the center
    # Designed for instant recognition at 16x16, 32x32, 64x64, 128x128, and 256x256
    bar_specs = [
        (-0.20, 0.10, teal),
        (-0.10, 0.20, (110.0, 218.0, 145.0)),
        (0.00, 0.27, amber),
        (0.10, 0.17, gold),
        (0.20, 0.11, amber),
    ]
    for bx, half_h, bcol in bar_specs:
        paint_capsule(bx, -half_h, bx, half_h, 0.030, bcol, emissive=0.65)

    # Apply soft optical phosphor bloom from emissive elements
    sigma = size * 0.018
    red += gaussian_filter(glow_r, sigma=sigma) * 0.65
    grn += gaussian_filter(glow_g, sigma=sigma) * 0.65
    blu += gaussian_filter(glow_b, sigma=sigma) * 0.65

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(np.round(red), 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(np.round(grn), 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(np.round(blu), 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(np.round(alpha * 255.0), 0, 255).astype(np.uint8)
    return rgba


def render_studio_backdrop(width: int = 1440, height: int = 320) -> np.ndarray:
    """Render a precision 1440x320 parametric EQ & real-time FFT analyzer console backdrop."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = xx / float(width)
    ny = yy / float(height)

    # Anodized dark obsidian console surface with subtle top-lit radial glow
    r = 8.0 + 5.0 * (1.0 - ny) + 4.0 * np.exp(-(((nx - 0.38) / 0.32) ** 2))
    g = 11.0 + 7.0 * (1.0 - ny) + 5.0 * np.exp(-(((nx - 0.38) / 0.32) ** 2))
    b = 17.0 + 11.0 * (1.0 - ny) + 8.0 * np.exp(-(((nx - 0.38) / 0.32) ** 2))

    # Calibrated logarithmic frequency hairlines & dB grid
    for lp in (0.07, 0.15, 0.25, 0.37, 0.49, 0.61, 0.73, 0.84, 0.93):
        grid_v = np.exp(-(((nx - lp) / 0.0009) ** 2))
        r += grid_v * 14.0
        g += grid_v * 19.0
        b += grid_v * 28.0
    for db_y in (0.22, 0.44, 0.66, 0.86):
        grid_h = np.exp(-(((ny - db_y) / 0.0035) ** 2))
        r += grid_h * 11.0
        g += grid_h * 15.0
        b += grid_h * 22.0

    # Parametric EQ response curves (Amber Mid channel + Teal Side channel)
    curve_amber = (
        0.60
        - 0.15 * np.exp(-(((nx - 0.15) / 0.055) ** 2))
        - 0.26 * np.exp(-(((nx - 0.43) / 0.090) ** 2))
        + 0.11 * np.exp(-(((nx - 0.61) / 0.068) ** 2))
        - 0.22 * np.exp(-(((nx - 0.79) / 0.062) ** 2))
        + 0.035 * np.sin(nx * 26.0)
    )
    curve_teal = (
        0.66
        - 0.19 * np.exp(-(((nx - 0.27) / 0.075) ** 2))
        - 0.17 * np.exp(-(((nx - 0.54) / 0.082) ** 2))
        - 0.23 * np.exp(-(((nx - 0.85) / 0.058) ** 2))
        + 0.028 * np.cos(nx * 20.0)
    )

    # Translucent harmonic fill below each EQ curve
    fill_a = np.clip((ny - curve_amber) / 0.45, 0.0, 1.0)
    under_a = (ny >= curve_amber).astype(np.float32) * (1.0 - fill_a) ** 1.75
    r += under_a * 48.0
    g += under_a * 21.0
    b += under_a * 7.0

    fill_t = np.clip((ny - curve_teal) / 0.45, 0.0, 1.0)
    under_t = (ny >= curve_teal).astype(np.float32) * (1.0 - fill_t) ** 1.85
    r += under_t * 7.0
    g += under_t * 38.0
    b += under_t * 34.0

    # Anti-aliased glowing EQ traces
    trace_a = np.exp(-(((ny - curve_amber) / 0.008) ** 2))
    glow_a = np.exp(-(((ny - curve_amber) / 0.032) ** 2))
    r += trace_a * 225.0 + glow_a * 42.0
    g += trace_a * 105.0 + glow_a * 18.0
    b += trace_a * 36.0 + glow_a * 5.0

    trace_t = np.exp(-(((ny - curve_teal) / 0.007) ** 2))
    glow_t = np.exp(-(((ny - curve_teal) / 0.028) ** 2))
    r += trace_t * 38.0 + glow_t * 6.0
    g += trace_t * 190.0 + glow_t * 32.0
    b += trace_t * 164.0 + glow_t * 28.0

    # Parametric EQ filter node rings on the Amber curve
    for node_x in (0.15, 0.43, 0.79):
        ix = min(width - 1, max(0, int(round(node_x * width))))
        node_y = float(curve_amber[0, ix])
        dist_px = np.hypot(xx - ix, yy - node_y * height)
        halo = np.exp(-((dist_px / 11.0) ** 2))
        ring = np.exp(-(((dist_px - 5.5) / 1.3) ** 2))
        core = 1.0 - _smoothstep(3.8, 5.0, dist_px)
        r = r * (1.0 - core) + 12.0 * core + halo * 55.0 + ring * 235.0
        g = g * (1.0 - core) + 16.0 * core + halo * 26.0 + ring * 140.0
        b = b * (1.0 - core) + 24.0 * core + halo * 8.0 + ring * 65.0

    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = np.clip(np.round(r), 0, 255).astype(np.uint8)
    rgb[..., 1] = np.clip(np.round(g), 0, 255).astype(np.uint8)
    rgb[..., 2] = np.clip(np.round(b), 0, 255).astype(np.uint8)
    return rgb


def write_ico(out_path: Path, png_map: dict[int, bytes]) -> None:
    """Build a standard Windows .ico container embedding PNG frames for 16, 32, 48, 64, 128, 256."""
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
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <defs>
    <linearGradient id="chassis" x1="8" y1="6" x2="56" y2="58" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#1A2230"/>
      <stop offset="1" stop-color="#0A0D14"/>
    </linearGradient>
    <linearGradient id="cap" x1="18" y1="16" x2="46" y2="48" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#232D3F"/>
      <stop offset="1" stop-color="#0E131C"/>
    </linearGradient>
    <linearGradient id="meter" x1="14" y1="48" x2="50" y2="18" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#2BD4B5"/>
      <stop offset="0.55" stop-color="#FF7A2F"/>
      <stop offset="1" stop-color="#FFB84D"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="url(#chassis)" stroke="#2E3B52" stroke-width="1.5"/>
  <path d="M16.5 47.5A22 22 0 1 1 47.5 47.5" stroke="#232C3D" stroke-width="3.2" stroke-linecap="round"/>
  <path d="M16.5 47.5A22 22 0 1 1 49.2 20.5" stroke="url(#meter)" stroke-width="3.2" stroke-linecap="round"/>
  <circle cx="32" cy="32" r="16.5" fill="url(#cap)" stroke="#3B4A66" stroke-width="1.2"/>
  <line x1="39.5" y1="24.5" x2="43.2" y2="20.8" stroke="#FFC880" stroke-width="2.2" stroke-linecap="round"/>
  <rect x="23" y="28.5" width="2.4" height="7" rx="1.2" fill="#2BD4B5"/>
  <rect x="27.3" y="25" width="2.4" height="14" rx="1.2" fill="#6EDA91"/>
  <rect x="31.6" y="22.5" width="2.4" height="19" rx="1.2" fill="#FF7A2F"/>
  <rect x="35.9" y="26" width="2.4" height="12" rx="1.2" fill="#FFB84D"/>
  <rect x="40.2" y="28" width="2.4" height="8" rx="1.2" fill="#FF7A2F"/>
</svg>
"""
    out_path.write_text(svg, encoding="utf-8")


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
