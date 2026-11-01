"""A pretend desktop: windows, a cursor, a key log. Lets the desk logic run without a screen."""

from fl_mcp import imaging
from fl_mcp.desk import Backend, Combo, Win

FL = Win(101, "Song - FL Studio 21", (100, 50, 1600, 900), "FL64.exe")
DIALOG = Win(102, "Save as", (500, 300, 600, 400), "FL64.exe")
BROWSER = Win(201, "Docs - Browser", (0, 0, 1920, 1040), "chrome.exe")


class FakeDesk(Backend):
    name = "fake"

    def __init__(self, front=None, windows=None, can_focus=True):
        self.wins = windows if windows is not None else [BROWSER, FL]
        self.front = front if front is not None else BROWSER
        self.can_focus = can_focus
        self.cursor_at = (500, 400)
        self.log = []
        self.on_key = None  # callback(combo) to play the part of FL
        self.captured = []

    def windows(self):
        return list(self.wins)

    def foreground(self):
        return self.front

    def focus(self, win):
        if self.can_focus:
            self.front = win
        return self.can_focus

    def cursor(self):
        return self.cursor_at

    def screen(self):
        return (0, 0, 1920, 1080)

    def capture(self, rect, max_width):
        self.captured.append(rect)
        x, y, w, h = rect
        w2 = min(w, max_width)
        h2 = max(1, round(h * w2 / w))
        return imaging.encode_png(bytes([30, 40, 50]) * (w2 * h2), w2, h2), w2, h2

    def key(self, combo: Combo):
        self.log.append(("key", "+".join((*combo.mods, combo.key))))
        if self.on_key:
            self.on_key(combo)

    def type(self, text):
        self.log.append(("type", text))

    def mouse(self, kind, x, y, x2=None, y2=None, amount=0):
        self.log.append(("mouse", kind, x, y, x2, y2, amount))
