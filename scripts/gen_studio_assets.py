"""Analytical SDF (Signed Distance Field) pro-audio asset generator for FL-MCP Studio.

Renders industrial-grade, zero-hallucination DAW hardware icons and FFT spectrum backdrops:
- src/fl_mcp/assets/logo.svg
- src/fl_mcp/assets/icon-256.png, icon-128.png, icon-64.png, favicon.png
- src/fl_mcp/assets/icon.ico (multi-size Windows icon: 16, 32, 48, 64, 128, 256)
- src/fl_mcp/assets/icon.icns (macOS iconset: ic07 128, ic08 256)
- src/fl_mcp/assets/studio-backdrop.webp (1440x320 72-band FFT + parametric EQ console backdrop)
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


def _sd_rounded_box(
    px: np.ndarray,
    py: np.ndarray,
    cx: float,
    cy: float,
    half_w: float,
    half_h: float,
    rad: float,
) -> np.ndarray:
    """Exact signed distance from (px, py) to an axis-aligned rounded rectangle."""
    qx = np.abs(px - cx) - (half_w - rad)
    qy = np.abs(py - cy) - (half_h - rad)
    out_d = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    in_d = np.minimum(np.maximum(qx, qy), 0.0)
    return out_d + in_d - rad


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
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


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
    return np.clip(
        np.round(zoom(rgba.astype(np.float32), (scale, scale, 1.0), order=1)), 0, 255
    ).astype(np.uint8)


def render_master_icon(size: int = 512) -> np.ndarray:
    """Render a 512x512 sub-pixel SDF DAW Console Fader-Bridge & Segmented Meter Ring icon."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    nx = (xx - cx) / (size / 2.0)
    ny = (yy - cy) / (size / 2.0)
    r = np.hypot(nx, ny)
    theta = np.arctan2(ny, nx)
    px_w = 2.0 / size

    # 1. Outer Continuous-Curvature Squircle Chassis (superellipse n=4.6, radius 0.92)
    pad = 0.92
    sq = (np.abs(nx / pad) ** 4.6 + np.abs(ny / pad) ** 4.6) ** (1.0 / 4.6)
    alpha = 1.0 - _smoothstep(1.0 - px_w * 1.2, 1.0 + px_w * 1.2, sq)

    # Anodized dark obsidian chassis lighting (top-left key light + subtle horizontal brushed grain)
    key_light = np.clip(-0.55 * nx - 0.78 * ny + 0.32, -1.0, 1.0)
    grain = 0.45 * np.sin(yy * 1.4) + 0.25 * np.cos(yy * 3.1)
    base = 16.0 + 11.0 * key_light - 5.5 * (r**1.35) + grain
    red = base * 0.78
    grn = base * 1.00
    blu = base * 1.38

    # Machined titanium outer bevel rim
    rim = np.exp(-(((sq - 0.976) / 0.016) ** 2))
    rim_spec = np.clip(-0.62 * nx - 0.78 * ny + 0.22, -0.35, 1.0)
    red += rim * (38.0 * rim_spec + 18.0)
    grn += rim * (46.0 * rim_spec + 22.0)
    blu += rim * (62.0 * rim_spec + 31.0)

    # 2. Recessed circular console well (r < 0.735)
    well_mask = 1.0 - _smoothstep(0.735 - px_w, 0.735 + px_w, r)
    well_rim = np.exp(-(((r - 0.735) / 0.009) ** 2)) * (-0.5 * nx - 0.7 * ny + 0.3)
    red = red * (1.0 - 0.22 * well_mask) + well_rim * 24.0
    grn = grn * (1.0 - 0.22 * well_mask) + well_rim * 30.0
    blu = blu * (1.0 - 0.22 * well_mask) + well_rim * 40.0

    # 3. Sleek Segmented 270-degree LED Meter Collar (r = 0.655, thickness = 0.040)
    # Angles mapped so 0.0 is 135 deg (bottom-left) sweeping 270 deg clockwise to 45 deg (bottom-right)
    deg = (np.degrees(theta) - 135.0) % 360.0
    in_sweep = (deg <= 270.0).astype(np.float32)
    ring_sd = np.abs(r - 0.655) - 0.021
    ring_cov = (1.0 - _smoothstep(-px_w, px_w, ring_sd)) * in_sweep

    # 24 discrete curved segments along the 270-deg sweep
    n_arc_segs = 24
    seg_pos = (deg / 270.0) * n_arc_segs
    seg_idx = np.floor(seg_pos)
    seg_frac = seg_pos - seg_idx
    gap_mask = _smoothstep(0.06, 0.13, seg_frac) * (1.0 - _smoothstep(0.87, 0.94, seg_frac))
    seg_cov = ring_cov * gap_mask

    glow_r = np.zeros_like(red)
    glow_g = np.zeros_like(grn)
    glow_b = np.zeros_like(blu)

    # Lit segments (0..18): smooth transition from Studio Cyan (#2BD4B5) -> Amber (#FF7A2F) -> Gold (#FFB84D)
    lit_mask = (seg_idx <= 18).astype(np.float32) * seg_cov
    peak_mask = (seg_idx == 22).astype(np.float32) * seg_cov
    unlit_mask = ((seg_idx > 18) & (seg_idx != 22)).astype(np.float32) * seg_cov

    u = np.clip(deg / (270.0 * (18.0 / n_arc_segs)), 0.0, 1.0)
    # 2-stop gradient: Cyan (43,212,181) -> Amber (255,122,47) -> Gold (255,186,76)
    c_r = np.where(u < 0.45, 43.0 + (255.0 - 43.0) * (u / 0.45), 255.0)
    c_g = np.where(
        u < 0.45,
        212.0 + (122.0 - 212.0) * (u / 0.45),
        122.0 + (186.0 - 122.0) * ((u - 0.45) / 0.55),
    )
    c_b = np.where(
        u < 0.45,
        181.0 + (47.0 - 181.0) * (u / 0.45),
        47.0 + (76.0 - 47.0) * ((u - 0.45) / 0.55),
    )

    red = red * (1.0 - seg_cov) + lit_mask * c_r + peak_mask * 255.0 + unlit_mask * 36.0
    grn = grn * (1.0 - seg_cov) + lit_mask * c_g + peak_mask * 77.0 + unlit_mask * 46.0
    blu = blu * (1.0 - seg_cov) + lit_mask * c_b + peak_mask * 94.0 + unlit_mask * 64.0

    glow_r += lit_mask * c_r * 0.50 + peak_mask * 255.0 * 0.45
    glow_g += lit_mask * c_g * 0.50 + peak_mask * 77.0 * 0.45
    glow_b += lit_mask * c_b * 0.50 + peak_mask * 94.0 * 0.45

    # 4. Sculpted Inner Medallion (r < 0.555) with machined metallic collar
    med_mask = 1.0 - _smoothstep(0.555 - px_w, 0.555 + px_w, r)
    conical = 0.50 + 0.24 * np.cos(2.0 * (theta + 0.8)) + 0.08 * np.cos(4.0 * (theta - 0.3))
    med_r = 12.0 + 12.0 * conical - 4.0 * (r / 0.55) ** 2
    med_g = 16.0 + 15.0 * conical - 5.0 * (r / 0.55) ** 2
    med_b = 24.0 + 21.0 * conical - 7.0 * (r / 0.55) ** 2
    red = red * (1.0 - med_mask) + med_r * med_mask
    grn = grn * (1.0 - med_mask) + med_g * med_mask
    blu = blu * (1.0 - med_mask) + med_b * med_mask

    collar = np.exp(-(((r - 0.555) / 0.007) ** 2)) * (0.45 + 0.55 * np.cos(theta + 2.2))
    red += collar * 52.0
    grn += collar * 62.0
    blu += collar * 80.0

    # 5. Four Precision Studio Mixer Fader / Step-Sequencer Columns inside the Medallion
    # Symmetrically spaced at x = -0.24, -0.08, +0.08, +0.24
    # Each has: a recessed dark track slot, a glowing active signal bar, and a tactile metallic fader cap
    fader_specs = [
        (-0.24, -0.02, (43.0, 212.0, 181.0)),  # Ch 1: Cyan-Teal
        (-0.08, -0.20, (255.0, 122.0, 47.0)),  # Ch 2: High FL Amber peak
        (0.08, -0.09, (255.0, 168.0, 68.0)),  # Ch 3: Warm Gold-Amber
        (0.24, -0.24, (255.0, 122.0, 47.0)),  # Ch 4: Full Master Amber
    ]
    slot_bot = 0.25
    slot_top = -0.27
    slot_cy = (slot_bot + slot_top) * 0.5
    slot_hh = (slot_bot - slot_top) * 0.5

    for fx, level_y, fcol in fader_specs:
        # Recessed dark vertical fader track
        sd_track = _sd_rounded_box(nx, ny, fx, slot_cy, 0.040, slot_hh, 0.038)
        tr_cov = 1.0 - _smoothstep(-px_w, px_w, sd_track)
        red = red * (1.0 - tr_cov) + 7.0 * tr_cov
        grn = grn * (1.0 - tr_cov) + 9.0 * tr_cov
        blu = blu * (1.0 - tr_cov) + 14.0 * tr_cov

        # Illuminated active signal bar from slot_bot up to level_y
        bar_cy = (slot_bot + level_y) * 0.5
        bar_hh = max(0.04, (slot_bot - level_y) * 0.5)
        sd_bar = _sd_rounded_box(nx, ny, fx, bar_cy, 0.036, bar_hh, 0.034)
        bar_cov = 1.0 - _smoothstep(-px_w, px_w, sd_bar)
        # Vertical brightness gradient along the lit bar
        v_grad = np.clip((slot_bot - ny) / max(0.01, slot_bot - level_y), 0.0, 1.0)
        b_r = fcol[0] * (0.72 + 0.28 * v_grad)
        b_g = fcol[1] * (0.72 + 0.28 * v_grad)
        b_b = fcol[2] * (0.72 + 0.28 * v_grad)
        red = red * (1.0 - bar_cov) + b_r * bar_cov
        grn = grn * (1.0 - bar_cov) + b_g * bar_cov
        blu = blu * (1.0 - bar_cov) + b_b * bar_cov

        glow_r += bar_cov * fcol[0] * 0.55
        glow_g += bar_cov * fcol[1] * 0.55
        glow_b += bar_cov * fcol[2] * 0.55

        # Crisp bright LED cap at level_y
        sd_cap = _sd_rounded_box(nx, ny, fx, level_y + 0.018, 0.046, 0.018, 0.014)
        cap_cov = 1.0 - _smoothstep(-px_w, px_w, sd_cap)
        red = red * (1.0 - cap_cov) + 255.0 * cap_cov
        grn = grn * (1.0 - cap_cov) + 238.0 * cap_cov
        blu = blu * (1.0 - cap_cov) + 210.0 * cap_cov

    # Optical phosphor bloom
    sigma = size * 0.016
    red += gaussian_filter(glow_r, sigma=sigma) * 0.62
    grn += gaussian_filter(glow_g, sigma=sigma) * 0.62
    blu += gaussian_filter(glow_b, sigma=sigma) * 0.62

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(np.round(red), 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(np.round(grn), 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(np.round(blu), 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(np.round(alpha * 255.0), 0, 255).astype(np.uint8)
    return rgba


def render_studio_backdrop(width: int = 1440, height: int = 320) -> np.ndarray:
    """Render a 1440x320 72-band 1/6-octave FFT Spectrum Analyzer + Mastering EQ backdrop."""
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = xx / float(width)
    ny = yy / float(height)

    # Anodized dark obsidian console surface with subtle top-lit radial glow
    r = 7.0 + 5.0 * (1.0 - ny) + 4.0 * np.exp(-(((nx - 0.42) / 0.34) ** 2))
    g = 10.0 + 7.0 * (1.0 - ny) + 5.0 * np.exp(-(((nx - 0.42) / 0.34) ** 2))
    b = 15.0 + 10.0 * (1.0 - ny) + 8.0 * np.exp(-(((nx - 0.42) / 0.34) ** 2))

    # Calibrated logarithmic frequency hairlines & dB grid
    for lp in (0.06, 0.14, 0.24, 0.36, 0.48, 0.60, 0.72, 0.84, 0.94):
        grid_v = np.exp(-(((nx - lp) / 0.0008) ** 2))
        r += grid_v * 13.0
        g += grid_v * 18.0
        b += grid_v * 26.0
    for db_y in (0.20, 0.40, 0.60, 0.80):
        grid_h = np.exp(-(((ny - db_y) / 0.0032) ** 2))
        r += grid_h * 10.0
        g += grid_h * 14.0
        b += grid_h * 21.0

    # Musical FFT spectrum envelope helper (evaluated both continuously and per-column)
    def _env(x: np.ndarray) -> np.ndarray:
        return (
            0.64
            - 0.26 * np.exp(-(((x - 0.11) / 0.038) ** 2))  # 55 Hz Kick fundamental
            - 0.19 * np.exp(-(((x - 0.19) / 0.045) ** 2))  # 110 Hz Bass body
            - 0.29 * np.exp(-(((x - 0.42) / 0.085) ** 2))  # 500 Hz - 1 kHz Chord/Lead
            - 0.18 * np.exp(-(((x - 0.63) / 0.065) ** 2))  # 3 kHz Presence
            - 0.22 * np.exp(-(((x - 0.80) / 0.055) ** 2))  # 8-12 kHz Hi-hat / Air
            + 0.025 * np.sin(x * 44.0)
            + 0.018 * np.cos(x * 78.0)
        )

    fft_env = _env(nx)

    # 72 discrete 1/6-octave vertical FFT analyzer bars underneath fft_env
    n_bars = 72
    bar_pos = nx * n_bars
    bar_idx = np.floor(bar_pos)
    bar_frac = bar_pos - bar_idx
    bar_mask_x = _smoothstep(0.12, 0.22, bar_frac) * (1.0 - _smoothstep(0.78, 0.88, bar_frac))

    # Quantize envelope height per bar + deterministic harmonic ripple (flat horizontal tops)
    bar_center_x = (bar_idx + 0.5) / n_bars
    ripple = 0.026 * np.sin(bar_idx * 1.9) + 0.016 * np.cos(bar_idx * 3.7)
    bar_top_y = np.clip(_env(bar_center_x) + ripple, 0.18, 0.92)

    # Horizontal LED segments inside each vertical FFT bar
    n_rows = 36
    row_frac = (ny * n_rows) - np.floor(ny * n_rows)
    row_mask_y = _smoothstep(0.10, 0.22, row_frac) * (1.0 - _smoothstep(0.78, 0.90, row_frac))

    in_bar = (ny >= bar_top_y).astype(np.float32) * bar_mask_x * row_mask_y
    v_norm = np.clip((1.0 - ny) / 0.82, 0.0, 1.0)
    # Color bars from Teal at bottom to Amber/Gold near peak
    bar_r = 43.0 + (255.0 - 43.0) * (v_norm**0.85)
    bar_g = 195.0 - 55.0 * (v_norm**1.2)
    bar_b = 175.0 * ((1.0 - v_norm) ** 1.3) + 40.0

    r += in_bar * bar_r * 0.42
    g += in_bar * bar_g * 0.42
    b += in_bar * bar_b * 0.42

    # Peak-hold cap above each FFT bar
    peak_y = bar_top_y - 0.025 - 0.015 * np.abs(np.sin(bar_idx * 0.9))
    peak_cap = np.exp(-(((ny - peak_y) / 0.0055) ** 2)) * bar_mask_x
    r += peak_cap * 255.0 * 0.55
    g += peak_cap * 150.0 * 0.55
    b += peak_cap * 65.0 * 0.55

    # Continuous Mastering Parametric EQ Curve (Amber) + Side-chain Curve (Teal)
    curve_teal = (
        0.66
        - 0.18 * np.exp(-(((nx - 0.26) / 0.072) ** 2))
        - 0.16 * np.exp(-(((nx - 0.53) / 0.080) ** 2))
        - 0.21 * np.exp(-(((nx - 0.84) / 0.055) ** 2))
        + 0.022 * np.cos(nx * 20.0)
    )

    trace_a = np.exp(-(((ny - fft_env) / 0.0075) ** 2))
    glow_a = np.exp(-(((ny - fft_env) / 0.028) ** 2))
    r += trace_a * 235.0 + glow_a * 44.0
    g += trace_a * 112.0 + glow_a * 19.0
    b += trace_a * 38.0 + glow_a * 6.0

    trace_t = np.exp(-(((ny - curve_teal) / 0.0065) ** 2))
    glow_t = np.exp(-(((ny - curve_teal) / 0.024) ** 2))
    r += trace_t * 38.0 + glow_t * 6.0
    g += trace_t * 195.0 + glow_t * 32.0
    b += trace_t * 168.0 + glow_t * 28.0

    # Parametric EQ filter control nodes on the Amber curve
    for node_x in (0.11, 0.42, 0.80):
        ix = min(width - 1, max(0, int(round(node_x * width))))
        node_y = float(fft_env[0, ix])
        dist_px = np.hypot(xx - ix, yy - node_y * height)
        halo = np.exp(-((dist_px / 11.0) ** 2))
        ring = np.exp(-(((dist_px - 5.2) / 1.25) ** 2))
        core = 1.0 - _smoothstep(3.6, 4.8, dist_px)
        r = r * (1.0 - core) + 11.0 * core + halo * 58.0 + ring * 245.0
        g = g * (1.0 - core) + 15.0 * core + halo * 28.0 + ring * 150.0
        b = b * (1.0 - core) + 22.0 * core + halo * 9.0 + ring * 70.0

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
      <stop offset="1" stop-color="#090C12"/>
    </linearGradient>
    <linearGradient id="medallion" x1="16" y1="14" x2="48" y2="50" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#1E2738"/>
      <stop offset="1" stop-color="#0B0F17"/>
    </linearGradient>
    <linearGradient id="arc" x1="12" y1="48" x2="50" y2="16" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="#2BD4B5"/>
      <stop offset="0.5" stop-color="#FF7A2F"/>
      <stop offset="1" stop-color="#FFB84D"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="url(#chassis)" stroke="#2E3B52" stroke-width="1.5"/>
  <path d="M17.2 46.8A21 21 0 1 1 46.8 46.8" stroke="#222B3C" stroke-width="3.2" stroke-linecap="round"/>
  <path d="M17.2 46.8A21 21 0 1 1 48.5 19.5" stroke="url(#arc)" stroke-width="3.2" stroke-linecap="round" stroke-dasharray="3.6 1.5"/>
  <circle cx="32" cy="32" r="17.5" fill="url(#medallion)" stroke="#384762" stroke-width="1.2"/>
  <rect x="23.2" y="23" width="2.8" height="17" rx="1.4" fill="#07090E"/>
  <rect x="28.1" y="23" width="2.8" height="17" rx="1.4" fill="#07090E"/>
  <rect x="33.0" y="23" width="2.8" height="17" rx="1.4" fill="#07090E"/>
  <rect x="37.9" y="23" width="2.8" height="17" rx="1.4" fill="#07090E"/>
  <rect x="23.2" y="31" width="2.8" height="9" rx="1.4" fill="#2BD4B5"/>
  <rect x="28.1" y="25" width="2.8" height="15" rx="1.4" fill="#FF7A2F"/>
  <rect x="33.0" y="28.5" width="2.8" height="11.5" rx="1.4" fill="#FFA844"/>
  <rect x="37.9" y="24" width="2.8" height="16" rx="1.4" fill="#FF7A2F"/>
  <rect x="22.8" y="30.2" width="3.6" height="1.6" rx="0.8" fill="#FFF0D6"/>
  <rect x="27.7" y="24.2" width="3.6" height="1.6" rx="0.8" fill="#FFF0D6"/>
  <rect x="32.6" y="27.7" width="3.6" height="1.6" rx="0.8" fill="#FFF0D6"/>
  <rect x="37.5" y="23.2" width="3.6" height="1.6" rx="0.8" fill="#FFF0D6"/>
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
