"""Random sequences of grants, holds, settlements, timeouts and expiries never break the books."""

import json
import random

import pytest

from flmcp_service import accounts, ledger
from flmcp_service.errors import ApiError
from tests.service.conftest import START

DAY = 86400
OPS = ["produce.compose", "produce.flp", "mix.balance"]


def check(db, acct, cfg):
    with db.tx() as c:
        now = db.now()
        ledger.sweep(c, acct, now, cfg)
        bal = ledger.balance(c, acct, now)
        booked = c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM entries WHERE account_id=?", (acct,)
        ).fetchone()[0]
        assert bal == booked, "the balance is the sum of the entries"
        for lot in c.execute("SELECT amount, remaining FROM lots WHERE account_id=?", (acct,)):
            assert 0 <= lot["remaining"] <= lot["amount"]
        held = 0
        for r in c.execute("SELECT * FROM reservations WHERE account_id=?", (acct,)):
            assert sum(n for _, n in json.loads(r["alloc"])) == r["cost"]
            if r["state"] == "held":
                held += r["cost"]
            if r["state"] == "settled":
                assert 0 <= r["charged"] <= r["cost"]
        granted = c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM entries WHERE account_id=? AND kind='grant'",
            (acct,),
        ).fetchone()[0]
        gone = -c.execute(
            "SELECT COALESCE(SUM(amount),0) FROM entries WHERE account_id=? AND kind='expire'",
            (acct,),
        ).fetchone()[0]
        charged = c.execute(
            "SELECT COALESCE(SUM(charged),0) FROM reservations WHERE account_id=?", (acct,)
        ).fetchone()[0]
        assert granted - gone - charged - held == bal, "credits are neither made nor lost"


@pytest.mark.parametrize("seed", range(20))
def test_the_books_always_balance(db, cfg, clock, seed):
    rng = random.Random(seed)
    with db.tx() as c:
        acct = accounts.account_for(c, "p@example.test", START)
    open_ids: list[tuple[str, list[str]]] = []
    for step in range(60):
        action = rng.choice(["grant", "reserve", "reserve", "settle", "settle", "time", "retry"])
        now = db.now()
        try:
            with db.tx() as c:
                if action == "grant":
                    days = rng.choice([None, 1, 5, 30])
                    ledger.grant(
                        c,
                        acct,
                        "adjust",
                        rng.randint(1, 40),
                        now,
                        now + days * DAY if days else None,
                    )
                elif action == "reserve":
                    ops = rng.sample(OPS, rng.randint(1, 3))
                    r = ledger.reserve(c, acct, ops, f"idem-{seed}-{step}", now, cfg)
                    open_ids.append((r["id"], ops))
                elif action == "retry" and open_ids:
                    rid, ops = rng.choice(open_ids)
                    row = c.execute("SELECT idem FROM reservations WHERE id=?", (rid,)).fetchone()
                    ledger.reserve(c, acct, ops, row["idem"], now, cfg)
                elif action == "settle" and open_ids:
                    rid, ops = rng.choice(open_ids)
                    done = rng.sample(ops, rng.randint(0, len(ops)))
                    ledger.settle(c, acct, rid, done, now, cfg)
                elif action == "time":
                    clock.advance(rng.choice([60, 700, DAY, 3 * DAY]))
        except ApiError as exc:
            assert exc.status in (402, 409), exc.code
        check(db, acct, cfg)
