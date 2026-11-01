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
