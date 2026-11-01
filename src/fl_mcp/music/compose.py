"""compose(): genre profile + brief -> Plan. Deterministic for a given seed."""

import hashlib
import random
import secrets
from dataclasses import replace

from fl_mcp.music import theory as th
from fl_mcp.music.genres import BASE_MIX, DRUM_ROLES, GENRES, MOODS, ROLE_ORDER, Genre, Sec
from fl_mcp.music.groove import PPQ, STEP, bar_hits, jitter_vel, micro, swing_delay
from fl_mcp.music.plan import Clip, Ev, Mix, Part, Pattern, Plan, Section

GROUPS = (
    ("Drums", DRUM_ROLES),
    ("Bass", ("bass",)),
    ("Harmony", ("chords", "pad")),
    ("Melody", ("lead", "arp")),
    ("FX", ("fx",)),
)
LENGTHS = ("sketch", "short", "full")
ONE_SHOT = 60  # samples play unpitched at C5 (FL's label) = MIDI 60


def fit_sections(g: Genre, length: str | int) -> list[Sec]:
    if isinstance(length, int):
        options = [fit_sections(g, k) for k in LENGTHS]
        return min(options, key=lambda o: abs(sum(s.bars for s in o) - length))
    if length == "full":
        return list(g.sections)
    core = [s for s in g.sections if s.core]
    if length == "short":
        return core
    if length == "sketch":
        return [
            replace(s, bars=max(4, s.bars // (4 if s.kind in ("intro", "outro") else 2)))
            for s in core
        ]
    raise ValueError(f"length must be one of {', '.join(LENGTHS)} or a bar count")


def unit_sizes(bars: int) -> list[int]:
    out = []
    while bars > 0:
        out.append(min(8, bars))
        bars -= out[-1]
    return out


def tidy_mono(events: list[Ev], end: float) -> list[Ev]:
    """Clamp durations so a monophonic line never overlaps itself or runs past `end`."""
    events = sorted(events, key=lambda e: (e.t, e.p))
    for i, e in enumerate(events):
        limit = (events[i + 1].t if i + 1 < len(events) else end) - e.t
        e.d = max(0.05, min(e.d, limit - 0.01))
    return events


class Composer:
    def __init__(
        self,
        g: Genre,
        seed: int,
        tonic: int,
        scale: str,
        family: str,
        progression: tuple[str, ...],
        energy: float,
        roles: set[str],
    ) -> None:
        self.g, self.seed, self.tonic, self.scale = g, seed, tonic, scale
        self.family, self.prog, self.energy, self.roles = family, progression, energy, roles
        self.per = g.chords.per
        self.prev_voicing: dict[str, list[int] | None] = {}

    def rng(self, *tag) -> random.Random:
        return random.Random(":".join(map(str, (self.seed, *tag))))

    # ---- harmony

    def chord(self, bar: int) -> tuple[int, tuple[int, ...]]:
        """(root pitch class, interval tuple) for a bar inside a unit."""
        root, iv = th.parse_chord(self.prog[(bar // self.per) % len(self.prog)])
        return (self.tonic + root) % 12, iv

    def chord_pcs(self, bar: int) -> list[int]:
        root, iv = self.chord(bar)
        return [(root + i) % 12 for i in iv]

    # ---- helpers

    def at(self, bar: int, step: float, rng: random.Random, role: str, ticks: int = 0) -> float:
        t = bar * 4 + step * STEP
        if step == int(step):
            t += swing_delay(int(step), self.g.swing)
        return t + (micro(rng, ticks) if ticks else 0.0)

    # ---- drums

    def kick_hits(self, key: str, bar: int, unit_bars: int, dens: float) -> list[tuple[int, int]]:
        rng = self.rng("kick", key, bar)
        variants = self.g.drums.get("kick", ())
        if not variants:
            return []
        vi = (
            0
            if bar % 4 == 0
            else rng.choices(range(len(variants)), [3] + [1] * (len(variants) - 1))[0]
        )
        hits = bar_hits(variants[vi], rng, dens)
        if bar == unit_bars - 1 and rng.random() < 0.22 and len(hits) > 1:
            hits = [h for h in hits if h[0] < 12]  # drop the last beat before the next phrase
        return hits

    def drums(
        self,
        role: str,
        sec: Sec,
        key: str,
        unit_bars: int,
        u: int,
        dens: float,
        prev_kind: str | None,
    ) -> list[Ev]:
        variants = self.g.drums[role]
        events: list[Ev] = []
        for bar in range(unit_bars):
            rng = self.rng(role, key, bar)
            last = bar == unit_bars - 1
            if role == "kick":
                hits = self.kick_hits(key, bar, unit_bars, dens)
                if sec.kind == "build" and bar == unit_bars - 1 and unit_bars >= 4:
                    hits = [h for h in hits if h[0] < 8]
            elif sec.kind == "build" and role == self.build_roll_role(sec):
                hits = self.roll(bar, unit_bars)
            else:
                alt = len(variants) > 1 and (
                    bar % 4 == 3 or (bar % 2 and rng.random() < 0.25 * (u + 1))
                )
                hits = bar_hits(
                    variants[1 if alt else 0]
                    if len(variants) < 3
                    else variants[rng.randrange(len(variants)) if bar % 4 else 0],
                    rng,
                    dens,
                )
                if last and role in ("snare", "clap") and rng.random() < 0.3 + 0.4 * dens:
                    hits = [h for h in hits if h[0] < 12] + [
                        (s, 60 + 15 * i)
                        for i, s in enumerate((12, 13, 14, 15))
                        if rng.random() < 0.8
                    ]
                if last and role in ("hat", "ohat") and rng.random() < 0.35:
                    hits = [h for h in hits if h[0] < 14]
            for step, vel in hits:
                spread = 6 if role in ("hat", "ohat", "perc") else 3
                events.append(
                    Ev(
                        self.at(bar, step, rng, role, 2 if role in ("hat", "ohat", "perc") else 0),
                        STEP * 0.5,
                        ONE_SHOT,
                        jitter_vel(vel, rng, spread),
                    )
                )
        return events

    def build_roll_role(self, sec: Sec) -> str | None:
        return "snare" if "snare" in sec.roles else ("clap" if "clap" in sec.roles else None)

    @staticmethod
    def roll(bar: int, unit_bars: int) -> list[tuple[int, int]]:
        p = (bar + 1) / unit_bars
        every = 4 if p <= 0.34 else (2 if p <= 0.67 else 1)
        return [(s, int(50 + 75 * (bar * 16 + s) / (unit_bars * 16))) for s in range(0, 16, every)]

    # ---- bass

    def bass(self, sec: Sec, key: str, unit_bars: int, dens: float) -> list[Ev]:
        b, events, prev = self.g.bass, [], None
        for bar in range(unit_bars):
            rng = self.rng("bass", key, bar)
            root_pc, _ = self.chord(bar)
            root = b.lo + (root_pc - b.lo) % 12
            pat = b.patterns[0] if len(b.patterns) == 1 else b.patterns[1 if bar % 4 == 3 else 0]
            if pat == "follow":
                kicks = self.kick_hits(key, bar, unit_bars, dens)
                steps = sorted({s: v for s, v in kicks}.items())
                extra = [
                    s for s in (6, 10, 14) if s not in dict(steps) and rng.random() < 0.12 * dens
                ]
                steps = sorted(steps + [(s, 90) for s in extra])
            else:
                steps = bar_hits(pat, rng, dens)
            if sec.intensity < 0.6:  # thin out in quiet sections, keep the downbeat
                steps = [h for i, h in enumerate(steps) if i == 0 or rng.random() < 0.7]
            for i, (s, v) in enumerate(steps):
                pitch = root
                if i > 0 and b.pitch == "root_oct" and rng.random() < 0.28 and root + 12 <= b.hi:
                    pitch = root + 12
                elif i > 0 and b.pitch == "root_fifth" and rng.random() < 0.35 and root + 7 <= b.hi:
                    pitch = root + 7
                nxt = steps[i + 1][0] if i + 1 < len(steps) else 16
                d = max(0.1, min(nxt - s, b.max_len) * STEP * 0.94)
                events.append(
                    Ev(
                        self.at(bar, s, rng, "bass"),
                        d,
                        pitch,
                        jitter_vel(v, rng, 3),
                        b.slide and prev is not None and pitch != prev,
                    )
                )
                prev = pitch
        return events

    # ---- chords and pads

    def harmony(self, role: str, spec, key: str, unit_bars: int, dens: float) -> list[Ev]:
        events = []
        sustained = spec.gate >= 16
        for bar in range(unit_bars):
            if sustained and bar % self.per:
                continue
            rng = self.rng(role, key, bar)
            root_pc, iv = self.chord(bar)
            raw = [60 + root_pc + i for i in iv]
            voicing = th.voice_lead(self.prev_voicing.get(role), raw, spec.lo, spec.hi)
            self.prev_voicing[role] = voicing
            pat = spec.patterns[1 if bar % 4 == 3 and len(spec.patterns) > 1 else 0]
            steps = [(0, 100)] if sustained else bar_hits(pat, rng, dens)
            span = 4.0 * (self.per if sustained else 1)
            for i, (s, v) in enumerate(steps):
                nxt = steps[i + 1][0] * STEP if i + 1 < len(steps) else 4.0
                d = min(spec.gate * STEP if spec.gate else nxt - s * STEP, span - s * STEP)
                for vi, pitch in enumerate(voicing):
                    off = (rng.randint(0, spec.strum) * vi) / PPQ if spec.strum else 0.0
                    start = self.at(bar, s, rng, role) + off
                    room = bar * 4 + (span if sustained else 4.0) - start  # stay inside the chord
                    events.append(
                        Ev(
                            start,
                            max(0.1, min(d * 0.97 - off, room)),
                            pitch,
                            jitter_vel(round(v * 0.82), rng, 4),
                        )
                    )
        return events

    def arp(self, key: str, unit_bars: int, dens: float) -> list[Ev]:
        a, events, idx = self.g.arp, [], 0
        for bar in range(unit_bars):
            rng = self.rng("arp", key, bar)
            root_pc, iv = self.chord(bar)
            tones = sorted(
                {
                    p
                    for o in range(-12, 48, 12)
                    for i in iv
                    if a.lo <= (p := 60 + root_pc + i + o) <= a.hi
                }
            )
            seq = {"up": tones, "down": tones[::-1], "updown": tones + tones[-2:0:-1]}.get(a.shape)
            for s, v in bar_hits(a.pattern, rng, dens):
                pitch = rng.choice(tones) if seq is None else seq[idx % len(seq)]
                idx += 1
                events.append(
                    Ev(self.at(bar, s, rng, "arp"), STEP * 0.55, pitch, jitter_vel(v - 10, rng, 4))
                )
        return events

    # ---- melody

    @staticmethod
    def avoid_run(
        pitch: int, hist: list[int], pool: list[int], prefer: list[int] | None = None
    ) -> int:
        """Never repeat a pitch more than twice in a row: step to the nearest other tone."""
        if len(hist) >= 2 and hist[-1] == hist[-2] == pitch:
            options = [p for p in (prefer or pool) if p != pitch] or [p for p in pool if p != pitch]
            return min(options, key=lambda p: abs(p - pitch))
        return pitch

    def lead(self, sec: Sec, key: str, unit_bars: int, occurrence: int, dens: float) -> list[Ev]:
        spec, events = self.g.lead, []
        pool = th.scale_pitches(self.tonic, self.scale, spec.lo, spec.hi)
        prev, hist = pool[len(pool) // 2], []
        theme = {"hook": "h", "drop": "h", "verse": "v", "bridge": "b"}.get(sec.kind, "i")
        for ph in range(0, unit_bars, 2):
            base = self.rng("lead", theme, ph % 8)  # same theme every time this kind returns
            var = self.rng("leadvar", key, ph, occurrence)  # but varied by occurrence
            cells = [base.choice(spec.cells), base.choice(spec.cells)]
            phrase = []
            for bi in range(min(2, unit_bars - ph)):
                bar = ph + bi
                tones = set(self.chord_pcs(bar))
                for s, v in bar_hits(cells[bi], base, 1.0):
                    strong = s % 4 == 0
                    cands = ([p for p in pool if p % 12 in tones] if strong else pool) or pool
                    near = [p for p in cands if 0 < abs(p - prev) <= (5 if strong else 4)] or cands
                    weights = [3 if abs(p - prev) <= 2 else 1 for p in near]
                    pitch = base.choices(near, weights)[0]
                    if var.random() < 0.25 * min(1, occurrence):  # return visits vary the line
                        pitch = var.choice([q for q in pool if abs(q - pitch) <= 4] or [pitch])
                    pitch = self.avoid_run(pitch, hist, pool)
                    hist.append(pitch)
                    phrase.append((bar, s, pitch, v))
                    prev = pitch
            if phrase:  # cadence: land the last note of the phrase on a chord tone
                bar, s, _, v = phrase[-1]
                end = [p for p in pool if p % 12 in self.chord_pcs(bar)] or pool
                final = self.avoid_run(min(end, key=lambda p: abs(p - prev)), hist[:-1], pool, end)
                hist[-1] = final
                phrase[-1] = (bar, s, final, v)
            if sec.intensity < 0.6:
                phrase = phrase[::2]
            for i, (bar, s, pitch, v) in enumerate(phrase):
                nxt = (
                    (phrase[i + 1][0] * 4 + phrase[i + 1][1] * STEP)
                    if i + 1 < len(phrase)
                    else (ph + 2) * 4
                )
                d = max(0.2, min(nxt - (bar * 4 + s * STEP), 2.0) * 0.92)
                t = self.at(bar, s, var, "lead", 2)
                if spec.ornament and d >= 0.5 and t >= 0.2 and var.random() < 0.25:
                    lower = [p for p in pool if p < pitch]
                    if lower:
                        events.append(Ev(t - 0.125, 0.125, lower[-1], max(1, v - 25)))
                events.append(Ev(t, d, pitch, jitter_vel(round(v * 0.9), var, 5)))
        return events

    # ---- transitions

    def fx(
        self, sec: Sec, unit_index: int, units: int, unit_bars: int, prev_kind: str | None
    ) -> list[Ev]:
        out = []
        if sec.kind == "build" and unit_index == units - 1:
            n = min(unit_bars, 4)
            out.append(Ev((unit_bars - n) * 4.0, n * 4.0, ONE_SHOT, 90))  # riser
        if sec.kind in ("drop", "hook") and unit_index == 0 and prev_kind in ("build", "break"):
            out.append(Ev(0.0, 2.0, ONE_SHOT, 120))  # impact
        if sec.kind == "break" and unit_index == 0 and prev_kind in ("drop", "hook"):
            out.append(Ev(0.0, 4.0, ONE_SHOT, 80))  # downlifter
        return out


def pick_key(
    rng: random.Random, key: str | None, g: Genre, mood: str | None
) -> tuple[int, str, str, list[str]]:
    notes: list[str] = []
    if key:
        tonic, scale = th.parse_key(key)
        if "scale" not in key.lower() and len(key.split()) == 1 and mood:
            scale = g.mood_scale[mood]
    else:
        tonic = th.pc_of(rng.choice(("C", "D", "E", "F", "G", "A", "A", "Bb", "F#", "C#")))
        scale = g.mood_scale[mood] if mood else g.default_scale
    family = scale
    if scale not in g.progressions:
        family = g.default_scale
        notes.append(f"{g.name} has no '{scale}' progressions; using {family} harmony")
        scale = family
    return tonic, scale, family, notes


def build_mix(g: Genre) -> dict[str, Mix]:
    out = {}
    for role in ROLE_ORDER:
        peak, pan, rev, dly, sc, hpf, eq = BASE_MIX[role]
        mix = Mix(role, peak, pan, [list(e) for e in eq], hpf, rev, dly, sc, g.sounds.get(role, ""))
        for k, v in g.mix_over.get(role, {}).items():
            setattr(mix, k, v)
        out[role] = mix
    return out


def compose(
    genre: str,
    *,
    key: str | None = None,
    bpm: int | None = None,
    length: str | int = "short",
    mood: str | None = None,
    energy: float = 0.75,
    seed: int | None = None,
    roles: list[str] | None = None,
) -> Plan:
    if genre not in GENRES:
        raise ValueError(f"unknown genre '{genre}'; use one of {', '.join(GENRES)}")
    if mood is not None and mood not in MOODS:
        raise ValueError(f"mood must be one of {', '.join(MOODS)}")
    if not 0.0 <= energy <= 1.0:
        raise ValueError("energy must be between 0 and 1")
    g = GENRES[genre]
    if seed is None:
        seed = secrets.randbelow(2**31)
    rng = random.Random(f"{seed}:{genre}")
    tonic, scale, family, notes = pick_key(rng, key, g, mood)
    bpm = bpm or g.bpm[1]
    if not 40 <= bpm <= 220:
        raise ValueError("bpm must be between 40 and 220")
    if not g.bpm[0] <= bpm <= g.bpm[2]:
        notes.append(f"{bpm} BPM is outside the usual {g.bpm[0]}-{g.bpm[2]} for {g.name}")
    progression = rng.choice(g.progressions[family])
    allowed = set(roles) if roles else set(ROLE_ORDER)
    unknown = allowed - set(ROLE_ORDER)
    if unknown:
        raise ValueError(f"unknown roles {sorted(unknown)}; use {', '.join(ROLE_ORDER)}")
    cp = Composer(g, seed, tonic, scale, family, progression, energy, allowed)

    sections: list[Section] = []
    patterns: list[Pattern] = []
    clips: list[Clip] = []
    seen: dict[str, int] = {}
    cache: dict[str, int] = {}
    bar = 0
    prev_kind: str | None = None
    occurrences: dict[str, int] = {}
    for sec in fit_sections(g, length):
        active = [r for r in ROLE_ORDER if r in sec.roles and r in allowed]
        occ = occurrences[sec.kind] = occurrences.get(sec.kind, -1) + 1
        sections.append(Section(sec.name, sec.kind, bar, sec.bars, sec.intensity, active))
        dens = max(0.2, min(1.5, sec.intensity * (0.5 + energy)))
        sizes = unit_sizes(sec.bars)
        offset = 0
        for ui, ubars in enumerate(sizes):
            letter = "ABCDEFGH"[ui % 8]
            key_ = f"{sec.name}:{letter}:{occ}"
            for track, (gname, group) in enumerate(GROUPS, start=1):
                parts: dict[str, Part] = {}
                for role in group:
                    if role not in active and not (role == "fx" and "fx" in allowed):
                        continue
                    if role in DRUM_ROLES and role in g.drums:
                        ev = cp.drums(role, sec, key_, ubars, ui + 2 * occ, dens, prev_kind)
                    elif role == "bass":
                        ev = tidy_mono(cp.bass(sec, key_, ubars, dens), ubars * 4.0)
                    elif role == "chords":
                        ev = cp.harmony("chords", g.chords, key_, ubars, dens)
                    elif role == "pad":
                        ev = cp.harmony("pad", g.pad, key_, ubars, dens)
                    elif role == "arp":
                        ev = cp.arp(key_, ubars, dens)
                    elif role == "lead":
                        ev = tidy_mono(cp.lead(sec, key_, ubars, occ, dens), ubars * 4.0)
                    elif role == "fx":
                        ev = cp.fx(sec, ui, len(sizes), ubars, prev_kind)
                    else:
                        continue
                    if ev:
                        parts[role] = Part(
                            role,
                            role in DRUM_ROLES or role == "fx",
                            sorted(ev, key=lambda e: (e.t, e.p)),
                        )
                if not parts:
                    continue
                sig = hashlib.sha1(
                    repr(
                        (
                            ubars,
                            [
                                (r, [(e.t, e.d, e.p, e.v, e.slide) for e in p.events])
                                for r, p in parts.items()
                            ],
                        )
                    ).encode()
                ).hexdigest()
                if sig not in cache:
                    seen[f"{sec.name} {letter} - {gname}"] = len(patterns)
                    cache[sig] = len(patterns)
                    patterns.append(Pattern(f"{sec.name} {letter} - {gname}", ubars, parts))
                clips.append(Clip(cache[sig], bar + offset, ubars, track))
            offset += ubars
        bar += sec.bars
        prev_kind = sec.kind

    used = {r for p in patterns for r in p.parts}
    return Plan(
        id=f"{genre}-{seed:x}",
        genre=genre,
        seed=seed,
        bpm=bpm,
        swing=g.swing,
        key=th.key_name(tonic, scale),
        tonic=tonic,
        scale=scale,
        progression=list(progression),
        roles=[r for r in ROLE_ORDER if r in used],
        sections=sections,
        patterns=patterns,
        clips=clips,
        mix=build_mix(g),
        master={"lufs": g.lufs, "ceiling_db": -1.0},
        notes=notes,
    )
