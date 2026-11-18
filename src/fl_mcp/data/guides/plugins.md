# Plugins and sounds

Every plugin FL can host is reachable by name, stock or third party. Read what is there, set values in real units, load a preset, and choose instruments by role.

## Read

- `fl_list kind=plugins`: every plugin in the project: where it sits (`c` channel rack, `m` mixer), its index and slot, name, parameter and preset counts.
- `fl_param target=mixer index=<track> slot=<0-9>` or `target=channel index=<channel>`: parameters as `[i, name, val, text]`: `val` is FL's 0-1 position, `text` is what the plugin shows ("-18 dB", "2.5 kHz", "4:1"). Use `find=<text>` or semantic category filters `find="cat:Filter"` (`Filter`, `Env`, `Comp`, `Space`, `Drive`, `Mod`, `Osc`, `Level`, customizable in `~/.fl-mcp/fx_prefs.json`). `start`/`limit` page with `next` cursor. `view=info` gives counts and preset; `view=io` reads routing, sends, `out_ctrls`, `out_voices`, FPC/drum `notes` pads, `mix`, `muted`, `track_slots_enabled`, `automation_enabled`, `midi_in_port`.
- Parameter names are whatever the plugin exposes (multi-word search matches tokens such as `"cutoff freq"` -> `"Filter 1 Cutoff Frequency"`). If a third-party plugin shows only "Param 1..n", use presets or read the window with `fl_desk`.

## Set

- One parameter in the plugin's own unit: `param="Threshold" real="-18 dB"`. The server solves against the plugin's displayed text, so the result really reads -18 dB; the answer returns `text` and `ok`. Units: dB, Hz and kHz, ms and s, %, ratios like 4:1, steps.
- Wrapper controls: `param="fl:mix" real="80%"` (dry/wet `0-1` or `%` on mixer slots), `param="fl:mute" value=1` (`1` bypass/mute or `0` active), `param="fl:slots" value=0` (track FX rack enable `1`/`0`), and `param="fl:focus"` (focuses the plugin editor on channel or mixer slot); each also works in `settings` name-value pairs.
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

{{instruments}}

Quick recipes: **kick** Fruity Kick, tune the pitch to the song's root note, short decay, a little click; **sub bass** 3x Osc with a sine/saw mix, low-pass about 800 Hz, mono; **pad** Harmor or Sytrus with attack 300 ms or more, a long release and a wide chorus; **pluck** Sakura or Plucked!, short decay; **lead** Sytrus, Harmor, Wasp or Toxic Biohazard with a filter envelope; **piano** FL Keys; **drums from samples** the Sampler or FPC, one pack per kit. For complex synths choose by preset (`view=presets`), then adjust two or three parameters.

## Adding a plugin or a sample

FL's API cannot create plugins, so load them the way a person does, driven from inside FL: `fl_ui action=load query="Fruity Limiter" index=<mixer track>` (no `index` for an instrument or sample, which becomes a new channel). It checks that something really appeared and tells you where; the automation guide has the step-by-step route and the `fl_desk` fallback. Afterwards `fl_list kind=plugins` confirms the track and slot.

## Sounds on disk

`fl_library action=scan` indexes FL's sample packs, SoundFonts, `.fst` presets, and `FL_MCP_SAMPLES`. `find` accepts single roles or comma-separated multi-role queries (`role="kick,snare,hat"`) and ranks by name and acoustic features; `similar path=<wav>` finds acoustically closest samples by weighted Euclidean distance (`dur`, `decay_ms`, `zcr_hz`, `low`, `rms_db`); `kit genre=<g>` picks one coherent drum kit; `info path=<file>` measures one file; `action=plugins` lists installed instruments, effects, `.sf2` SoundFonts, and `.fst` presets.
