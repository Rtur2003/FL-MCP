# FL Studio Formula Controller, Patcher, WaveShaper & Piano Roll Scripting Rulebook

## 1. Fruity Formula Controller — Mathematical Expression Bank
Use these verified expressions in **Fruity Formula Controller** (variables `a`, `b`, `c` in `[0.0, 1.0]`, `SongTime` in quarter-note beats, `Time` in milliseconds):

| Application | Formula Expression | Parameters (`a`, `b`, `c`) |
| :--- | :--- | :--- |
| **Tempo-Synced Sine LFO (1/4 Beat)** | `0.5 + Sin(SongTime * Pi * 2) * 0.5 * a` | `a` = LFO Depth (`0..1`) |
| **Sidechain Ducking Envelope (4-on-the-floor)** | `1 - (1 - Frac(SongTime)) ^ (2 + a * 6) * b` | `a` = Curve Tension, `b` = Duck Depth |
| **Bipolar Center-Detent Scaling** | `0.5 + (a - 0.5) * b` | `a` = Input Signal, `b` = Range Multiplier |
| **Quantized Step Sequencer (8 Steps)** | `Int(a * 8) / 7` | `a` = Ramp or LFO Input |
| **Inverse Crossfade Macro (`A -> B`)** | `a * (1 - c) + b * c` | `a` = Source A, `b` = Source B, `c` = Morph |
| **Soft-Knee Ceiling Clamp** | `Min(a, b) + Max(0, a - b) * 0.25` | `a` = Input, `b` = Knee Threshold |
| **Random Sample & Hold (Per Beat)** | `SeededRand(Int(SongTime * (1 + Int(b * 4)))) * a` | `a` = Depth, `b` = Subdivision (`1..4`) |
| **Logarithmic Filter Cutoff Mapper** | `(a ^ 2.4) * (c - b) + b` | `a` = Macro Knob, `b` = Min Cutoff, `c` = Max |

## 2. Fruity WaveShaper — Saturation & Clipping Transfer Curves
- **Warm Tube Asymmetric Drive (`808` / Bass)**:
  - Keep `0.0 -> 0.0` linear up to `0.45`, gentle convex S-curve `+1.8 dB` tension from `0.45 -> 0.85`, soft ceiling knee at `0.96`.
  - Enable **2x–4x Oversampling** and **Center (DC Block)** whenever driving sub-bass harmonics (`70–220 Hz`) so fundamentals never drift off zero.
- **Hard Digital Drum Clipper (Kick / Snare Transients)**:
  - Unity `1:1` diagonal up to `-2.5 dBFS` (`0.75`), flat horizontal ceiling from `0.75 -> 1.0`. Shaves `2–3 dB` of transient needle peaks without dulling punch like a slow-release limiter.

## 3. Native `.fsc` Groove Quantization (`Alt+Q`) & Score Library
FL-MCP ships **24 binary `.fsc` Score & Groove Quantization files** in `~/.fl-mcp/assets/scores/` and `Presets/Scores/FL-MCP/`:
- **Hardware Swing & Pocket Grids**: `MPC60_Swing_58pct.fsc`, `MPC60_Swing_62pct.fsc`, `MPC3000_Swing_66pct.fsc`, `SP1200_BoomBap_Pocket.fsc`, `J_Dilla_Lazy_Snare_Feel.fsc`, `House_Garage_Shuffle_16th.fsc`, `Reggaeton_Dembow_Grid.fsc`, `DnB_Amen_Break_Grid.fsc`.
- **Trap & Drill Articulation Grids**: `Trap_HiHat_Roll_32nd_Stutter.fsc`, `Trap_HiHat_Triplet_Bounce.fsc`, `UK_Drill_Counter_Snare_Grid.fsc`, `808_Octave_Glide_Pattern.fsc`.
- **12 Genre Harmonic Scores**: `Trap_Minor_140.fsc`, `Drill_Dark_Sliding_142.fsc`, `House_Piano_Stabs_124.fsc`, `Techno_Acid_303_132.fsc`, `LoFi_Rhodes_9ths_82.fsc`, `Synthwave_Arp_112.fsc`, `BoomBap_Soul_Chords_90.fsc`, `DnB_Liquid_Pads_174.fsc`, `RnB_NeoSoul_Voicings_96.fsc`, `Hyperpop_Supersaw_160.fsc`, `Ambient_Cathedral_70.fsc`, `Cinematic_Ostinato_100.fsc`.

## 4. Piano Roll Python Scripting (`flpianoroll` `.pyscript` API)
FL-MCP installs both **5 Native FL-MCP Piano Roll Scripts** and **130+ Community Piano Roll Scripts** into `~/.fl-mcp/assets/pyscripts/` and `Settings/Piano roll scripts/`:
- `FL-MCP Euclidean Rhythm.pyscript`: Bjorklund Euclidean rhythm generator (`Pulses`, `Steps`, `Rotation`, `MIDI Note`, `Velocity`).
- `FL-MCP Trap Hat Roll Generator.pyscript`: Subdivides selected notes into `1/16`, `1/24 Triplet`, `1/32`, or `1/64` rolls with crescendo/decay ramps.
- `FL-MCP 808 Glide & Sub Anchor.pyscript`: Centers sub-bass pan below `C3` (`MIDI 48`) and inserts octave portamento slide tails (`n.slide = True`).
- `FL-MCP Humanize & Micro-Swing.pyscript`: Applies deterministic off-beat pocket swing (`ticks`) and velocity humanization.
- `FL-MCP Voice-Leading Chord Voicer.pyscript`: Automatically opens closed bass intervals below `C3` (`MIDI 48`) up an octave to eliminate `180–320 Hz` mud.

## 5. Single-Cycle Waveforms (`AKWF-FREE`) for 3x Osc, Sytrus & Harmor
- `~/.fl-mcp/assets/waveforms/` contains **100+ single-cycle `600-sample` oscillator `.wav` files** (`AKWF_bw_blended`, `AKWF_fmsynth`, `AKWF_tb303`, `AKWF_obxd`, `AKWF_NES`, `AKWF_c64`, `AKWF_vgame`, `AKWF_epiano`, `AKWF_aguitar`, `AKWF_altosax`, `AKWF_violin`, `AKWF_cello`, `AKWF_theremin`).
- Load any `AKWF_*.wav` into **3x Osc** (Osc 1/2/3 ` Custom `?` shape), **Sytrus** (`OSC -> Convert shape to sine harmonics`), or **Harmor** (`IMG / Timbre` dropzone).
