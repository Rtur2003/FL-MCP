# Changelog

All notable changes to FL-MCP are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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

