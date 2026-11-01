# FL-MCP

MCP server for **FL Studio**. Any MCP client that can start a local server (Claude, Codex CLI, Gemini CLI, Cursor, VS Code, local models) can inspect and control the FL Studio running on your computer, and can produce a whole song fast: pick a genre, get drums in the Channel Rack, a MIDI file with every part, or a finished `.flp` project.

- **Local.** The server runs on your machine and talks to FL Studio through a virtual MIDI port. No cloud is involved in controlling FL.
- **11 tools, about 2.6k tokens** for the whole list. Flat schemas (no `$ref`/`anyOf`/`title`) and plain JSON results, so small and strict models can use them.
- **Safe link to FL:** a small controller script inside FL answers framed MIDI CC messages. No `exec`/`eval`, no network, no SysEx. Only allowlisted FL API modules are reachable.
- Windows and macOS, FL Studio 2024+ (scripting API 33+). Python 3.10-3.12 (`python-rtmidi` has no wheels for 3.13+; `uv` picks a matching Python for you).

> **Status.** The controller script runs against a fake FL built on the official API stubs, and every FL call is checked against those stubs. The `.flp` reader and writer are verified against real FL Studio 20.8.4 project files and an independent parser. It has **not** been run against a live FL Studio yet; please open an issue if something behaves differently.

## Setup

