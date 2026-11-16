# Changelog

All notable changes to FL-MCP are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-11-15

### Added
- **Plugin I/O Introspection, Pagination & Slot Wrapper Controls (`fl_param`, script `v6`)**: added `fl_param(..., view="io")` exposing upstream channels/tracks, mixer FX slot chain state (`muted`, `mix`, `track_slots_enabled`, `automation_enabled`), downstream sends, internal controller/voice outputs (`FPN_OutCtrl`, `FPN_OutVoice`), mapped pad/note labels (`FPN_Semitone`), and channel MIDI in / activity; added `"next"` cursor to `fl_param` parameter listing; added token-subset matching plus close-match hints on parameter lookup; and added reserved wrapper targets (`fl:mix`, `fl:mute`, `fl:slots`, `fl:focus`) usable in both single and batch `settings=` calls.
- **One-Command AI Client Registration (`fl_mcp.clients.install_client`, `fl-mcp config --write`, `fl-mcp setup --write`)**: automatically merges `fl-studio` into existing Claude Desktop, Cursor, VS Code, Windsurf, Gemini CLI, and Codex CLI (`config.toml`) configs with `.bak` backups and TOML/JSON validation, or invokes `claude mcp add` for Claude Code.
- **Non-Intrusive Desktop Control Modes (`stealth`, `polite`, `foreground`)**: added `fl-mcp desktop stealth|polite|foreground|status|on|off` (plus `fl-mcp setup --desktop [mode]` and `FL_MCP_DESKTOP` mode values), defaulting `on` to `polite`. `stealth` uses background `PrintWindow` + `PostMessageW` strictly with zero focus stealing; `polite` tries background capture/keystrokes first, waits for user idle (`GetLastInputInfo`, 1.2 s) before temporary focus, and restores the user's previous window immediately after interactive actions or once `export` starts rendering.
- **Isolated Hidden-Desktop Parallel Rendering (`render.hidden_popen`)**: on Windows, `fl_audio(action="render", project=...)` runs `FL64.exe /R` inside an isolated Win32 desktop (`CreateDesktopW`, `SW_HIDE`, `CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS`, `always_copy=True`, collision-safe render directories, and reliable process cleanup on error/timeout) so offline renders complete in parallel without interrupting an open FL Studio session or stealing window focus.
- **Offline `.fst` Preset & `.sf2` SoundFont Support (`fl_mcp.flp`, `library`, `produce`)**: added `read_fst()` (`FST_FORMAT = 48`), `patch_soundfont_blob()`, `read_soundfont_blob_path()`, `Flp.add_plugin_from_fst()`, `Flp.set_soundfont()`, `.sf2`/`.fst` indexing and resolution (`library.find_preset`, `library.list_soundfonts`, `fl_library plugins role=soundfont|preset`), preset/SoundFont name lookup in `sounds=`, `.fst` inspection via `fl_produce(action="inspect")`, and verified `.flp` build reports.
- **Arrangement `end` Markers & Offline Verification (`Flp.markers`, `Flp.set_end_marker`, `Flp.verify`)**: reads/updates arrangement `"end"` time markers automatically during `build()` to avoid truncated renders, and adds offline structural warnings to `fl_produce(action="inspect")` (`"verify"`) and `flp` build reports.
- **Dashboard, Status & Doctor Mode Exposure**: `fl_status`, `doctor_info()`, `fl-mcp doctor`, and the Live Studio web dashboard now report active desktop control mode (`stealth`, `polite`, `foreground`, or `off`), while `fl_audio(action="master")` fails early with guidance if `stealth` mode is active.
- **Persistent Background Service (`fl_mcp.service_daemon`, `fl-mcp service ...`)**: added detached console-free daemon lifecycle (`start`, `stop`, `restart`, `status`, `logs`, `run`, `autostart enable|disable|status`) hosting a unified ASGI Starlette/Uvicorn server with Studio Web UI, static assets (`/assets/*`), Streamable-HTTP MCP (`/mcp`), SSE MCP (`/sse`, `/messages/`), and a backoff-protected background telemetry poller; added `--transport http` support across `fl-mcp config`, `fl-mcp setup`, and `fl-mcp serve`.
- **Universal Path Resolution & Multi-Client Setup (`paths.py`, `clients.py`, `fl://info/paths`)**: added `FL_MCP_FL_USER_DIR`, Image-Line Registry `User data folder` / `Shared data` lookup, unrestricted Windows Shell Known Folder detection (supporting OneDrive and non-system drives) + macOS/Wine fallbacks, `antigravity` client registration, `resolve_uvx()` executable lookup, `client_statuses()` inspection, and the `fl://info/paths` resource (`status["paths"]` when `probe=true`).
- **Studio Web Visual Assets & Accessible UI (`src/fl_mcp/assets/`, `dashboard.html`, `.mcpb`)**: added custom machined hardware icon assets (`icon-64.png`, `icon-128.png`, `icon-256.png`, `favicon.png`, `logo.svg`, plus `icon.png` in `.mcpb` manifest) and a 6.8 KB acoustic slat/anodized metal header backdrop (`studio-backdrop.webp`), redesigned `dashboard.html` with full ARIA tablist keyboard navigation, `color-scheme: dark`, `content-visibility: auto`, high-DPI Piano Roll rendering, Service/Poller controls, Resolved Paths card, and unified English copy.

