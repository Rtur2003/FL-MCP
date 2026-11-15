"""Loading a plugin the way a person does, and knowing whether it worked."""

import json

import pytest

from fl_mcp import selftest, uiload
from fl_mcp.bridge import FLError
from tests.test_server import run

pytestmark = pytest.mark.anyio


def slots(fl, track):
    return dict(fl.fake.tracks[track]["slots"])


def test_an_effect_goes_to_the_first_free_slot_of_the_chosen_track(fl):
    got = uiload.load(fl.call, "Fruity Limiter", track=3)
    assert got == {
        "loaded": [{"plugin": "Fruity Limiter", "at": "mixer", "index": 3, "slot": 0}],
        "via": "Fruity Limiter",
    }
    assert slots(fl, 3) == {0: "Fruity Limiter"}
    assert fl.fake.cur_track == 3
    again = uiload.load(fl.call, "Fruity Limiter", track=3)
    assert again["loaded"][0]["slot"] == 1 and len(slots(fl, 3)) == 2


def test_an_instrument_becomes_a_new_channel(fl):
    got = uiload.load(fl.call, "3x Osc")
    assert got["loaded"] == [{"plugin": "3x Osc", "at": "channel", "index": 3, "slot": None}]
    assert fl.fake.chans[3]["plugin"] == "3x Osc"


def test_the_search_starts_from_the_top_of_the_browser_not_from_where_it_was(fl):
    fl.fake.browser_at = 6  # past everything
    assert uiload.load(fl.call, "sytrus")["loaded"][0]["plugin"] == "Sytrus"


def test_a_folder_with_the_same_name_does_not_stop_it(fl):
    fl.fake.browser.insert(1, ["Fruity Limiter presets", -100])  # matched first, loads nothing
    got = uiload.load(fl.call, "Fruity Limiter", track=2)
    assert got["via"] == "Fruity Limiter" and slots(fl, 2) == {0: "Fruity Limiter"}
    assert ("select", "Fruity Limiter presets") in fl.fake.browser_log


def test_something_that_loads_nothing_says_where_the_browser_stopped(fl):
    with pytest.raises(ValueError, match="stopped at 'Plugin database' but nothing was added"):
        uiload.load(fl.call, "Plugin database")


def test_an_unknown_name_is_reported_as_missing(fl):
    with pytest.raises(ValueError, match="'No Such Plugin' is not in FL's browser"):
        uiload.load(fl.call, "No Such Plugin", track=1)
    with pytest.raises(ValueError, match="needs query"):
        uiload.load(fl.call, "  ")


def test_a_long_tree_is_walked_in_pieces(fl):
    fl.fake.browser[1:1] = [[f"Folder {i}", -100] for i in range(30)]
    got = uiload.load(fl.call, "Fruity Limiter", track=1, steps=8)
    assert got["loaded"][0]["plugin"] == "Fruity Limiter"


def test_the_script_can_go_to_the_top(fl):
    fl.fake.browser_at = 5
    out = fl.call({"o": "ui", "a": "browser", "b": "top"})
    assert out == {"item": ["Plugin database", -100], "done": True} and fl.fake.browser_at == 0
    short = fl.call({"o": "ui", "a": "browser", "b": "top", "n": 2})
    assert short["done"] is False or fl.fake.browser_at == 0


async def test_the_tool_loads_and_reports(fl, monkeypatch, tmp_path):
    from fl_mcp import server

    monkeypatch.setattr(server, "bridge", fl.bridge)
    out = await run("fl_ui", {"action": "load", "query": "Fruity Limiter", "index": 4})
    assert out["loaded"][0] == {"plugin": "Fruity Limiter", "at": "mixer", "index": 4, "slot": 0}
    error = await run("fl_ui", {"action": "load", "query": "Nothing Here"}, expect_error=True)
    assert "not in FL's browser" in error
    error = await run("fl_ui", {"action": "load"}, expect_error=True)
    assert "needs query" in error


