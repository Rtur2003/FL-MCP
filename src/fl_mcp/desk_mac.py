"""macOS desktop backend: System Events (osascript) and screencapture; cliclick adds the mouse.

macOS asks once for permission: System Settings > Privacy & Security > Accessibility and Screen
Recording, for the app that runs the server (Claude, or the terminal)."""

import ctypes
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from fl_mcp.desk import Backend, Combo, DeskError, Win

APP = "FL Studio"
KEYCODES = {
    "enter": 36, "escape": 53, "tab": 48, "space": 49, "backspace": 51, "delete": 117,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "left": 123, "right": 124,
    "down": 125, "up": 126, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}  # fmt: skip
MODS = {"ctrl": "control down", "alt": "option down", "shift": "shift down", "cmd": "command down"}
LIST_WINDOWS = f"""
tell application "System Events"
  if not (exists process "{APP}") then return ""
  tell process "{APP}"
    set out to ""
    repeat with w in windows
      set p to position of w
      set s to size of w
      set out to out & (name of w) & "|" & (item 1 of p) & "|" & (item 2 of p) & "|" & ¬
        (item 1 of s) & "|" & (item 2 of s) & linefeed
    end repeat
    return out
  end tell
end tell
"""


def _run(cmd: list[str], timeout: float = 15.0) -> str:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeskError(f"{cmd[0]} failed: {exc}") from None
    if done.returncode != 0:
        err = done.stderr.strip()
        allow = " (allow Accessibility and Screen Recording in System Settings > Privacy)"
        raise DeskError(
            f"{cmd[0]}: {err}{allow if 'not allowed' in err or 'assistive' in err else ''}"
        )
    return done.stdout.strip()


def _osa(script: str) -> str:
    return _run(["osascript", "-e", script])


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MacBackend(Backend):
    name = "macos"

    def windows(self) -> list[Win]:
        found = []
        for i, line in enumerate(_osa(LIST_WINDOWS).splitlines()):
            parts = line.split("|")
            if len(parts) != 5:
                continue
            try:
                x, y, w, h = (int(float(p)) for p in parts[1:])
            except ValueError:
                continue
            found.append(Win(i + 1, parts[0], (x, y, w, h), APP))
        return found

    def foreground(self) -> Win | None:
        name = _osa(
            'tell application "System Events" to get name of first application process '
            "whose frontmost is true"
        )
        return Win(0, name, (0, 0, 0, 0), name) if name else None

    def focus(self, win: Win) -> bool:
        _osa(f'tell application "{APP}" to activate')
        return True

    def restore_window(self, win: Win) -> bool:
        if not win or not win.process:
            return False
        try:
            _osa(f'tell application {_quote(win.process)} to activate')
            return True
        except DeskError:
            return False

    def idle_seconds(self) -> float | None:
        try:
            cg = ctypes.CDLL(
                "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
            )
            fn = cg.CGEventSourceSecondsSinceLastEventType
            fn.argtypes = [ctypes.c_int32, ctypes.c_uint32]
            fn.restype = ctypes.c_double
            val = float(fn(1, 0xFFFFFFFF))
            return round(val, 2) if val >= 0 else None
        except Exception:
            return None

    def cursor(self) -> tuple[int, int]:
        if shutil.which("cliclick"):
            try:
                x, y = _run(["cliclick", "p:."]).split(",")
                return int(x), int(y)
            except ValueError:
                pass
        return 1, 1  # no way to know: the corner failsafe is off

    def screen(self) -> tuple[int, int, int, int]:
        text = _osa('tell application "Finder" to get bounds of window of desktop')
        x0, y0, x1, y1 = (int(p) for p in text.replace(" ", "").split(","))
        return x0, y0, x1 - x0, y1 - y0

    def capture(self, rect: tuple[int, int, int, int], max_width: int) -> tuple[bytes, int, int]:
        x, y, w, h = rect
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "shot.png"
            _run(["screencapture", "-x", "-t", "png", "-R", f"{x},{y},{w},{h}", str(png)])
            _run(["sips", "-Z", str(max_width), str(png)])  # never grows an image
            data = png.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        return data, width, height

    def key(self, combo: Combo) -> None:
        using = ", ".join(MODS[m] for m in combo.mods)
        tail = f" using {{{using}}}" if using else ""
        if combo.key in KEYCODES:
            action = f"key code {KEYCODES[combo.key]}"
        else:
            action = f"keystroke {_quote(combo.key)}"
        _osa(f'tell application "System Events" to {action}{tail}')

    def type(self, text: str) -> None:
        for line_no, line in enumerate(text.split("\n")):
            if line_no:
                _osa('tell application "System Events" to key code 36')
            if line:
                _osa(f'tell application "System Events" to keystroke {_quote(line)}')

    def mouse(
        self,
        kind: str,
        x: int,
        y: int,
        x2: int | None = None,
        y2: int | None = None,
        amount: int = 0,
    ) -> None:
        if shutil.which("cliclick"):
            steps = {
                "click": [f"c:{x},{y}"],
                "double": [f"dc:{x},{y}"],
                "right": [f"rc:{x},{y}"],
                "move": [f"m:{x},{y}"],
                "drag": [f"dd:{x},{y}", f"du:{x2},{y2}"],
            }
            if kind in steps:
                _run(["cliclick", *steps[kind]])
                return
        elif kind == "click":
            _osa(f'tell application "System Events" to click at {{{x}, {y}}}')
            return
        if kind == "scroll":
            raise DeskError("scrolling is not available on macOS: use the keyboard or the mouse")
        raise DeskError(f"'{kind}' needs cliclick on macOS: brew install cliclick")
