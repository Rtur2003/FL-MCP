"""Credit ledger.

Credits live in lots (a monthly allowance, a purchased pack, a refund...). A lot that expires is
used first. reserve() debits the lots straight away and remembers where the credits came from;
settle() keeps the price of the work that was delivered and returns the rest to the same lots; a
reservation nobody settles is returned in full when its hold runs out. Every movement is also
written to `entries`, so the balance is always the sum of the entries.
"""

import json
import sqlite3
import uuid

from flmcp_service.errors import ApiError
from flmcp_service.plans import Config


def _new_lot(
    conn: sqlite3.Connection,
    account_id: str,
    kind: str,
    amount: int,
    now: int,
    expires_at: int | None,
    source: str | None,
) -> int | None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO lots(account_id, kind, amount, remaining, expires_at, source,"
        " created_at) VALUES (?,?,?,?,?,?,?)",
        (account_id, kind, amount, amount, expires_at, source, now),
    )
    return cur.lastrowid if cur.rowcount else None


def _entry(conn: sqlite3.Connection, account_id: str, now: int, kind: str, amount: int, ref: str):
    conn.execute(
        "INSERT INTO entries(account_id, ts, kind, amount, ref) VALUES (?,?,?,?,?)",
        (account_id, now, kind, amount, ref),
    )


def grant(
    conn: sqlite3.Connection,
    account_id: str,
    kind: str,
    amount: int,
    now: int,
    expires_at: int | None = None,
    source: str | None = None,
) -> bool:
    """Add credits. A source (e.g. 'plan:<licence>:<period>') makes the grant happen only once."""
    if amount <= 0 or (expires_at is not None and expires_at <= now):
        return False
    lot = _new_lot(conn, account_id, kind, amount, now, expires_at, source)
    if lot is None:
        return False
    _entry(conn, account_id, now, "grant", amount, f"lot:{lot}")
    return True


def balance(conn: sqlite3.Connection, account_id: str, now: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(remaining), 0) FROM lots"
        " WHERE account_id=? AND (expires_at IS NULL OR expires_at>?)",
        (account_id, now),
    ).fetchone()[0]


def _give_back(conn: sqlite3.Connection, res: sqlite3.Row, amount: int, now: int, cfg: Config):
    """Return `amount` of a reservation to the lots it came from, the last one taken first."""
    left = amount
    for lot_id, taken in reversed(json.loads(res["alloc"])):
        if left <= 0:
            break
        part = min(taken, left)
        lot = conn.execute("SELECT expires_at FROM lots WHERE id=?", (lot_id,)).fetchone()
        if lot["expires_at"] is None or lot["expires_at"] > now:
            conn.execute("UPDATE lots SET remaining = remaining + ? WHERE id=?", (part, lot_id))
        else:  # that lot has expired meanwhile: the customer still gets the credits back
            expires = now + cfg.refund_days * 86400
            _new_lot(conn, res["account_id"], "refund", part, now, expires, None)
        left -= part
    _entry(conn, res["account_id"], now, "release", amount, f"res:{res['id']}")


def sweep(conn: sqlite3.Connection, account_id: str, now: int, cfg: Config) -> None:
    """Return holds that ran out and drop lots that expired."""
    stale = conn.execute(
        "SELECT * FROM reservations WHERE account_id=? AND state='held' AND expires_at<=?",
        (account_id, now),
    ).fetchall()
    for res in stale:
        _give_back(conn, res, res["cost"], now, cfg)
        conn.execute(
            "UPDATE reservations SET state='expired', settled_at=? WHERE id=?", (now, res["id"])
        )
    old = conn.execute(
        "SELECT id, remaining FROM lots WHERE account_id=? AND remaining>0"
        " AND expires_at IS NOT NULL AND expires_at<=?",
        (account_id, now),
    ).fetchall()
    for lot in old:
        conn.execute("UPDATE lots SET remaining=0 WHERE id=?", (lot["id"],))
        _entry(conn, account_id, now, "expire", -lot["remaining"], f"lot:{lot['id']}")


