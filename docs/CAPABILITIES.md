# What it can do, and what it cannot

Status key: **live** = works through the documented FL API, **assisted** = needs one step from the user, **file** = delivered as a file, **no** = not possible through any documented route (the server says so instead of faking it).

| Area | Status | Notes |
|---|---|---|
| Transport: play, stop, record, loop, metronome, tempo, position | live | |
| Save, undo | live | `save` writes the current project, `undo` is one step |
| Read project state, channels, mixer, patterns, playlist tracks | live | Paged; default and empty items skipped |
| Name, color, volume, pan, mute, solo of channels, mixer tracks, patterns, playlist tracks | live | Volume and pan in real units |
| Mixer routing: insert assignment, sends and send levels | live | |
| Mixer EQ (built-in 3-band): gain and frequency | live | The built-in EQ has shelves only; a real high-pass needs an EQ plugin |
| Plugin parameters of existing instruments and effects | live | List (filter by name), set by index, read back; values 0-1 |
| Drum patterns in the Channel Rack | live | Step sequencer: steps, velocity, pitch, swing |
| Melodies, chords, bass lines | assisted | Piano roll script: the user runs Tools > Scripting > FL MCP once per part |
| Whole song as notes | file | Standard MIDI File with a track per part and section markers |
| Whole song as a project | file | `.flp` built on the user's template (instruments, patterns, playlist) |
| Section markers | live | Time markers on the timeline |
| Level balance by measurement | live | `fl_mix`: plays, reads peaks, moves faders; needs audible playback |
| Create a channel or load a plugin | no | Not in the API. Use a template project |
| Load a sample or preset | no | Not in the API. Plans carry sound hints instead |
| Place playlist clips in a running project | no | Not in the API. Possible only in a written `.flp` |
| Render or export audio, loudness (LUFS), spectrum | no | Not in the API. Peak levels from the mixer meters are the only measurement |
| Open a project | no | Not in the API (saving is). The user opens the written `.flp` |

## How a professional works, and where this follows it

| Practice | What the server does |
|---|---|
| Choose tempo and key to the genre | Each genre has tempo ranges and default scales (house 120-128, techno 125-140, trap 130-160, boom bap 84-96, lo-fi 68-84...). Minor, major and the hicaz scale (oriental trap) are supported. |
| Arrange in sections | Intro, build, drop, break, outro with intensity curves and role lists per section. |
| Drums as one-shots | Drum parts are one-shots at a single pitch with short gates; hat, clap and percussion get their own groove (swing, ghost notes, rolls). |
| 808 bass | Glides are written as slide notes in a `.flp` (the MIDI file carries plain notes); mono bass lines are cleaned so notes never overlap. |
| Gain staging | Role peak targets that vary by genre (base values: kick -8 dBFS, bass -9, snare -10, clap -12, hats -20), master kept under -3 dBFS. |
| Pan and EQ by role | Hats, open hats, percussion and arps sit off-center. EQ bands (gain at a frequency) are applied to the built-in EQ; the high-pass frequency in the plan is advice, because the built-in EQ has no high-pass. |
| Sends | Reverb and delay send levels per role are part of the plan. |
| Sidechain | A ducking depth per role is in the plan as advice; it is applied through plugin settings that depend on the user's effect chain, which the server does not create. |
| Variation | Fills, drop-outs, rolls, humanization and melody variation (see ARCHITECTURE.md). |

## Not built yet (honest list)

- Audio analysis (loudness, spectrum, stereo width): needs rendered audio; a UI-automation tier for export is possible but fragile and not included.
- A scanner for installed plugins to pick sounds automatically.
- Recording automation by MIDI.
