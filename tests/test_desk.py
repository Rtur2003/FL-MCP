"""The desktop tier: key parsing, safety, screenshots, macros, and the Windows struct layout."""

import ctypes
import json
import struct
import sys
import zlib

import pytest

from fl_mcp import desk, imaging
from fl_mcp.desk import Combo, Desk, DeskError
from tests.fakedesk import BROWSER, DIALOG, FL, FakeDesk


class Time:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(**kw):
    clock = Time()
    fake = FakeDesk(**kw)
    return Desk(fake, clock.sleep, clock.now, platform="win32"), fake, clock


# ---- keys


@pytest.mark.parametrize(
    "text,expect",
    [
        ("ctrl+alt+y", Combo(("ctrl", "alt"), "y")),
        ("Control+S", Combo(("ctrl",), "s")),
        ("mod+shift+r", Combo(("ctrl", "shift"), "r")),
        ("enter", Combo((), "enter")),
        ("Esc", Combo((), "escape")),
        ("f9", Combo((), "f9")),
        ("ctrl+plus", Combo(("ctrl",), "+")),
        ("shift+pgdn", Combo(("shift",), "pagedown")),
        ("1", Combo((), "1")),
    ],
)
def test_key_combos_are_understood(text, expect):
    assert desk.parse_combo(text, "win32") == expect


def test_mod_is_cmd_on_a_mac_and_every_modifier_follows():
    assert desk.parse_combo("mod+alt+y", "darwin") == Combo(("cmd", "alt"), "y")
    assert desk.parse_combo("ctrl+s", "darwin") == Combo(("ctrl",), "s")


@pytest.mark.parametrize(
    "text,message",
    [
        ("", "is not a key"),
        ("ctrl+", "is not a key"),
        ("+s", "is not a key"),
        ("ctrl+ctrl+s", "repeats"),
        ("hyper+s", "unknown modifier"),
        ("super+r", "not allowed"),
        ("cmd+s", "not allowed"),
        ("ctrl+nosuchkey", "unknown key"),
        ("alt+f4", "blocked"),
        ("ctrl+alt+delete", "blocked"),
        ("ctrl+mod", "'mod' is a modifier"),
    ],
)
def test_bad_or_dangerous_keys_are_refused(text, message):
    with pytest.raises(DeskError, match=message):
        desk.parse_combo(text, "win32")


def test_quitting_the_app_is_blocked_on_a_mac():
    with pytest.raises(DeskError, match="quits"):
        desk.parse_combo("cmd+q", "darwin")


# ---- the switch


