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


def write_knob_ring_svg(out_path: Path) -> None:
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">',
        '  <circle cx="32" cy="32" r="28" fill="#141518" stroke="#2A2D34" stroke-width="1.5"/>',
        '  <circle cx="32" cy="32" r="22" fill="#1C1E22" stroke="#343840" stroke-width="1.2"/>',
        '  <path d="M 14.5 46.5 A 23 23 0 1 1 49.5 46.5"'
        ' stroke="#2A2D34" stroke-width="3" stroke-linecap="round"/>',
        '  <path d="M 14.5 46.5 A 23 23 0 1 1 44.0 14.0"'
        ' stroke="#FF7A2F" stroke-width="3" stroke-linecap="round"/>',
        '  <line x1="32" y1="32" x2="41" y2="17"'
        ' stroke="#ECEEF2" stroke-width="2.2" stroke-linecap="round"/>',
        '  <circle cx="32" cy="32" r="3" fill="#FF7A2F"/>',
        "</svg>",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_architecture_svg(out_path: Path) -> None:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 540" fill="none">
  <rect width="1200" height="540" rx="10" fill="#141518"/>
  <rect x="1" y="1" width="1198" height="538" rx="9" stroke="#2A2D34" stroke-width="2"/>
  <!-- Header bar -->
  <rect x="24" y="20" width="1152" height="48" rx="6" fill="#1C1E22" stroke="#2A2D34"/>
  <circle cx="48" cy="44" r="6" fill="#FF7A2F"/>
  <text x="66" y="49" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="15" font-weight="700" letter-spacing="0.06em">FL-MCP STUDIO // AUTONOMOUS 4-LAYER DAW CONTROL ARCHITECTURE</text>
  <text x="1154" y="49" fill="#2CC878" font-family="JetBrains Mono, Consolas, monospace" font-size="12" text-anchor="end">ITU-R BS.1770-4 // ONNX // SYSEX 0x7D</text>

  <!-- Column 1: AI Clients & Desktop App -->
  <rect x="24" y="90" width="250" height="424" rx="8" fill="#1C1E22" stroke="#2A2D34"/>
  <text x="44" y="122" fill="#FF7A2F" font-family="JetBrains Mono, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="0.08em">01 / CLIENTS &amp; STUDIO SHELL</text>
  <rect x="42" y="142" width="214" height="68" rx="6" fill="#141518" stroke="#343840"/>
  <text x="58" y="168" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">AI Coding &amp; Agent Clients</text>
  <text x="58" y="190" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">Claude / Cursor / Windsurf</text>
  <rect x="42" y="226" width="214" height="68" rx="6" fill="#141518" stroke="#343840"/>
  <text x="58" y="252" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">FL-MCP Studio Desktop</text>
  <text x="58" y="274" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">WebView2 / App-Mode / Tk</text>
  <rect x="42" y="310" width="214" height="84" rx="6" fill="#141518" stroke="#343840"/>
  <text x="58" y="336" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Autonomous Rulebooks</text>
  <text x="58" y="358" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">AGENTS.md / CLAUDE.md</text>
  <text x="58" y="376" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">Production / Mix / Master</text>
  <rect x="42" y="410" width="214" height="84" rx="6" fill="#141518" stroke="#343840"/>
  <text x="58" y="436" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">1-Command Onboarding</text>
  <text x="58" y="458" fill="#2CC878" font-family="JetBrains Mono, Consolas, monospace" font-size="11">fl-mcp onboard --auto</text>
  <text x="58" y="476" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">F10 Registry + loopMIDI</text>

  <!-- Column 2: MCP Core & Persistent Daemon -->
  <rect x="314" y="90" width="276" height="424" rx="8" fill="#1C1E22" stroke="#FF7A2F" stroke-width="1.5"/>
  <text x="334" y="122" fill="#FF7A2F" font-family="JetBrains Mono, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="0.08em">02 / PERSISTENT MCP DAEMON</text>
  <rect x="332" y="142" width="240" height="78" rx="6" fill="#141518" stroke="#343840"/>
  <text x="348" y="168" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">14 Focused MCP Tools</text>
  <text x="348" y="190" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">fl_state / fl_produce / fl_mix</text>
  <text x="348" y="206" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">&lt;16.5 KB Zero-Bloat Schema</text>
  <rect x="332" y="236" width="240" height="78" rx="6" fill="#141518" stroke="#343840"/>
  <text x="348" y="262" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Single-Flight MIDI Arbiter</text>
  <text x="348" y="284" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">127.0.0.1:8765 (/mcp &amp; /sse)</text>
  <text x="348" y="300" fill="#2CC878" font-family="JetBrains Mono, Consolas, monospace" font-size="10">Live TX/RX Telemetry Ring</text>
  <rect x="332" y="330" width="240" height="164" rx="6" fill="#141518" stroke="#343840"/>
  <text x="348" y="356" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Offline AI &amp; Audio Vault</text>
  <text x="348" y="380" fill="#FF7A2F" font-family="JetBrains Mono, Consolas, monospace" font-size="11">basic_pitch.onnx (AMT)</text>
  <text x="348" y="400" fill="#FF7A2F" font-family="JetBrains Mono, Consolas, monospace" font-size="11">silero_vad.onnx (Vocal Gate)</text>
  <text x="348" y="420" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">FluidR3_GM + TimGM6mb SF2</text>
  <text x="348" y="440" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">808/909 Kits + 48kHz IRs</text>
  <text x="348" y="460" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">BS.1770-4 LUFS &amp; 6-Zone Masking</text>

  <!-- Column 3: Multi-Path Execution Adapters -->
  <rect x="630" y="90" width="260" height="424" rx="8" fill="#1C1E22" stroke="#2A2D34"/>
  <text x="650" y="122" fill="#FF7A2F" font-family="JetBrains Mono, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="0.08em">03 / EXECUTION CHANNELS</text>
  <rect x="648" y="142" width="224" height="80" rx="6" fill="#141518" stroke="#343840"/>
  <text x="664" y="168" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">SysEx 0x7D MIDI Bridge</text>
  <text x="664" y="190" fill="#2CC878" font-family="JetBrains Mono, Consolas, monospace" font-size="11">7-Bit Chunked JSON RPC</text>
  <text x="664" y="208" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">loopMIDI / IAC Driver Bus</text>
  <rect x="648" y="236" width="224" height="80" rx="6" fill="#141518" stroke="#343840"/>
  <text x="664" y="262" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Piano Roll Sandbox Sync</text>
  <text x="664" y="284" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">inbox.json / state.json</text>
  <text x="664" y="302" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">Polyphonic Chords &amp; Rolls</text>
  <rect x="648" y="330" width="224" height="74" rx="6" fill="#141518" stroke="#343840"/>
  <text x="664" y="356" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Binary .FLP &amp; CLI Render</text>
  <text x="664" y="378" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">FLhd/FLdt + FL64.exe /R</text>
  <rect x="648" y="418" width="224" height="76" rx="6" fill="#141518" stroke="#343840"/>
  <text x="664" y="444" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Stealth Win32 / Quartz UI</text>
  <text x="664" y="466" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">PrintWindow / PostMessage</text>

  <!-- Column 4: FL Studio 25/26 Runtime -->
  <rect x="930" y="90" width="246" height="424" rx="8" fill="#1C1E22" stroke="#2A2D34"/>
  <text x="950" y="122" fill="#2CC878" font-family="JetBrains Mono, Consolas, monospace" font-size="11" font-weight="700" letter-spacing="0.08em">04 / FL STUDIO 25 / 26</text>
  <rect x="948" y="142" width="210" height="106" rx="6" fill="#141518" stroke="#343840"/>
  <text x="964" y="168" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">device_fl_mcp.py</text>
  <text x="964" y="190" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">transport / channels</text>
  <text x="964" y="210" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">mixer / plugins / arrangement</text>
  <text x="964" y="230" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">Real-Time Peak Metering</text>
  <rect x="948" y="264" width="210" height="106" rx="6" fill="#141518" stroke="#343840"/>
  <text x="964" y="290" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Apply FL-MCP Notes.pyscript</text>
  <text x="964" y="312" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">flpianoroll.score</text>
  <text x="964" y="332" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">Note / Marker Injection</text>
  <text x="964" y="352" fill="#8790A0" font-family="JetBrains Mono, Consolas, monospace" font-size="10">FL 2025 Sandbox Compliant</text>
  <rect x="948" y="386" width="210" height="108" rx="6" fill="#141518" stroke="#343840"/>
  <text x="964" y="412" fill="#ECEEF2" font-family="Inter, Segoe UI, sans-serif" font-size="13" font-weight="600">Plugin &amp; Preset Engine</text>
  <text x="964" y="434" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">70+ Native FL Plugins</text>
  <text x="964" y="454" fill="#9CA3B0" font-family="JetBrains Mono, Consolas, monospace" font-size="11">.fst / .fsc / .sf2 Loader</text>
  <text x="964" y="474" fill="#2CC878" font-family="JetBrains Mono, Consolas, monospace" font-size="10">Closed-Loop LUFS Audit</text>

  <!-- Connector Arrows -->
  <line x1="274" y1="275" x2="314" y2="275" stroke="#FF7A2F" stroke-width="2"/>
  <line x1="590" y1="182" x2="630" y2="182" stroke="#FF7A2F" stroke-width="2"/>
  <line x1="590" y1="275" x2="630" y2="275" stroke="#FF7A2F" stroke-width="2"/>
  <line x1="590" y1="367" x2="630" y2="367" stroke="#FF7A2F" stroke-width="2"/>
  <line x1="590" y1="455" x2="630" y2="455" stroke="#FF7A2F" stroke-width="2"/>
  <line x1="890" y1="182" x2="930" y2="182" stroke="#2CC878" stroke-width="2"/>
  <line x1="890" y1="275" x2="930" y2="275" stroke="#2CC878" stroke-width="2"/>
  <line x1="890" y1="367" x2="930" y2="425" stroke="#2CC878" stroke-width="2"/>
