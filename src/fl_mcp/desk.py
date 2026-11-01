"""Desktop tier: see FL Studio's screen and press its keys and buttons.

FL's scripting API cannot load a plugin, run the piano roll script or export audio. A person does
those by looking and clicking, so this gives the model the same eyes and hands. It is off until
`fl-mcp desktop on`, sends input only while an FL Studio window is in front (it brings FL forward
first, and refuses if it cannot), blocks keys that close windows or the system, and stops when
the mouse is in the screen's top-left corner."""

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fl_mcp.paths import data_dir


class DeskError(ValueError):
    pass


@dataclass(frozen=True)
class Win:
    handle: int
    title: str
    rect: tuple[int, int, int, int]  # left, top, width, height in screen pixels
    process: str


@dataclass(frozen=True)
class Combo:
    mods: tuple[str, ...]
    key: str


class Backend:
    """What the operating system has to provide. Windows and macOS implement it."""

    name = "none"

    def windows(self) -> list[Win]:
        raise NotImplementedError

    def foreground(self) -> Win | None:
        raise NotImplementedError

    def focus(self, win: Win) -> bool:
        raise NotImplementedError

    def cursor(self) -> tuple[int, int]:
        raise NotImplementedError

    def screen(self) -> tuple[int, int, int, int]:
        raise NotImplementedError

    def capture(self, rect: tuple[int, int, int, int], max_width: int) -> tuple[bytes, int, int]:
        """A PNG of the rectangle, shrunk to at most max_width wide: (png, width, height)."""
        raise NotImplementedError

    def key(self, combo: Combo) -> None:
        raise NotImplementedError

    def type(self, text: str) -> None:
        raise NotImplementedError

    def mouse(
        self,
        kind: str,
        x: int,
        y: int,
        x2: int | None = None,
        y2: int | None = None,
        amount: int = 0,
    ) -> None:
        raise NotImplementedError


# ---- switch

TRUE = ("1", "true", "yes", "on")


def flag_file() -> Path:
    return data_dir() / "desktop.on"


def enabled() -> bool:
    env = os.environ.get("FL_MCP_DESKTOP")
    if env is not None:
        return env.strip().lower() in TRUE
    return flag_file().is_file()


