# FL-MCP Mastering & Loudness Verification Rulebook

## 1. Closed-Loop Render & Verification (`fl_render` + `fl_audio`)
Never declare a mix or master complete based solely on mixer fader positions. Always execute the closed verification loop:
1. Render a bounce via `fl_render` (or analyze the latest exported WAV/FLAC).
2. Measure ITU-R BS.1770-4 loudness (`lufs_i`, `lufs_s_max`, `lufs_m_max`, `lra`), 4x oversampled `true_peak_db`, `crest_db`, stereo correlation (`corr`, `low_side_db`, `mono_loss_db`), and 7-band energy shares (`sub`, `bass`, `low_mid`, `mid`, `high_mid`, `high`, `air`).
3. Apply corrective moves one parameter at a time and re-verify.

## 2. Release & Streaming Targets
| Delivery Target / Genre | Integrated LUFS (`lufs_i`) | Max True Peak (`dBTP`) | Target Crest (`dB`) | Max Low-Side (`<120 Hz`) |
| :--- | :--- | :--- | :--- | :--- |
| **Streaming Standard (Spotify / Apple / YouTube)** | `-14.0 LUFS` (`±1.0`) | `-1.0 dBTP` | `8.5–12.5 dB` | `<= -14.0 dB` |
| **Trap / Drill / Hip-Hop Club** | `-8.5` to `-7.5 LUFS` | `-0.8 dBTP` | `6.5–9.5 dB` | `<= -16.0 dB` |
| **House / Techno / Club Master** | `-8.0` to `-6.5 LUFS` | `-0.8 dBTP` | `6.5–9.0 dB` | `<= -18.0 dB` |
| **Drum & Bass / Hyperpop** | `-7.5` to `-6.0 LUFS` | `-0.8 dBTP` | `6.0–8.5 dB` | `<= -16.0 dB` |
| **Lo-Fi / Boom Bap / R&B** | `-12.0` to `-10.0 LUFS` | `-1.0 dBTP` | `9.0–13.0 dB` | `<= -14.0 dB` |
| **Ambient / Cinematic / Orchestral** | `-18.0` to `-14.0 LUFS` | `-1.5 dBTP` | `12.0–18.0 dB` | `<= -12.0 dB` |

## 3. Mandatory Quality Gates
- **True Peak Ceiling**: Must not exceed `ceiling_db + 0.05 dBTP` (default `-1.0 dBTP`) to prevent inter-sample clipping in AAC/MP3/Opus transcoders.
- **Crest Factor Guardrail**:
  - `< 6.0 dB`: Over-limited / crushed transients — back off limiter threshold or lengthen compressor attack (`20–35 ms`).
  - `> 16.0 dB` (non-ambient): Under-glued dynamics — apply `1.5–2.5 dB` bus compression (`2:1`, slow attack, auto release).
- **Phase & Mono Compatibility**:
  - Stereo correlation `corr` must stay `>= 0.20`.
  - Mono fold-down loss `mono_loss_db` must stay `>= -3.0 dB`.
  - Low-frequency side energy `low_side_db` (`< 120 Hz`) must stay `<= -14.0 dB`.
- **DC Offset & Clipping**: Zero flat-top clipped sample triplets (`clipped == 0`) and DC offset `< 0.002`.
