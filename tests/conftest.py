from types import SimpleNamespace

import pytest

from tests.fakefl import FakeFL, LoopbackBridge, load_script


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
