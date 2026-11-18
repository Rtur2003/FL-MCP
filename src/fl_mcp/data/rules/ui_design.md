# FL-MCP Hardware-Calibrated Dark Studio UI & Visual Rulebook

## 1. Anti-AI-Slop Industrial Design Mandate
All FL-MCP Studio interfaces (`fl-mcp app`, `dashboard.html`, native Tkinter fallback, and documentation visuals) follow hardware-calibrated mastering console ergonomics inspired by **FL Studio 25 Graphite**, **FabFilter Pro-Q 4**, and **Ableton Live 12 Dark**:
- **Zero Neon Cyberpunk Clichés**: Ban purple-to-cyan gradients, glowing sci-fi orbs, decorative glassmorphism blur that hurts legibility, or diffusion-generated fake UI text.
- **Precision Hardware Surfaces**: Rely on 1px machined borders (`#2A2D34`), recessed meter wells (`#0E1013`), tabular numeric readouts (`font-variant-numeric: tabular-nums`), and high-contrast functional signal LEDs.

## 2. Calibrated Color Token Specification
| Token | Hex | Role | WCAG Contrast (vs `#141518`) |
| :--- | :--- | :--- | :--- |
| `--bg` | `#141518` | Primary matte graphite chassis background | — |
| `--bg-elev` | `#1C1E22` | Elevated module / rack card surface | — |
| `--bg-pane` | `#23262B` | Interactive control well / hover surface | — |
| `--line` | `#2A2D34` | Hairline 1px structural divider | — |
| `--line-hi` | `#3A3F4B` | Active / focused border contour | — |
| `--ink` | `#ECEEF2` | Primary titanium readout typography | `14.8 : 1` (AAA) |
| `--muted` | `#9CA3B0` | Secondary parameter labels | `6.7 : 1` (AA) |
| `--dim` | `#8790A0` | Sub-labels, units (`dB`, `LUFS`, `Hz`, `ms`) | `5.1 : 1` (AA) |
| `--accent` | `#FF7A2F` | Active signal amber (fader caps, focus ring) | `6.4 : 1` (AA) |
| `--ok` | `#2CC878` | Nominal headroom / connected LED green | `7.9 : 1` (AA) |
| `--warn` | `#E5A93B` | Pre-clip caution / drift amber-gold | `8.2 : 1` (AA) |
| `--err` | `#E04A4A` | True-peak clip / fault indicator red | `4.6 : 1` (AA) |

## 3. Typography, Iconography & Accessibility Standards
- **Self-Hosted Font Stack**:
  - UI Sans: `'Inter'` (`inter-400.woff2`, `inter-500.woff2`, `inter-600.woff2`, `inter-700.woff2`) with `font-size-adjust: from-font` to eliminate Cumulative Layout Shift (CLS).
  - Telemetry & DSP Mono: `'JetBrains Mono'` (`jetbrains-mono-400.woff2`, `jetbrains-mono-500.woff2`, `jetbrains-mono-600.woff2`) with `font-variant-numeric: tabular-nums`.
- **Crisp Vector Iconography**:
  - 1.75px stroke monochrome SVG symbols (`lucide-sprite.svg`) inheriting `currentColor`, marked `aria-hidden="true"` when paired with visible labels.
- **Keyboard & Screen Reader Ergonomics**:
  - WAI-ARIA tabs (`role="tablist"`, `role="tab"`, `role="tabpanel"`) with roving `tabindex` and ArrowLeft/ArrowRight/Home/End navigation.
  - Explicit `:focus-visible` `2px solid var(--accent)` ring (`outline-offset: 2px`) on every interactive control.
  - `min-block-size` instead of fixed `height` on text controls so 200% zoom and WCAG 1.4.12 text-spacing overrides never clip labels.
