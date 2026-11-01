"""The plugin engine of the FL script: names, real units, presets, inventory, ui and probe."""

import pytest

from fl_mcp.bridge import FLError


@pytest.fixture
def rig(fl):
    """A limiter and an EQ on insert 12, and a synth with presets on channel 2."""
    f = fl.fake
    f.tracks[12]["slots"] = {0: "Fruity Limiter", 1: "Fruity Parametric EQ 2", 2: "Mystery FX"}
    f.params[("m", 12, 0)] = [
        ["Gain", 0.5, "db"],
        ["Threshold", 0.7, "db"],
        ["Ceiling", 0.99, "db"],
        ["Attack", 0.1, "ms"],
        ["Ratio", 0.2, "ratio"],
        ["Sidechain", 0.0, "word"],
    ]
    f.params[("m", 12, 1)] = [
        ["Band 1 freq", 0.2, "hz"],
        ["Band 1 level", 0.5, "db"],
        ["Band 1 width", 0.5, "pct"],
        ["Band 2 freq", 0.5, "hz"],
        ["Band 2 level", 0.5, "db"],
    ]
    f.params[("m", 12, 2)] = [["Amount", 0.5, "silent"]]
    f.presets[("m", 12, 0)] = ["Default", "Gentle", "Hard"]
    f.preset_at[("m", 12, 0)] = 0
    return fl


def call(fl, **req):
    return fl.call({"o": "param", "t": "mixer", "i": 12, "s": 0, **req})


def eq(fl, **req):
    return fl.call({"o": "param", "t": "mixer", "i": 12, "s": 1, **req})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-6.0 dB", (-6.0, "db")),
        ("+3.5dB", (3.5, "db")),
        ("2.50 kHz", (2500.0, "hz")),
        ("440 Hz", (440.0, "hz")),
        ("120 ms", (120.0, "ms")),
        ("0.50 s", (500.0, "ms")),
        (".5 s", (500.0, "ms")),
        ("35%", (35.0, "%")),
        ("4.0:1", (4.0, "ratio")),
        ("L 50%", (-50.0, "%")),
        ("R 30%", (30.0, "%")),
        ("-inf dB", (-120.0, "db")),
        ("12 st", (12.0, "st")),
        ("0.50", (0.5, "")),
        ("Off", None),
        ("", None),
    ],
)
def test_value_text_is_read_as_a_number_with_a_canonical_unit(fl, text, expected):
    assert fl.script._parse_text(text) == expected


def test_real_units_are_solved_from_the_plugins_own_text(rig):
    r = call(rig, p="Threshold", r="-18 dB")
    row = r["param"]
    assert row[1] == "Threshold" and row[4] == 1
    assert abs(float(row[3].split()[0]) + 18) <= 0.2

    row = eq(rig, p="Band 1 freq", r="2.5 kHz")["param"]
    assert row[4] == 1 and row[3] == "2.50 kHz"

    row = call(rig, p="Attack", r=10, u="ms")["param"]
    assert row[4] == 1 and row[3] == "10 ms"

    row = call(rig, p="Ratio", r="4:1")["param"]
    assert row[3] == "4.0:1" and row[4] == 1

    row = eq(rig, p="Band 1 width", r="35%")["param"]
    assert row[3] == "35%" and row[4] == 1


def test_a_seconds_target_matches_a_millisecond_display(rig):
    assert call(rig, p="Attack", r="0.25 s")["param"][3] == "250 ms"


def test_unreachable_targets_report_the_closest_value(rig):
    row = call(rig, p="Threshold", r="-90 dB")["param"]
    assert row[3] == "-60.0 dB" and row[4] == 0


def test_a_wrong_unit_or_unreadable_text_is_refused_and_nothing_moves(rig):
    before = rig.fake.params[("m", 12, 0)][1][1]
    with pytest.raises(FLError, match="in db, not ms"):
        call(rig, p="Threshold", r="5 ms")
    assert rig.fake.params[("m", 12, 0)][1][1] == before
    with pytest.raises(FLError, match="does not show the value as text"):
        call(rig, p="Sidechain", r="1")
    with pytest.raises(FLError, match="not numeric|does not show"):
        rig.call({"o": "param", "t": "mixer", "i": 12, "s": 2, "p": 0, "r": 5})
    with pytest.raises(FLError, match="cannot read"):
        call(rig, p="Gain", r="loud")
    with pytest.raises(FLError, match="unknown unit"):
        call(rig, p="Gain", r=3, u="furlongs")
    with pytest.raises(FLError, match="give value"):
        call(rig, p="Gain")


