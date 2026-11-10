# FL-MCP

MCP server for **FL Studio**. Any MCP client that can start a local server (Claude, Codex CLI, Gemini CLI, Cursor, VS Code, local models) can inspect and control the FL Studio running on your computer: drive every plugin by name and in real units, produce a whole song fast, mix and master it by measurement, and do the things FL's scripting API cannot.

- **Local.** The server runs on your machine and talks to FL Studio through a virtual MIDI port. No cloud is involved in controlling FL.
- **14 tools, about 4k tokens** for the whole list (a test holds the line). Flat schemas (no `$ref`/`anyOf`/`title`) and plain JSON results, so small and strict models can use them. An opt-in 15th tool, `fl_desk`, adds screen and keyboard control.
- **Beyond the API.** Where FL's scripting API stops (load a plugin, write notes, export audio, create channels), a ladder of other routes takes over: FL's own UI driven from inside FL, generated `.flp` projects, FL's command-line render, and the desktop. See `docs/CAPABILITIES.md`.
- **Guides for every client.** What a model needs to know about producing, mixing and plugins ships as MCP resources and prompts, and as a Claude Code plugin with skills.
- **Safe link to FL:** a small controller script inside FL answers framed MIDI CC messages. No `exec`/`eval`, no network, no SysEx. Only allowlisted FL API modules are reachable.
- Windows and macOS, FL Studio 2024+ (scripting API 33+). Python 3.10-3.12 (`python-rtmidi` has no wheels for 3.13+; `uv` picks a matching Python for you).

> **Status.** Verified live against **FL Studio 2025 (`Producer Edition v25.2.5 [build 5319]`, API 40)** on Windows 11 (`fl-mcp selftest --deep`: 17/17 checks passing), as well as against a stateful fake FL built on the official API stubs, real FL Studio project files (`.flp`), and the ITU-R BS.1770 reference loudness filter. After setup run `fl-mcp selftest`: it checks every layer against your FL, puts back what it touched, and saves a report you can send with an issue.

## Setup

