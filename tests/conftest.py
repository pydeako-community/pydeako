"""Fixtures for the tests that run the client against the fake bridge."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from zeroconf import Zeroconf

from pydeako.deako import Deako
from pydeako.discover import _discover

from .fake_bridge import FakeDeakoConnect, default_devices


class _NoZeroconf(Zeroconf):
    """Stands in for the zeroconf DeakoDiscoverer would start by itself."""

    # pylint: disable-next=super-init-not-called
    def __init__(self) -> None:
        raise AssertionError("tests must pass their own loopback zeroconf")


@pytest.fixture(autouse=True)
def _no_zeroconf_on_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that lets DeakoDiscoverer start its own zeroconf."""
    monkeypatch.setattr(_discover, "Zeroconf", _NoZeroconf)


@pytest_asyncio.fixture(name="bridge")
async def fixture_bridge() -> AsyncIterator[FakeDeakoConnect]:
    """Start a fake bridge with three devices."""
    fake = FakeDeakoConnect(default_devices())
    await fake.start()
    yield fake
    await fake.stop()


@pytest_asyncio.fixture(name="client")
async def fixture_client(
    bridge: FakeDeakoConnect,
) -> AsyncIterator[Deako]:
    """Connect a Deako client to the fake bridge and find its devices."""
    deako = Deako(bridge.get_address, client_name="tests")
    await deako.connect()
    await deako.find_devices()
    assert len(deako.get_devices()) == len(bridge.devices)
    yield deako
    await deako.disconnect()
