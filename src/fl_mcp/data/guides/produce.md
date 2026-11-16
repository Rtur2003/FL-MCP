# Produce a track fast

One request becomes a playable, varied, in-key arrangement in FL Studio; then it is mixed and measured. Ask only for what is missing (genre, mood, rough length), choose sensible defaults for the rest, and say what you chose.

## The shortest path

1. `fl_status`, `fl_list kind=channels`: is a project open, what exists?
2. `fl_produce action=make genre=<g> mood=<m> length=short`: builds the plan (sections, drums, bass, chords, pad, lead, arp, mix targets), writes the drum patterns into the Channel Rack channels it can match by name (kick, clap, hat, bass...) with tempo, markers, EQ and pan, and exports one MIDI file with every part. Tell the user the plan in two lines (key, tempo, sections).
3. Melody and harmony: drag the MIDI file into FL (each track becomes a channel and sections become markers), or send one part: `fl_produce action=queue pattern=<name> role=lead`, then apply it (below).
4. A finished project instead: `fl_produce action=make genre=<g> template=<any saved .flp> sounds=auto` writes a new .flp (the template is never modified) with every pattern, the playlist, updated arrangement `end` markers, and sample/preset channels (`.wav`/`.ogg`/`.fst`/`.sf2` files, or indexed preset/SoundFont names resolved via `fl_library`). `action=inspect` checks any `.flp` structure and arrangement (`verify`) or inspects a `.fst` preset. Open it: `fl_desk macro=open path=...` when desktop control is on (`polite`/`foreground`), else ask the user to open it or render via `fl_audio action=render project=...`.
5. Mix and measure: see the mix guide.

`seed` repeats a song exactly, a new seed gives another take. `energy` 0.4 is sparse, 1.0 dense. `roles=kick,bass,lead` builds only those parts. `action=show pattern=<name> role=<r>` reads notes back before sending. `action=genres` lists genres.

## Genres

{{genres}}

## Notes into the piano roll

FL's API cannot write notes, so `fl_notes` queues them: `notes=[[midi, start_beat, length_beats, velocity], ...]` for the selected channel and pattern (`channel`, `pattern` to choose, `replace=true` to clear first), and opens the piano roll. Pass `apply=true` (when desktop control is on) or `fl_desk macro=script` to run Tools > Scripting > FL MCP right away, or ask the user to run it once and press Accept. Confirm with `fl_notes read=true`: `applied` must be true. MIDI 60 is C4 (FL labels it C5). A 4/4 bar is 4 beats; at most 1000 notes per call.

Drums can also be written directly with `fl_steps channel=<i> on=[0,4,8,12] velocity=100` (16 steps per bar, 0-based) or a compact velocity string: `fl_steps channel=<i> grid="z...x...|z.o.x.z." swing=20` (`.` off, `1-9`/`a-z` velocity, `|` bar separators ignored). For full `.flp` builds without hunting for file paths, pass `template=auto` (or `action=inspect template=list` to see discovered templates).

## Make it musical and not repetitive

- Stay in the key and scale the plan states. Bass roots follow the chord roots. Lead notes on strong beats are chord tones; passing notes go on weak beats. Keep a lead within about an octave and a half and repeat a short motif with small changes (call and response).
- Vary velocity: hats 60-100 with the offbeats accented, ghost notes 30-50, no two bars identical. Swing 50-60 % where the genre calls for it (boom bap, lo-fi, house).
- Put a fill or a change at the end of every 4 or 8 bars. Contrast sections: remove the kick and bass in a break, add a riser or a filter sweep before a drop. Do not flatten the plan by adding every role to every section.
- One thing owns the low end. A short kick (under about 100 ms) with a sustained bass works; otherwise duck the bass under the kick. Tune the kick's pitch to the song's root note for a tight sub.
- Sounds: take the whole drum kit from one pack (`fl_library action=kit genre=<g>`) for cohesion; layer a clap on a snare, keep hats from one family; pick instruments per role from the plugins guide.

## Checks before you say it is done

`fl_list kind=channels` (names, routing to mixer tracks), `fl_transport action=play` to audition, `fl_mix action=doctor`, `fl_audio action=render` and read the advice. Report what was made, what was measured, and anything left for the user.
