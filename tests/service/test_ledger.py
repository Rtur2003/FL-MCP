import json

import pytest

from flmcp_service import accounts, ledger
from flmcp_service.errors import ApiError
from tests.service.conftest import START

DAY = 86400


@pytest.fixture
def acct(db, cfg):
    with db.tx() as c:
        return accounts.account_for(c, "a@example.test", START)


def balance(db, acct, at=None):
    with db.tx() as c:
        return ledger.balance(c, acct, at or db.now())


def entries_total(db, acct):
    with db.tx() as c:
        ledger.sweep(c, acct, db.now(), plans_cfg())
        return c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM entries WHERE account_id=?", (acct,)
        ).fetchone()[0]


def plans_cfg():
    from flmcp_service import plans

    return plans.load()


def grant(db, acct, amount, days=None, source=None, kind="plan"):
    with db.tx() as c:
        exp = START + days * DAY if days else None
        return ledger.grant(c, acct, kind, amount, db.now(), exp, source)


def reserve(db, acct, cfg, ops=("produce.compose",), idem="idem-00001"):
    with db.tx() as c:
        return ledger.reserve(c, acct, list(ops), idem, db.now(), cfg)


def settle(db, acct, cfg, rid, done):
    with db.tx() as c:
        return ledger.settle(c, acct, rid, list(done), db.now(), cfg)


def test_a_grant_with_a_source_happens_once(db, acct):
    assert grant(db, acct, 100, source="plan:x:1") is True
    assert grant(db, acct, 100, source="plan:x:1") is False
    assert balance(db, acct) == 100


def test_nonsense_grants_are_ignored(db, acct):
    assert grant(db, acct, 0) is False
    assert grant(db, acct, -5) is False
    with db.tx() as c:  # already expired
        assert ledger.grant(c, acct, "plan", 10, START, START - 1) is False
    assert balance(db, acct) == 0


def test_reserve_debits_at_once_and_settle_charges_only_what_was_delivered(db, acct, cfg):
    grant(db, acct, 100)
    r = reserve(db, acct, cfg, ["produce.compose", "produce.flp"])
    assert r["cost"] == 8 and r["balance"] == 92 and r["state"] == "held"
    s = settle(db, acct, cfg, r["id"], ["produce.compose"])
    assert (s["charged"], s["refunded"], s["balance"]) == (5, 3, 95)
    assert balance(db, acct) == 95 == entries_total(db, acct)


def test_a_failed_job_costs_nothing(db, acct, cfg):
    grant(db, acct, 10)
    r = reserve(db, acct, cfg)
    s = settle(db, acct, cfg, r["id"], [])
    assert (s["charged"], s["refunded"], s["balance"]) == (0, 5, 10)


def test_not_enough_credits_is_a_402_and_changes_nothing(db, acct, cfg):
    grant(db, acct, 4)
    with pytest.raises(ApiError) as e:
        reserve(db, acct, cfg)
    assert (e.value.status, e.value.code) == (402, "insufficient_credits")
    assert e.value.extra == {"cost": 5, "balance": 4}
    assert balance(db, acct) == 4


def test_unknown_and_repeated_operations_are_refused(db, acct, cfg):
    grant(db, acct, 50)
    for ops in (["nope"], [], ["mix.balance", "mix.balance"]):
        with pytest.raises(ApiError) as e:
            reserve(db, acct, cfg, ops)
        assert e.value.status == 400
    assert balance(db, acct) == 50


def test_a_retried_reserve_does_not_charge_twice(db, acct, cfg):
    grant(db, acct, 50)
    first = reserve(db, acct, cfg, idem="same-key-1")
    again = reserve(db, acct, cfg, idem="same-key-1")
    assert first["id"] == again["id"] and balance(db, acct) == 45
    with pytest.raises(ApiError) as e:
        reserve(db, acct, cfg, ["mix.balance"], idem="same-key-1")
    assert e.value.code == "idempotency_conflict"