</svg>
"""  # noqa: E501
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(svg, encoding="utf-8")


def render_showcase_plate(out_png: Path, out_webp: Path) -> None:
    """Render a hardware-calibrated 1440x720 dark DAW telemetry & mixer console plate."""
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1440, 720
    img = Image.new("RGB", (w, h), (20, 21, 24))
    draw = ImageDraw.Draw(img)

    # Subtle 1px grid hairlines
    for gx in range(0, w, 48):
        draw.line([(gx, 0), (gx, h)], fill=(25, 27, 31), width=1)
    for gy in range(0, h, 48):
        draw.line([(0, gy), (w, gy)], fill=(25, 27, 31), width=1)

    font_sm = ImageFont.load_default()
    font_lg = ImageFont.load_default()

    # Outer chassis border & top transport bar
    draw.rectangle([16, 16, w - 17, h - 17], outline=(42, 45, 52), width=2)
    draw.rectangle([28, 28, w - 29, 76], fill=(28, 30, 34), outline=(42, 45, 52), width=1)
    draw.ellipse([44, 46, 56, 58], fill=(255, 122, 47))
    draw.text(
        (68, 45),
        "FL-MCP STUDIO // MASTERING TELEMETRY & SYSEX CONSOLE",
        fill=(236, 238, 242),
        font=font_lg,
    )
    draw.text(
        (w - 420, 45),
        "BRIDGE: ONLINE (0.8 ms)   LUFS-I: -14.0   dBTP: -1.1",
        fill=(44, 200, 120),
        font=font_sm,
    )

    # Left panel: 8-Channel Mixer Meter & Motorized Fader Bank
    draw.rectangle([28, 92, 680, 688], fill=(28, 30, 34), outline=(42, 45, 52), width=1)
    draw.text(
        (46, 108),
        "01 // CALIBRATED 8-BUS MIXER & PEAK METER BRIDGE",
        fill=(255, 122, 47),
        font=font_sm,
    )
    strips = [
        ("MASTER", -1.1, 0.82, True),
        ("KICK 808", -6.2, 0.72, False),
        ("SUB BASS", -7.4, 0.68, False),
        ("SNARE CLAP", -6.8, 0.74, False),
        ("HI-HATS", -11.5, 0.56, False),
        ("SYNTH LEAD", -8.9, 0.66, False),
        ("CHORD PAD", -12.4, 0.52, False),
        ("PLATE IR", -16.8, 0.42, False),
    ]
    for idx, (label, db_val, fader_pos, is_master) in enumerate(strips):
        sx = 46 + idx * 78
        draw.rectangle([sx, 138, sx + 68, 668], fill=(20, 21, 24), outline=(42, 45, 52), width=1)
        # Dual stereo peak meter bars
        m_top, m_bot = 160, 430
        level_h = int((m_bot - m_top) * min(1.0, max(0.08, (db_val + 36.0) / 36.0)))
        for ch_off in (14, 24):
            draw.rectangle([sx + ch_off, m_top, sx + ch_off + 6, m_bot], fill=(12, 13, 16))
            col = (255, 122, 47) if is_master else (44, 200, 120)
            draw.rectangle([sx + ch_off, m_bot - level_h, sx + ch_off + 6, m_bot], fill=col)
            # Peak hold tick
            draw.rectangle(
                [sx + ch_off, m_bot - level_h - 6, sx + ch_off + 6, m_bot - level_h - 4],
                fill=(236, 238, 242),
            )
        # Motorized fader track & cap
        f_top, f_bot = 456, 610
        draw.rectangle([sx + 46, f_top, sx + 50, f_bot], fill=(10, 11, 13))
        cap_y = int(f_bot - fader_pos * (f_bot - f_top))
        draw.rectangle([sx + 46, cap_y, sx + 50, f_bot], fill=(255, 122, 47))
        cap_col = (255, 122, 47) if is_master else (228, 232, 238)
        draw.rectangle([sx + 38, cap_y - 8, sx + 58, cap_y + 8], fill=cap_col, outline=(20, 21, 24))
        draw.line([(sx + 40, cap_y), (sx + 56, cap_y)], fill=(20, 21, 24), width=2)
        draw.text((sx + 8, 626), f"{db_val:+.1f} dB", fill=(236, 238, 242), font=font_sm)
        draw.text((sx + 8, 646), label[:9], fill=(156, 163, 176), font=font_sm)

    # Top-Right panel: 7-Band ITU-R BS.1770-4 Spectrum & Unmasking Analyzer
    draw.rectangle([696, 92, w - 29, 392], fill=(28, 30, 34), outline=(42, 45, 52), width=1)
    draw.text(
        (714, 108),
        "02 // 7-BAND WELCH SPECTRUM & 6-ZONE SPECTRAL UNMASKING",
        fill=(255, 122, 47),
        font=font_sm,
    )
    draw.rectangle([714, 136, w - 47, 372], fill=(15, 17, 20), outline=(42, 45, 52), width=1)
    bands = [
        ("SUB 20-60", -9.2, 0.72),
        ("BASS 60-250", -6.4, 0.86),
        ("LOW-MID 500", -12.8, 0.58),
        ("MID 500-2k", -8.1, 0.78),
        ("HI-MID 2-6k", -10.4, 0.67),
        ("HIGH 6-12k", -13.5, 0.54),
        ("AIR 12-20k", -17.2, 0.41),
    ]
    for b_idx, (b_name, b_db, b_norm) in enumerate(bands):
        bx = 736 + b_idx * 94
        b_top, b_bot = 162, 336
        bh = int((b_bot - b_top) * b_norm)
        draw.rectangle([bx, b_top, bx + 68, b_bot], fill=(22, 24, 28), outline=(36, 39, 45))
        draw.rectangle([bx + 4, b_bot - bh, bx + 64, b_bot - 2], fill=(255, 122, 47))
        draw.text((bx + 8, b_bot - bh - 16), f"{b_db:.1f} dB", fill=(236, 238, 242), font=font_sm)
        draw.text((bx + 2, 346), b_name, fill=(156, 163, 176), font=font_sm)

    # Bottom-Right panel: 16-Step Sequencer & AI/Audio Vault Inventory
    draw.rectangle([696, 408, w - 29, 688], fill=(28, 30, 34), outline=(42, 45, 52), width=1)
    draw.text(
        (714, 424),
        "03 // 16-STEP RACK MATRIX & OFFLINE AI / AUDIO VAULT",
        fill=(255, 122, 47),
        font=font_sm,
    )
    rows = [
        ("808 KICK", "1000100010010000"),
        ("909 SNARE", "0000100000001000"),
        ("CLOSED HAT", "1111111111111111"),
        ("OPEN HAT", "0010001000100010"),
        ("MOOG BASS", "1001001010010100"),
    ]
    for r_idx, (r_name, bits) in enumerate(rows):
        ry = 456 + r_idx * 34
        draw.text((714, ry + 6), r_name, fill=(236, 238, 242), font=font_sm)
        for s_idx, bit in enumerate(bits):
            sx = 830 + s_idx * 34
            is_on = bit == "1"
            fill_c = (
                (255, 122, 47)
                if is_on
                else ((34, 37, 43) if (s_idx // 4) % 2 == 0 else (24, 26, 30))
            )
            out_c = (255, 158, 92) if is_on else (48, 52, 60)
            draw.rectangle([sx, ry, sx + 28, ry + 24], fill=fill_c, outline=out_c, width=1)

    draw.rectangle([714, 634, w - 47, 672], fill=(20, 21, 24), outline=(42, 45, 52), width=1)
    draw.text(
        (728, 648),
        "VAULT READY // basic_pitch.onnx | silero_vad.onnx | FluidR3_GM.sf2 | "
        "TimGM6mb.sf2 | 81 Drum WAVs | 18 48kHz IRs | 12 Genre MIDIs",
        fill=(44, 200, 120),
        font=font_sm,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_png, format="PNG", optimize=True)
    img.save(out_webp, format="WEBP", quality=92)


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
    write_knob_ring_svg(ASSETS / "knob-ring.svg")

    docs_assets = ROOT / "docs" / "assets"
    docs_assets.mkdir(parents=True, exist_ok=True)
    write_architecture_svg(docs_assets / "fl-mcp-architecture.svg")
    render_showcase_plate(
        docs_assets / "fl-mcp-studio-console.png",
        docs_assets / "fl-mcp-studio-console.webp",
    )

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
