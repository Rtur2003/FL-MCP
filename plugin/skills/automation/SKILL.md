---
name: automation
description: "Do what FL's scripting API cannot: drive FL's browser from inside FL, write .flp projects, render audio and control the screen (opt-in desktop tier), checking every step."
when_to_use: "Use when something must be loaded, created, exported or clicked that the normal tools cannot do: adding a plugin or sample, new channels or playlist clips, exporting audio, applying piano roll notes."
---

# When the API is not enough

Nothing is impossible, the method just has to change. Go down this ladder and stop at the first rung that does the job; each one is slower and less certain than the one above it.

| Need | Rung | Tools |
| --- | --- | --- |
| Read or change something that exists (tempo, faders, mute, steps, plugin values) | 1. FL's scripting API | `fl_status` (`include="summary,memory"`), `fl_list`, `fl_set`, `fl_transport`, `fl_steps`, `fl_param`, `fl_call` |
| Notes in the piano roll | 1b. piano roll script (`inbox.json` + `_BAKED` fallback) | `fl_notes` (`apply=true`), or `fl_desk macro=script` (WinRT OCR arms first run) |
| Open a window, press a key, walk FL's browser, load a plugin/sample | 2. FL's UI (browser + Win `TQuickPopupMenuWindow` / macOS JXA `Add` menu) | `fl_ui` |
| New channels, patterns, playlist clips, full arrangements, `.fsc` scores | 3. binary FLP/ZIP/FSC engine (FL 20–26+, 500 inserts) | `fl_produce` (`flp`, `arrange`, `inspect`) |
| Audio/MIDI/ZIP export & analysis | 4. FL command line (`wav/mp3/ogg/flac` + `/M` `mid` + `/Z` `zip`) | `fl_audio action=render` |
| Anything a person can see and click | 5. the desktop (`stealth` / `polite` / `foreground`) | `fl_desk` |

Check what this FL build offers (cached per FL version) with `fl_status include="summary,probe,memory"`. Enable `FL_MCP_READONLY=1` or `--profile read-only|compact|produce|mix` when safe exploration is needed.

## Rung 2: FL's browser from inside FL

**Loading in one call:** `fl_ui action=load query="Fruity Limiter" index=0` selects mixer track `index` (an effect goes to its first free slot; leave `index` out for an instrument or sample, which becomes a new channel), focuses the browser, walks the tree from the top to the first item named like `query`, loads it, and checks the project. It answers with where the plugin appeared, or says plainly that nothing was added (then use a longer part of the name, or the steps below). Several browser items can share a name (a plugin and its preset folder); it tries the next one.

The same thing by hand: `fl_ui action=window window=browser mode=focus` brings the browser forward. `action=browser browse=caption` reads the focused item. `browse=goto query="Fruity Limiter" count=200 expand=true` steps down the tree (opening folders) until an item name contains the query, and returns where it stopped. `browse=select` loads the focused plugin or sample into the selected channel or mixer slot, `browse=preview` plays it. First select what should receive it: `fl_set kind=channel index=<i> select=true` or `kind=mixer ... select=true`. Verify with `fl_list kind=plugins`. `browse=top` goes back to the first item. Stepping is quirky across FL versions: if `goto` stops short, `browse=scan` shows what the tree holds.

`action=key keys="down,enter"` presses keys in the focused FL window: it can change or delete things, so name what you are about to do first.

## Rung 3: a project file

A `.flp` can be written with the notes, patterns, playlist and sample channels in place (`fl_produce action=make genre=<g> template=<a saved project> sounds=auto`). The template is never touched. Open the result with `fl_desk macro=open path=<new.flp>` or ask the user to; answer a "save changes?" question in the screenshot only with the user's say-so.

## Rung 5: the desktop

Needs `fl-mcp desktop stealth|polite|foreground|on` and a restart. Modes:

- `stealth`: strictly non-intrusive (`PrintWindow` capture and `PostMessage` shortcuts only; never steals focus or moves the user's cursor).
- `polite` (default for `on`): prefers background capture and keystrokes; waits until the user is idle (1.2 s) before focusing FL when required, and restores the previous foreground window afterwards.
- `foreground`: immediate bring-to-front focus and cursor fallback allowed.

Rules:

1. `fl_desk action=shot` first. Positions are read off the latest shot (background capture reports `"background": true` in the result), so take a new one whenever the screen may have changed. Its size is up to 1568 px wide; coordinates are in that picture, the tool scales them.
2. One small step, then look: every action returns a new picture unless `look=false`. Check the result before the next step; stop and report when the screen is not what you expected.
3. Input goes to FL windows only. In `polite` or `foreground`, if FL cannot be brought forward the call fails and nothing is sent. Blocked: keys that close windows or leave FL. Moving the mouse to the top-left corner stops everything.
4. Prefer keys to clicks: `key keys="mod+s"` (`mod` is Ctrl on Windows, Cmd on a Mac), `type text=...` for names and paths, `click`/`double`/`right`/`drag`/`scroll` only where there is no key.
5. Macros do the common jobs and report: `script` tries background `PostMessage` before focusing FL (falling back only in `polite`/`foreground`), then confirms `state.json`; `export` renders the open project (File > Export, wav or mp3) to a new file; `open` loads a project; `save` saves.
6. Never confirm destructive dialogs (discard changes, overwrite, delete) for the user. Show the picture and ask.

## After any step

Verify with the reading tools, not with the picture alone: `fl_list`, `fl_param`, `fl_status`, `fl_notes read=true`, `fl_audio`.
