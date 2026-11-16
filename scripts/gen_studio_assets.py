"""Deterministic, 4x/8x-supersampled pro-audio asset generator for FL-MCP Studio.

Generates mathematically exact, zero-hallucination DAW hardware icons and console backdrops:
- src/fl_mcp/assets/logo.svg
- src/fl_mcp/assets/icon-256.png, icon-128.png, icon-64.png, favicon.png
- src/fl_mcp/assets/icon.ico (multi-size Windows icon: 16, 24, 32, 48, 64, 128, 256)
- src/fl_mcp/assets/icon.icns (multi-size macOS iconset: ic07 128, ic08 256)
- src/fl_mcp/assets/studio-backdrop.webp (1440x320 parametric EQ / FFT console backdrop)
"""

from __future__ import annotations

import io
import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "fl_mcp" / "assets"


def _lerp_color(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(round(c1[0] + (c2[0] - c1[0]) * t)),
        int(round(c1[1] + (c2[1] - c1[1]) * t)),
        int(round(c1[2] + (c2[2] - c1[2]) * t)),
    )


def render_master_icon(size: int = 1024) -> Image.Image:
    """Render a 1024x1024 precision CNC-machined DAW encoder & spectral bridge icon."""
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    nx = (xx - cx) / (size / 2.0)
    ny = (yy - cy) / (size / 2.0)
    r = np.hypot(nx, ny)
    theta = np.arctan2(ny, nx)  # -pi .. pi

    # Squircle signed distance field (superellipse n=4.6, radius 0.90)
    pad = 0.91
    sq = (np.abs(nx / pad) ** 4.6 + np.abs(ny / pad) ** 4.6) ** (1.0 / 4.6)
    alpha = np.clip((1.0 - sq) * (size * 0.35), 0.0, 1.0)

    # Base anodized obsidian chassis gradient (top-left lit)
    light_dir = (-0.55 * nx - 0.75 * ny + 0.35)
    base_val = 14.0 + 10.0 * np.clip(light_dir, -1.0, 1.0) - 5.0 * (r**1.5)
    red = np.clip(base_val * 0.82, 8, 32)
    grn = np.clip(base_val * 1.02, 10, 40)
    blu = np.clip(base_val * 1.38, 15, 54)

    # Outer chamfered titanium rim (0.955 < sq < 1.0)
    rim_band = np.exp(-(((sq - 0.975) / 0.016) ** 2))
    rim_spec = np.clip(-0.6 * nx - 0.8 * ny, -0.4, 1.0)
    red += rim_band * (36.0 * rim_spec + 18.0)
    grn += rim_band * (44.0 * rim_spec + 22.0)
    blu += rim_band * (58.0 * rim_spec + 30.0)

    # Recessed meter well (0.54 < r < 0.76)
    well = np.clip(1.0 - np.abs(r - 0.645) / 0.115, 0.0, 1.0)
    red -= well * 5.0
    grn -= well * 6.0
    blu -= well * 8.0

    # Knurled metallic encoder bezel (0.46 < r < 0.525)
    bezel = np.exp(-(((r - 0.492) / 0.022) ** 2))
    knurl = 0.5 + 0.5 * np.cos(theta * 96.0)
    bezel_light = 0.55 + 0.45 * np.cos(theta + 2.15)
    red += bezel * (28.0 * bezel_light + 9.0 * knurl)
    grn += bezel * (34.0 * bezel_light + 11.0 * knurl)
    blu += bezel * (45.0 * bezel_light + 15.0 * knurl)

    # Inner brushed aluminum encoder cap (r < 0.465) with conical anisotropic sheen
    cap_mask = np.clip((0.468 - r) * (size * 0.25), 0.0, 1.0)
    conical = (
        0.50
        + 0.30 * np.cos(2.0 * (theta + 0.78))
        + 0.12 * np.cos(4.0 * (theta - 0.35))
        + 0.04 * np.sin(36.0 * theta)
    )
    cap_r = 16.0 + 18.0 * conical - 8.0 * (r / 0.47) ** 2
    cap_g = 21.0 + 22.0 * conical - 9.0 * (r / 0.47) ** 2
    cap_b = 30.0 + 30.0 * conical - 11.0 * (r / 0.47) ** 2
    red = red * (1.0 - cap_mask) + cap_r * cap_mask
    grn = grn * (1.0 - cap_mask) + cap_g * cap_mask
    blu = blu * (1.0 - cap_mask) + cap_b * cap_mask

    # Crisp specular ring around encoder cap (r == 0.458)
    cap_rim = np.exp(-(((r - 0.458) / 0.006) ** 2)) * (0.5 + 0.5 * np.cos(theta + 2.2))
    red += cap_rim * 55.0
    grn += cap_rim * 65.0
    blu += cap_rim * 82.0

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = np.clip(red, 0, 255).astype(np.uint8)
    rgba[..., 1] = np.clip(grn, 0, 255).astype(np.uint8)
    rgba[..., 2] = np.clip(blu, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)

    img = Image.fromarray(rgba, mode="RGBA")
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    gdraw = ImageDraw.Draw(glow_layer)

    # 1. Calibrated 270-degree dB LED meter ring (33 segments: 135 deg -> 405 deg)
    num_segs = 33
    active_seg = 24  # Active gain position at ~330 deg (top-right, 2 o'clock)
    start_deg = 135.0
    span_deg = 270.0
    r_in = size * 0.285
    r_out = size * 0.348
    r_out_major = size * 0.364

    teal = (43, 212, 181)
    amber = (255, 122, 47)
    gold = (255, 178, 74)
    crimson = (255, 77, 94)

    for i in range(num_segs):
        frac = i / (num_segs - 1)
        deg = start_deg + frac * span_deg
        rad = math.radians(deg)
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        is_major = i % 4 == 0
        ro = r_out_major if is_major else r_out
        x0 = cx + r_in * cos_a
        y0 = cy + r_in * sin_a
        x1 = cx + ro * cos_a
        y1 = cy + ro * sin_a
        w = int(round(size * (0.015 if is_major else 0.011)))

        if i <= active_seg:
            if frac < 0.38:
                col = _lerp_color(teal, amber, frac / 0.38)
            else:
                col = _lerp_color(amber, gold, (frac - 0.38) / 0.42)
            gdraw.line([(x0, y0), (x1, y1)], fill=(*col, 190), width=w + int(size * 0.014))
            draw.line([(x0, y0), (x1, y1)], fill=(*col, 255), width=w)
        elif i == num_segs - 2:
            # Peak-hold indicator segment in red
            gdraw.line([(x0, y0), (x1, y1)], fill=(*crimson, 150), width=w + int(size * 0.012))
            draw.line([(x0, y0), (x1, y1)], fill=(*crimson, 245), width=w)
        else:
            unlit = (52, 64, 84, 210) if i < 28 else (78, 36, 46, 210)
            draw.line([(x0, y0), (x1, y1)], fill=unlit, width=w)

    # 2. Single precision encoder position notch aligned with active_seg
    active_deg = start_deg + (active_seg / (num_segs - 1)) * span_deg
    active_rad = math.radians(active_deg)
    px0 = cx + (size * 0.165) * math.cos(active_rad)
    py0 = cy + (size * 0.165) * math.sin(active_rad)
    px1 = cx + (size * 0.212) * math.cos(active_rad)
    py1 = cy + (size * 0.212) * math.sin(active_rad)
    pw = int(round(size * 0.022))
    gdraw.line([(px0, py0), (px1, py1)], fill=(255, 122, 47, 220), width=pw + int(size * 0.018))
    draw.line([(px0, py0), (px1, py1)], fill=(255, 195, 120, 255), width=pw)

    # 3. Bold 5-bar Studio Spectral / Step-Sequencer Bridge Emblem in the center
    # Remains 100% legible at 16x16, 32x32, 64x64, and 256x256
    bar_heights = [0.11, 0.21, 0.29, 0.18, 0.12]
    bar_colors = [
        teal,
        _lerp_color(teal, amber, 0.45),
        amber,
        gold,
        amber,
    ]
    bar_w = size * 0.032
    gap = size * 0.052
    for idx, (bh, bcol) in enumerate(zip(bar_heights, bar_colors, strict=True)):
        bx = cx + (idx - 2) * gap
        half_h = (size * bh) / 2.0
        rect = [bx - bar_w / 2, cy - half_h, bx + bar_w / 2, cy + half_h]
        rad_corner = bar_w / 2
        gdraw.rounded_rectangle(
            [rect[0] - 4, rect[1] - 4, rect[2] + 4, rect[3] + 4],
            radius=rad_corner + 4,
            fill=(*bcol, 135),
        )
        draw.rounded_rectangle(rect, radius=rad_corner, fill=(*bcol, 255))

    glow_blurred = glow_layer.filter(ImageFilter.GaussianBlur(radius=size * 0.016))
    img = Image.alpha_composite(img, glow_blurred)
    img = Image.alpha_composite(img, overlay)
    return img


