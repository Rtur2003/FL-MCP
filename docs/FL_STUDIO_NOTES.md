# FL Studio Internals & Automation Notes

Empirical format and Win32 behavior notes verified on FL Studio 2024 / 2025 during FL-MCP v0.3.0 development.

## 1. Non-Intrusive Win32 Automation

- **Hidden Desktop Command-Line Rendering (`render.hidden_popen`)**:
  - `FL64.exe /R /E<fmt> /F"<src_dir>" /O"<out_dir>"` renders all `.flp` files in `<src_dir>` into `<out_dir>`.
  - Copying the target `.flp` into an isolated temporary folder (`always_copy=True`) avoids re-rendering sibling projects and prevents lock/state collisions.
  - Launching the child process inside a private Win32 desktop (`CreateDesktopW("FLMCP_Render_<pid>_<tid>", ...)` assigned to `STARTUPINFOW.lpDesktop`, plus `STARTF_USESHOWWINDOW`, `wShowWindow = SW_HIDE`, and `CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS`) keeps splash screens and progress dialogs completely off the user's visible desktop.
- **Background Capture & Keystrokes (`desk_win.py`)**:
  - Window thumbnails use `PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)` with DWM frame cropping and all-black detection, allowing inspection without unminimizing or focusing FL in `stealth`/`polite` modes.
  - Key combos sent without focus attach to the target window's GUI thread (`GetWindowThreadProcessId` + `GUITHREADINFO.hwndFocus` + `AttachThreadInput`), update modifier states via `SetKeyboardState`, and post `WM_KEYDOWN` / `WM_KEYUP` (or `WM_SYSKEYDOWN`/`WM_SYSKEYUP` for Alt combinations) with `PostMessageW`.
  - `GetLastInputInfo` tracks seconds since the user's last mouse or keyboard activity so `polite` mode waits (idle threshold 1.2 s) before requesting focus and restores the user's previous foreground window afterwards.
- **Context Menu Confirmation (`popupkeys.py`)**:
  - Browser item loading opens `TQuickPopupMenuWindow`. Confirming Item 1 tries `PostMessageW(VK_RETURN)` followed by client-coordinate `WM_MOUSEMOVE` / `WM_LBUTTONDOWN` / `WM_LBUTTONUP`; dismissing tries `VK_ESCAPE` followed by `WM_CANCELMODE`.
  - Physical cursor fallback (`SetCursorPos` + `mouse_event`, saving and restoring `GetCursorPos`) is disabled by default (`allow_cursor_fallback=False`) and only enabled in `foreground` mode.

## 2. Piano Roll Script Delivery (`FL MCP.pyscript`)

- `FL MCP.pyscript` avoids showing a parameter dialog by reading queued notes directly from `~/.fl-mcp/inbox.json` and writing an acknowledgement with the processed `serial` and timestamp to `~/.fl-mcp/state.json`.
- Once invoked once via **Tools > Scripting > FL MCP**, FL Studio repeats the last run piano roll script via its shortcut (`Ctrl+Alt+Y` on Windows, `Cmd+Alt+Y` on macOS), which `fl_desk macro=script` delivers via `PostMessageW` first and confirms via `state.json`.

## 3. Binary `.fst` Presets & SoundFont Player (`flp.py`)

- **`.fst` Preset Format (`FST_FORMAT = 48`)**:
  - Shares the `FLhd` / `FLdt` container and TLV event encoding with `.flp` files, with header format field `48`.
  - Key channel/plugin events: `201` (`PLUGIN_NAME`, UTF-16LE), `212` (52-byte plugin flags), `203` (`CHAN_NAME`, UTF-16LE), `128` (`CHAN_COLOR`), and `213` (`PLUGIN_PARAMS` state blob).
  - `read_fst()` extracts these fields and decodes the `.sf2` path when present; `Flp.add_plugin_from_fst()` converts preset events into a valid Channel Rack slot without needing FL open.
- **SoundFont Player Event `213` Payload**:
  - Stores a 56-byte fixed header prefix followed by a ULEB128-encoded byte length and UTF-8 `.sf2` path, followed by trailing plugin state bytes.
  - `read_soundfont_blob_path()` and `patch_soundfont_blob()` replace the `.sf2` path length-prefixed cleanly while preserving both prefix and tail bytes verbatim.

## 4. Arrangement Markers & Offline Checks (`Flp.verify`)

- **Arrangement Time Markers**:
  - Encoded after the playlist event (`233`) using `148` (`TIME_MARKER_POS`, uint32 `(flags << 24) | ticks`, where `flags == 0` for normal markers), marker metadata bytes (`33`, `34`), and `205` (`TIME_MARKER_NAME`).
  - Updating or adding the `"end"` marker prevents truncated FL renders when generated arrangements extend beyond the template's original end marker.
- **`Flp.verify()`**:
  - Computes bars, duration from tempo/PPQ/clips, channel count, loaded plugins (`.fst`/`.sf2`), pattern counts, empty patterns, and warns on empty playlists, zero-note patterns, truncated `"end"` markers, or all channels routed strictly to Master (`insert 0`).
