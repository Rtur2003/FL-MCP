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

**Quickest zero-touch setup (`fl-mcp onboard`):**

```bash
fl-mcp onboard --install-midi --desktop
```

`fl-mcp onboard` installs the FL Studio controller & piano roll scripts (FL 20–26+), ensures the virtual MIDI port (`loopMIDI` on Windows / CoreMIDI IAC on macOS), pairs FL Studio's F10 MIDI settings automatically in Registry on Windows (when FL is closed), starts the persistent background Studio + HTTP MCP daemon, and registers detected AI clients across **12 clients** (`claude-desktop`, `claude-code`, `cursor`, `vscode`, `windsurf`, `gemini`, `antigravity`, `codex`, `cline`, `roo-code`, `zed`, `opencode`). Use `fl-mcp config <client|all> --write --transport http` or Studio's **One-Click Auto-Setup** button for individual control.

**Manual setup steps:**

**1. Install** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install "fl-mcp[audio] @ git+https://github.com/Rtur2003/FL-MCP"
fl-mcp setup --write
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

**5. Add to your client:** `fl-mcp config <client|all> [--write]` prints or writes the config for `claude-desktop`, `claude-code`, `cursor`, `vscode`, `windsurf`, `gemini`, `antigravity`, `codex`, `cline`, `roo-code`, `zed`, and `opencode` (`--source auto|pypi|git|local`, `--transport http|stdio`). Or use `fl-mcp onboard` to auto-configure scripts, virtual MIDI, FL F10 bindings, daemon, and detected clients together. For example:

```bash
claude mcp add fl-studio -- uvx --from "fl-mcp[audio]" fl-mcp
```

Claude Desktop can instead install the `.mcpb` extension (`python packaging/build_mcpb.py` builds it); full release bundles (wheel, sdist, `.mcpb`, Claude Code plugin zip, standalone FL scripts bundle, `SHA256SUMS.txt`) are built via `python packaging/build_release.py` or automatically when a `v*` tag is pushed.


### Studio desktop app, background service & live traffic inspector

```bash
fl-mcp app                # launch FL-MCP Studio Desktop App (or run `fl-mcp-studio`)
fl-mcp app --native       # open zero-dependency native tkinter desktop control panel
fl-mcp app --install-shortcut   # create Start Menu / macOS .app / Linux .desktop launcher
fl-mcp service start      # detached daemon: Studio UI + http://127.0.0.1:8765/mcp + SSE
fl-mcp service status     # inspect running daemon PID, endpoints, autostart & log path
fl-mcp service autostart enable   # optional login autostart (Registry / launchd / systemd)
fl-mcp traffic --follow   # stream live MIDI TX/RX frames and MCP calls in terminal
fl-mcp templates          # list auto-discovered .flp project templates
```

`fl-mcp app` (and the GUI entrypoint `fl-mcp-studio`) launches the **FL-MCP Studio Desktop Application** in a chromeless OS window (Edge/Chrome `--app=` or `pywebview`, with a built-in `tkinter` fallback via `--native`) across Windows, macOS, and Linux (`x86_64` and `arm64`). It provides interactive DAW transport controls, Mixer & Channel Rack mute/solo buttons, live chord progression queuing/preview, 1-click song plan composition, 1-click FL script installation, 1-click AI client registration (HTTP & Stdio), and real-time MIDI CC / MCP traffic inspection (`~/.fl-mcp/traffic.jsonl`). Standalone native desktop app bundles are built via `python packaging/build_app.py`.

### Claude Code plugin

```
/plugin marketplace add Rtur2003/FL-MCP
/plugin install fl-studio@fl-mcp
```

The plugin starts the server and adds five skills (**setup**, **produce**, **mix**, **plugins**, **automation**) and a `mix-engineer` subagent. See `plugin/README.md`.

## Tools