def _view(conn: sqlite3.Connection, res: sqlite3.Row, now: int) -> dict:
    out = {
        "id": res["id"],
        "state": res["state"],
        "cost": res["cost"],
        "balance": balance(conn, res["account_id"], now),
    }
    if res["state"] == "held":
        out["expires_at"] = res["expires_at"]
    if res["state"] == "settled":
        out["charged"] = res["charged"]
        out["refunded"] = res["cost"] - res["charged"]
    return out


def reserve(
    conn: sqlite3.Connection,
    account_id: str,
    ops: list[str],
    idem: str,
    now: int,
    cfg: Config,
) -> dict:
    prices = {}
    for op in ops:
        if op not in cfg.costs:
            raise ApiError(400, "unknown_op", f"unknown operation '{op}'")
        prices[op] = cfg.costs[op]
    if not prices or len(prices) != len(ops):
        raise ApiError(400, "bad_request", "ops must be a non-empty list of distinct names")
    sweep(conn, account_id, now, cfg)
    again = conn.execute(
        "SELECT * FROM reservations WHERE account_id=? AND idem=?", (account_id, idem)
    ).fetchone()
    if again is not None:  # a retried request: same answer, nothing is charged twice
        if set(json.loads(again["ops"])) != set(ops):
            raise ApiError(409, "idempotency_conflict", "that key was used for other operations")
        return _view(conn, again, now)
    cost = sum(prices.values())
    have = balance(conn, account_id, now)
    if have < cost:
        raise ApiError(
            402, "insufficient_credits", f"needs {cost} credits, balance is {have}",
            cost=cost, balance=have,
        )  # fmt: skip
    alloc, left = [], cost
    lots = conn.execute(
        "SELECT id, remaining FROM lots WHERE account_id=? AND remaining>0"
        " AND (expires_at IS NULL OR expires_at>?) ORDER BY expires_at IS NULL, expires_at, id",
        (account_id, now),
    ).fetchall()
    for lot in lots:
        part = min(lot["remaining"], left)
        conn.execute("UPDATE lots SET remaining = remaining - ? WHERE id=?", (part, lot["id"]))
        alloc.append([lot["id"], part])
        left -= part
        if not left:
            break
    rid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO reservations(id, account_id, idem, state, ops, cost, alloc, created_at,"
        " expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (rid, account_id, idem, "held", json.dumps(prices), cost, json.dumps(alloc), now,
         now + cfg.hold_seconds),
    )  # fmt: skip
    _entry(conn, account_id, now, "hold", -cost, f"res:{rid}")
    return {
        "id": rid,
        "state": "held",
        "cost": cost,
        "balance": have - cost,
        "expires_at": now + cfg.hold_seconds,
    }


def settle(
    conn: sqlite3.Connection, account_id: str, rid: str, done: list[str], now: int, cfg: Config
) -> dict:
    """Charge for the operations in `done` (those that were delivered) and return the rest."""
    sweep(conn, account_id, now, cfg)
    res = conn.execute(
        "SELECT * FROM reservations WHERE id=? AND account_id=?", (rid, account_id)
    ).fetchone()
    if res is None:
        raise ApiError(404, "unknown_reservation", "no such reservation")
    prices = json.loads(res["ops"])
    if len(set(done)) != len(done) or not set(done) <= set(prices):
        raise ApiError(400, "bad_request", "done must be distinct operations that were reserved")
    if res["state"] == "expired":
        raise ApiError(409, "reservation_expired", "the hold timed out; the credits came back")
    if res["state"] == "settled":
        if sorted(json.loads(res["done"])) != sorted(done):
            raise ApiError(409, "already_settled", "this reservation was settled differently")
        return _view(conn, res, now)
    charge = sum(prices[op] for op in done)
    if res["cost"] - charge:
        _give_back(conn, res, res["cost"] - charge, now, cfg)
    conn.execute(
        "UPDATE reservations SET state='settled', done=?, charged=?, settled_at=? WHERE id=?",
        (json.dumps(done), charge, now, rid),
    )
    res = conn.execute("SELECT * FROM reservations WHERE id=?", (rid,)).fetchone()
    return _view(conn, res, now)


def history(conn: sqlite3.Connection, account_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT ts, kind, amount, ref FROM entries WHERE account_id=? ORDER BY id DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
