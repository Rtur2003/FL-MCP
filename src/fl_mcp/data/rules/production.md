# FL-MCP Production & Arrangement Rulebook

## 1. Autonomous Workflow Pipeline
1. **Inspect First (`fl_state`)**: Always read the active project state (`status`, `channels`, `mixer`, `patterns`, `plugins`) before adding or modifying patterns, notes, or channels.
2. **Choose or Seed Template (`fl_produce`)**:
   - FL Studio MIDI scripts cannot spawn generator channels from thin air without desktop UI interaction or a pre-seeded `.flp` template.
   - Discover available `.flp` templates via `fl_produce(action="templates")` or seed a multi-channel project from `~/.fl-mcp/templates/` before writing multi-instrument arrangements.
3. **Library & Vault First (`fl_library`)**:
   - Search indexed drum kits, `~/.fl-mcp/assets/samples/` (TR-808, TR-909, Dirt-Samples), `~/.fl-mcp/assets/soundfonts/` (`TimGM6mb.sf2`, `FluidR3_GM.sf2`), and `~/.fl-mcp/assets/midi/` before asking the user for external packs.
   - Match samples by spectral centroid, attack time, decay profile, and RMS energy (`fl_library(action="similar")`).

## 2. Piano Roll vs. Step Sequencer Routing
- **Step Sequencer (`fl_steps`)**: Use for 16-step percussive triggers (kick, snare, clap, closed/open hats, rimshots) when pitch is static.
- **Piano Roll (`fl_notes` / `fl_score`)**:
  - Mandatory for pitched 808 basslines, chord progressions, arpeggios, counter-melodies, and 32nd/triplet hi-hat rolls.
  - **FL Studio 2025/2026 Sandbox Rule**: Piano roll scripts execute inside `Settings/Piano roll scripts` and read/write `inbox.json` and `state.json` in that folder when `FL_MCP_DIR` is not overridden.

## 3. Arrangement Arc & Energy Curve Framework
| Section | Bars | Energy | Core Arrangement Rules |
| :--- | :--- | :--- | :--- |
| **Intro** | 4–8 | 25–40% | Establish harmonic motif or filtered pad; keep sub-bass silent so the drop has contrast. |
| **Verse / A** | 8–16 | 55–70% | Introduce main groove (kick, snare, bassline) with restrained upper-mid density for vocal pocket. |
| **Pre-Chorus / Build** | 4–8 | 70–85% | High-pass low end (`90–150 Hz`) in final 2 bars; add snare rolls, risers, or tension voicings. |
| **Drop / Chorus** | 8–16 | 95–100% | Full sub weight (`35–60 Hz`), wide chord layers, open hats/rides, and primary hook motif. |
| **Bridge / Breakdown** | 4–8 | 35–50% | Strip drums; re-voice chords (inversions or borrowed modal mixture) and introduce spatial FX. |
| **Outro** | 4–8 | 20–30% | Decay rhythmic layers every 2 bars; leave clean reverb/delay tail for DJ or playlist transition. |

## 4. Voice-Leading & Low-End Discipline
- **Sub & 808 Mono Anchor**: Keep all fundamentals below `120 Hz` strictly monophonic and phase-centered. Never stack chorus or wide unison detune on sub-bass.
- **Chord Voicing Spread**: Keep bass intervals wider than a fifth below `C3` (`MIDI 48`) to prevent low-mid mud (`180–320 Hz`). Use 7th/9th tensions above `C4` (`MIDI 60`).
- **Humanization**: Vary note velocities (`±6–14`) and micro-timing on off-beats (`5–18 ms` for Lo-Fi/Boom Bap/R&B) so arrangements never sound mechanically flat.