| Tool | What it does |
| --- | --- |
| `fl_produce` | **Fast production.** Builds a genre-aware song plan, expands existing loops into sections (`arrange` keeping user melodies intact), exports MIDI, or writes full `.flp`/`.zip` projects (`flp`, `inspect` for `.flp/.zip/.fsc/.fst`) |
| `fl_mix` | `doctor` finds issues; `fix` applies 1-call pan/HPF/mud EQ/clip protection; `meter` & `balance` drive faders; `bus` routes sends; `chain` sets compressors/limiters/reverbs/delays by meaning |
| `fl_audio` | **Audio & DSP.** BS.1770 loudness, active RMS, spectrum, stereo & advice; `render` (`wav/mp3/ogg/flac` + CLI `/M` `mid` & `/Z` `zip`); `transcribe` (BPM, key, F0 melody); `masking` (vocal vs backing EQ cuts); `compare` & `master` |
| `fl_param` | **Any plugin**: filter by name or semantic category (`find="cat:Filter"`), solve real units (`real="-18 dB"`), batch `settings=`, read/load presets & `view="io"` routing |
| `fl_list` | channels, mixer tracks (up to 500 inserts), patterns, playlist tracks or every plugin in the project |
| `fl_library` | indexes sample packs, `.sf2` & `.fst`; multi-role CSV `find` (`kick,snare,hat`), acoustic closeness `similar`, and genre drum kits |
| `fl_ui` | drives FL UI without mouse (`TQuickPopupMenuWindow` on Windows / JXA `Add` menu on macOS): focus windows, press keys, walk browser, `load` plugins/samples |
| `fl_status` | FL version, tempo, state; `include="summary,probe,log,memory"` returns 1-call project summary + 3-layer context/memory + cached API probe + traffic telemetry |
| `fl_transport` | play, pause, stop, record, loop mode, metronome, tempo, position (bar), save, undo |
| `fl_set` | name, color, volume, pan, mute, solo, stereo separation (`stereo`), disk record arm (`arm`), routing (`fx`, `send`/`level`), select across channel/mixer/pattern/playlist |
| `fl_steps` | step sequencer: `on`/`off`/`clear` or velocity `grid` (`z.x.\|o...*4` tiling) with `swing`; warns if pattern length exceeds grid |
| `fl_notes` | queue/apply piano roll notes, chord progressions (`Am7@0:4` or `bar\|beat` `Dm7@2\|1:n/8`), `transform=` DSL (`humanize`, `swing`, `transpose`, `snap`, `legato`, `ramp`, `ratchet`), `delete`, `clear`, `preview`, `_BAKED` pyscript fallback |
| `fl_call` | raw FL scripting API, batched (`modules`: transport mixer channels patterns playlist plugins ui general arrangement) |
| `fl_api` | API reference for `fl_call`: signatures and `midi` constants (`api.json` through v45+); works without FL |
| `fl_desk` | *opt-in* (`fl-mcp desktop stealth\|polite\|foreground\|on`). Non-intrusive screen capture (`PrintWindow`), background `PostMessage` keys, clicks, and macros that run the piano roll script, export audio, open and save projects |

Try: *"Make a 128 BPM techno track in F# minor, then mix and master it."*

## Fast production

`fl_produce` composes with a seeded engine (the same seed gives the same song) in 12 genres: house, techno, trance, drum and bass, dubstep, trap, oriental trap (hicaz), boom bap, lo-fi, pop, synthwave, reggaeton. A plan has sections (intro, build, drop, break...), patterns for kick, snare, clap, hats, percussion, bass, chords, pad, arp, lead and fx, a playlist, markers, and mix targets per role (peak level, pan, EQ, send levels, sound hints). Fills, drop-outs, rolls and varying humanization keep it from sounding like a loop.

| Action | Result |
| --- | --- |
| `make` | plan + deliver: drums go into Channel Rack channels (matched by name: kick, hat, bass, lead...) as named patterns, with tempo, markers, EQ and pan; plus a `.mid` with every part, written to `~/.fl-mcp/out/` |
| `plan`, `show`, `genres` | build and read plans without touching FL |
| `apply`, `midi` | deliver or export a stored plan again |
| `queue` | send one melodic part to the piano roll (see below) |
| `inspect` | read a template `.flp`: channels, roles it can fill, patterns, playlist |
| `flp` | write a full project: your template plus every pattern with all notes and the playlist; `sounds=auto` also adds sample channels from one pack that `fl_library` chose |

**Templates.** Pass `template="auto"` (or a template name like `"4x4"`) to build on auto-discovered FL Studio templates, `inspect` with `template="list"` to list them, or pass any `.flp` path you saved (at least one named pattern with a note and one playlist clip). The template is never modified; the result is a new file. Channel names match roles automatically (`Kick`, `808 Bass`, `Lead Synth`...); `channels="kick=0,bass=3"` overrides. Open the result with `fl_desk macro=open` (desktop control on) or by hand.

### Piano roll notes

FL's API cannot write notes directly. `fl_notes` and `fl_produce action=queue` write them to `~/.fl-mcp/inbox.json` **and** embed a `_BAKED` fallback inside `FL MCP.pyscript` (handling sealed FL 25.2.5+ builds), select the channel, and focus the piano roll. Apply them with `fl_desk macro=script` (with WinRT OCR first-run popup arming on Windows) or run **Tools > Scripting > FL MCP** once — it **applies immediately (no Accept dialog)** and writes `state.json` back. `fl_notes(read=true)` verifies arrival (`applied: true`) and returns score stats. Notes support `[midi_note, start_beat, length_beats, velocity]`, `bar|beat` chord timing (`Dm7@2|1:n/8`), and `transform=` (`humanize`, `swing`, `transpose`, `snap`, `legato`, `ramp`, `ratchet`).

