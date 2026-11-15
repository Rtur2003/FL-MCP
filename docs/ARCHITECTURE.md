# Architecture

FL-MCP controls the FL Studio on the same computer. Nothing here is hosted; the only optional network traffic is the licence service (see COMMERCE.md).

```
MCP client (any model)         Claude Code plugin: skills generated from the same guides
   |  stdio, JSON-RPC            MCP resources + prompts: fl://guide/<topic>, make_beat, ...
   v
fl_mcp.server --- produce / mixing / chain / music / flp / library / audio / selftest
   |             (pure Python, no FL needed)         |                    |
   |  JSON framed in MIDI CC messages                |  files             |  keys, mouse, pixels
   v                                                 v                    v
virtual MIDI port                               .flp, .mid, .wav      desktop backend
(loopMIDI on Windows, IAC on macOS)             FL command line       (opt-in, FL windows only)
   |                                                 |                    |
   v                                                 v                    v
device_FLMCP.py (controller script, embedded Python)         FL Studio
   |  documented FL scripting API only
   v
FL Studio
```

The scripting API is the main door. Because it is limited, there are more (the **capability ladder**, see `CAPABILITIES.md`): the **piano roll script** (`FL MCP.pyscript`), FL's own **UI driven from inside FL** (`ui.navigateBrowser`, key presses), **files** (a `.flp` written for FL to open, a MIDI file, FL's command-line render), and, only when switched on, the **desktop** (screenshots, keys, mouse).

## The link

| Part | Decision |
|---|---|
| Transport | 7-bit MIDI CC messages, no SysEx, so there is no buffer limit to hit. Requests on MIDI channel 15, answers on 16, so one loopback port serves both directions. |
| Frame | `BEGIN(id)`, `DATA(byte)...`, `SUM_A`, `SUM_B` (Fletcher-style checksum modulo 127). A bad checksum is answered with `bad frame` and the server retries with the same id. |
| Exactly once | The script caches its last answer by `(frame id, checksum)`, so a retried request is answered from the cache instead of running twice. |
| Size | A request is at most 16384 bytes. Batches (`produce`, `fl_call`) are cut below 12000 bytes. |
| Handshake | The first call is a `ping`; the server refuses a script older than the version it needs and tells the user to run `fl-mcp install`. |

## The controller script

`src/fl_mcp/fl/device_FLMCP.py` runs inside FL on FL's older embedded Python, so it avoids newer syntax. It understands nineteen operations: `ping status list set tr steps param roll call gain eq peaks markers note grid batch ui probe diag`.

- **Allowlist.** `call` can reach only the FL API modules `transport mixer channels patterns playlist plugins ui general arrangement`; arguments named like `midi.NAME` resolve to constants. There is no `exec`, `eval`, file or network access.
- **Real units.** FL stores volume and EQ as 0-1 values with a non-linear mapping. The script solves for the stored value whose read-back in dB or Hz equals the target (bisection against `getTrackVolume(i, 1)`, `getEqGain(..., 1)`, `getEqFrequency(..., 1)`), so "-8 dB" means -8 dB.
- **Plugins by name and in the plugin's own units.** `param` finds a parameter by name, then solves against the text the plugin itself shows (`plugins.getParamValueString`): 17 samples bracket the wanted value, bisection finishes, the stored value is restored if nothing reaches it, and the answer reports the text FL really shows. Units understood: dB, Hz/kHz, ms/s, %, ratios, steps. Presets: names via `plugins.getName(..., midi.FPN_Preset, ...)`, stepping via `nextPreset`/`prevPreset`.
- **Interface.** `ui` shows, hides and focuses windows, presses an allowlisted set of keys, and walks FL's browser (`navigateBrowser`, `toggleBrowserNode`, `getFocusedNodeCaption`) with a step limit and loop detection. `probe` reports which optional API functions this FL build has; `diag` returns the script's last errors from a small ring buffer.
- **Undo.** A `batch` is one undo step (`general.saveUndo`), so a whole production can be undone with one Ctrl+Z.
- **Measurement.** `peaks` reads `mixer.getTrackPeaks`; the host samples it over time (`mixing.py`).

## What FL's API cannot do, and what is done instead

| Missing in the API | Route used |
|---|---|
| Create channels, patterns, playlist clips | A written `.flp` built on a template (`flp.py`), including cloned **sampler channels** that point at real files; the user (or `fl_desk macro=open`) opens it |
| Load a plugin or sample | FL's own browser driven from inside FL (`uiload.py`: select the target track, focus the browser, go to the top, walk to the first item named like the query, select it, then read the plugin inventory before and after; the next same-named item is tried when nothing appeared); the desktop tier as the fallback |
| Write notes to a pattern | Drums: the step sequencer. Melody and chords: the piano roll script reads `~/.fl-mcp/inbox.json`; `fl_desk macro=script` runs it with FL's "run last script" shortcut and checks `state.json` for the queued serial. Everything: a MIDI file |
| Render or export audio | FL's command line (`FL64.exe /R /E<fmt> project.flp`, `render.py`), or File > Export through the desktop tier for the project that is open (`desk.export`) |
| Measure the mix | `audio.py` on the rendered file (BS.1770 loudness, true peak, spectrum, stereo); mixer peak meters during playback for levels |
| Know what is installed | `library.py` reads FL's plugin database and walks the sample folders, then measures samples (duration, decay, brightness, low end) |
| Anything on screen | `desk.py` with a Windows (user32/GDI) or macOS (System Events) backend |

