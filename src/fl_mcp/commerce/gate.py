"""Licence gate for the metered tools.

A build without a licence service (the default) meters nothing. With one, the metered tools
reserve credits before they work and settle afterwards: the customer pays for what was delivered,
and a hold nobody settles returns by itself. All other tools never touch the network.
"""

import json
import logging
import os
import socket
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

import anyio

from fl_mcp import __version__
from fl_mcp.commerce import keys
from fl_mcp.paths import data_dir

log = logging.getLogger("fl_mcp")

Post = Callable[[str, str, "dict | None", "str | None"], "tuple[int, dict]"]
LOCAL = ("localhost", "127.0.0.1", "::1")


class LicenseError(Exception):
    """Expected failure; the message is meant for the model and the user."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a token must never follow a redirect to another host


def service_url() -> str:
    env = os.environ.get("FL_MCP_SERVICE")
    if env is not None:
        return env.strip()
    try:
        raw = resources.files("fl_mcp.commerce").joinpath("issuer.json").read_text("utf-8")
        return str(json.loads(raw).get("service", "")).strip()
    except (OSError, ValueError):
        return ""


def _transport(base: str, timeout: float = 10.0) -> Post:
    host = urlsplit(base)
    if host.scheme != "https" and host.hostname not in LOCAL:
        raise LicenseError("the licence service must be an https address")
    opener = urllib.request.build_opener(_NoRedirect)

    def post(method: str, path: str, body: dict | None, token: str | None) -> tuple[int, dict]:
        headers = {"Accept": "application/json", "User-Agent": f"fl-mcp/{__version__}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + path, data, headers, method=method)
        try:
            with opener.open(req, timeout=timeout) as res:
                raw, code = res.read(1_000_000), res.status
        except urllib.error.HTTPError as exc:
            raw, code = exc.read(1_000_000), exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise LicenseError(
                f"Could not reach the licence service ({host.netloc}): {reason}. Tools that use "
                "credits need a connection; everything else works offline."
            ) from None
        try:
            return code, json.loads(raw) if raw else {}
        except ValueError:
            return code, {}

    return post


def _fail(status: int, data: dict) -> NoReturn:
    code = data.get("code")
    if code == "insufficient_credits":
        msg = f"Not enough credits: this needs {data.get('cost')}, you have {data.get('balance')}."
        if data.get("topup_url"):
            msg += f" Top up at {data['topup_url']}"
    elif code == "license_inactive":
        msg = f"Your subscription is {data.get('subscription', 'inactive')}; renew it to continue."
    elif status == 401:
        msg = "This computer is not signed in. Run `fl-mcp login <licence key>` in a terminal."
    elif code == "device_limit":
        names = ", ".join(d.get("name", "?") for d in data.get("devices", []))
        msg = (
            f"{data.get('detail')} In use: {names}. Free a seat with `fl-mcp login <key> "
            "--replace <device name>`."
        )
    else:
        msg = data.get("detail") or f"the licence service answered {status}"
    raise LicenseError(msg)


class Gate:
    def __init__(
        self, service: str | None = None, post: Post | None = None, state: Path | None = None
    ) -> None:
        self.service = (service_url() if service is None else service).strip().rstrip("/")
        self._post = post
        self._state = state

    @property
    def metered(self) -> bool:
        return bool(self.service)

    # -- local state: device id and token, readable only by the user

    def _path(self) -> Path:
        return self._state or data_dir() / "account.json"

    def _load(self) -> dict[str, Any]:
        try:
            st = json.loads(self._path().read_text("utf-8"))
        except (OSError, ValueError):
            st = {}
        if not isinstance(st, dict):
            st = {}
        if st.get("service") != self.service:
            st = {"device_id": st.get("device_id")}  # a token is only good for its own service
        st.setdefault("device_id", None)
        if not st["device_id"]:
            st["device_id"] = uuid.uuid4().hex
        st["service"] = self.service
        return st

    def _save(self, st: dict[str, Any]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, path)

    def _send(
        self, method: str, path: str, body: dict | None = None, token: str | None = None
    ) -> tuple[int, dict]:
        if self._post is None:
            self._post = _transport(self.service)
        return self._post(method, path, body, token)

    # -- sign in / out

    def login(self, key: str, name: str | None = None, replace: str | None = None) -> dict:
        if not self.metered:
            raise LicenseError("This build has no licence service; nothing needs a licence.")
        shown = keys.display(key)
        if shown is None:
            raise LicenseError(
                "That is not a licence key (FLMCP-XXXXX-XXXXX-XXXXX-XXXXX); check for typos."
            )
        st = self._load()
        body: dict[str, Any] = {
            "key": shown,
            "device_id": st["device_id"],
            "name": (name or socket.gethostname() or "this computer")[:80],
        }
        if replace:
            body["replace"] = replace
        status, data = self._send("POST", "/v1/activate", body)
        if status != 200:
            _fail(status, data)
        st.update(token=data["token"], plan=data["plan"], balance=data["balance"])
        self._save(st)
        return {"plan": data["plan"], "credits": data["balance"], "until": data["period_end"]}

    def logout(self) -> None:
        st = self._load()
        token = st.pop("token", None)
        if token and self.metered:
            try:
                self._send("POST", "/v1/devices/self/revoke", None, token)
            except LicenseError:
                pass  # signing out must work offline too; the seat can be freed with --replace
        for field in ("plan", "balance"):
            st.pop(field, None)
        self._save(st)

    def _token(self) -> str:
        st = self._load()
        if st.get("token"):
            return st["token"]
        env = os.environ.get("FL_MCP_LICENSE", "").strip()
        if env:  # the desktop extension passes the key it asked for at install time
            self.login(env)
            return self._load()["token"]
        raise LicenseError(
            "This needs a licence. Run `fl-mcp login <licence key>` in a terminal, then try "
            "again. Tools that do not use credits keep working."
        )

    def _remember(self, balance: int) -> None:
        st = self._load()
        if st.get("token"):
            st["balance"] = balance
            self._save(st)

    def status(self) -> dict:
        """Plan and credits for fl_status and `fl-mcp account`; never raises."""
        if not self.metered:
            return {"metered": False}
        try:
            token = self._token()
            code, data = self._send("GET", "/v1/account", None, token)
            if code != 200:
                _fail(code, data)
        except LicenseError as exc:
            st = self._load()
            out: dict[str, Any] = {"signed_in": bool(st.get("token")), "note": str(exc)}
            if st.get("token") and st.get("balance") is not None:
                out.update(plan=st.get("plan"), credits=st["balance"], cached=True)
            return out
        self._remember(data["balance"])
        return {
            "plan": data["plan"],
            "credits": data["balance"],
            "until": data["period_end"],
            "costs": data["costs"],
            "devices": [d["name"] for d in data["devices"]],
        }

    # -- spending

    def reserve(self, ops: tuple[str, ...]) -> dict:
        token = self._token()
        idem = uuid.uuid4().hex  # the same key on the retry: a lost answer never charges twice
        for attempt in range(2):
            try:
                status, data = self._send(
                    "POST", "/v1/credits/reserve", {"ops": list(ops), "idem": idem}, token
                )
                break
            except LicenseError:
                if attempt:
                    raise
        if status != 200:
            _fail(status, data)
        if data.get("state") != "held":
            raise LicenseError("The licence service did not accept the request; try again.")
        self._remember(data["balance"])
        return data

    def settle(self, rid: str, done: list[str]) -> dict:
        token = self._token()
        for attempt in range(3):
            try:
                status, data = self._send(
                    "POST", f"/v1/credits/{rid}/settle", {"done": done}, token
                )
                break
            except LicenseError:
                if attempt == 2:
                    raise
        if status != 200:
            _fail(status, data)
        self._remember(data["balance"])
        return data

    def spend(self, *ops: str) -> "Spend":
        return Spend(self, ops)


class Spend:
    """`async with gate.spend(op, ...) as bill:` then bill.ok(op) for each one delivered."""

    def __init__(self, gate: Gate, ops: tuple[str, ...]) -> None:
        self.gate, self.ops, self.done, self.id = gate, ops, [], None

    def ok(self, op: str) -> None:
        if op in self.ops and op not in self.done:
            self.done.append(op)

    async def __aenter__(self) -> "Spend":
        if self.ops and self.gate.metered:
            self.id = (await anyio.to_thread.run_sync(self.gate.reserve, self.ops))["id"]
        return self

    async def __aexit__(self, *exc: object) -> bool:
        if self.id is not None:
            with anyio.CancelScope(shield=True):  # a cancelled call still settles what it did
                try:
                    await anyio.to_thread.run_sync(self.gate.settle, self.id, list(self.done))
                except LicenseError as err:
                    log.warning("could not settle credits (the hold will return them): %s", err)
        return False
