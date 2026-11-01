"""Reference licence and credit service for FL MCP (HTTP API, version 1)."""

import hmac
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from flmcp_service import accounts, ledger, plans, webhooks
from flmcp_service.db import DB
from flmcp_service.errors import ApiError

MAX_WEBHOOK = 64 * 1024


@dataclass
class Settings:
    db: str = "flmcp.db"
    admin_token: str = ""  # empty: the admin API is switched off
    webhook_secrets: dict[str, str] = field(default_factory=dict)
    config: str | None = None
    topup_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        pairs = (
            p.split("=", 1) for p in os.environ.get("FLMCP_WEBHOOK_SECRETS", "").split(",") if p
        )
        return cls(
            db=os.environ.get("FLMCP_DB", "flmcp.db"),
            admin_token=os.environ.get("FLMCP_ADMIN_TOKEN", ""),
            webhook_secrets={k.strip(): v.strip() for k, v in pairs},
            config=os.environ.get("FLMCP_CONFIG") or None,
            topup_url=os.environ.get("FLMCP_TOPUP_URL", ""),
        )


class ActivateIn(BaseModel):
    key: str = Field(max_length=64)
    device_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(default="", max_length=80)
    replace: str | None = Field(default=None, max_length=64)


class ReserveIn(BaseModel):
    ops: list[str] = Field(min_length=1, max_length=8)
    idem: str = Field(min_length=8, max_length=64)


class SettleIn(BaseModel):
    done: list[str] = Field(default_factory=list, max_length=8)


class LicenseIn(BaseModel):
    email: str = Field(max_length=254)
    plan: str = Field(max_length=32)
    days: int | None = Field(default=None, ge=1, le=3660)


class GrantIn(BaseModel):
    email: str = Field(max_length=254)
    credits: int = Field(gt=0, le=1_000_000)
    days: int | None = Field(default=None, ge=1, le=3660)


def _bearer(authorization: str | None) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ApiError(401, "invalid_token", "send the device token as 'Authorization: Bearer'")
    return token.strip()


def create_app(settings: Settings | None = None, clock: Callable[[], float] = time.time) -> FastAPI:
    s = settings or Settings.from_env()
    cfg = plans.load(s.config)
    db = DB(s.db, clock)
    app = FastAPI(title="FL MCP licence service", version="1", docs_url=None, redoc_url=None)

    def problem(status: int, code: str, detail: str, **extra) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
        body = {"type": "about:blank", "status": status, "code": code, "detail": detail, **extra}
        return JSONResponse(body, status, headers, media_type="application/problem+json")

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        extra = dict(exc.extra)
        if exc.code == "insufficient_credits":
            extra["topup_url"] = s.topup_url
        return problem(exc.status, exc.code, exc.detail, **extra)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError):
        what = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()[:3]
        )
        return problem(422, "bad_request", f"invalid request ({what})")

    def admin(authorization: str | None) -> None:
        if not s.admin_token:
            raise ApiError(404, "not_found", "no such endpoint")
        if not hmac.compare_digest(_bearer(authorization).encode(), s.admin_token.encode()):
            raise ApiError(401, "invalid_token", "wrong admin token")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/v1/activate")
    def activate(body: ActivateIn):
        with db.tx() as conn:
            name = body.name.strip() or "unnamed device"
            return accounts.activate(
                conn, body.key, body.device_id, name, body.replace, db.now(), cfg
            )

    @app.get("/v1/account")
    def account(authorization: str | None = Header(None)):
        with db.tx() as conn:
            now = db.now()
            lic = accounts.authenticate(conn, _bearer(authorization), now)
            return {
                **accounts.describe(conn, lic, now, cfg),
                "devices": accounts.devices(conn, lic["id"]),
                "topup_url": s.topup_url,
            }

    @app.post("/v1/credits/reserve")
    def reserve(body: ReserveIn, authorization: str | None = Header(None)):
        with db.tx() as conn:
            now = db.now()
            lic = accounts.authenticate(conn, _bearer(authorization), now)
            return ledger.reserve(conn, lic["account_id"], body.ops, body.idem, now, cfg)

    @app.post("/v1/credits/{rid}/settle")
    def settle(rid: str, body: SettleIn, authorization: str | None = Header(None)):
        with db.tx() as conn:
            now = db.now()
            lic = accounts.authenticate(conn, _bearer(authorization), now)
            return ledger.settle(conn, lic["account_id"], rid, body.done, now, cfg)

    @app.post("/v1/devices/self/revoke", status_code=204)
    def revoke(authorization: str | None = Header(None)):
        with db.tx() as conn:
            accounts.revoke_self(
                conn, accounts.authenticate(conn, _bearer(authorization), db.now())
            )

    @app.post("/v1/webhooks/{provider}")
    async def webhook(provider: str, request: Request):
        secret = s.webhook_secrets.get(provider)
        if secret is None:
            raise ApiError(404, "unknown_provider", "no such provider")
        if int(request.headers.get("content-length") or 0) > MAX_WEBHOOK:
            raise ApiError(413, "too_large", "event too large")
        body = await request.body()
        if len(body) > MAX_WEBHOOK:
            raise ApiError(413, "too_large", "event too large")
        if not webhooks.verify(secret, request.headers.get("x-flmcp-signature"), body, db.now()):
            raise ApiError(401, "bad_signature", "signature missing, wrong or too old")
        try:
            event = json.loads(body)
        except ValueError:
            event = None
        if not isinstance(event, dict):
            raise ApiError(400, "bad_request", "the body must be a JSON object")

        def run():
            with db.tx() as conn:
                return webhooks.apply(conn, provider, event, db.now(), cfg)

        return await run_in_threadpool(run)

    @app.post("/v1/admin/licenses")
    def new_license(body: LicenseIn, authorization: str | None = Header(None)):
        admin(authorization)
        with db.tx() as conn:
            now = db.now()
            aid = accounts.account_for(conn, body.email, now)
            end = now + body.days * 86400 if body.days else None
            lid, key = accounts.create_license(conn, aid, body.plan, now, cfg, end)
            row = conn.execute("SELECT * FROM licenses WHERE id=?", (lid,)).fetchone()
            accounts.start_period(conn, row, now, row["period_end"], now, cfg)
            return {"license": lid, "key": key, "plan": body.plan, "period_end": row["period_end"]}

    @app.post("/v1/admin/grants")
    def grant(body: GrantIn, authorization: str | None = Header(None)):
        admin(authorization)
        with db.tx() as conn:
            now = db.now()
            aid = accounts.account_for(conn, body.email, now)
            end = now + body.days * 86400 if body.days else None
            ledger.grant(conn, aid, "adjust", body.credits, now, end)
            return {"balance": ledger.balance(conn, aid, now)}

    @app.get("/v1/admin/accounts")
    def lookup(email: str, authorization: str | None = Header(None)):
        admin(authorization)
        with db.tx() as conn:
            now = db.now()
            row = conn.execute(
                "SELECT id FROM accounts WHERE email=?", (email.strip().lower(),)
            ).fetchone()
            if row is None:
                raise ApiError(404, "not_found", "no such account")
            ledger.sweep(conn, row["id"], now, cfg)
            lics = conn.execute(
                "SELECT id, plan, status, period_end FROM licenses WHERE account_id=?", (row["id"],)
            ).fetchall()
            return {
                "balance": ledger.balance(conn, row["id"], now),
                "licenses": [dict(r) for r in lics],
                "entries": ledger.history(conn, row["id"]),
            }

    return app
