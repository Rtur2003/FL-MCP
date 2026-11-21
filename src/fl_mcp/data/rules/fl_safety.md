# FL-MCP Bridge, Binary & Desktop Safety Rulebook

## 1. MIDI CC Bridge Protocol Invariants
- **7-Bit MIDI CC Transport (No SysEx Buffer Limits)**: All bridge frames between `fl_mcp` and `device_FLMCP.py` use MIDI Control Change messages on Channel 15 (`REQ_CH=14`, host → FL) and Channel 16 (`RES_CH=15`, FL → host): `CC 102` (`BEGIN` + frame ID), `CC 103` (`DATA` 7-bit ASCII JSON bytes), and `CC 104`/`CC 105` (`SUM_A`/`SUM_B` Fletcher mod-127 checksum).
- **Single-Flight Serialized I/O**: A virtual MIDI port handles one request-response transaction at a time. When multiple AI clients (Claude, Cursor, Antigravity, Studio UI) run concurrently, route through the persistent `fl-mcp service` daemon (`http://127.0.0.1:8765/mcp`) so MIDI access is serialized safely.
- **7-Bit Payload Budget**: Keep individual command payloads compact (`MAX_REQ = 16384` bytes TX, `_MAX_OUT = 8192` bytes RX); paginate large channel/mixer/plugin queries (`s`, `n`) rather than requesting unbounded dumps.

## 2. Non-Destructive `.flp` Binary Surgery (`fl_flp`)
- **Read-Only Inspection Default**: `summary`, `channels`, `patterns`, `notes`, `arrangements`, `mixer`, and `verify` never mutate the source `.flp`.
- **Mandatory Backup Before Write**: Whenever cloning or patching a binary `.flp` project (`clone`, `set_tempo`, `swap_samples`), write to a distinct output path or create an automatic `.bak` copy first, and run structural chunk verification (`FLhd` + `FLdt` event stream integrity) before returning success.

## 3. Desktop Automation Guardrails (`fl_desk`)
- **Opt-In Kill Switch**: Desktop UI automation (`fl_desk`) is disabled unless `~/.fl-mcp/desktop.on` exists (`fl-mcp desktop polite|stealth|foreground`).
- **Stealth & Polite First**:
  - Always prefer background Win32 `PrintWindow` / `PostMessage` (`stealth`) so FL Studio never steals focus from the user's active window.
  - In `polite` mode, wait for user input idle (`>= 1.5 s` since last mouse/keyboard event) before any foreground fallback, and restore the user's previous foreground window immediately afterward.
  - Emergency abort: moving the cursor to the top-left screen corner `(0, 0)` aborts any active desktop sequence immediately.