def set_enabled(on: bool) -> Path:
    path = flag_file()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("on\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return path


# ---- keys

NAMED = {
    "enter", "escape", "tab", "space", "backspace", "delete", "insert", "home", "end",
    "pageup", "pagedown", "left", "up", "right", "down", *(f"f{i}" for i in range(1, 13)),
}  # fmt: skip
ALIASES = {
    "control": "ctrl", "option": "alt", "return": "enter", "esc": "escape", "del": "delete",
    "ins": "insert", "pgup": "pageup", "pgdn": "pagedown", "spacebar": "space",
    "command": "cmd", "meta": "cmd", "super": "win", "windows": "win", "plus": "+", "minus": "-",
}  # fmt: skip
BLOCKED = (
    ({"alt"}, "f4", "closes the window"),
    ({"cmd"}, "q", "quits the application"),
    ({"ctrl", "alt"}, "delete", "is the system security screen"),
)


def parse_combo(text: str, platform: str | None = None) -> Combo:
    """'ctrl+alt+y' -> Combo. 'mod' is Ctrl on Windows and Cmd on macOS."""
    platform = platform or sys.platform
    mac = platform == "darwin"
    tokens = [ALIASES.get(t.strip().lower(), t.strip().lower()) for t in text.split("+")]
    if not tokens or not tokens[-1] or any(not t for t in tokens[:-1]):
        raise DeskError(f"'{text}' is not a key: write it like 'ctrl+s', 'enter' or 'f9'")
    *mods, key = tokens
    mods = [("cmd" if mac else "ctrl") if m == "mod" else m for m in mods]
    if key == "mod":
        raise DeskError("'mod' is a modifier: write mod+s")
    for m in mods:
        if m == "win" or (m == "cmd" and not mac):
            raise DeskError(f"'{m}' is not allowed on this system: it could leave FL Studio")
        if m not in ("ctrl", "alt", "shift", "cmd"):
            raise DeskError(f"unknown modifier '{m}' in '{text}': use ctrl, alt, shift or mod")
    if len(set(mods)) != len(mods):
        raise DeskError(f"'{text}' repeats a modifier")
    if not (key in NAMED or len(key) == 1):
        raise DeskError(f"unknown key '{key}': use a letter, digit, enter, escape, f1-f12...")
    for need, bad, why in BLOCKED:
        if key == bad and need <= set(mods):
            raise DeskError(f"'{text}' is blocked: it {why}")
    return Combo(tuple(mods), key)


# ---- the desk

FL_PROCESSES = {"fl64.exe", "fl.exe", "fl studio.exe", "fl studio"}
MAX_WIDTH = 1568


@dataclass
class Shot:
    origin: tuple[int, int]
    scale: float
    size: tuple[int, int]


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class Desk:
    def __init__(
        self,
        backend: Backend,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        platform: str | None = None,
    ) -> None:
        self.backend, self.sleep, self.clock = backend, sleep, clock
        self.platform = platform or sys.platform
        self.last: Shot | None = None

    # -- windows

    def is_fl(self, win: Win) -> bool:
        extra = {p.strip().lower() for p in os.environ.get("FL_MCP_FL_PROCESS", "").split(",") if p}
        name = win.process.lower()
        if name:
            return name in FL_PROCESSES or name in extra
        return win.title.startswith("FL Studio")

    def find_fl(self) -> Win | None:
        mine = [w for w in self.backend.windows() if self.is_fl(w)]
        if not mine:
            return None
        titled = [w for w in mine if "FL Studio" in w.title]
        pool = titled or mine
        return max(pool, key=lambda w: w.rect[2] * w.rect[3])

    def focus_fl(self) -> Win:
        self._failsafe()
        front = self.backend.foreground()
        if front is not None and self.is_fl(front):
            return front
        main = self.find_fl()
        if main is None:
            raise DeskError("FL Studio is not running: no FL window was found")
        self.backend.focus(main)
        front = self.backend.foreground()
        if front is None or not self.is_fl(front):
            raise DeskError(
                "could not bring FL Studio to the front, so nothing was sent "
                f"(in front: '{front.title if front else 'unknown'}'). Click FL Studio once"
            )
        self.sleep(0.1)
        return front

    def _failsafe(self) -> None:
        if self.backend.cursor() == (0, 0):
            raise DeskError("stopped: the mouse is in the top-left corner (failsafe). Move it away")

    def status(self) -> dict[str, Any]:
        main = self.find_fl()
        front = self.backend.foreground()
        return {
            "backend": self.backend.name,
            "fl": None
            if main is None
            else {"title": main.title, "rect": list(main.rect), "process": main.process},
            "fl_in_front": bool(front and self.is_fl(front)),
            "in_front": front.title if front else None,
            "screen": list(self.backend.screen()),
            "last_shot": None
            if self.last is None
            else {"size": list(self.last.size), "scale": round(self.last.scale, 3)},
        }

    # -- seeing

    def shot(self, full: bool = False, max_width: int = MAX_WIDTH) -> tuple[bytes, dict[str, Any]]:
        """A PNG of all of FL Studio's windows (dialogs too) or, with full, the whole screen."""
        sx, sy, sw, sh = self.backend.screen()
        if full:
            rect, title = (sx, sy, sw, sh), "screen"
        else:
            self.focus_fl()  # nothing else may cover it; refuses when FL is not there
            mine = [w for w in self.backend.windows() if self.is_fl(w) and min(w.rect[2:]) >= 60]
            if not mine:
                raise DeskError("FL Studio has no visible window to capture")
            x0 = max(min(w.rect[0] for w in mine), sx)
            y0 = max(min(w.rect[1] for w in mine), sy)
            x1 = min(max(w.rect[0] + w.rect[2] for w in mine), sx + sw)
            y1 = min(max(w.rect[1] + w.rect[3] for w in mine), sy + sh)
            rect, title = (x0, y0, x1 - x0, y1 - y0), f"{len(mine)} FL windows"
        png, w2, h2 = self.backend.capture(rect, max_width)
        self.last = Shot((rect[0], rect[1]), w2 / rect[2], (w2, h2))
        return png, {"of": title, "size": [w2, h2], "scale": round(self.last.scale, 3)}

    def to_screen(self, x: int, y: int) -> tuple[int, int]:
        if self.last is None:
            raise DeskError("take a screenshot first (action=shot): positions are read off it")
        w, h = self.last.size
        if not (0 <= x < w and 0 <= y < h):
            raise DeskError(f"({x}, {y}) is outside the last screenshot, which is {w}x{h}")
        return self.last.origin[0] + round(x / self.last.scale), self.last.origin[1] + round(
            y / self.last.scale
        )

    # -- acting

    def point(
        self,
        kind: str,
        x: int,
        y: int,
        x2: int | None = None,
        y2: int | None = None,
        amount: int = 0,
    ) -> None:
        self.focus_fl()
        sx, sy = self.to_screen(x, y)
        end: tuple[int | None, int | None] = (None, None)
        if x2 is not None and y2 is not None:
            end = self.to_screen(x2, y2)
        if kind == "drag" and end[0] is None:
            raise DeskError("drag needs to_x and to_y")
        self.backend.mouse(kind, sx, sy, end[0], end[1], amount)

    def press(self, keys: str) -> list[str]:
        combos = [parse_combo(k, self.platform) for k in keys.split(",") if k.strip()]
        if not combos:
            raise DeskError("keys is empty: for example 'ctrl+s' or 'down,down,enter'")
        if len(combos) > 40:
            raise DeskError("at most 40 keys per call")
        self.focus_fl()
        for combo in combos:
            self.backend.key(combo)
            self.sleep(0.05)
        return [("+".join((*c.mods, c.key))) for c in combos]

    def write(self, text: str) -> int:
        if not text:
            raise DeskError("text is empty")
        if len(text) > 400:
            raise DeskError("at most 400 characters per call")
        self.focus_fl()
        self.backend.type(text)
        return len(text)

    def wait(self, ms: int) -> None:
        self.sleep(max(0, min(ms, 10000)) / 1000)

    # -- macros: what people do in FL that the API cannot

    def _keys(self, text: str) -> None:
        self.focus_fl()
        self.backend.key(parse_combo(text, self.platform))

    def _text(self, text: str) -> None:
        self.focus_fl()
        self.backend.type(text)

    def script(self, folder: Path | None = None, timeout: float = 5.0) -> dict[str, Any]:
        """Run the piano roll script again (FL's 'run last script' shortcut) and see if it took."""
        folder = folder or data_dir()
        queued = (_json(folder / "inbox.json") or {}).get("serial")
        before = (_json(folder / "state.json") or {}).get("ts", 0)
        self._keys("mod+alt+y")
        start = self.clock()
        while self.clock() - start < timeout:
            self.sleep(0.25)
            state = _json(folder / "state.json") or {}
            if (queued is not None and state.get("serial") == queued) or (
                queued is None and state.get("ts", 0) > before
            ):
                return {"applied": True, "waited_s": round(self.clock() - start, 1)}
        return {
            "applied": False,
            "note": "the script did not run. In FL's piano roll open Tools > Scripting > "
            "FL MCP once and press Accept (FL then repeats it with this shortcut), or check "
            "the screenshot for a dialog that is waiting",
        }

    def export(self, path: Path, fmt: str = "wav", timeout: float = 900.0) -> dict[str, Any]:
        """File > Export through FL's own dialogs, then wait for the file to be complete."""
        if fmt not in ("wav", "mp3"):
            raise DeskError("export makes wav or mp3: use fl_audio render for other formats")
        path = path.expanduser().resolve()
        if path.suffix.lower() != f".{fmt}":
            path = path.with_suffix(f".{fmt}")
        if path.exists():
            raise DeskError(f"{path} already exists: choose another name")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._keys("mod+r" if fmt == "wav" else "mod+shift+r")
        self.sleep(1.5)
        self._text(str(path))
        self.sleep(0.2)
        self._keys("enter")
        self.sleep(1.5)
        self._keys("enter")  # the render options dialog's Start button
        start, size, stable = self.clock(), -1, 0
        while self.clock() - start < timeout:
            self.sleep(1.0)
            if path.is_file():
                now = path.stat().st_size
                stable = stable + 1 if now == size and now > 0 else 0
                size = now
                if stable >= 2:
                    return {"audio": str(path), "fmt": fmt, "seconds": round(self.clock() - start)}
        raise DeskError(
            f"no finished {fmt} appeared at {path} within {timeout:.0f} s: look at the "
            "screenshot, a dialog may be waiting (FL's export dialogs differ between versions)"
        )

    def open_project(self, path: Path) -> dict[str, Any]:
        path = path.expanduser().resolve()
        if path.suffix.lower() != ".flp" or not path.is_file():
            raise DeskError(f"not an existing .flp project: {path}")
        self._keys("mod+o")
        self.sleep(1.2)
        self._text(str(path))
        self.sleep(0.2)
        self._keys("enter")
        self.sleep(2.5)
        return {
            "opened": str(path),
            "note": "if FL asks to save the current project, answer it in the screenshot",
        }

    def save(self) -> dict[str, Any]:
        self._keys("mod+s")
        self.sleep(1.0)
        return {"saved": True, "note": "an untitled project opens a Save As dialog: see the shot"}


# ---- shared instance

_desk: Desk | None = None


def make_backend() -> Backend:
    if sys.platform == "win32":
        from fl_mcp.desk_win import WindowsBackend

        return WindowsBackend()
    if sys.platform == "darwin":
        from fl_mcp.desk_mac import MacBackend

        return MacBackend()
    raise DeskError("desktop control supports Windows and macOS, where FL Studio runs")


def get() -> Desk:
    global _desk
    if _desk is None:
        _desk = Desk(make_backend())
    return _desk


def use(desk: Desk | None) -> None:
    """Replace (or with None, drop) the shared desk: tests, or after a backend error."""
    global _desk
    _desk = desk
