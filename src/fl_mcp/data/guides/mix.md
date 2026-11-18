# Mix and master

Order matters: level and balance, then tone, then dynamics, then space, then the master. A balance problem is never fixed on the master limiter.

## The loop

1. `fl_mix action=doctor`: problems worst first (clipping, headroom, routing, pan, missing plugins). Fix the worst before anything else.
2. `fl_mix action=fix`: one-call cleanup — sets role stereo spread (hats/perc/keys panned, kick/bass/snare centered), applies clean low-shelf HPF + mud dips (`260–320 Hz`), flags unrouted `fx=0` channels, and pulls down clipping inserts.
3. `fl_mix action=balance`: moves faders so each role peaks at its target while the project plays `seconds` (default 6) from `bar`. Targets in dBFS peak: kick -8, snare -10, clap -12, hat -20, open hat -22, bass -9, chords -16, pad -20, lead -14, arp -18, fx -16; the master peaks under -3 before its limiter. `targets="1=-8,2=-9"` overrides by mixer track. `action=meter` only reads (supports >64 inserts seamlessly in chunked batches).
4. `fl_mix action=bus targets="1,2,3->10@0.35" role="Drum Bus"`: creates and balances send routing to reverb, delay, or stem buses in one call.
5. `fl_mix action=chain`: shows each track's plugins against what its role wants and in what order; `apply=true` sets compressor, limiter, reverb and delay values by meaning on plugins already there, and names missing ones.
6. Hear & inspect deeper:
   - `fl_audio action=render fmt=<wav|mp3|ogg|flac|mid|zip>` (`/M` MIDI export, `/Z` Zipped Loop Package supported alongside audio renders, including parallel hidden-desktop render on Windows while FL stays open).
   - `fl_audio action=masking path=<vocal.wav> reference=<beat.wav>`: measures critical-band spectral collision and returns exact `freq_hz` / `gain_db` / `q` carving suggestions.
   - `fl_audio action=compare reference=<file>`: compares loudness, crest, width, and 7-band spectrum.
   - `fl_audio action=transcribe path=<audio.wav>`: extracts BPM, Krumhansl key/mode, and F0 melody notes (`fl_notes` tuples).

## Reading the measurements

| Measurement | Healthy | If not |
| --- | --- | --- |
| Integrated loudness (LUFS) | near the genre target below; streaming services turn everything to about -14 | lower or raise the input gain of the master limiter by the difference, not the faders |
| True peak (dBTP) | at or under -1 | set the limiter ceiling to -1.0 so lossy encoders do not distort |
| Crest factor | 6-9 dB electronic, 9-14 acoustic | under 6: over-limited, ease the limiter; over 16: glue with bus compression |
| Clipped samples, DC | none, DC under 0.002 | find the track that clips (`fl_mix action=meter`); high-pass at 20 Hz on the master |
| Stereo correlation, mono loss | correlation above 0.2, mono loss under 3 dB | check phase of wide synths and stereo effects |
| Low-end side level | low end centred (side under about -14 dB below 120 Hz) | keep kick, bass and sub mono |
| Loudness range (LU) | 4-8 electronic, more for dynamic music | |

Loudness targets (LUFS integrated): {{lufs}}. Do not chase loudness before the balance is right.

## Where things live in frequency

sub 20-60 Hz (weight, only kick and bass), bass 60-250 (body), low mid 250-500 (mud: cut here when it sounds boxy), mid 500-2k (the instrument itself), high mid 2-6k (presence and harshness), high 6-12k (hats, air), air 12-20k. Cut before you boost; moves of 1.5-4 dB are plenty. Two instruments fighting: cut one where the other needs room, or pan them apart.

## Compression starting points

| Role | Threshold | Ratio | Attack | Release |
| --- | --- | --- | --- | --- |
| kick | -12 dB | 3:1 | 15 ms | 80 ms |
| snare, clap | -14 dB | 3-4:1 | 8-10 ms | 90-100 ms |
| bass | -18 dB | 4:1 | 20 ms | 150 ms |
| chords | -20 dB | 2:1 | 30 ms | 200 ms |
| lead | -18 dB | 3:1 | 10 ms | 120 ms |
| drum bus | -16 dB | 3:1 | 30 ms | 150 ms |

Set them in real units: `fl_param target=mixer index=<track> slot=<n> settings="Threshold=-18 dB, Ratio=4:1, Attack=20 ms"`. Aim for 2-4 dB of gain reduction.

## Space and width

Reverb and delay belong on sends (one reverb, one delay, shared) rather than inserts: `fl_set kind=mixer index=<track> send=<fx track> level=0.3`. Pads and leads take more, kicks and bass none. Pan hats and percussion lightly (+-0.2 to 0.3), keep kick, snare, bass and lead near the centre. Reverb pre-delay 20-40 ms keeps a sound clear.

## Master

Chain: gentle EQ, bus glue (optional), limiter last with ceiling -1.0 dB. Raise loudness with the limiter's input gain until the integrated loudness reaches the target while the true peak stays under -1. `fl_audio action=master genre=<g>` (or `target=<LUFS>`) does exactly that loop: it sets the ceiling, exports the open project, moves the limiter's gain (found by meaning, in dB; a threshold-driven limiter is turned down instead) by the loudness error, and measures again, up to three adjustments. It needs `fl_desk` for the exports and a limiter on the master (`fl_ui action=load query="Fruity Limiter" index=0`), reports each pass and the gain it started from, and stops with a note when the limiter saturates. Never put the master fader above 0 dB (volume 0.8).
