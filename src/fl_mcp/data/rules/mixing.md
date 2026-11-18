# FL-MCP Mixing, Gain-Staging & Spectral Unmasking Rulebook

## 1. Gain-Staging & Headroom Hierarchy
1. **Pre-Mix Diagnostic (`fl_mix(action="doctor")`)**: Audit unrouted channels, clipping inserts (`>= 0.0 dBFS`), muted buses, and missing master safety limiters before moving faders.
2. **Anchor-First Balance (`fl_mix(action="stage")`)**:
   - **Kick & 808/Sub**: Anchor at `-9.0` to `-6.0 dBFS` individual peak so their summed bus sits near `-5.0 dBFS`.
   - **Snare / Clap / Lead Vocal**: Match perceived foreground punch (`-8.0` to `-6.5 dBFS`).
   - **Harmonic Bed (Keys, Pads, Guitars)**: Tuck `-14.0` to `-11.0 dBFS` with stereo width leaving center space for lead/vocal.
   - **Percussion & Ear Candy**: Place `-18.0` to `-13.0 dBFS` with transient control.
   - **Master Pre-Limiter Target**: Maintain `-6.0` to `-3.0 dBFS` peak headroom before the final dynamics stage.

## 2. Six-Zone Spectral Unmasking Matrix (`fl_audio(action="masking")`)
When comparing a foreground stem (vocal, lead synth, or kick) against a backing bed:
- **Neural VAD Gating (`silero_vad.onnx`)**: When available in `~/.fl-mcp/models/silero_vad.onnx`, measure foreground vocal energy strictly across active voiced frames (`p >= 0.45`) so silent gaps never skew the masking delta.
- **Zone 1 — Low Warmth (`120–250 Hz`, Q `1.4`)**: High-pass non-bass instruments and dip fundamental buildup on pads/guitars.
- **Zone 2 — Boxy Mud (`250–500 Hz`, Q `1.5`)**: Carve `-2.0` to `-4.5 dB` on backing buses when masking index exceeds `25`.
- **Zone 3 — Midrange Body (`500–1200 Hz`, Q `1.2`)**: Pocket chord instruments so lead fundamentals remain authoritative.
- **Zone 4 — Intelligibility & Clarity (`1200–2800 Hz`, Q `1.4`)**: Notch backing synths/guitars by `-1.5` to `-3.5 dB` where vocal consonants sit.
- **Zone 5 — Presence & Bite (`2800–5200 Hz`, Q `1.5`)**: Protect ear-canal sensitivity zone (`3.5 kHz`); use dynamic EQ or dip backing presence rather than over-boosting the lead.
- **Zone 6 — Sibilance & Air (`5200–10000 Hz`, Q `1.2`)**: De-ess harsh transients before adding high-shelf air above `10 kHz`.

## 3. Spatial Bus & Send Engineering
- Prefer shared **Short Room** (`0.4–0.8 s`, `~/.fl-mcp/assets/irs/Room_*.wav`), **Vocal Plate** (`1.4–2.2 s`, `Plate_*.wav`), and **Concert Hall** (`2.4–3.8 s`, `Hall_*.wav`) convolution sends over placing 15 separate reverb instances on individual inserts.
- High-pass all reverb and delay return buses at `180–250 Hz` and low-pass at `7.5–10 kHz` so spatial tails never cloud the sub or clash with lead air.
