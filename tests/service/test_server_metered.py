"""The MCP tools against a licence service: what is charged, what is free, what fails cleanly."""

import json

import pytest
from mcp import Client

from fl_mcp import mixing, server
from fl_mcp.bridge import FLError
from tests.test_server_pro import studio  # noqa: F401  (pytest fixture)

pytestmark = pytest.mark.anyio


@pytest.fixture
def metered(monkeypatch, gate, issue, studio):  # noqa: F811
    monkeypatch.setattr(server, "gate", gate)
    gate.login(issue())
    calls = []
    inner = gate._post
    gate._post = lambda *a: calls.append((a[0], a[1])) or inner(*a)
    studio.calls = calls
    return studio


async def run(tool, args=None, expect_error=False):
    async with Client(server.mcp) as client:
        result = await client.call_tool(tool, args or {})
    assert result.is_error is expect_error, result.content[0].text
    text = result.content[0].text
    return text if expect_error else json.loads(text)


def credits(gate):
    return gate.status()["credits"]


@pytest.fixture
def template_file(tmp_path):
    from tests.flpgen import template

    path = tmp_path / "template.flp"
    path.write_bytes(template(channels=("Kick", "808 Bass", "Lead Synth")))
    return str(path)


async def test_status_shows_the_account_only_when_metered(metered, gate, monkeypatch):
    out = await run("fl_status")
    assert out["account"]["plan"] == "pro" and out["account"]["credits"] == 300
    monkeypatch.setattr(server, "gate", type(gate)("", None, gate._state))
    assert "account" not in await run("fl_status")


async def test_a_new_plan_costs_credits_and_the_balance_follows(metered, gate):
    out = await run("fl_produce", {"genre": "house", "seed": 3, "length": "sketch"})
    assert out["plan"]["id"] == "house-3" and credits(gate) == 295


async def test_a_project_file_costs_extra(metered, gate, template_file):
    await run(
        "fl_produce",
        {"genre": "house", "seed": 4, "length": "sketch", "template": template_file},
    )
    assert credits(gate) == 300 - 5 - 3
    await run("fl_produce", {"action": "flp", "plan": "house-4", "template": template_file})
    assert credits(gate) == 300 - 5 - 3 - 3, "re-writing the project is a project, not a new plan"


async def test_working_with_a_plan_you_already_have_is_free(metered, gate, template_file):
    await run("fl_produce", {"genre": "trap", "seed": 1, "length": "sketch"})
    before = list(metered.calls)
    for args in (
        {"action": "genres"},
        {"action": "show", "plan": "trap-1"},
        {"action": "show", "plan": "trap-1", "pattern": "0"},
        {"action": "apply", "plan": "trap-1"},
        {"action": "midi", "plan": "trap-1"},
        {"action": "inspect", "template": template_file},
    ):
        await run("fl_produce", args)
    assert metered.calls == before, "no request left the machine"
    assert credits(gate) == 295


async def test_invalid_requests_are_refused_before_anything_is_reserved(metered):
    before = list(metered.calls)
    for args in (
        {"action": "make"},
        {"action": "flp", "genre": "house"},
        {"action": "inspect"},
    ):
        await run("fl_produce", args, expect_error=True)
    assert metered.calls == before


async def test_a_project_that_cannot_be_written_is_not_charged_but_the_plan_is(
    metered, gate, tmp_path
):
    bad = tmp_path / "bad.flp"
    bad.write_bytes(b"not a project")
    error = await run(
        "fl_produce",
        {"genre": "house", "seed": 5, "length": "sketch", "template": str(bad)},
        expect_error=True,
    )
    assert "not an FL Studio project" in error
    assert credits(gate) == 295, "the plan was delivered, the project was not"
    again = await run("fl_produce", {"action": "show", "plan": "house-5"})
    assert again["plan"]["id"] == "house-5"


async def test_running_out_of_credits_is_explained_and_nothing_is_composed(
    metered, gate, svc, issue, monkeypatch, tmp_path
):
    from fl_mcp.commerce import Gate

    poor = Gate(gate.service, gate._post, tmp_path / "poor.json")
    poor.login(issue(email="poor@example.test", plan="trial"))
    monkeypatch.setattr(server, "gate", poor)
    for seed in range(3):
        await run("fl_produce", {"genre": "house", "seed": seed, "length": "sketch"})
    error = await run(
        "fl_produce", {"genre": "house", "seed": 9, "length": "sketch"}, expect_error=True
    )
    assert "needs 5, you have 0" in error and "example.test/topup" in error
    assert not (metered.dir / "plans" / "house-9.json").exists()


async def test_balancing_the_mix_is_charged_when_it_ran(metered, gate, monkeypatch):
    monkeypatch.setattr(mixing.time, "sleep", lambda _: None)
    f = metered.fake
    f.peaks = {0: 0.8, 1: 0.9, 3: 0.2}
    await run("fl_mix", {"seconds": 1})
    assert credits(gate) == 300, "measuring is free"
    base = {1: 0.9, 3: 0.28}
    f.peak_fn = lambda i: (
        0.3 * sum(base[t] * f.tracks[t]["vol"] / 0.8 for t in base)
        if i == 0
        else base.get(i, 0.0) * f.tracks[i]["vol"] / 0.8
    )
    await run("fl_mix", {"action": "balance", "seconds": 1, "targets": "1=-8,3=-9"})
    assert credits(gate) == 298


async def test_a_balance_that_fails_is_not_charged(metered, gate, monkeypatch):
    def broken(*_args, **_kwargs):
        raise FLError("FL Studio stopped answering")

    monkeypatch.setattr(mixing, "balance", broken)
    error = await run(
        "fl_mix", {"action": "balance", "seconds": 1, "targets": "1=-8"}, expect_error=True
    )
    assert "stopped answering" in error and credits(gate) == 300


async def test_mix_arguments_are_checked_before_credits_are_reserved(metered):
    before = list(metered.calls)
    await run("fl_mix", {"action": "balance", "targets": "kick"}, expect_error=True)
    assert metered.calls == before


async def test_not_signed_in_gives_the_way_out(monkeypatch, gate, studio):  # noqa: F811
    monkeypatch.setattr(server, "gate", gate)
    error = await run("fl_produce", {"genre": "house", "seed": 1}, expect_error=True)
    assert "fl-mcp login" in error
    assert (await run("fl_produce", {"action": "genres"}))["house"], "free tools still work"
