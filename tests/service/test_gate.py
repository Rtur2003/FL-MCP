import json
import os
import stat
import sys

import pytest

from fl_mcp.commerce import Gate, LicenseError
from fl_mcp.commerce import gate as gate_module

pytestmark = pytest.mark.anyio


def balance(svc, issue_email="a@example.test"):
    res = svc.http.get(
        "/v1/admin/accounts",
        params={"email": issue_email},
        headers={"Authorization": "Bearer admin-secret"},
    )
    return res.json()["balance"]


def test_without_a_service_nothing_is_metered(tmp_path):
    g = Gate("", None, tmp_path / "a.json")
    assert not g.metered and g.status() == {"metered": False}
    with pytest.raises(LicenseError, match="no licence service"):
        g.login("FLMCP-AAAAA-AAAAA-AAAAA-AAAAA")


def test_the_service_address_comes_from_the_environment_or_the_build(monkeypatch):
    monkeypatch.delenv("FL_MCP_SERVICE", raising=False)
    assert gate_module.service_url() == ""  # the repository ships unmetered
    monkeypatch.setenv("FL_MCP_SERVICE", " https://x.example.test ")
    assert gate_module.service_url() == "https://x.example.test"
    assert Gate().metered


def test_login_stores_a_private_token_and_reports_the_plan(gate, issue, tmp_path):
    out = gate.login(issue())
    assert out["plan"] == "pro" and out["credits"] == 300
    state = json.loads((tmp_path / "account.json").read_text())
    assert state["token"] and len(state["device_id"]) == 32
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "account.json").st_mode) == 0o600


def test_a_mistyped_key_never_reaches_the_service(gate, svc):
    calls = []
    inner = gate._post
    gate._post = lambda *a: calls.append(a) or inner(*a)
    with pytest.raises(LicenseError, match="not a licence key"):
        gate.login("FLMCP-NOT-A-KEY")
    assert calls == []


def test_login_problems_are_explained(gate, issue):
    from fl_mcp.commerce import keys

    with pytest.raises(LicenseError, match="not found"):
        gate.login(keys.new_key())
    key = issue(plan="trial")
    gate.login(key)
    other = Gate(gate.service, gate._post, gate._state.with_name("other.json"))
    with pytest.raises(LicenseError, match="In use: .*--replace"):
        other.login(key)
    other.login(key, replace=json.loads(gate._state.read_text())["device_id"][:8])
    assert "not signed in" in gate.status()["note"], "the replaced computer is signed out"


def test_status_shows_credits_prices_and_survives_an_outage(gate, issue):
    gate.login(issue())
    out = gate.status()
    assert out["plan"] == "pro" and out["credits"] == 300 and out["costs"]["mix.balance"] == 2

    def down(*_):
        raise LicenseError("Could not reach the licence service")

    gate._post = down
    off = gate.status()
    assert off["cached"] is True and off["credits"] == 300 and "Could not reach" in off["note"]


def test_status_when_signed_out_says_how_to_sign_in(gate):
    out = gate.status()
    assert out["signed_in"] is False and "fl-mcp login" in out["note"]


async def test_spending_charges_only_what_was_delivered(gate, issue, svc):
    gate.login(issue())
    async with gate.spend("produce.compose", "produce.flp") as bill:
        bill.ok("produce.compose")
    assert balance(svc) == 295


async def test_a_failure_costs_nothing_and_the_error_survives(gate, issue, svc):
    gate.login(issue())
    with pytest.raises(RuntimeError, match="boom"):
        async with gate.spend("produce.compose"):
            raise RuntimeError("boom")
    assert balance(svc) == 300


async def test_not_enough_credits_stops_the_work_before_it_starts(gate, issue, svc):
    gate.login(issue(plan="trial"))  # 15 credits
    for _ in range(3):
        async with gate.spend("produce.compose") as bill:
            bill.ok("produce.compose")
    ran = []
    with pytest.raises(LicenseError, match="needs 5, you have 0.*example.test/topup"):
        async with gate.spend("produce.compose"):
            ran.append(1)
    assert ran == [] and balance(svc) == 0


async def test_free_calls_do_not_touch_the_network(gate):
    gate._post = lambda *a: pytest.fail("a free call went to the service")
    async with gate.spend() as bill:
        bill.ok("nothing")
    unmetered = Gate("", None, gate._state)
    async with unmetered.spend("produce.compose"):
        pass


async def test_not_signed_in_means_no_work(gate):
    with pytest.raises(LicenseError, match="fl-mcp login"):
        async with gate.spend("mix.balance"):
            pytest.fail("work ran without a licence")


