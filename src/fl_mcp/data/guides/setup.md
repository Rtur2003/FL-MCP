# Set up and fix the FL Studio link

The server talks to FL Studio through a virtual MIDI port called `FL MCP`. Three things must hold: the port exists, FL's controller script is installed and assigned to it, and the server finds the port. `fl-mcp onboard` automates every layer on one command.

## Fastest onboarding (`fl-mcp onboard`)

Run `fl-mcp onboard --install-midi --desktop` (or click **One-Click Auto-Setup** in `fl-mcp-studio`):
1. Ensures the virtual MIDI port (`FL MCP`): checks/configures `loopMIDI` on Windows (and installs via `winget` when `--install-midi` is passed, restarting detached if the port isn't live yet) or checks CoreMIDI IAC Driver on macOS.
2. Installs the controller script (`device_FLMCP.py`) and piano roll script (`FL MCP.pyscript`) into FL Studio's user data folder (resolving FL 20–26+ numerically so FL 26 > FL 25).
3. On Windows, **if FL Studio is closed**, writes the F10 MIDI Input/Output & `'FL MCP'` Controller Type bindings into `HKCU\Software\Image-Line\FL Studio <N>` automatically. (If FL is open, close it once and re-run `onboard` so FL does not overwrite settings on exit.)
4. Starts the persistent background Studio + HTTP MCP daemon (`http://127.0.0.1:8765/mcp`) and registers detected AI clients (`claude-desktop`, `claude-code`, `cursor`, `vscode`, `windsurf`, `gemini`, `antigravity`, `codex`, `cline`, `roo-code`, `zed`, `opencode`).

## Manual / per-client steps

- `fl-mcp setup --write --client <client>` (or `fl-mcp setup --auto` for full onboarding).
- `fl-mcp config <client|all> --source <auto|pypi|git|local> --transport <stdio|http> --write` (`--status` shows all 12 clients).
- `fl-mcp doctor` checks every layer including FL Registry F10 bindings (`loopMIDI` / CoreMIDI IAC).
- `fl-mcp selftest` (`--deep` to test browser loading + undo).
- Tool profiles & safety: `fl-mcp serve --profile <full|compact|produce|mix|read-only>` (`FL_MCP_PROFILE`) or `FL_MCP_READONLY=1` for strict read-only inspection. The audio tools need `uv tool install "fl-mcp[audio]"`.

Live monitoring & Studio tools:
- `fl-mcp app` (or `fl-mcp-studio`): launches FL-MCP Studio as a native desktop app window (`--native` tkinter fallback, `--install-shortcut` Start Menu / `.app` / `.desktop`).
- `fl-mcp service start`: persistent Studio UI + `/mcp` + `/sse` endpoints + telemetry poller (`service stop|status|autostart enable`).
- `fl-mcp dashboard`: opens Studio UI (`--browser` opens a browser tab).
- `fl-mcp traffic --follow`: streams live MIDI & MCP traffic.
- `fl-mcp templates`: lists auto-discovered `.flp` project templates on disk.

## When something does not work

Call `fl_status include="summary,probe,log,memory"` first (or read `fl://info/paths` / `fl://live/studio`):

| Symptom | Cause | Fix |
| --- | --- | --- |
| "No answer from FL Studio" | FL closed, port not assigned, F10 mismatch | Run `fl-mcp doctor`; close FL and run `fl-mcp onboard` on Windows (or check IAC Driver on macOS) |
| selftest: "script in FL is vN" | old script copy | Run `fl-mcp install` and restart FL |
| `fl_notes` queued, nothing in piano roll | piano roll script never armed in this FL session | Run **Tools > Scripting > FL MCP** once (applies immediately — no Accept dialog; afterwards `mod+alt+y` or `fl_desk macro=script` arms/repeats automatically, with WinRT OCR & `_BAKED` script fallback) |
| `fl_param` has no unit text | plugin omits formatted strings | Set 0–1 `value`, filter with `find="cat:Filter"`, or inspect via `fl_desk` |
| `fl_audio render`: "FL Studio is open" | Rendering the open project needs `fl_desk` unless `.flp` path given | Pass `project=<file.flp>` on Windows for hidden-desktop render, or use `fl_desk` |
| `fl_desk`: "Windows blocked the input" | FL runs as Administrator | Run AI client/Studio normally alongside non-admin FL |

## What the API cannot do

FL's scripting API cannot create plugins, write arbitrary piano roll notes directly, or render audio files. FL-MCP covers these through `_BAKED`/pyscript queue + WinRT OCR arming (`fl_notes`), FL browser + macOS JXA menu automation (`fl_ui`), offline `.flp`/`.zip`/`.fsc` engine (`fl_produce`), CLI hidden-desktop rendering (`fl_audio render`), and opt-in stealth/polite screen control (`fl_desk`).
