# What it can do, and by which route

Routes, from most to least certain. The server tries them in this order and says which one it used.

1. **API**: FL's documented scripting API, through the controller script.
2. **UI**: FL's own interface driven from inside FL (windows, keys, the browser) through the same script. Works on what the FL build exposes (`fl_status probe=true`).
3. **File**: a `.flp`, `.mid` or audio file written or read on disk.
4. **Command line**: FL started with `/R /E<fmt> /F<folder> /O<folder>` inside a hidden Win32 desktop (`CreateDesktopW` + `SW_HIDE`, `always_copy=True`) to render a saved `.flp` in parallel without stealing focus or interrupting an open FL window.
5. **Desktop**: screenshots, keys and mouse (`stealth`, `polite`, `foreground`). Off until `fl-mcp desktop on` (defaults to `polite`).

Confidence: **verified** = tested against real FL files or a reference implementation; **tested** = tested against the fake FL built on the official stubs; **unseen** = depends on live FL behaviour no test here can show (the first `fl-mcp selftest` on a real machine settles it).

| Area | Route | Confidence | Notes |
| --- | --- | --- | --- |
| Transport: play, stop, record, loop, metronome, tempo, position; save, undo | API | tested | `undo` is one step; a produced song is one undo step |
| Read project state, channels, mixer, patterns, playlist tracks, **every plugin** | API | tested | Paged; default and empty items skipped |
| Name, color, volume, pan, mute, solo of channels, mixer tracks, patterns, playlist tracks | API | tested | Volume and pan in real units |
| Mixer routing: insert assignment, sends and send levels | API | tested | |
| **Any plugin, stock or third party**: find a parameter by name, set it in the plugin's own units, set many at once | API | verified (FL 2025) | Solves against the plugin's displayed text; plugins that report no text fall back to 0-1 values |
| Presets: list names, load by name, step | API | verified (FL 2025) | Depends on the plugin reporting presets to the API |
| Plugin chains by role: check order, set compressor/limiter/reverb/delay values by meaning | API | verified (FL 2025) | Works on plugins already loaded |
| Drum patterns in the Channel Rack | API | verified (FL 2025) | Step sequencer: steps, velocity, pitch |
| Melodies, chords, bass lines | File + UI | verified (FL 2025) | Dialogless piano roll script in `Settings/Piano roll scripts`; `fl_desk macro=script` repeats it with FL's shortcut and verifies |
| Whole song as notes | File | verified | Standard MIDI File, a track per part, section markers |
| Whole song as a project, with samples | File | verified | `.flp` authoring on a template: channels, samplers, `.fst` presets, `.sf2` SoundFonts, playlist clips, `end` markers, and `verify()` offline checks; checked against FL 20.8.4, FL 2025 `.flp`/`.fst`, and an independent parser |
| Open or save a project | Desktop | verified (FL 2025) | `fl_desk macro=open` / `save` (waits for idle and restores focus in `polite` mode) |
| Create a channel, load a plugin or sample | UI, then Desktop | verified (FL 2025) | `fl_ui action=load` confirms `TQuickPopupMenuWindow` via `PostMessageW`/client coords without moving the cursor (cursor fallback only in `foreground` mode); `fl-mcp selftest --deep` tests it on your FL |
| Place playlist clips | File | verified | In a written `.flp` (auto-updates arrangement `end` marker) |
| Window state, key presses, browser walking | UI | verified (FL 2025) | Uses `ui.navigateBrowser` and friends (`probe` confirms 13/13 on FL 2025 API 40) |
| Render or export audio | Command line, Desktop | verified (FL 2025) | `FL64.exe /R /E<fmt> /F... /O...` in an isolated hidden Win32 desktop when `project=` is given on Windows, or `fl_desk macro=export` in `polite`/`foreground` for the open project |
| Loudness (LUFS), loudness range, true peak, crest, clipping, DC | File | verified | BS.1770-4 / EBU 3342 on the rendered file |
| Spectrum, stereo width, correlation, mono loss, low-end width | File | tested | 7 bands; correlation and mid/side from the channels |
| Compare with a reference track | File | tested | Band differences with advice |
| Master to a loudness target | API + Desktop | verified (FL 2025) | `fl_audio master`: export, measure, move the limiter's gain by the error, repeat (3 adjustments at most) |
| Level balance by measurement | API | verified (FL 2025) | `fl_mix`: plays, reads peaks, moves faders; needs audible playback |
| Problem finding: clipping, headroom, routing, pan, missing plugins | API | verified (FL 2025) | `fl_mix doctor`, worst first |
| Find sounds: sample packs, installed plugins; rank by role and by measured sound; pick a coherent kit | File | verified (FL 2025) | `fl_library`; samples measured for length, decay, brightness, low end |
| See the screen, click, type | Desktop | verified (Windows 11) / tested (macOS) | `stealth` (`PrintWindow` + `PostMessage`, zero focus steal), `polite` (background first, idle gate + restore window), and `foreground`; opt-in |
| Recording by MIDI, plugin sidechain source, automation clips | not done | | Possible through the desktop tier case by case; no tool of its own yet |

## How a professional works, and where this follows it

| Practice | What the server does |
| --- | --- |
| Choose tempo and key to the genre | Each genre has tempo ranges and default scales (house 120-128, techno 125-140, trap 130-160, boom bap 84-96, lo-fi 68-84...). Minor, major and the hicaz scale (oriental trap) are supported. |
| Arrange in sections | Intro, build, drop, break, outro with intensity curves and role lists per section. |
| Drums as one-shots, from one kit | Drum parts are one-shots with short gates; hat, clap and percussion get their own groove (swing, ghost notes, rolls). `fl_library kit` picks all drum samples from one pack. |
| 808 bass | Glides are written as slide notes in a `.flp`; mono bass lines are cleaned so notes never overlap. |
| Gain staging | Role peak targets that vary by genre (base values: kick -8 dBFS, bass -9, snare -10, clap -12, hats -20), master kept under -3 dBFS before the limiter; `fl_mix balance` moves faders until they are met. |
| EQ and compression by role | High-pass per role, small cuts and lifts, compressor starting points per role (`data/plugins.json`), set in real units on the plugins the track has. |
| Sends | Reverb and delay on shared send tracks; send levels per role are part of the plan. |
| Check with meters | LUFS against the genre's release loudness, true peak under -1 dBTP, crest factor, stereo and mono compatibility, spectrum against a reference. |
| Variation | Fills, drop-outs, rolls, humanization and melody variation (see ARCHITECTURE.md). |

Sidechain ducking depth is part of the plan as advice; the plugin's sidechain *source* is chosen in the plugin's own window, which the API cannot reach (the desktop tier can).
