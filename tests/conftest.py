import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("FL_MCP_DESKTOP", "0")

from tests.fakefl import FakeFL, LoopbackBridge, load_script  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_menu(monkeypatch):
    from fl_mcp import popupkeys

    monkeypatch.setattr(popupkeys, "window", lambda: 0)


@pytest.fixture
def fl(monkeypatch):
    fake = FakeFL()
    fake.install(monkeypatch)
    script = load_script()
    bridge = LoopbackBridge(script, fake)
    return SimpleNamespace(fake=fake, script=script, bridge=bridge, call=bridge.call)


@pytest.fixture
def anyio_backend():
    return "asyncio"
