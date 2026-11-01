"""Billing events. The payment provider (a merchant of record such as Paddle or Lemon Squeezy, or
Stripe) is reached through a thin adapter that turns its payloads into the normalized events below
and signs them like this module expects; see docs/COMMERCE.md.

  subscription.started       id, email, plan, ref, period_start, period_end
  subscription.renewed       id, ref, period_start, period_end
  subscription.plan_changed  id, ref, plan
  subscription.canceled      id, ref            (works until period_end)
  subscription.past_due      id, ref
  subscription.expired       id, ref
  topup.paid                 id, email, pack
"""

import hashlib
import hmac
import sqlite3
from collections.abc import Callable

from flmcp_service import accounts, ledger
from flmcp_service.errors import ApiError
from flmcp_service.plans import Config

TOLERANCE = 300


def sign(secret: str, body: bytes, t: int) -> str:
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, header: str | None, body: bytes, now: int) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in (header or "").split(","))
        t = int(parts["t"])
        got = parts["v1"]
    except (KeyError, ValueError):
        return False
    good = sign(secret, body, t).split("v1=")[1]
    return abs(now - t) <= TOLERANCE and hmac.compare_digest(good.encode(), got.encode())


def _text(ev: dict, field: str) -> str:
    value = ev.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "bad_request", f"'{field}' is required")
    return value.strip()


def _int(ev: dict, field: str, optional: bool = False) -> int | None:
    value = ev.get(field)
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiError(400, "bad_request", f"'{field}' must be a unix time in seconds")
    return value


def _licence(conn: sqlite3.Connection, provider: str, ev: dict) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM licenses WHERE provider=? AND provider_ref=?", (provider, _text(ev, "ref"))
    ).fetchone()
    if row is None:  # events can arrive out of order: a non-2xx answer makes the provider retry
        raise ApiError(409, "unknown_subscription", "no such subscription yet; retry later")
    return row


def _started(conn, provider, ev, now, cfg):
    start, end = _int(ev, "period_start"), _int(ev, "period_end")
    row = conn.execute(
        "SELECT * FROM licenses WHERE provider=? AND provider_ref=?", (provider, _text(ev, "ref"))
    ).fetchone()
    if row is None:
        aid = accounts.account_for(conn, _text(ev, "email"), now)
        lid, key = accounts.create_license(
            conn, aid, _text(ev, "plan"), now, cfg, end, provider, _text(ev, "ref"), renews=True
        )
        info = {"email": _text(ev, "email"), "plan": _text(ev, "plan"), "key": key}
        accounts.outbox_add(conn, aid, "license_key", info, now)
        row = conn.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
    accounts.start_period(conn, row, start, end, now, cfg)
    return {"license": row["id"]}


def _renewed(conn, provider, ev, now, cfg):
    row = _licence(conn, provider, ev)
    start, end = _int(ev, "period_start"), _int(ev, "period_end")
    conn.execute("UPDATE licenses SET status='active', period_end=? WHERE id=?", (end, row["id"]))
    accounts.start_period(conn, row, start, end, now, cfg)
    return {"license": row["id"]}


def _plan_changed(conn, provider, ev, now, cfg):
    row = _licence(conn, provider, ev)
    plan = _text(ev, "plan")
    if plan not in cfg.plans:
        raise ApiError(400, "unknown_plan", f"unknown plan '{plan}'")
    conn.execute("UPDATE licenses SET plan=? WHERE id=?", (plan, row["id"]))
    return {"license": row["id"]}


def _status(status: str):
    def run(conn, provider, ev, now, cfg):
        row = _licence(conn, provider, ev)
        conn.execute("UPDATE licenses SET status=? WHERE id=?", (status, row["id"]))
        return {"license": row["id"]}

    return run


def _topup(conn, provider, ev, now, cfg):
    pack = _text(ev, "pack")
    if pack not in cfg.packs:
        raise ApiError(400, "unknown_pack", f"unknown pack '{pack}'")
    aid = accounts.account_for(conn, _text(ev, "email"), now)
    expires = now + cfg.topup_days * 86400 if cfg.topup_days else None
    ledger.grant(
        conn, aid, "topup", cfg.packs[pack], now, expires, f"topup:{provider}:{_text(ev, 'id')}"
    )
    return {"account": aid}


HANDLERS: dict[str, Callable] = {
    "subscription.started": _started,
    "subscription.renewed": _renewed,
    "subscription.plan_changed": _plan_changed,
    "subscription.canceled": _status("canceled"),
    "subscription.past_due": _status("past_due"),
    "subscription.expired": _status("revoked"),
    "topup.paid": _topup,
}


def apply(conn: sqlite3.Connection, provider: str, ev: dict, now: int, cfg: Config) -> dict:
    """Apply one event exactly once; a repeated event id is acknowledged and ignored."""
    event_id = _text(ev, "id")
    handler = HANDLERS.get(_text(ev, "type"))
    if handler is None:
        raise ApiError(400, "unknown_event", f"unknown event type '{ev.get('type')}'")
    fresh = conn.execute(
        "INSERT OR IGNORE INTO webhook_events(provider, event_id, received_at) VALUES (?,?,?)",
        (provider, event_id, now),
    )
    if not fresh.rowcount:
        return {"duplicate": True}
    return handler(conn, provider, ev, now, cfg)