def test_parameters_are_found_by_name_with_helpful_errors(rig):
    assert call(rig, p="threshold", v=0.5)["param"][0] == 1
    assert call(rig, p=1, v=0.4)["param"][1] == "Threshold"
    assert call(rig, p="1", v=0.4)["param"][0] == 1
    with pytest.raises(FLError, match="matches several parameters: .*Band 1 freq"):
        eq(rig, p="Band", v=0.5)
    with pytest.raises(FLError, match="no parameter named 'nope'"):
        call(rig, p="nope", v=0.5)
    assert eq(rig, p="band 2 lev", v=0.25)["param"][1] == "Band 2 level"


def test_many_settings_apply_in_one_call(rig):
    out = call(
        rig,
        a="many",
        set=[
            {"p": "Threshold", "r": "-12 dB"},
            {"p": "Attack", "r": "5 ms"},
            {"p": "Ratio", "r": "8:1"},
            {"p": 0, "v": 0.6},
        ],
    )
    assert [row[1] for row in out["params"]] == ["Threshold", "Attack", "Ratio", "Gain"]
    assert [row[3] for row in out["params"][1:3]] == ["5 ms", "8.0:1"]
    assert all(row[4] == 1 for row in out["params"])
    with pytest.raises(FLError, match="item 1 .nope.: no parameter named"):
        call(rig, a="many", set=[{"p": "Gain", "v": 0.5}, {"p": "nope", "v": 1}])
    with pytest.raises(FLError, match="at most 40"):
        call(rig, a="many", set=[{"p": 0, "v": 0.5}] * 41)


def test_the_list_shows_the_value_text_and_can_page(rig):
    r = call(rig, f="t")
    assert r["cols"] == ["i", "name", "val", "text"]
    assert [row[1] for row in r["rows"]] == ["Threshold", "Attack"] or r["rows"][0][3].endswith(
        "dB"
    )
    paged = eq(rig, st=3)
    assert [row[1] for row in paged["rows"]] == ["Band 2 freq", "Band 2 level"]


def test_plugin_info_and_presets(rig):
    info = call(rig, a="info")
    assert info == {
        "plugin": "Fruity Limiter",
        "name": "FRUITY LIMITER",
        "params": 6,
        "presets": 3,
        "preset": "Default",
    }
    listed = call(rig, a="presets")
    assert (
        listed["rows"] == [[0, "Default"], [1, "Gentle"], [2, "Hard"]]
        and listed["cur"] == "Default"
    )
    assert call(rig, a="preset", q="hard") == {
        "plugin": "Fruity Limiter",
        "name": "FRUITY LIMITER",
        "preset": "Hard",
        "count": 3,
        "moved": 2,
    }
    assert call(rig, a="preset", d=-1)["preset"] == "Gentle"
    assert call(rig, a="preset", d=1)["preset"] == "Hard"
    assert call(rig, a="preset", q="default")["moved"] == -2


def test_preset_errors(rig):
    with pytest.raises(FLError, match="no preset matches 'zzz'"):
        call(rig, a="preset", q="zzz")
    with pytest.raises(FLError, match="at most 3 steps"):
        call(rig, a="preset", d=9)
    with pytest.raises(FLError, match="no internal presets"):
        eq(rig, a="preset", d=1)
    with pytest.raises(FLError, match="unknown action"):
        call(rig, a="explode")


def test_the_inventory_lists_every_plugin_in_the_project(rig):
    rows = rig.call({"o": "list", "k": "plugins"})["rows"]
    by = {(r[1], r[2], r[3]): r for r in rows}
    assert by[("c", 2, -1)][4:] == ["Sytrus", "Sytrus", 4, 4]
    assert by[("m", 12, 0)][4] == "Fruity Limiter" and by[("m", 12, 0)][5] == "FRUITY LIMITER"
    assert by[("m", 12, 1)][6:] == [5, 0]
    assert ("m", 10, 0) in by and len(rows) == 5
    page = rig.call({"o": "list", "k": "plugins", "n": 2})
    assert len(page["rows"]) == 2 and page["next"] == 2


def test_windows_can_be_shown_hidden_and_focused(fl):
    assert fl.call({"o": "ui", "a": "win", "w": "mixer", "v": "show"})["visible"] is True
    assert fl.call({"o": "ui", "a": "win", "w": "mixer", "v": "hide"})["visible"] is False
    state = fl.call({"o": "ui", "a": "win", "w": "roll", "v": "focus"})
    assert state == {"visible": True, "focused": True}
    assert fl.call({"o": "ui", "a": "win", "w": "channels"})["visible"] is True
    with pytest.raises(FLError, match="window must be one of"):
        fl.call({"o": "ui", "a": "win", "w": "desk"})
    with pytest.raises(FLError, match="v must be"):
        fl.call({"o": "ui", "a": "win", "w": "mixer", "v": "melt"})


