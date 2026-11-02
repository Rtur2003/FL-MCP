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
