# FL-MCP

MCP server for **FL Studio**. Lets any MCP client (Claude, ChatGPT/Codex, Gemini CLI, Cursor, VS Code, local models) inspect and control a running FL Studio: transport, tempo, mixer, channels, patterns, step sequencer, plugin parameters and the piano roll.

- **9 tools, about 2k tokens** for the whole tool list. Flat schemas (no `$ref`/`anyOf`/`title`), plain JSON results, so small and strict models can use them.
- **Safe link to FL:** a small controller script inside FL answers framed MIDI CC messages. No `exec`/`eval`, no network, no SysEx. Only allowlisted FL API modules are reachable.
- Windows and macOS, FL Studio 2024+ (scripting API 33+). Python 3.10-3.12 (`python-rtmidi` has no wheels for 3.13+; `uv` picks a matching Python for you).

> Status: 124 automated tests run the real controller script against a fake FL built on the official API stubs, and every FL call is checked against those stubs. It has not been run against a live FL Studio yet; please open an issue if something behaves differently.

## Setup

**1. Install** (needs [uv](https://docs.astral.sh/uv/)):

```bash
uv tool install git+https://github.com/Rtur2003/FL-MCP
fl-mcp install
```

`fl-mcp install` copies the controller script and the piano roll script into `Documents/Image-Line/FL Studio/Settings` (use `--fl-dir` if your user data folder is elsewhere).

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
| `fl_status` | FL version, project title, tempo, play/record state, position, current pattern/channel/track |
| `fl_list` | channels, mixer tracks, patterns or playlist tracks (paged, unused ones skipped) |
| `fl_transport` | play, pause, stop, record, loop mode, metronome, tempo, position (bar), save, undo |
| `fl_set` | name, color, volume, pan, mute, solo, routing (`fx`, `send`/`level`), select on a channel, mixer track, pattern or playlist track |
| `fl_steps` | step sequencer of a channel: set/clear steps, velocity, pitch; reads back |
| `fl_notes` | queue piano roll notes (see below) and read the piano roll back |
| `fl_param` | list and set plugin parameters of a channel instrument or a mixer effect slot |
| `fl_call` | raw FL scripting API, batched (`modules`: transport mixer channels patterns playlist plugins ui general arrangement) |
| `fl_api` | API reference for `fl_call`: signatures and `midi` constants; works without FL |

Try: *"Set the tempo to 128, solo the Kick channel and put a 4-on-the-floor in pattern 1."*

### Piano roll notes

FL's API cannot write notes. `fl_notes` writes them to `~/.fl-mcp/inbox.json`, selects the channel and opens the piano roll. The user then runs **Tools > Scripting > FL MCP** in the piano roll and presses Accept (repeat runs do not duplicate notes). That run also exports the piano roll, so `fl_notes(read=true)` can confirm and read it back. Notes are `[midi_note, start_beat, length_beats, velocity]`.

## Limits

FL's scripting API cannot create channels or plugins, load samples, place playlist clips, or render audio; this server does not fake those. Everything else above is done through documented API calls.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FL_MCP_PORT` | `FL MCP` | MIDI port name (substring match; `IAC` is tried on macOS) |
| `FL_MCP_TIMEOUT` | `2.5` | seconds to wait per attempt (two attempts) |
| `FL_MCP_DIR` | `~/.fl-mcp` | folder shared with the piano roll script (set it before `fl-mcp install`) |
| `FL_MCP_DEBUG` | unset | log to stderr |

## Troubleshooting

Run `fl-mcp doctor`. Typical causes of *No answer from FL Studio*: the port is not enabled for both input and output with the same port number, controller type is not `FL MCP`, the script was installed after FL started, or a modal dialog is open in FL. `FL MCP script is outdated`: run `fl-mcp install` again and restart FL. The FL script log is in `View > Script output`.

## How it works

The server sends each request as JSON framed in 7-bit MIDI CC messages (channel 15 to FL, channel 16 back) with a checksum and automatic retry; the controller script de-duplicates retried requests, so nothing runs twice. The same port carries both directions, so a loopback port is enough.

## Development

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python scripts/gen_api_index.py   # refresh src/fl_mcp/api.json from the API stubs
```

MIT licensed. The API index holds only function signatures and constants generated from [fl-studio-api-stubs](https://github.com/MaddyGuthridge/FL-Studio-API-Stubs).
