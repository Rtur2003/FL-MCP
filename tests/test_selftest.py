"""The live self-test, run against the FL fake: every check ends in ok, skip or a clear failure."""

import json

import pytest

from fl_mcp import selftest
from fl_mcp.bridge import FLError


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FL_MCP_FL_DIR", str(tmp_path / "nowhere"))
    monkeypatch.delenv("FL_MCP_DESKTOP", raising=False)
    return tmp_path


def by_name(report):
    return {c["check"]: c for c in report["checks"]}


def test_everything_the_fake_offers_passes_and_the_rest_is_skipped_with_a_reason(fl, home):
    report = selftest.run(fl.call)
    checks = by_name(report)
    failed = {k: v["detail"] for k, v in checks.items() if v["status"] == "fail"}
    assert not failed, failed
    for name in ("link", "status", "features", "lists", "mixer write", "tempo write", "windows"):
        assert checks[name]["status"] == "ok", (name, checks[name])
    assert "script v5" in checks["link"]["detail"]
    assert report["summary"]["fail"] == 0 and report["summary"]["ok"] >= 7
    assert (
        checks["desktop"]["status"] == "skip" and "fl-mcp desktop on" in checks["desktop"]["detail"]
    )
    assert checks["command-line render"]["status"] == "skip"


def test_writes_are_put_back(fl, home):
    before = (
        fl.call({"o": "status"})["bpm"],
        fl.call({"o": "list", "k": "mixer", "s": 0, "n": 9, "a": 0}),
    )
    selftest.run(fl.call)
    after = (
        fl.call({"o": "status"})["bpm"],
        fl.call({"o": "list", "k": "mixer", "s": 0, "n": 9, "a": 0}),
    )
    assert before == after


def test_a_plugin_is_exercised_in_real_units_and_restored(fl, home):
    checks = by_name(selftest.run(fl.call))
    assert checks["plugin params"]["status"] == "ok", checks["plugin params"]
    assert "Sytrus (channel 2)" in checks["plugin params"]["detail"]
    assert checks["plugin real units"]["status"] in ("ok", "skip"), checks["plugin real units"]
    assert checks["plugin presets"]["status"] in ("ok", "skip"), checks["plugin presets"]


def test_without_a_link_the_rest_is_skipped_not_failed(home):
    def dead(req):
        raise FLError("No answer from FL Studio")

    report = selftest.run(dead)
    checks = by_name(report)
    assert checks["link"]["status"] == "fail" and "No answer" in checks["link"]["detail"]
    assert checks["status"]["status"] == "skip" and checks["status"]["detail"] == "no link to FL"
    assert report["summary"]["fail"] == 1


def test_an_outdated_script_is_named_with_the_fix(fl, home):
    def old(req):
        out = fl.call(req)
        return {**out, "ver": 1} if req["o"] == "ping" else out

    detail = by_name(selftest.run(old))["link"]["detail"]
    assert "script in FL is v1" in detail and "fl-mcp install" in detail


def test_a_failed_write_still_restores_what_it_touched(fl, home):
    seen = []

    def flaky(req):
        if req["o"] == "set":
            seen.append(req["p"])
            if len(seen) == 1:
                raise FLError("FL refused")
        return fl.call(req)

    checks = by_name(selftest.run(flaky))
    assert (
        checks["mixer write"]["status"] == "fail"
        and "FL refused" in checks["mixer write"]["detail"]
    )
    assert len(seen) == 2  # the put-back still happened


def test_the_report_reads_as_text_and_is_saved_as_json(fl, home):
    report = selftest.run(fl.call)
    text = selftest.render_text(report)
    assert "[ ok ] link" in text and "ok," in text.splitlines()[-1]
    path = selftest.save(report)
    assert json.loads(path.read_text())["summary"] == report["summary"]
    assert path.name == "selftest.json"


def test_default_mixer_and_param_failure_do_not_cascade(fl, home):
    def default_mixer_and_broken_params(req):
        if req["o"] == "list" and req.get("k") == "mixer" and req.get("a") == 1:
            out = fl.call(req)
            return {**out, "rows": out["rows"][:1]}
        if req["o"] == "param" and req.get("a") == "list":
            raise FLError("param error")
        return fl.call(req)

    checks = by_name(selftest.run(default_mixer_and_broken_params))
    assert checks["mixer write"]["status"] == "ok"
    assert checks["plugin params"]["status"] == "fail"
    assert checks["plugin real units"]["status"] == "skip"
