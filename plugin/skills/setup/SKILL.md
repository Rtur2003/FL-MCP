---
name: setup
description: "Install, connect and troubleshoot the FL Studio MCP server: the virtual MIDI port, the controller script, fl-mcp setup, doctor and selftest, and what failures mean."
when_to_use: "Use when the user is installing the FL Studio MCP, a tool says 'No answer from FL Studio' or times out, the piano roll script does nothing, or credits or desktop control need configuring."
---

# Set up and fix the FL Studio link

The server talks to FL Studio through a virtual MIDI port called `FL MCP`. Three things must hold: the port exists, FL's controller script is installed and assigned to it, and the server finds the port. `fl-mcp setup` does the parts a program can do and prints the rest.

## First time

1. Create the port. Windows: install loopMIDI and add a port named `FL MCP`. macOS: Audio MIDI Setup > MIDI Studio > IAC Driver > "Device is online", add a port.
2. Run `fl-mcp setup --write --client <client>` (default `claude-desktop`; or `fl-mcp config <client> --write`). It copies the controller script and the piano roll script into FL's user data folder, checks the port, and registers the server directly in your AI client config (with a `.bak` backup if the file already exists; omit `--write` to inspect snippets first).
3. In FL Studio open Options > MIDI Settings. Under Input select the `FL MCP` port, set Controller type to `FL MCP`, enable it. Under Output select the same port and give it the same port number as the input.
4. Restart FL Studio. Run `fl-mcp doctor` (each layer in one line), then `fl-mcp selftest` (every feature, small writes put back, report saved to `selftest.json`).

`fl-mcp selftest --deep` additionally loads Fruity Balance through FL's browser onto a mixer track and undoes it: it tells you whether adding plugins works on your FL build (save the project first; if undo does not take it back, delete the plugin by hand).

Optional: `fl-mcp desktop on` (or `stealth`, `polite`, `foreground`, `status`) lets the model see FL's screen and drive shortcuts (`fl_desk`), and `fl-mcp setup --desktop` enables `polite` mode automatically. `polite` (default when on) captures via background `PrintWindow` and waits for you to be idle before focusing FL, restoring your previous window afterwards; `stealth` never steals focus. Browser popup confirmation (`fl_ui`) avoids moving your mouse cursor unless in `foreground` mode. The audio tools need the audio extra: `uv tool install "fl-mcp[audio]"`.

Live monitoring, background daemon & troubleshooting tools:
- `fl-mcp app` (or `fl-mcp-studio`): launches FL-MCP Studio as a native desktop app window (`--native` for built-in tkinter, `--install-shortcut` for Start Menu / `.app` / `.desktop`) and ensures the background service is running.
- `fl-mcp service start`: launches the detached background service hosting both the Studio App backend (`http://127.0.0.1:8765/`) and persistent HTTP/SSE MCP endpoints (`/mcp`, `/sse`) plus a non-intrusive telemetry poller (`service stop`, `service status`, `service autostart enable`).
- `fl-mcp config <client> --transport http --write`: registers your AI client (including `claude-desktop`, `cursor`, `vscode`, `windsurf`, `gemini`, `antigravity`, `codex`) against the background service endpoint instead of spawning stdio per session.
- `fl-mcp dashboard`: opens the Studio desktop app (`--browser` to open in a browser tab instead; shows MIDI TX/RX frames, latency, mixer meters, Channel Rack, Piano Roll canvas, 7-band audio spectrum, universal paths, and discovered `.flp` templates).
- `fl-mcp traffic --follow`: streams live MIDI & MCP traffic counters and failures in the terminal.
- `fl-mcp templates`: lists auto-discovered `.flp` project templates on disk (`fl_produce action=flp template=auto`).

## When something does not work

Call `fl_status probe=true log=true` first (or read `fl://info/paths`): it lists the API functions this FL build has, resolved universal paths (`FL_MCP_FL_USER_DIR`, `FL_MCP_FL_DIR`), the controller script's last errors, and recent MIDI TX/RX telemetry (also available as `fl://live/traffic` and `fl://live/studio`). Then:

| Symptom | Cause | Fix |
| --- | --- | --- |
| "No answer from FL Studio" | FL closed, port not assigned, script not loaded, wrong port name | `fl-mcp doctor`; check MIDI Settings (step 3); set `FL_MCP_PORT` if the port has another name |
| selftest: "script in FL is vN" | old script copy | `fl-mcp install`, restart FL |
| `fl_notes` queued, nothing in the piano roll | the piano roll script never ran | in the piano roll: Tools > Scripting > FL MCP, press Accept once; afterwards `fl_desk macro=script` (or Ctrl+Alt+Y) repeats it |
| `fl_param` says a parameter has no text | plugin does not report value strings | set 0-1 `value`, or read it on screen with `fl_desk` |
| `fl_param real=` cannot reach a value | the parameter's range does not contain it | read `text` after the call: it shows what FL really set |
| `fl_audio render`: "FL Studio is open" | Rendering the *open* project needs `fl_desk` (`polite`/`foreground`) unless a `.flp` path is given | Pass `project=<file.flp>` on Windows to render in a hidden background desktop alongside open FL, use `fl_desk`, or save and close FL |
| `fl_desk`: "Windows blocked the input" | FL runs as administrator | start Claude as administrator too, or run FL normally |
| Credits | `fl_status` shows the balance; `fl-mcp account` shows prices | tell the user instead of retrying |

## What the API cannot do

FL's scripting API cannot create channels or plugins, place playlist clips, or export audio. The other layers cover that: a generated `.flp` (`fl_produce template=`), FL's own browser driven from inside FL (`fl_ui`), the command line render (`fl_audio render`) and the desktop tier (`fl_desk`). The automation guide says which to use when.
