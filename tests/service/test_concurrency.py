"""Parallel requests against one SQLite file must not overspend, double grant or lock up."""

import threading

import pytest

from flmcp_service import accounts, ledger, webhooks
from flmcp_service.errors import ApiError
from tests.service.conftest import START


def run_parallel(count, work):
    results, errors = [], []
    start = threading.Barrier(count)

    def go(i):
        start.wait()
        try:
            results.append(work(i))
        except ApiError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results, errors


def test_parallel_reservations_cannot_overspend(db, cfg):
    with db.tx() as c:
        acct = accounts.account_for(c, "p@example.test", START)
        ledger.grant(c, acct, "plan", 20, START)

    def reserve(i):
        with db.tx() as c:
            return ledger.reserve(c, acct, ["produce.compose"], f"idem-{i:04d}-xx", db.now(), cfg)

    done, refused = run_parallel(8, reserve)
    assert len(done) == 4 and len(refused) == 4
    assert {e.code for e in refused} == {"insufficient_credits"}
    with db.tx() as c:
        assert ledger.balance(c, acct, db.now()) == 0


def test_the_same_retried_request_in_parallel_charges_once(db, cfg):
    with db.tx() as c:
        acct = accounts.account_for(c, "p@example.test", START)
        ledger.grant(c, acct, "plan", 20, START)

    def reserve(_):
        with db.tx() as c:
            return ledger.reserve(c, acct, ["produce.compose"], "one-key-0001", db.now(), cfg)

    done, refused = run_parallel(6, reserve)
    assert not refused and len({r["id"] for r in done}) == 1
    with db.tx() as c:
        assert ledger.balance(c, acct, db.now()) == 15


def test_a_webhook_delivered_twice_at_once_grants_once(db, cfg):
    event = {
        "id": "evt-1",
        "type": "subscription.started",
        "email": "w@example.test",
        "plan": "pro",
        "ref": "sub_1",
        "period_start": START,
        "period_end": START + 30 * 86400,
    }

    def deliver(_):
        with db.tx() as c:
            return webhooks.apply(c, "generic", dict(event), db.now(), cfg)

    done, refused = run_parallel(5, deliver)
    assert not refused
    assert sum(1 for r in done if r.get("duplicate")) == 4
    with db.tx() as c:
        total = c.execute("SELECT COALESCE(SUM(amount),0) FROM lots").fetchone()[0]
        licences = c.execute("SELECT COUNT(*) FROM licenses").fetchone()[0]
    assert total == 300 and licences == 1


@pytest.mark.parametrize("workers", [8])
def test_many_writers_do_not_lock_each_other_out(db, cfg, workers):
    with db.tx() as c:
        acct = accounts.account_for(c, "p@example.test", START)
        ledger.grant(c, acct, "plan", 1000, START)

    def cycle(i):
        for n in range(5):
            with db.tx() as c:
                r = ledger.reserve(c, acct, ["mix.balance"], f"idem-{i}-{n:03d}-x", db.now(), cfg)
            with db.tx() as c:
                ledger.settle(c, acct, r["id"], ["mix.balance"], db.now(), cfg)

    done, refused = run_parallel(workers, cycle)
    assert not refused and len(done) == workers
    with db.tx() as c:
        assert ledger.balance(c, acct, db.now()) == 1000 - workers * 5 * 2
