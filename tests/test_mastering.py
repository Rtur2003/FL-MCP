"""The master loop: find the limiter, move its gain by the loudness error, measure again."""

import pytest

from fl_mcp import mastering


class Meter:
    """Stands in for render + analysis: loudness follows the limiter's gain, like a limiter does."""

    def __init__(self, fl, base=-18.0, slope=1.0, ceiling_ok=True, peak=-1.5, silent=False):
        self.fl, self.base, self.slope, self.peak, self.silent = fl, base, slope, peak, silent
        self.calls = 0

    def gain_db(self):
        return self.fl.fake.param_value_db(("m", 0, 9), 0)

    def __call__(self):
        self.calls += 1
        if self.silent:
            return {"lufs_i": None, "true_peak_db": -120.0}
        lufs = self.base + self.slope * self.gain_db() / 1.0
        return {"lufs_i": round(lufs, 1), "true_peak_db": self.peak, "crest_db": 8.0, "advice": []}


@pytest.fixture
def limited(fl):
    fl.fake.add_master_limiter(gain=0.5)  # Gain shows -30.0 dB, Ceiling -6.0 dB
    fl.fake.param_value_db = lambda key, i: -60 + 60 * fl.fake.params[key][i][1]
    return fl


def test_it_finds_the_limiter_on_the_master(limited):
    assert mastering.find_limiter(limited.call) == {"slot": 9, "plugin": "Fruity Limiter"}


def test_a_master_without_a_limiter_says_how_to_add_one(fl):
    with pytest.raises(ValueError, match="no limiter.*fl_ui action=load"):
        mastering.find_limiter(fl.call)
    fl.fake.tracks[0]["slots"][0] = "Fruity Parametric EQ 2"
    fl.fake.params[("m", 0, 0)] = [["Band 1 gain", 0.5, "db"]]
    with pytest.raises(ValueError, match="no limiter"):
        mastering.find_limiter(fl.call)


def test_the_gain_moves_by_the_error_until_the_target_is_met(limited):
    meter = Meter(limited, base=-18.0)  # loudness = -18 + gain(-30): far too quiet
    out = mastering.run(limited.call, meter, target=-24.0, ceiling=-1.0)
    assert out["reached"] is True and abs(out["passes"][-1]["lufs"] + 24.0) <= 0.5
    assert out["limiter"]["gain_param"] == "Gain" and out["limiter"]["original_gain_db"] == -30.0
    assert out["passes"][0]["gain_db"] == -30.0 and len(out["passes"]) in (2, 3)
    assert out["final"]["lufs_i"] == out["passes"][-1]["lufs"]
    ceiling = limited.fake.params[("m", 0, 9)][1][1]
    assert -60 + 60 * ceiling == pytest.approx(-1.0, abs=0.1)  # the ceiling was set first


def test_an_already_good_master_is_left_alone(limited):
    meter = Meter(limited, base=-18.0)
    out = mastering.run(limited.call, meter, target=-48.0)
    assert out["reached"] is True and len(out["passes"]) == 1 and meter.calls == 1
    assert out["limiter"]["final_gain_db"] == -30.0


def test_a_limiter_that_stops_getting_louder_is_called_saturated(limited):
    meter = Meter(limited, base=-18.0, slope=0.1)  # gain barely changes the loudness
    out = mastering.run(limited.call, meter, target=-10.0)
    assert out["reached"] is False
    assert any("saturating" in n for n in out["notes"]) and len(out["passes"]) == 2


def test_it_never_loops_forever(limited):
    meter = Meter(limited, base=-18.0, slope=0.0)  # nothing changes at all
    out = mastering.run(limited.call, meter, target=-10.0, passes=2)
    assert meter.calls <= 3 and out["reached"] is False


def test_a_peak_over_the_ceiling_lowers_the_ceiling_and_fails_the_goal(limited):
    meter = Meter(limited, base=-18.0, peak=0.3)
    out = mastering.run(limited.call, meter, target=-24.0, ceiling=-1.0)
    assert out["reached"] is False and out["final"]["true_peak_db"] == 0.3
    ceiling_db = -60 + 60 * limited.fake.params[("m", 0, 9)][1][1]
    assert ceiling_db < -1.0  # pushed down by the overshoot


def test_a_silent_render_is_not_mistaken_for_quiet(limited):
    with pytest.raises(ValueError, match="render is silent"):
        mastering.run(limited.call, Meter(limited, silent=True), target=-8.0)


def test_a_limiter_without_a_db_gain_is_explained(fl):
    fl.fake.tracks[0]["slots"][9] = "Fruity Limiter"
    fl.fake.params[("m", 0, 9)] = [["Gain", 0.5, "pct"], ["Ceiling", 0.9, "db"]]
    with pytest.raises(ValueError, match="cannot find a dB gain or threshold.*Gain, Ceiling"):
        mastering.run(fl.call, lambda: {}, target=-8.0)


def test_a_threshold_driven_limiter_is_turned_down_to_get_louder(fl):
    fl.fake.tracks[0]["slots"][9] = "Pro-L 2"
    fl.fake.params[("m", 0, 9)] = [["Threshold", 0.5, "db"], ["Ceiling", 0.9, "db"]]

    def meter():  # a lower threshold is louder
        db = -60 + 60 * fl.fake.params[("m", 0, 9)][0][1]
        return {"lufs_i": -50.0 - db, "true_peak_db": -1.5, "crest_db": 8.0, "advice": []}

    out = mastering.run(fl.call, meter, target=-8.0, ceiling=-1.0)
    assert out["limiter"]["gain_param"] == "Threshold" and out["reached"] is True
    assert out["limiter"]["final_gain_db"] < out["limiter"]["original_gain_db"]