**1. Install** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install git+https://github.com/Rtur2003/FL-MCP
```

```bash
fl-mcp install
```

`fl-mcp install` copies the controller script and the piano roll script into `Documents/Image-Line/FL Studio/Settings` (use `--fl-dir` if your user data folder is elsewhere).

Claude Desktop can instead install the `.mcpb` extension (`python packaging/build_mcpb.py` builds it); you still do steps 2 and 3.

**2. Loopback MIDI port**

- Windows: install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) and create a port named `FL MCP`.
- macOS: Audio MIDI Setup > MIDI Studio > enable the **IAC Driver** (found automatically).

**3. FL Studio:** `Options > MIDI Settings`

- Input: select `FL MCP`, enable it, controller type **FL MCP**, port number `N`.
- Output: select `FL MCP`, enable it, port number the **same `N`**.
- Restart FL Studio (or reselect the controller type to reload the script).

Check everything with `fl-mcp doctor`.

**4. Add to your client** (stdio):

```json
{ "mcpServers": { "fl-studio": { "command": "fl-mcp" } } }
```

| Client | How |
|---|---|
| Claude Code | `claude mcp add fl-studio -- fl-mcp` |
| Claude Desktop, Cursor, Gemini CLI | the JSON above (`claude_desktop_config.json`, `~/.cursor/mcp.json`, `~/.gemini/settings.json`) |
| VS Code | `.vscode/mcp.json`: `{ "servers": { "fl-studio": { "type": "stdio", "command": "fl-mcp" } } }` |
| Codex CLI | `~/.codex/config.toml`: `[mcp_servers.fl-studio]` with `command = "fl-mcp"` |

Without installing: `"command": "uvx", "args": ["--from", "git+https://github.com/Rtur2003/FL-MCP", "fl-mcp"]`.

## Tools

| Tool | What it does |
|---|---|
| `fl_produce` | **Fast production.** Builds a genre-aware song plan and delivers it (see below) |
| `fl_mix` | Measures the mix with FL's own peak meters while the project plays; `balance` moves faders to role targets (kick -8 dB, bass -9 dB, hats -20 dB...) |
| `fl_status` | FL version, project title, tempo, play/record state, position, current pattern/channel/track |
| `fl_list` | channels, mixer tracks, patterns or playlist tracks (paged, unused ones skipped) |
| `fl_transport` | play, pause, stop, record, loop mode, metronome, tempo, position (bar), save, undo |
| `fl_set` | name, color, volume, pan, mute, solo, routing (`fx`, `send`/`level`), select on a channel, mixer track, pattern or playlist track |
| `fl_steps` | step sequencer of a channel: set/clear steps, velocity, pitch; reads back |
| `fl_notes` | queue piano roll notes and read the piano roll back |
| `fl_param` | list and set plugin parameters of a channel instrument or a mixer effect slot |
| `fl_call` | raw FL scripting API, batched (`modules`: transport mixer channels patterns playlist plugins ui general arrangement) |
| `fl_api` | API reference for `fl_call`: signatures and `midi` constants; works without FL |

Try: *"Make a 128 BPM techno track in F# minor, then balance the mix."*

## Fast production

`fl_produce` composes with a seeded engine (the same seed gives the same song) in 12 genres: house, techno, trance, drum and bass, dubstep, trap, oriental trap (hicaz), boom bap, lo-fi, pop, synthwave, reggaeton. A plan has sections (intro, build, drop, break...), patterns for kick, snare, clap, hats, percussion, bass, chords, pad, arp, lead and fx, a playlist, markers, and mix targets per role (peak level, pan, EQ, send levels, sound hints). Fills, drop-outs, rolls and varying humanization keep it from sounding like a loop.

| Action | Result |
|---|---|
| `make` | plan + deliver: drums go into Channel Rack channels (matched by name: kick, hat, bass, lead...) as named patterns, with tempo, markers, EQ and pan; plus a `.mid` with every part, written to `~/.fl-mcp/out/` |
| `plan`, `show`, `genres` | build and read plans without touching FL |
| `apply`, `midi` | deliver or export a stored plan again |
| `queue` | send one melodic part to the piano roll (see below) |
| `inspect` | read a template `.flp`: channels, roles it can fill, patterns, playlist |
| `flp` | write a full project: your template plus every pattern with all notes and the playlist |

**Templates.** FL's scripting API cannot create channels, plugins or playlist clips, so a finished project is built on a template `.flp` you made: the instruments you want, one named pattern with at least one note, and one clip in the playlist. The template is never modified; the result is a new file next to the plans. Channel names are matched to roles (`Kick`, `808 Bass`, `Lead Synth`...); `channels="kick=0,bass=3"` overrides the match.

### Piano roll notes

FL's API cannot write notes. `fl_notes` and `fl_produce action=queue` write them to `~/.fl-mcp/inbox.json`, select the channel and open the piano roll. You then run **Tools > Scripting > FL MCP** in the piano roll and press Accept (repeat runs do not duplicate notes). That run also exports the piano roll, so `fl_notes(read=true)` can confirm and read it back. Notes are `[midi_note, start_beat, length_beats, velocity]`.

## Limits

FL's scripting API cannot create channels or plugins, load samples, place playlist clips, or render audio; this server does not fake those. It works around them where a documented route exists (step sequencer, piano roll script, MIDI file, template project) and says so when it cannot. `docs/CAPABILITIES.md` lists what is possible and what is not.

## Licence and credits

This repository builds an **unmetered** server: nothing needs a licence. A commercial build can bake in a licence service address; then `fl_produce` (new plans, `.flp` projects) and `fl_mix balance` use credits, everything else stays free and offline. `fl-mcp login`, `fl-mcp logout` and `fl-mcp account` manage the licence. See `docs/COMMERCE.md`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FL_MCP_PORT` | `FL MCP` | MIDI port name (substring match; `IAC` is tried on macOS) |
| `FL_MCP_TIMEOUT` | `2.5` | seconds to wait per attempt (two attempts) |
| `FL_MCP_DIR` | `~/.fl-mcp` | folder for plans, output and the piano roll exchange (set it before `fl-mcp install`) |
| `FL_MCP_DEBUG` | unset | log to stderr |
| `FL_MCP_SERVICE` | unset | licence service address (https); unset means unmetered |
| `FL_MCP_LICENSE` | unset | licence key to sign in with automatically (set by the desktop extension) |

## Troubleshooting

Run `fl-mcp doctor`. Typical causes of *No answer from FL Studio*: the port is not enabled for both input and output with the same port number, controller type is not `FL MCP`, the script was installed after FL started, or a modal dialog is open in FL. `FL MCP script is outdated`: run `fl-mcp install` again and restart FL. The FL script log is in `View > Script output`.

## How it works

The server sends each request as JSON framed in 7-bit MIDI CC messages (channel 15 to FL, channel 16 back) with a checksum and automatic retry; the controller script de-duplicates retried requests, so nothing runs twice. The same port carries both directions, so a loopback port is enough. `docs/ARCHITECTURE.md` has the details.

## Development

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python scripts/gen_api_index.py   # refresh src/fl_mcp/api.json from the API stubs
```

MIT licensed. The API index holds only function signatures and constants generated from [fl-studio-api-stubs](https://github.com/MaddyGuthridge/FL-Studio-API-Stubs). See `NOTICE` for how the `.flp` format knowledge was gathered.