**1. Install** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install "fl-mcp[audio] @ git+https://github.com/Rtur2003/FL-MCP"
```

```bash
fl-mcp setup
```

`fl-mcp setup` copies the controller script and the piano roll script into `Documents/Image-Line/FL Studio/Settings`, checks the MIDI port, and prints the exact text that registers the server with your client. (`fl-mcp install` does only the copying; `--fl-dir` points at a user data folder elsewhere.) The `audio` extra adds numpy, scipy and soundfile for loudness analysis; leave it out for a smaller install.

**2. Loopback MIDI port**

- Windows: install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) and create a port named `FL MCP`.
- macOS: Audio MIDI Setup > MIDI Studio > enable the **IAC Driver** (found automatically).

**3. FL Studio:** `Options > MIDI Settings`

- Input: select `FL MCP`, enable it, controller type **FL MCP**, port number `N`.
- Output: select `FL MCP`, enable it, port number the **same `N`**.
- Restart FL Studio (or reselect the controller type to reload the script).

**4. Check:** `fl-mcp doctor` (each layer in one line), then `fl-mcp selftest` (every feature, with a report; `--deep` also loads a plugin through FL's browser and undoes it).

**5. Add to your client:** `fl-mcp config <client>` prints the exact text and file for `claude-desktop`, `claude-code`, `cursor`, `vscode`, `windsurf`, `gemini` and `codex` (`--source git` to run from this repository, `--source local` to run from the Python that is installed). For example:

```bash
claude mcp add fl-studio -- uvx --from "fl-mcp[audio]" fl-mcp
```

Claude Desktop can instead install the `.mcpb` extension (`python packaging/build_mcpb.py` builds it); you still do steps 2 and 3.

### Claude Code plugin

```
/plugin marketplace add Rtur2003/FL-MCP
/plugin install fl-studio@fl-mcp
```

The plugin starts the server and adds five skills (**setup**, **produce**, **mix**, **plugins**, **automation**) and a `mix-engineer` subagent. See `plugin/README.md`.

## Tools

| Tool | What it does |
|---|---|
| `fl_produce` | **Fast production.** Builds a genre-aware song plan and delivers it: drums into the Channel Rack, a MIDI file, or a finished `.flp` with samples (see below) |
| `fl_mix` | `doctor` finds problems worst first; `meter` and `balance` use FL's peak meters while the project plays (kick -8 dB, bass -9 dB, hats -20 dB...); `chain` checks each track's plugins against its role and sets compressor, limiter, reverb and delay values by meaning |
| `fl_audio` | **Hear the mix.** LUFS (integrated, short-term, momentary), loudness range, true peak, crest factor, clipping, DC, stereo width and mono loss, a 7-band spectrum and plain advice; renders the project first (the open one through `fl_desk`, or a saved `.flp` through FL's command line with FL closed), compares with a reference; `master` moves the master limiter's gain until the loudness hits the genre's target, measuring after each step |
| `fl_param` | **Any plugin**, stock or third party: list parameters by name, set one in real units (`real="-18 dB"`), set many (`settings="Threshold=-18 dB, Ratio=4:1"`), read and load presets |
| `fl_list` | channels, mixer tracks, patterns, playlist tracks or every plugin in the project |
| `fl_library` | indexes your sample packs and plugins; ranks samples for a role by name and by measured sound; picks a coherent drum kit for a genre |
| `fl_ui` | drives FL's own interface from inside FL, no mouse: show and focus windows, press keys, walk the browser; `load` finds a plugin or sample by name, loads it onto a mixer track or as a new channel, and checks that it appeared |
| `fl_status` | FL version, project, tempo, state; `probe` lists the API features this FL build has, `log` the script's last errors |
| `fl_transport` | play, pause, stop, record, loop mode, metronome, tempo, position (bar), save, undo |
| `fl_set` | name, color, volume, pan, mute, solo, routing (`fx`, `send`/`level`), select on a channel, mixer track, pattern or playlist track |
| `fl_steps` | step sequencer of a channel: set/clear steps, velocity, pitch; reads back |
| `fl_notes` | queue piano roll notes and read the piano roll back |
| `fl_call` | raw FL scripting API, batched (`modules`: transport mixer channels patterns playlist plugins ui general arrangement) |
| `fl_api` | API reference for `fl_call`: signatures and `midi` constants; works without FL |
| `fl_desk` | *opt-in* (`fl-mcp desktop on`). See and drive FL's screen: screenshots, keys, clicks, and macros that run the piano roll script, export audio, open and save projects |

Try: *"Make a 128 BPM techno track in F# minor, then mix and master it."*

## Fast production

`fl_produce` composes with a seeded engine (the same seed gives the same song) in 12 genres: house, techno, trance, drum and bass, dubstep, trap, oriental trap (hicaz), boom bap, lo-fi, pop, synthwave, reggaeton. A plan has sections (intro, build, drop, break...), patterns for kick, snare, clap, hats, percussion, bass, chords, pad, arp, lead and fx, a playlist, markers, and mix targets per role (peak level, pan, EQ, send levels, sound hints). Fills, drop-outs, rolls and varying humanization keep it from sounding like a loop.

| Action | Result |
|---|---|
| `make` | plan + deliver: drums go into Channel Rack channels (matched by name: kick, hat, bass, lead...) as named patterns, with tempo, markers, EQ and pan; plus a `.mid` with every part, written to `~/.fl-mcp/out/` |
| `plan`, `show`, `genres` | build and read plans without touching FL |
| `apply`, `midi` | deliver or export a stored plan again |
| `queue` | send one melodic part to the piano roll (see below) |
| `inspect` | read a template `.flp`: channels, roles it can fill, patterns, playlist |
| `flp` | write a full project: your template plus every pattern with all notes and the playlist; `sounds=auto` also adds sample channels from one pack that `fl_library` chose |

**Templates.** A finished project is built on a template `.flp` you made (any saved project with one named pattern holding a note and one playlist clip). It is never modified; the result is a new file. Channel names are matched to roles (`Kick`, `808 Bass`, `Lead Synth`...); `channels="kick=0,bass=3"` overrides the match. Open the result with `fl_desk macro=open` (desktop control on) or by hand.

### Piano roll notes

FL's API cannot write notes. `fl_notes` and `fl_produce action=queue` write them to `~/.fl-mcp/inbox.json`, select the channel and open the piano roll. Apply them with `fl_desk macro=script` (it presses FL's "run last script" shortcut and confirms the notes arrived) or run **Tools > Scripting > FL MCP** yourself once and press Accept. That run also exports the piano roll, so `fl_notes(read=true)` confirms and reads it back. Notes are `[midi_note, start_beat, length_beats, velocity]`.

## Desktop control (opt-in)

```bash
fl-mcp desktop on      # then restart your AI client; `off` undoes it, `test` takes a screenshot
```

`fl_desk` gives the model eyes and hands for what no API covers: `shot` returns a picture of FL's windows (dialogs included), `click`/`double`/`right`/`drag`/`scroll` take positions off the last picture, `type` and `key` enter text and combos (`mod+s`; `mod` is Ctrl on Windows, Cmd on a Mac). Safety: input goes to FL windows only (FL is brought forward first, and nothing is sent if that fails), keys that close windows or leave FL are blocked, and moving the mouse to the top-left corner stops everything. Windows uses user32/GDI (no extra packages); macOS uses System Events and `screencapture` (grant Accessibility and Screen Recording; `cliclick` adds double-click, right-click and drag).

## Guides, prompts and resources

`fl://guide/setup`, `produce`, `mix`, `plugins` and `automation` are MCP resources (markdown, with tables filled from the real genre and plugin data). The prompts `make_beat`, `mix_check`, `master`, `fix_connection` and `load_plugin` carry the task plus the relevant guide, so clients that surface prompts as slash commands get one-shot workflows. The Claude Code plugin's skills are generated from the same files (`python scripts/build_plugin.py`; a test fails if they drift).