### Changed
- **Cursor-Preserving Browser Menu Confirmation (`popupkeys.py` & `uiload.py`)**: replaced unconditional physical `SetCursorPos` popup clicks with `PostMessageW` (`VK_RETURN`/`VK_ESCAPE`) followed by client-coordinate `WM_MOUSEMOVE`/`WM_LBUTTONDOWN`/`WM_LBUTTONUP` or `WM_CANCELMODE`; physical cursor fallback and `focus_fl()` now run only in `foreground` mode (with cursor position saved and restored).
- Updated guides (`setup`, `automation`, `produce`, `mix`), `docs/ARCHITECTURE.md`, `docs/CAPABILITIES.md`, and added `docs/FL_STUDIO_NOTES.md`.

## [0.2.0] - 2026-11-13

### Added
- **Live Incoming/Outgoing Telemetry (`fl_mcp.telemetry`)**: records every virtual MIDI bridge TX/RX exchange (bytes, CC frame counts, retries, latency ms, errors) and every MCP tool invocation across processes into `~/.fl-mcp/traffic.jsonl` plus `~/.fl-mcp/snapshot.json`.
- **Live Studio & Traffic Web Dashboard (`fl-mcp dashboard`)**: zero-dependency dark DAW monitor on `127.0.0.1:8765` displaying real-time incoming/outgoing MIDI & MCP traffic frames, latency sparkline, Mixer console strips & live peak meters, Channel Rack routing, HTML5 Canvas Piano Roll (`inbox.json` vs `state.json`), 7-band audio spectrum & LUFS metrics, auto-discovered templates, and system doctor status.
- **Terminal Traffic & Template CLIs (`fl-mcp traffic`, `fl-mcp templates`)**: stream/filter live MIDI and MCP traffic (`--follow`, `--midi`, `--mcp`, `--errors`, `--json`, `--clear`) and inspect ready `.flp` project templates on disk.
- **Live MCP Resources**: added `fl://live/traffic` and `fl://live/studio` so MCP clients can inspect TX/RX telemetry and cached studio state without spending tool schema budget; `fl_status(log=True)` now also includes `"traffic"` summary.
- **Velocity Step Grid & Swing in `fl_steps`**: exposed compact `.`/`0-9`/`a-z` velocity grid strings (`grid`, `swing`) alongside bar-grouped visual step strips (`"view"`).
- **One-Shot Piano Roll Apply in `fl_notes`**: added `apply=True` when `fl_desk` is enabled to queue and run `FL MCP.pyscript` in a single call.
- **Auto Template Discovery (`fl_produce template="auto"` / `inspect template="list"`)**: automatically scans FL Studio's built-in Templates and user template folders for valid `.flp` starter projects.
- **Automated Release Bundler (`packaging/build_release.py` & `.github/workflows/release.yml`)**: produces wheel, sdist, deterministic `.mcpb` Desktop Extension, Claude Code plugin zip, standalone FL scripts bundle zip (with PowerShell/Python offline installer), `RELEASE_NOTES.md`, and `SHA256SUMS.txt`.

### Changed
- Tightened MCP tool schema descriptions so all new parameters fit comfortably under the 16,500-byte cross-model schema budget (~15.6 KB total).
- Updated `fl://guide/produce` and `fl://guide/setup` guides (and regenerated Claude Code plugin skills).

## [0.1.0] - 2026-10-30

### Added
- Initial release with 14 flat-schema MCP tools (`fl_status`, `fl_produce`, `fl_mix`, `fl_list`, `fl_transport`, `fl_set`, `fl_steps`, `fl_notes`, `fl_param`, `fl_call`, `fl_library`, `fl_ui`, `fl_audio`, `fl_api`) plus opt-in `fl_desk`.
- Verified live against FL Studio 2025 (`Producer Edition v25.2.5 [build 5319]`, API 40), ITU-R BS.1770 loudness analysis, lossless `.flp` project patching, and `.mcpb` desktop packaging.

