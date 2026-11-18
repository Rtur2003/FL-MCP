# Produce a track fast

One request becomes a playable, varied, in-key arrangement in FL Studio; then it is mixed and measured. Check `fl_status include="summary,memory"` first to respect existing producer context and channels. State a crisp **Production Brief** (genre, key, BPM, section map) when choices are inferred.

## The shortest path

1. `fl_status include="summary,memory"`: inspect active tempo, pattern, channels, mixer tracks, plugins, and saved producer context in one call.
2. `fl_produce action=make genre=<g> mood=<m> length=short`: builds a section plan (intro, verse, build, drop, break, outro), writes drum patterns into Channel Rack channels matched by name with tempo, markers, EQ and pan, and exports a multi-track MIDI file. Tell the user the Production Brief in two lines (key, tempo, sections).
3. Expanding an existing loop (Melody-Preservation Rule): `fl_produce action=arrange template=<file.flp|.zip> length=short` promotes a saved user loop `.flp` or Zipped Loop Package (`.zip`) into full contrasting sections (dropping drums/bass in Intro/Break/Outro) while **preserving every user pattern and melody untouched**.
4. Building a full project from scratch: `fl_produce action=make genre=<g> template=<.flp or auto> sounds=auto` writes a new `.flp` (supporting FL 21–26+ 80/88-byte playlist formats and up to 500 mixer inserts) with patterns, playlist clips, updated `end` markers, and sample/preset/SoundFont channels. `action=inspect` reads `.flp`, `.zip`, `.fsc` scores, or `.fst` presets.
5. Mix and measure: see the mix guide.

`seed` repeats a song deterministically; `energy` 0.4–1.0 scales density; `roles=kick,bass,lead` isolates stems; `action=genres` lists supported styles.

## Genres

{{genres}}

## Notes into the piano roll

`fl_notes` queues notes (`[midi, start_beat, length_beats, vel]`) or chord progressions in either beat offset (`Am7@0:4`) or `bar|beat` + note value notation (`Dm7@2|1:n/8, G7@2|3:1/4@95`), and applies optional musical transforms via `transform="humanize:10,swing:60,snap:A minor,legato:0.95,ratchet:3"`. Passing `transform` without `notes` transforms the current `state.json` notes directly.

Every queue write updates `inbox.json` AND embeds `_BAKED` into the installed `FL MCP.pyscript` (so sandboxed FL 25.2.5+ builds work reliably). Pass `apply=true` (when `fl_desk` is enabled) or run **Tools > Scripting > FL MCP** manually — it **applies immediately (no Accept dialog)**, merges `(pitch, time, color)` in place, and writes back `state.json`. Verify with `fl_notes read=true` (which also returns compact `summary` stats). MIDI 60 is C4 (labeled C5 in FL).

Drums can also be programmed with `fl_steps channel=<i> on=[0,4,8,12]` or compact velocity strings: `fl_steps channel=<i> grid="z...x...|z.o.x.z.*4" swing=20` (`*N` tiles across N bars; FL MCP warns if your grid is shorter than the target pattern length).

## Make it musical and not repetitive

- Stay in the key and scale declared in the Production Brief; use `fl_audio action=transcribe` on audio stems to extract BPM, key, and monophonic F0 melody notes (`[midi, beat, dur, vel]`) directly into `fl_notes`.
- Vary velocity and timing (`transform="humanize:10,swing:58"`), add fill/ratchet variation at 4/8-bar boundaries (`ratchet:3@1`), and preserve user melodies when arranging or harmonizing existing projects.
- One element owns sub energy (<100 Hz); check kits with `fl_library action=kit genre=<g>` or find matching samples with `fl_library action=similar path=<wav>`.

## Checks before you say it is done

`fl_list kind=channels` (names & mixer routing), `fl_transport action=play`, `fl_mix action=doctor`, `fl_audio action=render` + read measurements/advice. Report what changed, what was measured, and any manual steps remaining.
