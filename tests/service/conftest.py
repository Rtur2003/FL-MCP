from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from flmcp_service import plans  # noqa: E402
from flmcp_service.app import Settings, create_app  # noqa: E402
from flmcp_service.db import DB  # noqa: E402

START = 1_800_000_000
SECRET = "whsec-test"


class Clock:
    def __init__(self) -> None:
        self.t = START

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: int) -> None:
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cfg():
    return plans.load()


@pytest.fixture
def db(tmp_path, clock):
    return DB(str(tmp_path / "ledger.db"), clock)


@pytest.fixture
def svc(tmp_path, clock):
    settings = Settings(
        db=str(tmp_path / "svc.db"),
        admin_token="admin-secret",
        webhook_secrets={"generic": SECRET},
        topup_url="https://example.test/topup",
    )
    app = create_app(settings, clock)
    return SimpleNamespace(
        app=app,
        http=TestClient(app),
        clock=clock,
        settings=settings,
        db=DB(settings.db, clock),
        cfg=plans.load(),
    )


def wire(svc):
    """A Gate transport that talks to the in-process service instead of the network."""

    def post(method, path, body, token):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        res = svc.http.request(method, path, json=body, headers=headers)
        return res.status_code, (res.json() if res.content else {})

    return post


@pytest.fixture
def issue(svc):
    def make(email="a@example.test", plan="pro", days=None):
        body = {"email": email, "plan": plan, **({"days": days} if days else {})}
        res = svc.http.post(
            "/v1/admin/licenses", json=body, headers={"Authorization": "Bearer admin-secret"}
        )
        assert res.status_code == 200, res.text
        return res.json()["key"]

    return make


@pytest.fixture
def gate(svc, tmp_path):
    from fl_mcp.commerce import Gate

    return Gate("https://licence.example.test", wire(svc), tmp_path / "account.json")