def test_deep_selftest_loads_then_undoes(fl, monkeypatch, tmp_path):
    for var, val in (("FL_MCP_DIR", "d"), ("HOME", "h"), ("FL_MCP_FL_DIR", "x")):
        monkeypatch.setenv(var, str(tmp_path / val))
    before = {i: dict(t["slots"]) for i, t in enumerate(fl.fake.tracks)}
    report = selftest.run(fl.call, deep="Fruity Limiter")
    check = next(c for c in report["checks"] if c["check"] == "browser load + undo")
    assert check["status"] == "ok" and "then undone" in check["detail"], check
    assert {i: dict(t["slots"]) for i, t in enumerate(fl.fake.tracks)} == before


def test_deep_selftest_names_a_leftover_when_undo_does_not_work(fl, monkeypatch, tmp_path):
    for var, val in (("FL_MCP_DIR", "d"), ("HOME", "h"), ("FL_MCP_FL_DIR", "x")):
        monkeypatch.setenv(var, str(tmp_path / val))

    def no_undo(req):
        if req["o"] == "tr" and req["a"] == "undo":
            return {}
        return fl.call(req)

    report = selftest.run(no_undo, deep="Fruity Limiter")
    check = next(c for c in report["checks"] if c["check"] == "browser load + undo")
    assert check["status"] == "fail" and "delete it by hand" in check["detail"]


def test_deep_is_off_unless_asked_for(fl, monkeypatch, tmp_path):
    for var, val in (("FL_MCP_DIR", "d"), ("HOME", "h"), ("FL_MCP_FL_DIR", "x")):
        monkeypatch.setenv(var, str(tmp_path / val))
    report = selftest.run(fl.call)
    assert "browser load + undo" not in [c["check"] for c in report["checks"]]
    assert json.dumps(report)  # serialisable
    assert FLError  # imported for the dead-link tests elsewhere


def test_the_menu_fl_opens_is_confirmed_from_outside_and_never_navigated(fl):
    fl.fake.popup_mode = True
    sent = []

    def confirm():
        sent.append("confirm")
        return fl.fake.confirm_menu()

    got = uiload.load(
        fl.call, "Fruity Limiter", track=3, confirm=confirm, dismiss=fl.fake.dismiss_menu
    )
    assert got["loaded"] == [{"plugin": "Fruity Limiter", "at": "mixer", "index": 3, "slot": 0}]
    assert sent == ["confirm"] and not fl.fake.in_popup
    assert "down" not in fl.fake.keys and "enter" not in fl.fake.keys


def test_a_menu_that_loads_nothing_is_closed_and_reported(fl):
    fl.fake.popup_mode = True
    closed = []

    def dismiss():
        closed.append(1)
        return fl.fake.dismiss_menu()

    with pytest.raises(ValueError, match="nothing was added"):
        uiload.load(fl.call, "Fruity Limiter", track=3, confirm=lambda: False, dismiss=dismiss)
    assert not fl.fake.in_popup and closed


def test_menu_keys_go_to_the_menu_window_and_only_return_or_escape(monkeypatch):
    from fl_mcp import popupkeys

    posted = []

    class User32:
        def FindWindowW(self, cls, title):  # noqa: N802
            assert cls == popupkeys.CLASS
            return 0 if len(posted) >= 2 else 77

        def PostMessageW(self, hwnd, msg, wparam, lparam):  # noqa: N802
            posted.append((hwnd, msg, wparam))

    monkeypatch.setattr(popupkeys, "_user32", lambda: User32())
    monkeypatch.setattr(popupkeys, "window", lambda: 0 if len(posted) >= 2 else 77)
    assert popupkeys.confirm() is True
    assert posted == [(77, popupkeys.WM_KEYDOWN, 0x0D), (77, popupkeys.WM_KEYUP, 0x0D)]
    assert {w for _, _, w in posted} == {popupkeys.VK_RETURN}  # no arrow key, ever


def test_without_a_menu_window_nothing_is_sent(monkeypatch):
    from fl_mcp import popupkeys

    monkeypatch.setattr(popupkeys, "window", lambda: 0)
    assert popupkeys.confirm() is False
    assert popupkeys.dismiss() is True


