# Architecture

FL-MCP controls the FL Studio on the same computer. Nothing here is hosted; the only optional network traffic is the licence service (see COMMERCE.md).

```
MCP client (any model)
   |  stdio, JSON-RPC
   v
fl_mcp.server  --- produce / mixing / music / flp (pure Python, no FL needed)
   |  JSON framed in MIDI CC messages
   v
virtual MIDI port (loopMIDI on Windows, IAC on macOS)
   |
   v
device_FLMCP.py  (controller script inside FL Studio's embedded Python)
   |  documented FL scripting API only
   v
FL Studio
```

Two more doors into FL exist because the scripting API is limited: the **piano roll script** (`FL MCP.pyscript`, run by the user from Tools > Scripting) and **files** (a MIDI file the user drags in, a `.flp` the user opens).

## The link

| Part | Decision |
|---|---|
| Transport | 7-bit MIDI CC messages, no SysEx, so there is no buffer limit to hit. Requests on MIDI channel 15, answers on 16, so one loopback port serves both directions. |
| Frame | `BEGIN(id)`, `DATA(byte)...`, `SUM_A`, `SUM_B` (Fletcher-style checksum modulo 127). A bad checksum is answered with `bad frame` and the server retries with the same id. |
| Exactly once | The script caches its last answer by `(frame id, checksum)`, so a retried request is answered from the cache instead of running twice. |
| Size | A request is at most 16384 bytes. Batches (`produce`, `fl_call`) are cut below 12000 bytes. |
| Handshake | The first call is a `ping`; the server refuses a script older than the version it needs and tells the user to run `fl-mcp install`. |

## The controller script

`src/fl_mcp/fl/device_FLMCP.py` runs inside FL on FL's older embedded Python, so it avoids newer syntax. It understands sixteen operations: `ping status list set tr steps param roll call gain eq peaks markers note grid batch`.

- **Allowlist.** `call` can reach only the FL API modules `transport mixer channels patterns playlist plugins ui general arrangement`; arguments named like `midi.NAME` resolve to constants. There is no `exec`, `eval`, file or network access.
- **Real units.** FL stores volume and EQ as 0-1 values with a non-linear mapping. The script solves for the stored value whose read-back in dB or Hz equals the target (bisection against `getTrackVolume(i, 1)`, `getEqGain(..., 1)`, `getEqFrequency(..., 1)`), so "-8 dB" means -8 dB.
- **Undo.** A `batch` is one undo step (`general.saveUndo`), so a whole production can be undone with one Ctrl+Z.
- **Measurement.** `peaks` reads `mixer.getTrackPeaks`; the host samples it over time (`mixing.py`).

## What FL's API cannot do, and what is done instead

| Missing in the API | Route used |
|---|---|
| Create channels or plugins | Fill channels that already exist (matched by name), or build on a template `.flp` |
| Write notes to a pattern | Drums: the step sequencer (`grid` op). Melody and chords: the piano roll script reads `~/.fl-mcp/inbox.json`. Everything: a MIDI file. |
| Place playlist clips | Only through a written `.flp` (see below) |
| Render or export audio | Not available. Reported as a limit rather than faked. |
| Load samples or presets | Not available. The plan's `sound` hints tell the user or model what to load. |

## Production pipeline

1. `music.compose(genre, key, bpm, length, mood, energy, seed, roles)` returns a `Plan`: sections, patterns (parts of events `t, d, p, v`), a playlist of clips, markers and mix targets per role. It is deterministic for a seed. Genre profiles live in `music/genres.py`, rhythm in `music/groove.py` (a small groove DSL with swing as MPC percent), harmony and scales in `music/theory.py`, assembly in `music/compose.py`.
2. `produce.save_plan` stores the plan as JSON under `~/.fl-mcp/plans`. The model never receives the notes, only a summary, so a song costs a few hundred tokens.
3. Delivery:
   - `apply_live`: tempo, named and colored patterns, drum steps through the sequencer, section markers, per-role pan and EQ. One undo step.
   - `export_midi`: a Standard MIDI File with every part and section markers.
   - `export_flp`: `flp.py` patches the template (see below).
   - `queue`: one part to the piano roll through the inbox file.
4. `mixing.balance` plays the project, measures peaks per track and moves faders until each role is near its target; it reports faders that hit their limit instead of hiding it.

### Anti-monotony

The engine varies on purpose: fills at phrase ends, drop-outs before drops, snare and hat rolls, per-section density, humanization that changes by section, melodies that vary on repeats, and no pitch repeated more than four times in a row. Chords and arpeggios are keyed to their unit so repeats differ.

## The `.flp` module

`src/fl_mcp/flp.py` reads a project as a list of events (`id`, payload) and writes it back. Events it does not understand are kept byte for byte, so a file from a newer FL version round-trips unchanged; truncated or corrupt files are refused with a reason.

Patch operations: set tempo, replace a pattern's notes and name, clone a pattern from a seed (the template's named pattern with a note), and append or replace playlist clips. Playlist records are 32 bytes up to FL 20 and 60 bytes from FL 21; when the length fits both, the project version decides. Output is a new file; the template is never changed.

Risk, stated plainly: FL does not publish this format, and FL Studio itself is the only complete judge of a file. The module is verified against real FL 20.8.4 projects and an independent parser, but a project written for a newer FL version should be opened and checked once by a person.

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
| Bundle | The unpacked `.mcpb` is started over stdio and lists its tools. |

## Extending

- **A genre:** add a profile in `music/genres.py`; the genre tests iterate over all profiles.
- **An FL operation:** add it to `device_FLMCP.py`, register it in `_OPS`, raise `VERSION`, and raise `SCRIPT_VERSION` in `bridge.py` so old installs are detected.
- **A metered operation:** add its price to the service config and wrap the work in `_spend("name")` in `server.py`.