## Production pipeline

1. `music.compose(genre, key, bpm, length, mood, energy, seed, roles)` returns a `Plan`: sections, patterns (parts of events `t, d, p, v`), a playlist of clips, markers and mix targets per role. It is deterministic for a seed. Genre profiles live in `music/genres.py`, rhythm in `music/groove.py` (a small groove DSL with swing as MPC percent), harmony and scales in `music/theory.py`, assembly in `music/compose.py`.
2. `produce.save_plan` stores the plan as JSON under `~/.fl-mcp/plans`. The model never receives the notes, only a summary, so a song costs a few hundred tokens.
3. Delivery:
   - `apply_live`: tempo, named and colored patterns, drum steps through the sequencer, section markers, per-role pan and EQ. One undo step.
   - `export_midi`: a Standard MIDI File with every part and section markers.
   - `export_flp`: `flp.py` patches the template (see below).
   - `queue`: one part to the piano roll through the inbox file.
4. `mixing.balance` plays the project, measures peaks per track and moves faders until each role is near its target; it reports faders that hit their limit instead of hiding it.
5. `chain.py` compares the plugins on a track with what its role wants (`data/plugins.json`: kinds, order, recipes) and, with `apply`, sets starting values by *meaning*: `plugins_kb.match_intents` finds the threshold, ratio, attack, release, ceiling, wet and so on of any plugin by name words and by the unit in its value text, so it works on plugins it has never seen.
6. `fl_audio` closes the loop: `render.py` or `desk.export` produces audio, `audio.report` measures it and turns the numbers into plain advice against the genre's loudness target. `mastering.py` automates the last step: it finds the limiter on the master, picks its dB gain (or threshold) parameter by meaning, and moves it by the loudness error, measuring after each change.

### Anti-monotony

The engine varies on purpose: fills at phrase ends, drop-outs before drops, snare and hat rolls, per-section density, humanization that changes by section, melodies that vary on repeats, and no pitch repeated more than four times in a row. Chords and arpeggios are keyed to their unit so repeats differ.

## The `.flp` module

`src/fl_mcp/flp.py` reads a project as a list of events (`id`, payload) and writes it back. Events it does not understand are kept byte for byte, so a file from a newer FL version round-trips unchanged; truncated or corrupt files are refused with a reason.

Patch operations: set tempo, replace a pattern's notes and name, clone a pattern from a seed (the template's named pattern with a note), append or replace playlist clips, add instrument channels from `.fst` plugin presets (`FST_FORMAT = 48`) or `.sf2` SoundFonts (via `patch_soundfont_blob` on SoundFont Player event `213`), manage arrangement `end` time markers (`148`/`205`), and validate structure/routing/notes/markers offline via `verify()`. Playlist records are 32 bytes up to FL 20, 60 bytes from FL 21, and 80 bytes in FL 2025; when the length fits multiple formats, the project version decides. Output is a new file; the template is never changed.

Risk, stated plainly: FL does not publish this format, and FL Studio itself is the only complete judge of a file. The module is verified against real FL 20.8.4 and FL 2025 `.flp`/`.fst` files and an independent parser, but a project written for a newer FL version should be opened and checked once by a person.

## Audio analysis