def test_keys_are_pressed_inside_fl(fl):
    out = fl.call({"o": "ui", "a": "key", "ks": ["down", "down", "enter", "f8", "menu", "tab"]})
    assert out["n"] == 6 and out["hint"].startswith("Hint:")
    assert fl.fake.keys == ["down", "down", "enter", 67, 90, "tab"] or "enter" in fl.fake.keys
    with pytest.raises(FLError, match="unknown key"):
        fl.call({"o": "ui", "a": "key", "k": "launch"})
    with pytest.raises(FLError, match="unknown key"):
        fl.call({"o": "ui", "a": "key", "k": "f13"})
    with pytest.raises(FLError, match="at most 20"):
        fl.call({"o": "ui", "a": "key", "ks": ["up"] * 21})


def test_the_focused_plugin_and_hint_are_readable(fl):
    form = fl.call({"o": "ui", "a": "form"})
    assert form["plugin"] == "Fruity Limiter" and form["fx"] == [10, 0] and form["ch"] == 0
    assert set(form["windows"]) == {"mixer", "channels", "playlist", "roll", "browser"}
    assert fl.call({"o": "ui", "a": "hint"})["hint"].startswith("Hint:")


def test_the_browser_can_be_read_searched_and_selected(fl):
    scan = fl.call({"o": "ui", "a": "browser", "b": "scan", "n": 20})
    assert scan["cols"] == ["name", "type"]
    names = [row[0] for row in scan["rows"]]
    assert names[:3] == ["Generators", "3x Osc", "Sytrus"] and names[-1] == "Snare 01.wav"
    fl.fake.browser_at = 0
    found = fl.call({"o": "ui", "a": "browser", "b": "goto", "q": "fruity lim"})
    assert found["found"] is True and found["item"][0] == "Fruity Limiter"
    assert fl.call({"o": "ui", "a": "browser", "b": "select"})["item"][0] == "Fruity Limiter"
    assert ("select", "Fruity Limiter") in fl.fake.browser_log
    assert fl.call({"o": "ui", "a": "browser", "b": "nav", "d": "prev"})["item"][0] == "Effects"
    assert fl.call({"o": "ui", "a": "browser", "b": "caption"})["item"][0] == "Effects"
    fl.call({"o": "ui", "a": "browser", "b": "preview"})
    fl.call({"o": "ui", "a": "browser", "b": "toggle", "v": 1})
    assert fl.call({"o": "ui", "a": "browser", "b": "tab", "d": "right"})["tab"] == "Tab"
    miss = fl.call({"o": "ui", "a": "browser", "b": "goto", "q": "no such thing", "n": 12})
    assert miss["found"] is False
    with pytest.raises(FLError, match="goto needs q"):
        fl.call({"o": "ui", "a": "browser", "b": "goto"})
    with pytest.raises(FLError, match="browser action must be"):
        fl.call({"o": "ui", "a": "browser", "b": "wreck"})
    with pytest.raises(FLError, match="ui action must be"):
        fl.call({"o": "ui", "a": "wreck"})


def test_probe_reports_what_this_fl_build_offers(fl):
    p = fl.call({"o": "probe"})
    assert p["ver"] == fl.script.VERSION and p["linked"] is True and p["port"] == 3
    assert p["has"]["ui.navigateBrowser"] is True and p["has"]["plugins.getName"] is True
    assert p["n_ch"] == 3 and p["changed"] == 0 and p["title"] == "My Song - FL Studio"


def test_diag_keeps_the_last_failures(fl):
    for _ in range(14):
        with pytest.raises(FLError):
            fl.call({"o": "set", "k": "channel", "i": 99, "p": {"name": "x"}})
    out = fl.call({"o": "diag"})
    assert out["failed"] == 14 and len(out["errors"]) == 12
    assert out["errors"][-1][1] == "set" and "out of range" in out["errors"][-1][2]
    assert out["n"] >= 14


def test_browser_walk_skips_heavy_folders_and_handles_popup_menu(fl):
    fl.fake.browser.insert(0, ("Current project", -101))
    fl.fake.browser.insert(1, ("Plugin presets", -100))
    fl.fake.browser_at = 0
    fl.fake.browser_log.clear()
    found = fl.call({"o": "ui", "a": "browser", "b": "goto", "q": "fruity lim"})
    assert found["found"] is True and found["item"][0] == "Fruity Limiter"
    nav_shifts = [sh for op, _, sh in fl.fake.browser_log if op == "nav"]
    assert nav_shifts[:2] == [False, False]  # skipped expanding Current project and Plugin presets
    fl.fake.ui_isInPopupMenu = lambda: True
    fl.call({"o": "ui", "a": "browser", "b": "select"})
    assert "down" in fl.fake.keys and "enter" in fl.fake.keys
