"""The compact `settings` syntax of fl_param: 'Threshold=-18 dB, Ratio=4:1, Gain=0.6'."""

import re

NUMBER = re.compile(r"[-+]?(\d+\.?\d*|\.\d+)")
MAX_ITEMS = 40


def parse_settings(text: str) -> list[dict]:
    """Name=value pairs. A bare number is a 0-1 position; a value with a unit (or `~` before a
    number) is a real value that the plugin's own text is solved against."""
    items = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, eq, value = part.rpartition("=")
        name, value = name.strip(), value.strip()
        if not eq or not name or not value:
            raise ValueError(f"'{part}' should look like Name=value")
        if value.startswith("~"):
            items.append({"p": name, "r": value[1:].strip()})
        elif NUMBER.fullmatch(value):
            items.append({"p": name, "v": float(value)})
        else:
            items.append({"p": name, "r": value})
    if not items:
        raise ValueError("settings is empty; use 'Name=value, Name=value'")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"at most {MAX_ITEMS} settings per call")
    return items


def is_steps(text: str) -> bool:
    """'+2' and '-1' step through presets; anything else is a preset name."""
    return bool(re.fullmatch(r"[-+]\d+", text.strip()))


# ---- plugin value text: the host-side twin of the FL script parser (a test keeps them equal)

UNITS = {
    "db": ("db", 1.0),
    "hz": ("hz", 1.0),
    "khz": ("hz", 1000.0),
    "ms": ("ms", 1.0),
    "sec": ("ms", 1000.0),
    "s": ("ms", 1000.0),
    "%": ("%", 1.0),
    "st": ("st", 1.0),
    "semitones": ("st", 1.0),
    "semitone": ("st", 1.0),
    "ct": ("ct", 1.0),
    "cents": ("ct", 1.0),
    "cent": ("ct", 1.0),
    "x": ("x", 1.0),
    ":1": ("ratio", 1.0),
    "oct": ("oct", 1.0),
    "bpm": ("bpm", 1.0),
}
_UNIT_KEYS = sorted(UNITS, key=len, reverse=True)
_FIRST_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")


def parse_value(text: str) -> tuple[float, str] | None:
    """'2.5 kHz' -> (2500.0, 'hz'); '-6 dB' -> (-6.0, 'db'); 'Off' -> None."""
    s = str(text).strip()
    if not s:
        return None
    low = s.lower()
    if "inf" in low:
        return (-120.0 if "-" in low else 120.0), "db"
    found = _FIRST_NUMBER.search(s)
    if found is None:
        return None
    value = float(found.group())
    if s[:1] in "LlRr" and s[1:2] in " 0123456789" and s[1:2] != "":
        value = -abs(value) if s[0] in "Ll" else abs(value)
    rest = s[found.end() :].lower().replace(" ", "")
    if value == 1.0 and rest.startswith(":"):
        rhs = _FIRST_NUMBER.match(rest[1:])
        if rhs and not rest[1 + rhs.end() :]:
            return float(rhs.group()), "ratio"
    for key in _UNIT_KEYS:
        if rest.startswith(key):
            unit, scale = UNITS[key]
            return value * scale, unit
    return value, ""