`audio.py` follows ITU-R BS.1770-4 and EBU Tech 3342: K-weighting (the two standard biquads, computed for any sample rate from the standard's analogue prototype and verified against its 48 kHz coefficients), 400 ms momentary blocks every 100 ms (75 % overlap) and 3 s short-term blocks every second, absolute (-70 LUFS) and relative (-10 LU) gating, loudness range from the 10th to the 95th percentile with a -20 LU relative gate, and true peak on a 4x oversampled signal. Spectrum bands, stereo correlation, mid/side level, low-end side level, mono compatibility, DC and clipping are measured on the same file. `advise` turns the numbers into sentences a model can act on. numpy, scipy and soundfile are an optional extra; without them `fl_audio` says so.

## The desktop tier

`desk.py` holds the policy and the macros; `desk_win.py` and `desk_mac.py` hold the operating-system calls behind a `Backend` interface, so the policy is tested against a fake desktop.

- **Three non-intrusive modes (`stealth`, `polite`, `foreground`)**:
  - `stealth` never steals window focus or moves the cursor: screenshots rely strictly on background `PrintWindow(..., PW_RENDERFULLCONTENT)` with DWM frame cropping, and piano-roll script hotkeys use `AttachThreadInput` + `PostMessageW` to FL's focused window (`GUITHREADINFO.hwndFocus`).
  - `polite` (default for `fl-mcp desktop on`) tries background capture and `PostMessageW` first, waits until `GetLastInputInfo` reports the user has been idle (>= 1.2 s) before bringing FL forward when a UI dialog demands focus, and calls `restore_window` to return focus to the user's previous window afterwards.
  - `foreground` permits immediate focus changes and fallback cursor operations.
- **Hidden-desktop CLI rendering & cursor-less popups**: On Windows, `render.hidden_popen` spawns `FL64.exe /R` inside a private `CreateDesktopW` desktop (`FLMCP_Render_<pid>_<tid>`) with `SW_HIDE`, `CREATE_NO_WINDOW`, and `BELOW_NORMAL_PRIORITY_CLASS`, copying isolated projects via `always_copy=True` so parallel offline renders never surface windows or collide with open FL. `popupkeys.py` confirms/dismisses `TQuickPopupMenuWindow` via `PostMessageW` (`VK_RETURN`/`VK_ESCAPE`, then client coordinates / `WM_CANCELMODE`) and only touches physical cursor fallback in `foreground` mode (restoring cursor position afterwards).
- Key combos are parsed to a small grammar (`mod+shift+r`); window-closing and system keys are blocked. A mouse in the top-left corner raises a failsafe.
- A screenshot captures FL's main window in the background or the union of FL's visible windows in the foreground, shrunk to at most 1568 px wide; later clicks are given in that picture's coordinates and scaled back. A click before any screenshot is refused.
- Fixed-width ctypes structures (`INPUT` = 40 B, `LASTINPUTINFO` = 8 B, `GUITHREADINFO` = 72 B on 64-bit) keep struct sizes portable across OSes. The Windows backend requests per-monitor DPI awareness so coordinates stay exact.
- Off by default: `fl-mcp desktop on|stealth|polite|foreground` writes `desktop.on` (or `FL_MCP_DESKTOP=stealth|polite|foreground|1|0`); `fl_desk` is not registered while off. See `docs/FL_STUDIO_NOTES.md` for verified binary/Win32 format notes.

## Guides, prompts and the plugin

The knowledge a model needs lives in five markdown files in `src/fl_mcp/data/guides`. `guides.py` fills their tables from the live data (genre profiles, loudness targets, instruments) and serves them as MCP resources and prompts. `scripts/build_plugin.py` wraps the same files as the skills of the Claude Code plugin (`plugin/`), writes the manifest from the package version, and `--check` (run by a test) fails if a committed file differs. Another test checks that every `fl_*` tool and every `argument=` a guide mentions exists, so a guide cannot describe a tool that is gone.

## Self-test and setup

`fl-mcp selftest` runs `selftest.py` against the FL that is running: link and script version, status, every list, a small write and put-back of one mixer volume and the tempo, a plugin parameter solved to its own displayed text and restored, presets, windows, the browser, and what the audio, render and desktop layers have. The result is a JSON report. `fl-mcp setup` installs the scripts, checks the MIDI port and prints the client text from `clients.py`.

## Testing

| Layer | How |
|---|---|
| FL script | Runs for real against a fake FL built on the official stubs (`tests/fakefl.py`). |
| API contract | An AST test checks every FL call in the script against the stubs' signatures; mutation-tested. |
| Link | Frame, checksum, retry and de-duplication tests, including dropped frames. |
| MCP surface | In-memory client against the server: schemas stay flat, descriptions short, results plain JSON. |
| Music | Properties (no overlapping one-shots, notes in key, section structure, seeds reproduce), MIDI files re-read with mido. |
| `.flp` | Byte-exact round trip, every truncation fails cleanly, real files and an independent parser when available. |
| Credits | Random sequences of grants, holds, settlements and expiries must keep the books balanced; mutations of the ledger are caught. |
| Plugins | Parameter lookup, real-unit solving (including failures that restore the value), presets, the interface and browser ops, against a fake with several plugin families. |
| Sounds | Library scan, role ranking and kit selection on generated audio; generated `.flp` sampler channels re-read by an independent parser (when available) and checked against a real FL 20.8.4 file. |
| Audio | Known tones give known LUFS at several sample rates; gating, loudness range, true peak between samples, spectrum, stereo, clipping; render with a fake FL process. |
| Desktop | Key grammar and blocked keys, foreground checks before every step, failsafe, click scaling, macros against a fake desktop, PNG encoding, Windows structure sizes. |
| Self-test | Run against the FL fake: every check ends ok or skipped with a reason; writes are put back, also when one fails. |
| Guides and plugin | Committed plugin equals generated plugin; front matter limits; every tool and argument in a guide exists; `claude plugin validate --strict` when the CLI is present. |
| Bundle | The unpacked `.mcpb` is started over stdio and lists its tools. |

## Extending

- **A genre:** add a profile in `music/genres.py`; the genre tests iterate over all profiles.
- **An FL operation:** add it to `device_FLMCP.py`, register it in `_OPS`, raise `VERSION`, and raise `SCRIPT_VERSION` in `bridge.py` so old installs are detected.
- **A metered operation:** add its price to the service config and wrap the work in `_spend("name")` in `server.py`.