def test_settle_is_idempotent_and_refuses_a_different_outcome(db, acct, cfg):
    grant(db, acct, 50)
    r = reserve(db, acct, cfg, ["produce.compose", "produce.flp"])
    one = settle(db, acct, cfg, r["id"], ["produce.compose"])
    two = settle(db, acct, cfg, r["id"], ["produce.compose"])
    assert one == two and balance(db, acct) == 45
    with pytest.raises(ApiError) as e:
        settle(db, acct, cfg, r["id"], ["produce.compose", "produce.flp"])
    assert e.value.code == "already_settled" and balance(db, acct) == 45


def test_settle_rejects_operations_that_were_not_reserved(db, acct, cfg):
    grant(db, acct, 50)
    r = reserve(db, acct, cfg)
    with pytest.raises(ApiError) as e:
        settle(db, acct, cfg, r["id"], ["mix.balance"])
    assert e.value.status == 400 and balance(db, acct) == 45


def test_a_reservation_belongs_to_its_account(db, acct, cfg):
    grant(db, acct, 50)
    r = reserve(db, acct, cfg)
    with db.tx() as c:
        other = accounts.account_for(c, "b@example.test", START)
    with pytest.raises(ApiError) as e:
        settle(db, other, cfg, r["id"], [])
    assert e.value.status == 404


def test_an_unsettled_hold_comes_back_when_it_runs_out(db, acct, cfg, clock):
    grant(db, acct, 50)
    r = reserve(db, acct, cfg)
    assert balance(db, acct) == 45
    clock.advance(cfg.hold_seconds)
    with db.tx() as c:
        ledger.sweep(c, acct, db.now(), cfg)
    assert balance(db, acct) == 50 == entries_total(db, acct)
    with pytest.raises(ApiError) as e:
        settle(db, acct, cfg, r["id"], ["produce.compose"])
    assert e.value.code == "reservation_expired"


def test_the_lot_that_expires_first_is_used_first(db, acct, cfg):
    grant(db, acct, 10, days=None, kind="topup")
    grant(db, acct, 10, days=30, kind="plan")
    r = reserve(db, acct, cfg)
    with db.tx() as c:
        rows = c.execute("SELECT kind, remaining FROM lots ORDER BY id").fetchall()
    assert [(x["kind"], x["remaining"]) for x in rows] == [("topup", 10), ("plan", 5)]
    assert json.loads(_alloc(db, r["id"]))[0][1] == 5


def _alloc(db, rid):
    with db.tx() as c:
        return c.execute("SELECT alloc FROM reservations WHERE id=?", (rid,)).fetchone()[0]


def test_expired_credits_are_dropped_and_recorded(db, acct, cfg, clock):
    grant(db, acct, 10, days=30)
    clock.advance(31 * DAY)
    assert balance(db, acct) == 0
    with db.tx() as c:
        ledger.sweep(c, acct, db.now(), cfg)
        kinds = [r["kind"] for r in c.execute("SELECT kind FROM entries ORDER BY id")]
    assert kinds == ["grant", "expire"] and entries_total(db, acct) == 0


def test_credits_returned_for_a_lot_that_expired_meanwhile_still_arrive(db, acct, cfg, clock):
    short = type(cfg)(**{**cfg.__dict__, "hold_seconds": 3 * DAY})
    short = type(cfg)(**{**cfg.__dict__, "hold_seconds": 3 * DAY})
    grant(db, acct, 5, days=1)
    r = reserve(db, acct, short)
    clock.advance(2 * DAY)  # the lot is gone, the hold is still open
    s = settle(db, acct, short, r["id"], [])
    assert s["refunded"] == 5 and s["balance"] == 5
    with db.tx() as c:
        kinds = {x["kind"] for x in c.execute("SELECT kind FROM lots")}
    assert "refund" in kinds and entries_total(db, acct) == 5