## Desktop control (opt-in)

```bash
fl-mcp desktop polite     # default for `on` (`stealth`, `polite`, or `foreground`; restart AI client; `off` undoes it, `test` captures FL)
```

`fl_desk` gives the model eyes and hands for what no API covers without interrupting your workflow: `stealth` strictly uses background `PrintWindow` capture and `PostMessage` keystrokes (never steals focus or moves the cursor); `polite` (default) tries background paths first, waits until you are idle before temporary focus if needed, and restores your previous window afterwards; `foreground` switches directly to FL. Input goes to FL windows only, dangerous keys (`alt+f4`, `ctrl+alt+del`, `cmd+q`) are blocked, and moving the mouse to `(0, 0)` triggers an immediate failsafe. Windows uses user32/GDI/dwmapi; macOS uses System Events, CoreGraphics idle detection, and `screencapture` (`cliclick` adds double-click, right-click and drag).

## Guides, prompts and resources

`fl://guide/setup`, `produce`, `mix`, `plugins` and `automation` are MCP resources (markdown, with tables filled from the real genre and plugin data), alongside `fl://live/traffic`, `fl://live/studio`, and `fl://info/paths` for live telemetry, cached studio state, and resolved universal paths. The prompts `make_beat`, `mix_check`, `master`, `fix_connection` and `load_plugin` carry the task plus the relevant guide, so clients that surface prompts as slash commands get one-shot workflows. The Claude Code plugin's skills are generated from the same files (`python scripts/build_plugin.py`; a test fails if they drift).

## Limits

FL's scripting API cannot create channels or plugins, load samples, place playlist clips, or render audio; this server does not fake those. The `.flp` writer, FL's own UI, the command-line render and the desktop tier cover them; each reports plainly when it cannot. `docs/CAPABILITIES.md` lists what is possible, by which route, and how sure we are. `docs/RESEARCH.md` records what other FL, Ableton and DaVinci Resolve MCPs do and how paid MCP access is built.

## Licence and credits

This repository builds an **unmetered** server: nothing needs a licence. A commercial build can bake in a licence service address; then `fl_produce` (new plans, `.flp` projects) and `fl_mix balance` use credits, everything else stays free and offline. `fl-mcp login`, `fl-mcp logout` and `fl-mcp account` manage the licence. See `docs/COMMERCE.md`.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `FL_MCP_PORT` | `FL MCP` | MIDI port name (substring match; `IAC` is tried on macOS) |
| `FL_MCP_TIMEOUT` | `2.5` | seconds to wait per attempt (two attempts) |
| `FL_MCP_DIR` | `~/.fl-mcp` | folder for plans, output, the library index and the piano roll exchange (set it before `fl-mcp install`) |
| `FL_MCP_SAMPLES` | unset | extra sample folders for `fl_library` (path-separated) |
| `FL_MCP_FL_DIR` | auto | FL Studio install folder(s) (for the command-line render) |
| `FL_MCP_FL_USER_DIR` | auto | FL Studio user data folder (`Settings/`, if moved outside Documents/Registry) |
| `FL_MCP_PROJECTS` | unset | extra folders where finished projects and exports are looked for |
| `FL_MCP_DESKTOP` | unset | `stealth`, `polite`, `foreground`, `1` (`polite`), or `0` (overrides `fl-mcp desktop`) |
| `FL_MCP_FL_PROCESS` | unset | extra process name(s) that count as FL Studio for `fl_desk` |
| `FL_MCP_DEBUG` | unset | log to stderr |
| `FL_MCP_TRAFFIC` | `1` | record MIDI TX/RX and MCP traffic to `~/.fl-mcp/traffic.jsonl` (`0` disables) |
| `FL_MCP_TEMPLATES` | unset | extra `.flp` template folders for `template="auto"` (path-separated) |
| `FL_MCP_PROFILE` | `full` | tool profile (`full`, `compact`, `produce`, `mix`, `read-only`) or `--profile` |
| `FL_MCP_READONLY` | unset | `1` blocks all mutating bridge/project/note operations safely |
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
.venv/bin/python packaging/build_release.py  # build wheel, sdist, .mcpb, plugin & scripts bundles into dist/
claude plugin validate plugin --strict       # when the claude CLI is installed
```

MIT licensed. The API index holds only function signatures and constants generated from [fl-studio-api-stubs](https://github.com/MaddyGuthridge/FL-Studio-API-Stubs). See `NOTICE` for how the `.flp` format knowledge was gathered.
