---
name: plugins
description: "Find and control any FL Studio plugin by name in real units, load presets, choose instruments and effects by role, and find sounds on disk."
when_to_use: "Use when the user wants a plugin parameter set, a preset loaded, a synth or effect chosen, a sample or drum kit picked, or asks what a plugin does."
---

# Plugins and sounds

Every plugin FL can host is reachable by name, stock or third party. Read what is there, set values in real units, load a preset, and choose instruments by role.

## Read

- `fl_list kind=plugins`: every plugin in the project: where it sits (`c` channel rack, `m` mixer), its index and slot, name, parameter and preset counts.
- `fl_param target=mixer index=<track> slot=<0-9>` or `target=channel index=<channel>`: parameters as `[i, name, val, text]`: `val` is FL's 0-1 position, `text` is what the plugin shows ("-18 dB", "2.5 kHz", "4:1"). `find=<part of a name>` filters, `start`/`limit` page. `view=info` gives counts and the current preset.
- Parameter names are whatever the plugin exposes. If a third-party plugin shows only "Param 1..n", use presets or read the window with `fl_desk`.

## Set

- One parameter in the plugin's own unit: `param="Threshold" real="-18 dB"`. The server solves against the plugin's displayed text, so the result really reads -18 dB; the answer returns `text` and `ok`. Units: dB, Hz and kHz, ms and s, %, ratios like 4:1, steps.
- Several at once: `settings="Threshold=-18 dB, Ratio=4:1, Attack=20 ms, Gain=0.6"` (a bare number is the 0-1 position). At most 40 per call.
- 0-1 position: `param=<name or index> value=0.5`.
- Presets: `view=presets` lists names; `preset="Warm Pad"` loads one; `preset="+1"` or `"-1"` steps through them.
- Meaning instead of names: `fl_mix action=chain apply=true` finds the threshold, ratio, attack, release, makeup, ceiling, wet, decay, pre-delay, low cut, high cut, feedback, time, width and drive parameters of a plugin by meaning and unit and sets them for the track's role.

## Stock effects (starting points)

| Job | Plugin | Start with |
| --- | --- | --- |
| EQ | Fruity Parametric EQ 2 | high-pass the role (table in the mix guide), cut 200-300 Hz if boxy, +2 dB at 8-12 kHz for air |
| Compression | Fruity Limiter (compressor section), Maximus (multiband), Fruity Compressor | threshold -18 dB, ratio 3-4:1, 2-4 dB of reduction |
| Limiter | Fruity Limiter | last on the master, ceiling -1.0 dB |
| Reverb | Fruity Reeverb 2, LuxeVerb | on a send, 25-35 % wet, pre-delay 20-40 ms, low cut 150 Hz |
| Delay | Fruity Delay 3 | on a send, feedback 30-40 %, time synced to the tempo |
| Saturation | Fruity Fast Dist, Fruity WaveShaper, Soundgoodizer | small amounts add harmonics to bass on small speakers |
| Width | Fruity Stereo Shaper | widen mids and highs; keep the lows mono |
| Modulation | Fruity Chorus, Fruity Phaser, Fruity Flanger | light, on pads and leads |
| Filter | Fruity Filter, Fruity Love Philter | sweeps for builds |
| Pitch | Pitcher, NewTone, Vocodex | vocals |
| Meter | Wave Candy, Spectroman | look, do not change the sound |

## Instruments by role

- **kick**: Fruity Kick (Kick synth: tune the pitch and decay); Drumsynth Live (Synthesised drums); Sampler (A sample on a channel: the standard way to play one-shots)
- **snare**: Drumsynth Live (Synthesised drums); Sampler (A sample on a channel: the standard way to play one-shots)
- **hat**: Drumsynth Live (Synthesised drums); Sampler (A sample on a channel: the standard way to play one-shots)
- **clap**: Sampler (A sample on a channel: the standard way to play one-shots)
- **perc**: Drumsynth Live (Synthesised drums); Sampler (A sample on a channel: the standard way to play one-shots)
- **bass**: 3X Osc (Three oscillators: the quickest way to a clean sub or simple saw); Sytrus (FM/subtractive hybrid with many presets); Harmor (Additive synth; strong presets for leads and pads); Toxic Biohazard (Aggressive modulation synth); Transistor Bass (Acid bass line machine); Wasp (Gritty monosynth); Flex (Preset-based synth with a large library)
- **chords**: Fl Keys (Piano and electric piano sampler); Sytrus (FM/subtractive hybrid with many presets); Flex (Preset-based synth with a large library); Direct Wave (Multisample player)
- **pad**: 3X Osc (Three oscillators: the quickest way to a clean sub or simple saw); Sytrus (FM/subtractive hybrid with many presets); Harmor (Additive synth; strong presets for leads and pads); Flex (Preset-based synth with a large library); Kepler (Vintage-flavoured polysynth); Direct Wave (Multisample player)
- **lead**: 3X Osc (Three oscillators: the quickest way to a clean sub or simple saw); Sytrus (FM/subtractive hybrid with many presets); Harmor (Additive synth; strong presets for leads and pads); Sakura (Physical model of plucked strings); Toxic Biohazard (Aggressive modulation synth); Wasp (Gritty monosynth); Flex (Preset-based synth with a large library); Kepler (Vintage-flavoured polysynth); Plucked (Physical model: plucks and guitars); Direct Wave (Multisample player)
- **arp**: Plucked (Physical model: plucks and guitars)
- **fx**: Sampler (A sample on a channel: the standard way to play one-shots)
- **drums**: Fpc (Pad-based drum sampler; pad names come from the kit)
- **pluck**: Sakura (Physical model of plucked strings)

Quick recipes: **kick** Fruity Kick, tune the pitch to the song's root note, short decay, a little click; **sub bass** 3x Osc with a sine/saw mix, low-pass about 800 Hz, mono; **pad** Harmor or Sytrus with attack 300 ms or more, a long release and a wide chorus; **pluck** Sakura or Plucked!, short decay; **lead** Sytrus, Harmor, Wasp or Toxic Biohazard with a filter envelope; **piano** FL Keys; **drums from samples** the Sampler or FPC, one pack per kit. For complex synths choose by preset (`view=presets`), then adjust two or three parameters.

## Adding a plugin or a sample

FL's API cannot create plugins, so load them the way a person does, driven from inside FL: the automation guide shows the `fl_ui` browser route (`browse=goto query="Fruity Limiter"`, then `select`) and the `fl_desk` fallback. Afterwards check with `fl_list kind=plugins` that it landed on the track you meant.

## Sounds on disk

`fl_library action=scan` indexes FL's sample packs and the folders in `FL_MCP_SAMPLES` once. `find role=<kick|snare|clap|hat|ohat|perc|bass|fx|chords|pad|lead>` ranks samples by name and by measured sound (length, decay, brightness, low end); `kit genre=<g>` picks one coherent drum kit; `info path=<file>` measures one file; `action=plugins` lists installed plugins by kind.