def test_desktop_control_is_off_until_switched_on(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    assert not desk.enabled()
    desk.set_enabled(True)
    assert desk.enabled()
    monkeypatch.setenv("FL_MCP_DESKTOP", "0")
    assert not desk.enabled()
    monkeypatch.setenv("FL_MCP_DESKTOP", "yes")
    assert desk.enabled()
    monkeypatch.delenv("FL_MCP_DESKTOP")
    desk.set_enabled(False)
    assert not desk.enabled()


# ---- safety


def test_input_is_refused_unless_fl_studio_ends_up_in_front():
    d, fake, _ = make(can_focus=False)
    with pytest.raises(DeskError, match="could not bring FL Studio to the front.*Docs - Browser"):
        d.press("ctrl+s")
    assert fake.log == []


def test_fl_is_brought_forward_first_and_only_then_keys_are_sent():
    d, fake, _ = make()
    assert d.press("ctrl+s, enter") == ["ctrl+s", "enter"]
    assert fake.front is FL
    assert fake.log == [("key", "ctrl+s"), ("key", "enter")]


def test_a_missing_fl_is_reported_not_guessed():
    d, fake, _ = make(windows=[BROWSER])
    with pytest.raises(DeskError, match="FL Studio is not running"):
        d.press("space")


def test_dialogs_of_fl_count_as_fl_but_other_programs_do_not():
    d, fake, _ = make(front=DIALOG, windows=[BROWSER, FL, DIALOG])
    d.press("enter")
    assert fake.log == [("key", "enter")]
    assert d.is_fl(DIALOG) and not d.is_fl(BROWSER)


def test_the_corner_failsafe_stops_everything():
    d, fake, _ = make(front=FL)
    fake.cursor_at = (0, 0)
    with pytest.raises(DeskError, match="top-left corner"):
        d.press("space")
    with pytest.raises(DeskError, match="top-left corner"):
        d.write("hello")
    assert fake.log == []


def test_key_and_text_limits():
    d, _, _ = make(front=FL)
    with pytest.raises(DeskError, match="keys is empty"):
        d.press(" , ")
    with pytest.raises(DeskError, match="at most 40"):
        d.press(",".join(["a"] * 41))
    with pytest.raises(DeskError, match="text is empty"):
        d.write("")
    with pytest.raises(DeskError, match="at most 400"):
        d.write("x" * 401)


# ---- seeing and pointing


def test_a_shot_covers_all_of_fl_and_clicks_are_read_off_it():
    d, fake, _ = make(front=FL, windows=[BROWSER, FL, DIALOG])
    png, meta = d.shot()
    assert (
        png.startswith(b"\x89PNG") and meta["size"] == [1568, 882] and meta["of"] == "2 FL windows"
    )
    assert fake.captured == [(100, 50, 1600, 900)]
    d.point("click", 784, 441)  # the middle of the picture
    _, kind, x, y, *_ = fake.log[-1]
    assert (kind, x, y) == ("click", 900, 500)  # the middle of the window on the real screen


def test_a_shot_is_scaled_back_to_screen_pixels_for_clicks():
    d, fake, _ = make(front=FL)
    d.shot()
    assert d.last.scale == pytest.approx(1568 / 1600)
    d.point("double", 0, 0)
    assert fake.log[-1][:4] == ("mouse", "double", 100, 50)
    d.point("drag", 100, 100, 200, 300)
    _, kind, x, y, x2, y2, _ = fake.log[-1]
    assert (x, y) == (100 + round(100 / d.last.scale), 50 + round(100 / d.last.scale))
    assert (x2, y2) == (100 + round(200 / d.last.scale), 50 + round(300 / d.last.scale))


def test_clicking_before_any_shot_or_off_the_picture_is_refused():
    d, fake, _ = make(front=FL)
    with pytest.raises(DeskError, match="take a screenshot first"):
        d.point("click", 5, 5)
    d.shot()
    with pytest.raises(DeskError, match="outside the last screenshot"):
        d.point("click", 5000, 5)
    with pytest.raises(DeskError, match="drag needs"):
        d.point("drag", 5, 5)
    assert fake.log == []


def test_the_whole_screen_can_be_captured_too():
    d, fake, _ = make(front=FL)
    _, meta = d.shot(full=True)
    assert meta["of"] == "screen" and fake.captured == [(0, 0, 1920, 1080)]


def test_status_says_where_fl_is_and_whether_it_is_in_front():
    d, fake, _ = make()
    first = d.status()
    assert first["fl"]["title"] == "Song - FL Studio 21" and first["fl_in_front"] is False
    assert first["in_front"] == "Docs - Browser" and first["last_shot"] is None
    fake.front = FL
    d.shot()
    second = d.status()
    assert second["fl_in_front"] is True and second["last_shot"]["size"] == [1568, 882]


# ---- macros


def test_the_script_macro_confirms_that_the_notes_arrived(tmp_path):
    d, fake, _ = make(front=FL)
    (tmp_path / "inbox.json").write_text(json.dumps({"serial": 77, "notes": []}))

    def fl_runs_the_script(combo):
        if combo == Combo(("ctrl", "alt"), "y"):
            (tmp_path / "state.json").write_text(json.dumps({"ts": 1, "serial": 77}))

    fake.on_key = fl_runs_the_script
    out = d.script(tmp_path)
    assert out["applied"] is True and fake.log == [("key", "ctrl+alt+y")]


def test_the_script_macro_says_what_to_do_when_nothing_happened(tmp_path):
    d, fake, _ = make(front=FL)
    (tmp_path / "inbox.json").write_text(json.dumps({"serial": 5, "notes": []}))
    out = d.script(tmp_path, timeout=2)
    assert out["applied"] is False and "Tools > Scripting" in out["note"]


def test_export_types_the_path_and_waits_for_a_finished_file(tmp_path):
    d, fake, clock = make(front=FL)
    target = tmp_path / "out" / "mix.wav"
    presses = []

    def fl_renders(combo):
        presses.append(combo.key)
        if presses.count("enter") == 2:  # the Start button
            target.write_bytes(b"x" * 100)

    fake.on_key = fl_renders
    out = d.export(target, "wav")
    assert out["audio"] == str(target)
    assert fake.log[0] == ("key", "ctrl+r") and fake.log[1] == ("type", str(target))
    assert [e for e in fake.log if e[0] == "key"][1:] == [("key", "enter"), ("key", "enter")]


def test_export_to_mp3_uses_the_mp3_shortcut_and_never_overwrites(tmp_path):
    d, fake, clock = make(front=FL)
    target = tmp_path / "mix.mp3"

    def fl_renders(combo):
        if combo.key == "enter":
            target.write_bytes(b"x" * 10)

    fake.on_key = fl_renders
    d.export(tmp_path / "mix", "mp3")
    assert fake.log[0] == ("key", "ctrl+shift+r") and target.exists()
    with pytest.raises(DeskError, match="already exists"):
        d.export(target, "mp3")
    with pytest.raises(DeskError, match="wav or mp3"):
        d.export(tmp_path / "x.ogg", "ogg")


def test_a_stuck_export_is_explained_with_the_screenshot_hint(tmp_path):
    d, _, clock = make(front=FL)
    with pytest.raises(DeskError, match="no finished wav appeared.*dialog may be waiting"):
        d.export(tmp_path / "never.wav", "wav", timeout=5)


def test_open_and_save(tmp_path):
    d, fake, _ = make(front=FL)
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")
    out = d.open_project(flp)
    assert out["opened"] == str(flp)
    assert fake.log == [("key", "ctrl+o"), ("key", "ctrl+a"), ("type", str(flp)), ("key", "enter")]
    with pytest.raises(DeskError, match="not an existing .flp"):
        d.open_project(tmp_path / "nope.flp")
    fake.log.clear()
    d.save()
    assert fake.log == [("key", "ctrl+s")]


def test_the_foreground_is_checked_before_every_macro_step(tmp_path):
    d, fake, _ = make(front=FL)
    flp = tmp_path / "song.flp"
    flp.write_bytes(b"FLhd")

    def steals_focus(combo):
        fake.front, fake.can_focus = BROWSER, False  # something else grabs the screen

    fake.on_key = steals_focus
    with pytest.raises(DeskError, match="could not bring FL Studio to the front"):
        d.open_project(flp)
    assert fake.log == [("key", "ctrl+o")]  # the path was never typed into the other window


# ---- images


def png_chunks(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        (size,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + size]
        (crc,) = struct.unpack(">I", data[pos + 8 + size : pos + 12 + size])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        chunks[kind] = body
        pos += 12 + size
    return chunks


def test_png_encoding_is_valid_and_lossless():
    rgb = bytes([(i * 7) % 256 for i in range(5 * 3 * 3)])
    chunks = png_chunks(imaging.encode_png(rgb, 5, 3))
    assert struct.unpack(">IIBBBBB", chunks[b"IHDR"]) == (5, 3, 8, 2, 0, 0, 0)
    raw = zlib.decompress(chunks[b"IDAT"])
    rows = [raw[i * 16 : (i + 1) * 16] for i in range(3)]
    assert all(r[0] == 0 for r in rows) and b"".join(r[1:] for r in rows) == rgb
    with pytest.raises(ValueError, match="does not match"):
        imaging.encode_png(b"abc", 5, 3)


def test_windows_bitmaps_swap_blue_and_red():
    bgra = bytes([1, 2, 3, 255, 10, 20, 30, 255])
    assert imaging.bgra_to_rgb(bgra, 2, 1) == bytes([3, 2, 1, 30, 20, 10])
    with pytest.raises(ValueError):
        imaging.bgra_to_rgb(bgra, 3, 1)


def test_shrinking_keeps_the_picture_and_never_grows_it():
    w, h = 8, 4
    rgb = b"".join(bytes([x * 30, y * 60, 9]) for y in range(h) for x in range(w))
    same, w1, h1 = imaging.resize(rgb, w, h, 20)
    assert (same, w1, h1) == (rgb, w, h)
    small, w2, h2 = imaging.resize(rgb, w, h, 4)
    assert (w2, h2) == (4, 2) and len(small) == 4 * 2 * 3
    assert small[0:3] == bytes([30, 60, 9])  # the pixel at the middle of the top-left cell


# ---- the Windows layout (checked on any 64-bit system, with fixed-width field types)


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit layout")
def test_windows_structures_have_the_sizes_user32_expects():
    from fl_mcp import desk_win as w

    assert ctypes.sizeof(w.INPUT) == 40 and ctypes.sizeof(w.MOUSEINPUT) == 32
    assert ctypes.sizeof(w.KEYBDINPUT) == 24 and ctypes.sizeof(w.RECT) == 16
    assert ctypes.sizeof(w.BITMAPINFOHEADER) == 40 and ctypes.sizeof(w.BITMAPINFO) == 52
    event = w.INPUT(type=w.INPUT_KEYBOARD, ki=w.KEYBDINPUT(0x59, 0, w.KEYEVENTF_KEYUP, 0, 0))
    assert event.ki.wVk == 0x59 and event.type == 1


def test_the_windows_backend_refuses_to_start_elsewhere():
    from fl_mcp import desk_win

    if sys.platform != "win32":
        with pytest.raises(DeskError, match="only runs on Windows"):
            desk_win.WindowsBackend()


def test_key_codes_cover_everything_a_combo_can_name():
    from fl_mcp import desk_win

    for name in desk.NAMED | {"ctrl", "alt", "shift"}:
        assert name in desk_win.VK, name
    assert desk_win.VK["f1"] == 0x70 and desk_win.VK["f12"] == 0x7B


class StealthFakeDesk(FakeDesk):
    def __init__(self, *, idle_s=None, bg_png=None, bg_post=False, **kw):
        super().__init__(**kw)
        self.idle_vals = list(idle_s) if isinstance(idle_s, list) else [idle_s]
        self.bg_png = bg_png
        self.bg_post = bg_post
        self.posted = []
        self.restored = []

    def idle_seconds(self):
        if len(self.idle_vals) > 1:
            return self.idle_vals.pop(0)
        return self.idle_vals[0]

    def capture_window(self, win, max_width, *, allow_unminimize=False):
        if self.bg_png is None:
            return None
        return self.bg_png, min(win.rect[2], max_width), 400

    def post_key(self, win, combo):
        self.posted.append((win.handle, "+".join((*combo.mods, combo.key))))
        if self.on_key and self.bg_post:
            self.on_key(combo)
        return self.bg_post

    def restore_window(self, win):
        self.restored.append(win)
        self.front = win
        return True


def test_desktop_modes_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    monkeypatch.delenv("FL_MCP_DESK_MODE", raising=False)
    assert desk.mode() == "off" and desk.enabled() is False

    desk.set_enabled(True)
    assert desk.mode() == "polite" and desk.enabled() is True
    for m in ("stealth", "foreground", "polite"):
        desk.set_enabled(m)
        assert desk.mode() == m and desk.enabled() is True
    desk.set_enabled("off")
    assert desk.mode() == "off" and desk.enabled() is False

    with pytest.raises(DeskError, match="desktop mode must be"):
        desk.set_enabled("invalid")

    monkeypatch.setenv("FL_MCP_DESKTOP", "stealth")
    assert desk.mode() == "stealth"
    monkeypatch.setenv("FL_MCP_DESKTOP", "0")
    assert desk.mode() == "off"


def test_stealth_mode_never_steals_focus_and_supports_background_shot_and_script(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "inbox.json").write_text(json.dumps({"serial": 42}))
    fake = StealthFakeDesk(
        front=BROWSER,
        bg_png=imaging.encode_png(bytes([1, 2, 3]) * (200 * 400), 200, 400),
        bg_post=True,
    )
    fake.on_key = lambda _c: (notes / "state.json").write_text(json.dumps({"serial": 42, "ts": 1}))
    clock = Time()
    d = Desk(fake, clock.sleep, clock.now, platform="win32", mode="stealth")

    with pytest.raises(DeskError, match="stealth.*focus steal"):
        d.focus_fl()
    assert fake.front == BROWSER

    _png, meta = d.shot()
    assert meta["background"] is True and fake.front == BROWSER

    res = d.script(folder=notes)
    assert res["applied"] is True and res["background"] is True
    assert fake.front == BROWSER and fake.posted == [(FL.handle, "ctrl+alt+y")]

    fake.bg_png = None
    with pytest.raises(DeskError, match="cannot be captured in the background"):
        d.shot()


def test_polite_mode_waits_for_idle_and_restores_previous_window(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "inbox.json").write_text(json.dumps({"serial": 7}))
    fake = StealthFakeDesk(front=BROWSER, idle_s=[0.1, 0.2, 2.0], bg_post=False)
    fake.on_key = lambda _c: (notes / "state.json").write_text(json.dumps({"serial": 7, "ts": 2}))
    clock = Time()
    d = Desk(fake, clock.sleep, clock.now, platform="win32", mode="polite")

    res = d.script(folder=notes)
    assert res["applied"] is True and res["background"] is False
    assert fake.restored == [BROWSER] and fake.front == BROWSER

    busy_fake = StealthFakeDesk(front=BROWSER, idle_s=[0.1])
    busy_clock = Time()
    busy_d = Desk(busy_fake, busy_clock.sleep, busy_clock.now, platform="win32", mode="polite")
    with pytest.raises(DeskError, match="user is actively typing"):
        busy_d.focus_fl()


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit layout")
def test_windows_v030_structures_match_win32_abi():
    from fl_mcp import desk_win as w

    assert ctypes.sizeof(w.LASTINPUTINFO) == 8
    assert ctypes.sizeof(w.GUITHREADINFO) == 72


def test_desk_script_arms_first_run_fallback_when_shortcut_inactive(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "inbox.json").write_text(json.dumps({"serial": 99}))

    class ArmableFake(StealthFakeDesk):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.armed_with = []

        def arm_piano_roll_script(self, script_name="FL MCP", win=None):
            self.armed_with.append((script_name, win.handle if win else None))
            (notes / "state.json").write_text(json.dumps({"serial": 99, "ts": 5}))
            return True

    fake = ArmableFake(front=BROWSER, idle_s=2.0, bg_post=False)
    clock = Time()
    d = Desk(fake, clock.sleep, clock.now, platform="win32", mode="polite")
    res = d.script(folder=notes, timeout=0.6)
    assert res["applied"] is True and res.get("armed") is True
    assert fake.armed_with == [("FL MCP", FL.handle)]