def test_popup_confirm_and_dismiss_avoid_cursor_stealing_by_default(monkeypatch):
    from fl_mcp import popupkeys

    posted = []
    open_menu = [True]

    class Rect:
        def __init__(self):
            self.left, self.top, self.right, self.bottom = 100, 200, 320, 420

    class User32:
        def FindWindowW(self, cls, title):  # noqa: N802
            return 88 if open_menu[0] else 0

        def GetWindowRect(self, hwnd, ptr):  # noqa: N802
            r = ptr._obj
            r.left, r.top, r.right, r.bottom = 100, 200, 320, 420
            return 1

        def PostMessageW(self, hwnd, msg, wparam, lparam):  # noqa: N802
            posted.append((hwnd, msg, wparam, lparam))
            if msg == popupkeys.WM_LBUTTONUP:
                open_menu[0] = False

        def SetCursorPos(self, x, y):  # noqa: N802
            raise AssertionError("cursor must not move without allow_cursor_fallback")

        def mouse_event(self, *args):
            raise AssertionError("physical mouse_event must not run by default")

    monkeypatch.setattr(popupkeys, "_user32", lambda: User32())
    monkeypatch.setattr(popupkeys.time, "sleep", lambda _s: None)
    assert popupkeys.confirm() is True
    assert any(m == popupkeys.WM_LBUTTONDOWN for _, m, _, _ in posted)
    assert any(m == popupkeys.WM_LBUTTONUP for _, m, _, _ in posted)

    posted.clear()
    open_menu[0] = True

    class DismissU32(User32):
        def PostMessageW(self, hwnd, msg, wparam, lparam):  # noqa: N802
            posted.append((hwnd, msg, wparam, lparam))
            if msg == popupkeys.WM_CANCELMODE:
                open_menu[0] = False

    monkeypatch.setattr(popupkeys, "_user32", lambda: DismissU32())
    assert popupkeys.dismiss() is True
    assert (88, popupkeys.WM_CANCELMODE, 0, 0) in posted


def test_popup_cursor_fallback_restores_original_cursor(monkeypatch):
    from fl_mcp import popupkeys

    cursor = [640, 360]
    moves = []
    open_menu = [True]

    class User32:
        def FindWindowW(self, cls, title):  # noqa: N802
            return 99 if open_menu[0] else 0

        def GetWindowRect(self, hwnd, ptr):  # noqa: N802
            r = ptr._obj
            r.left, r.top, r.right, r.bottom = 100, 200, 300, 400
            return 1

        def PostMessageW(self, hwnd, msg, wparam, lparam):  # noqa: N802
            return 1

        def GetCursorPos(self, ptr):  # noqa: N802
            p = ptr._obj
            p.x, p.y = cursor[0], cursor[1]
            return 1

        def SetCursorPos(self, x, y):  # noqa: N802
            moves.append((x, y))
            cursor[0], cursor[1] = x, y

        def mouse_event(self, flags, *args):
            if flags == popupkeys.MOUSEEVENTF_LEFTUP:
                open_menu[0] = False

    monkeypatch.setattr(popupkeys, "_user32", lambda: User32())
    monkeypatch.setattr(popupkeys.time, "sleep", lambda _s: None)
    assert popupkeys.confirm(allow_cursor_fallback=True) is True
    assert len(moves) == 2 and moves[-1] == (640, 360)


def test_uiload_skips_focus_fl_unless_mode_is_foreground(fl, tmp_path, monkeypatch):
    from fl_mcp import desk

    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path))
    focus_calls = []

    class SpyDesk:
        def focus_fl(self):
            focus_calls.append(1)

    monkeypatch.setattr(desk, "get", lambda: SpyDesk())

    desk.set_enabled("polite")
    fl.fake.popup_mode = True
    fallback_flags = []

    def confirm_spy(allow_cursor_fallback=False):
        fallback_flags.append(allow_cursor_fallback)
        return fl.fake.confirm_menu()

    uiload.load(fl.call, "Fruity Limiter", track=3, confirm=confirm_spy, dismiss=lambda _=False: True)
    assert focus_calls == []
    assert fallback_flags == [False]

    desk.set_enabled("foreground")
    fl.fake.popup_mode = True
    fallback_flags.clear()
    uiload.load(fl.call, "Fruity Limiter", track=3, confirm=confirm_spy, dismiss=lambda _=False: True)
    assert focus_calls == [1]
    assert fallback_flags == [True]
