"""Accounts, licences and device activations."""

import hashlib
import json
import secrets
import sqlite3
import uuid

from fl_mcp.commerce import keys
from flmcp_service import ledger
from flmcp_service.errors import ApiError
from flmcp_service.plans import Config, require_usable


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _spec(cfg: Config, plan: str):
    if plan not in cfg.plans:  # the plan table changed after the licence was issued
        raise ApiError(409, "unknown_plan", f"plan '{plan}' is no longer offered; contact support")
    return cfg.plans[plan]


def account_for(conn: sqlite3.Connection, email: str, now: int) -> str:
    email = email.strip().lower()
    if "@" not in email:
        raise ApiError(400, "bad_request", "a valid email is required")
    row = conn.execute("SELECT id FROM accounts WHERE email=?", (email,)).fetchone()
    if row:
        return row["id"]
    conn.execute(
        "INSERT INTO accounts(id, email, created_at) VALUES (?,?,?)",
        (aid := uuid.uuid4().hex, email, now),
    )
    return aid


def create_license(
    conn: sqlite3.Connection,
    account_id: str,
    plan: str,
    now: int,
    cfg: Config,
    period_end: int | None = None,
    provider: str = "",
    ref: str = "",
    renews: bool = False,
) -> tuple[str, str]:
    """Returns (licence id, the licence key); the key is stored hashed and cannot be shown again.
    A licence with an end date that does not renew (a trial, a hand-issued one) is `canceled`,
    which means exactly that: valid until period_end, no grace period."""
    if plan not in cfg.plans:
        raise ApiError(400, "unknown_plan", f"unknown plan '{plan}'")
    spec = cfg.plans[plan]
    if period_end is None and spec.days:
        period_end = now + spec.days * 86400
    key = keys.new_key()
    lid = uuid.uuid4().hex
    status = "active" if renews or period_end is None else "canceled"
    conn.execute(
        "INSERT INTO licenses(id, account_id, key_hash, plan, status, period_end, provider,"
        " provider_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (lid, account_id, keys.hash_key(key), plan, status, period_end, provider, ref, now),
    )
    return lid, key


def start_period(
    conn: sqlite3.Connection,
    lic: sqlite3.Row | dict,
    period_start: int,
    period_end: int | None,
    now: int,
    cfg: Config,
) -> bool:
    """Give a licence its allowance for one billing period, once however often it is called."""
    spec = _spec(cfg, lic["plan"])
    source = f"plan:{lic['id']}:{period_start}"
    return ledger.grant(conn, lic["account_id"], "plan", spec.credits, now, period_end, source)


def outbox_add(conn: sqlite3.Connection, account_id: str, kind: str, payload: dict, now: int):
    conn.execute(
        "INSERT INTO outbox(account_id, kind, payload, created_at) VALUES (?,?,?,?)",
        (account_id, kind, json.dumps(payload), now),
    )


def activate(
    conn: sqlite3.Connection,
    key: str,
    device_id: str,
    name: str,
    replace: str | None,
    now: int,
    cfg: Config,
) -> dict:
    digest = keys.hash_key(key)
    if digest is None:
        raise ApiError(400, "bad_key", "that is not a licence key; check it for typos")
    lic = conn.execute("SELECT * FROM licenses WHERE key_hash=?", (digest,)).fetchone()
    if lic is None:
        raise ApiError(404, "unknown_key", "licence key not found")
    require_usable(lic["status"], lic["period_end"], now)
    limit = _spec(cfg, lic["plan"]).devices
    mine = conn.execute(
        "SELECT * FROM devices WHERE license_id=? AND device_id=?", (lic["id"], device_id)
    ).fetchone()
    others = [
        d
        for d in conn.execute(
            "SELECT * FROM devices WHERE license_id=? AND revoked=0 ORDER BY last_seen",
            (lic["id"],),
        )
        if d["device_id"] != device_id
    ]
    if (mine is None or mine["revoked"]) and len(others) >= limit:
        victims = [
            d
            for d in others
            if replace
            and (d["name"] == replace or (len(replace) >= 4 and d["device_id"].startswith(replace)))
        ]
        if len(victims) > 1:
            raise ApiError(409, "ambiguous_replace", "more than one device matches; use its id")
        if victims:
            conn.execute("UPDATE devices SET revoked=1 WHERE pk=?", (victims[0]["pk"],))
            others.remove(victims[0])
        if len(others) >= limit:
            raise ApiError(
                409,
                "device_limit",
                f"this licence allows {limit} device(s); sign one out or pass replace",
                devices=[
                    {"id": d["device_id"][:8], "name": d["name"], "last_seen": d["last_seen"]}
                    for d in others
                ],
            )
    token = secrets.token_urlsafe(32)
    if mine is None:
        conn.execute(
            "INSERT INTO devices(license_id, device_id, name, token_hash, created_at, last_seen)"
            " VALUES (?,?,?,?,?,?)",
            (lic["id"], device_id, name, _hash(token), now, now),
        )
    else:
        conn.execute(
            "UPDATE devices SET name=?, token_hash=?, last_seen=?, revoked=0 WHERE pk=?",
            (name, _hash(token), now, mine["pk"]),
        )
    return {"token": token, **describe(conn, lic, now, cfg)}


def describe(conn: sqlite3.Connection, lic: sqlite3.Row, now: int, cfg: Config) -> dict:
    ledger.sweep(conn, lic["account_id"], now, cfg)
    return {
        "plan": lic["plan"],
        "status": lic["status"],
        "period_end": lic["period_end"],
        "balance": ledger.balance(conn, lic["account_id"], now),
        "costs": cfg.costs,
    }


def authenticate(conn: sqlite3.Connection, token: str, now: int) -> sqlite3.Row:
    """The licence a device token belongs to (with the device row's pk as `device_pk`)."""
    row = conn.execute(
        "SELECT l.*, d.pk AS device_pk, d.device_id FROM devices d"
        " JOIN licenses l ON l.id = d.license_id WHERE d.token_hash=? AND d.revoked=0",
        (_hash(token),),
    ).fetchone()
    if row is None:
        raise ApiError(401, "invalid_token", "this device is not activated; sign in again")
    require_usable(row["status"], row["period_end"], now)
    conn.execute("UPDATE devices SET last_seen=? WHERE pk=?", (now, row["device_pk"]))
    return row


def revoke_self(conn: sqlite3.Connection, lic: sqlite3.Row) -> None:
    conn.execute("UPDATE devices SET revoked=1 WHERE pk=?", (lic["device_pk"],))


def devices(conn: sqlite3.Connection, license_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT device_id, name, last_seen FROM devices WHERE license_id=? AND revoked=0"
        " ORDER BY last_seen DESC",
        (license_id,),
    ).fetchall()
    return [
        {"id": r["device_id"][:8], "name": r["name"], "last_seen": r["last_seen"]} for r in rows
    ]