def render_studio_backdrop(width: int = 1440, height: int = 320) -> Image.Image:
    """Render a precision parametric EQ & real-time FFT analyzer console backdrop."""
    scale = 2
    w, h = width * scale, height * scale
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = xx / w
    ny = yy / h

    # Dark anodized gunmetal rack background with subtle vertical lighting gradient
    r = 8.0 + 6.0 * (1.0 - ny) + 4.0 * np.exp(-(((nx - 0.35) / 0.35) ** 2))
    g = 11.0 + 8.0 * (1.0 - ny) + 5.0 * np.exp(-(((nx - 0.35) / 0.35) ** 2))
    b = 17.0 + 12.0 * (1.0 - ny) + 7.0 * np.exp(-(((nx - 0.35) / 0.35) ** 2))

    # Parametric EQ response curves (Amber Mid channel + Teal Side channel)
    curve_amber = (
        0.58
        - 0.14 * np.exp(-(((nx - 0.14) / 0.055) ** 2))
        - 0.24 * np.exp(-(((nx - 0.42) / 0.095) ** 2))
        + 0.10 * np.exp(-(((nx - 0.60) / 0.070) ** 2))
        - 0.20 * np.exp(-(((nx - 0.78) / 0.065) ** 2))
        + 0.04 * np.sin(nx * 28.0)
    )
    curve_teal = (
        0.64
        - 0.18 * np.exp(-(((nx - 0.26) / 0.075) ** 2))
        - 0.16 * np.exp(-(((nx - 0.54) / 0.085) ** 2))
        - 0.22 * np.exp(-(((nx - 0.84) / 0.060) ** 2))
        + 0.03 * np.cos(nx * 22.0)
    )

    # Smooth translucent fill below each EQ curve
    fill_a = np.clip((ny - curve_amber) / 0.45, 0.0, 1.0)
    under_a = (ny >= curve_amber).astype(np.float32) * (1.0 - fill_a) ** 1.7
    r += under_a * 46.0
    g += under_a * 20.0
    b += under_a * 7.0

    fill_t = np.clip((ny - curve_teal) / 0.45, 0.0, 1.0)
    under_t = (ny >= curve_teal).astype(np.float32) * (1.0 - fill_t) ** 1.8
    r += under_t * 6.0
    g += under_t * 36.0
    b += under_t * 32.0

    # Crisp glowing EQ traces
    trace_a = np.exp(-(((ny - curve_amber) / 0.0085) ** 2))
    r += trace_a * 210.0
    g += trace_a * 96.0
    b += trace_a * 32.0

    trace_t = np.exp(-(((ny - curve_teal) / 0.0075) ** 2))
    r += trace_t * 34.0
    g += trace_t * 175.0
    b += trace_t * 150.0

    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[..., 0] = np.clip(r, 0, 255).astype(np.uint8)
    rgb[..., 1] = np.clip(g, 0, 255).astype(np.uint8)
    rgb[..., 2] = np.clip(b, 0, 255).astype(np.uint8)

    img = Image.fromarray(rgb, mode="RGB").convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Calibrated logarithmic frequency grid hairlines
    log_positions = [0.06, 0.14, 0.24, 0.36, 0.48, 0.60, 0.72, 0.83, 0.93]
    for lp in log_positions:
        gx = int(round(lp * w))
        draw.line([(gx, 0), (gx, h)], fill=(120, 148, 190, 22), width=2)
    for db_row in (0.22, 0.42, 0.62, 0.82):
        gy = int(round(db_row * h))
        draw.line([(0, gy), (w, gy)], fill=(120, 148, 190, 18), width=2)

    # Parametric EQ node handles on the amber curve
    for node_x in (0.14, 0.42, 0.78):
        ix = int(round(node_x * w))
        iy = int(round(float(curve_amber[0, ix]) * h))
        rad = 11
        draw.ellipse(
            [ix - rad * 2, iy - rad * 2, ix + rad * 2, iy + rad * 2],
            fill=(255, 122, 47, 55),
        )
        draw.ellipse(
            [ix - rad, iy - rad, ix + rad, iy + rad],
            fill=(14, 19, 28, 255),
            outline=(255, 150, 72, 240),
            width=4,
        )

    return img.resize((width, height), Image.Resampling.LANCZOS).convert("RGB")


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
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="url(#chassis)" stroke="#2E3B52" stroke-width="1.5"/>
  <path d="M16.5 47.5A22 22 0 1 1 47.5 47.5" stroke="#232C3D" stroke-width="3.2" stroke-linecap="round"/>
  <path d="M16.5 47.5A22 22 0 1 1 49.2 20.5" stroke="#FF7A2F" stroke-width="3.2" stroke-linecap="round"/>
  <circle cx="32" cy="32" r="16.5" fill="url(#cap)" stroke="#3B4A66" stroke-width="1.2"/>
  <line x1="39.5" y1="24.5" x2="43.2" y2="20.8" stroke="#FFB24A" stroke-width="2.2" stroke-linecap="round"/>
  <rect x="23" y="28.5" width="2.4" height="7" rx="1.2" fill="#2BD4B5"/>
  <rect x="27.3" y="25" width="2.4" height="14" rx="1.2" fill="#7AD18B"/>
  <rect x="31.6" y="22.5" width="2.4" height="19" rx="1.2" fill="#FF7A2F"/>
  <rect x="35.9" y="26" width="2.4" height="12" rx="1.2" fill="#FFB24A"/>
  <rect x="40.2" y="28" width="2.4" height="8" rx="1.2" fill="#FF7A2F"/>
</svg>
"""
    out_path.write_text(svg, encoding="utf-8")


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = render_master_icon(1024)

    png_bytes: dict[int, bytes] = {}
    for sz, name in (
        (256, "icon-256.png"),
        (128, "icon-128.png"),
        (64, "icon-64.png"),
        (32, "favicon.png"),
    ):
        resized = master.resize((sz, sz), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        png_bytes[sz] = data
        (ASSETS / name).write_bytes(data)

    # Windows multi-resolution .ico
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.resize((256, 256), Image.Resampling.LANCZOS).save(
        ASSETS / "icon.ico", format="ICO", sizes=ico_sizes
    )

    # macOS .icns
    write_icns(ASSETS / "icon.icns", png_bytes[128], png_bytes[256])

    # Vector SVG logo
    write_logo_svg(ASSETS / "logo.svg")

    # Parametric EQ / FFT Console Header Backdrop
    backdrop = render_studio_backdrop(1440, 320)
    backdrop.save(ASSETS / "studio-backdrop.webp", format="WEBP", quality=92, method=6)
    print("Generated all studio assets in", ASSETS)


if __name__ == "__main__":
    main()