## Limits

FL's scripting API cannot create channels or plugins, load samples, place playlist clips, or render audio; this server does not fake those. The `.flp` writer, FL's own UI, the command-line render and the desktop tier cover them; each reports plainly when it cannot. `docs/CAPABILITIES.md` lists what is possible, by which route, and how sure we are. `docs/RESEARCH.md` records what other FL, Ableton and DaVinci Resolve MCPs do and how paid MCP access is built.

## Licence and credits

This repository builds an **unmetered** server: nothing needs a licence. A commercial build can bake in a licence service address; then `fl_produce` (new plans, `.flp` projects) and `fl_mix balance` use credits, everything else stays free and offline. `fl-mcp login`, `fl-mcp logout` and `fl-mcp account` manage the licence. See `docs/COMMERCE.md`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FL_MCP_PORT` | `FL MCP` | MIDI port name (substring match; `IAC` is tried on macOS) |
| `FL_MCP_TIMEOUT` | `2.5` | seconds to wait per attempt (two attempts) |
| `FL_MCP_DIR` | `~/.fl-mcp` | folder for plans, output, the library index and the piano roll exchange (set it before `fl-mcp install`) |
| `FL_MCP_SAMPLES` | unset | extra sample folders for `fl_library` (path-separated) |
| `FL_MCP_FL_DIR` | auto | FL Studio install folder(s) (for the command-line render) |
| `FL_MCP_PROJECTS` | unset | extra folders where finished projects and exports are looked for |
| `FL_MCP_DESKTOP` | unset | `1` turns `fl_desk` on, `0` off (overrides `fl-mcp desktop`) |
| `FL_MCP_FL_PROCESS` | unset | extra process name(s) that count as FL Studio for `fl_desk` |
| `FL_MCP_DEBUG` | unset | log to stderr |
| `FL_MCP_SERVICE` | unset | licence service address (https); unset means unmetered |
| `FL_MCP_LICENSE` | unset | licence key to sign in with automatically (set by the desktop extension and the plugin) |

## Troubleshooting

Run `fl-mcp doctor`, then `fl-mcp selftest`. Typical causes of *No answer from FL Studio*: the port is not enabled for both input and output with the same port number, controller type is not `FL MCP`, the script was installed after FL started, or a modal dialog is open in FL. `FL MCP script is outdated`: run `fl-mcp install` again and restart FL. The FL script log is in `View > Script output`; `fl_status log=true` shows the script's own error list. The `setup` guide has a symptom-to-fix table.

## How it works

The server sends each request as JSON framed in 7-bit MIDI CC messages (channel 15 to FL, channel 16 back) with a checksum and automatic retry; the controller script de-duplicates retried requests, so nothing runs twice. The same port carries both directions, so a loopback port is enough. `docs/ARCHITECTURE.md` has the details.

## Development

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python scripts/gen_api_index.py    # refresh src/fl_mcp/api.json from the API stubs
.venv/bin/python scripts/build_plugin.py     # regenerate plugin/ after editing a guide
claude plugin validate plugin --strict       # when the claude CLI is installed
```

MIT licensed. The API index holds only function signatures and constants generated from [fl-studio-api-stubs](https://github.com/MaddyGuthridge/FL-Studio-API-Stubs). See `NOTICE` for how the `.flp` format knowledge was gathered.