async def test_a_lost_answer_is_retried_without_charging_twice(gate, issue, svc):
    gate.login(issue())
    inner = gate._post
    state = {"dropped": False}

    def flaky(method, path, body, token):
        res = inner(method, path, body, token)  # the service did process it...
        if path.endswith("/reserve") and not state["dropped"]:
            state["dropped"] = True
            raise LicenseError("Could not reach the licence service")  # ...but we never heard
        return res

    gate._post = flaky
    async with gate.spend("produce.compose") as bill:
        bill.ok("produce.compose")
    assert state["dropped"] and balance(svc) == 295


async def test_a_settle_that_cannot_be_delivered_never_hides_the_result(gate, issue, svc, caplog):
    gate.login(issue())
    inner = gate._post

    def settle_down(method, path, body, token):
        if path.endswith("/settle"):
            raise LicenseError("Could not reach the licence service")
        return inner(method, path, body, token)

    gate._post = settle_down
    async with gate.spend("produce.compose") as bill:
        bill.ok("produce.compose")
    assert "could not settle" in caplog.text
    svc.clock.advance(700)  # the hold runs out and the customer gets the credits back
    assert balance(svc) == 300


def test_the_desktop_extension_signs_in_with_the_key_it_was_given(gate, issue, monkeypatch):
    monkeypatch.setenv("FL_MCP_LICENSE", issue())
    assert gate.status()["credits"] == 300


def test_a_bad_key_from_the_environment_is_reported_not_ignored(gate, monkeypatch):
    monkeypatch.setenv("FL_MCP_LICENSE", "FLMCP-AAAAA-AAAAA-AAAAA-AAAAA")
    assert "not a licence key" in gate.status()["note"] or "not found" in gate.status()["note"]


def test_logout_frees_the_seat_and_forgets_the_token(gate, issue):
    key = issue(plan="trial")
    gate.login(key)
    gate.logout()
    state = json.loads(gate._state.read_text())
    assert "token" not in state and state["device_id"]
    Gate(gate.service, gate._post, gate._state.with_name("b.json")).login(key)


def test_logout_works_offline(gate, issue):
    gate.login(issue())

    def down(*_):
        raise LicenseError("offline")

    gate._post = down
    gate.logout()
    assert "token" not in json.loads(gate._state.read_text())


def test_a_token_is_only_used_for_the_service_that_issued_it(gate, issue, tmp_path):
    gate.login(issue())
    elsewhere = Gate("https://other.example.test", gate._post, gate._state)
    assert elsewhere._load().get("token") is None
    assert elsewhere._load()["device_id"] == gate._load()["device_id"]


def test_the_service_must_be_https_unless_it_is_local():
    with pytest.raises(LicenseError, match="https"):
        gate_module._transport("http://licence.example.test")
    gate_module._transport("http://127.0.0.1:8000")
    gate_module._transport("https://licence.example.test")


def test_redirects_are_not_followed():
    handler = gate_module._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.test") is None


def test_a_corrupt_state_file_is_replaced(gate, issue):
    gate._state.write_text("{not json")
    assert gate.login(issue())["plan"] == "pro"


def test_the_command_line_signs_in_and_out(gate, issue, monkeypatch, capsys):
    from fl_mcp import cli

    monkeypatch.setattr(gate_module, "service_url", lambda: gate.service)
    monkeypatch.setattr(gate_module, "_transport", lambda base: gate._post)
    monkeypatch.setenv("FL_MCP_DIR", str(gate._state.parent))
    key = issue()
    with pytest.raises(SystemExit) as done:
        cli.main(["login", key, "--name", "studio"])
    assert done.value.code == 0 and "Signed in: pro plan, 300 credits" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["account"])
    assert json.loads(capsys.readouterr().out)["credits"] == 300
    with pytest.raises(SystemExit) as done:
        cli.main(["login", "FLMCP-nope"])
    assert done.value.code == 1 and "not a licence key" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["logout"])
    assert "Signed out" in capsys.readouterr().out


def test_the_command_line_says_so_when_there_is_nothing_to_sign_in_to(monkeypatch, capsys):
    from fl_mcp import cli

    monkeypatch.setenv("FL_MCP_SERVICE", "")
    with pytest.raises(SystemExit) as done:
        cli.main(["account"])
    assert done.value.code == 0 and "nothing is metered" in capsys.readouterr().out


def test_a_missing_key_is_asked_for_without_echo(gate, issue, monkeypatch, capsys):
    from fl_mcp import cli

    monkeypatch.setattr(gate_module, "service_url", lambda: gate.service)
    monkeypatch.setattr(gate_module, "_transport", lambda base: gate._post)
    monkeypatch.setenv("FL_MCP_DIR", str(gate._state.parent))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: issue())
    with pytest.raises(SystemExit) as done:
        cli.main(["login"])
    assert done.value.code == 0


def test_an_unfilled_extension_placeholder_is_not_mistaken_for_a_key(gate, monkeypatch):
    monkeypatch.setenv("FL_MCP_LICENSE", "${user_config.license_key}")
    out = gate.status()
    assert out["signed_in"] is False and "fl-mcp login" in out["note"]
