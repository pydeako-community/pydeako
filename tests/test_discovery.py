"""
Test discovery with a real zeroconf instance and the fake bridge.

Zeroconf listens on loopback only, and the bridge's mDNS records are put
straight into its cache instead of being announced. A test-only service
type keeps any real bridge on the network out of the tests.
"""

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from zeroconf import ServiceInfo, Zeroconf

# zeroconf keeps this name importable but leaves it out of __all__.
from zeroconf import DNSIncoming  # type: ignore[attr-defined] # isort: skip
from zeroconf.asyncio import AsyncZeroconf

from pydeako import Deako, DeakoDiscoverer, DevicesNotFoundError
from pydeako.discover import _discover

from .fake_bridge import FakeDeakoConnect

TEST_TYPE = "_deako-test._tcp.local."
SERVICE = f"Fake Bridge.{TEST_TYPE}"
WAIT_S = 5


def _announce(zeroconf: Zeroconf, port: int, ttl: int = 120) -> None:
    """Hand zeroconf the records a bridge on this port would announce."""
    info = ServiceInfo(
        TEST_TYPE,
        SERVICE,
        addresses=[socket.inet_aton("127.0.0.1")],
        port=port,
        server="fake-bridge.local.",
    )
    for packet in zeroconf.generate_service_broadcast(info, ttl).packets():
        zeroconf.record_manager.async_updates_from_response(
            DNSIncoming(packet)
        )


async def _wait_for_addresses(
    discoverer: DeakoDiscoverer,
    expected: list[str],
) -> None:
    """Wait until the browser thread has put these addresses in the pool."""
    for _ in range(int(WAIT_S / 0.01)):
        if list(discoverer.address_pool.addresses) == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"pool is {list(discoverer.address_pool.addresses)}, not {expected}"
    )


@pytest_asyncio.fixture(name="zeroconf")
async def fixture_zeroconf(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Zeroconf]:
    """Start zeroconf on loopback, browsing for the test-only type."""
    monkeypatch.setattr(_discover, "DEAKO_TYPE", TEST_TYPE)
    monkeypatch.setattr(_discover, "POLLING_INTERVAL_S", 0.01)
    monkeypatch.setattr(_discover, "TIMEOUT_S", 0.05)
    aiozc = AsyncZeroconf(interfaces=["127.0.0.1"])
    await aiozc.zeroconf.async_wait_for_start()
    yield aiozc.zeroconf
    await aiozc.async_close()


@pytest_asyncio.fixture(name="discoverer")
async def fixture_discoverer(
    zeroconf: Zeroconf,
) -> AsyncIterator[DeakoDiscoverer]:
    """Browse with the library's discoverer; stop its thread afterwards."""
    discoverer = DeakoDiscoverer(zeroconf)
    yield discoverer
    # cancel() joins the browser thread, which may be waiting for this loop.
    await asyncio.to_thread(discoverer.cancel)


@pytest.mark.asyncio
async def test_no_bridge_raises(discoverer: DeakoDiscoverer) -> None:
    """Test get_address gives up when nothing was discovered."""
    with pytest.raises(DevicesNotFoundError):
        await discoverer.get_address()


@pytest.mark.asyncio
async def test_client_connects_through_discovery(
    bridge: FakeDeakoConnect,
    zeroconf: Zeroconf,
    discoverer: DeakoDiscoverer,
) -> None:
    """Test the client finds, connects to and controls a discovered bridge."""
    _announce(zeroconf, bridge.port)
    await _wait_for_addresses(discoverer, [bridge.address])
    assert await discoverer.get_address() == (bridge.address, SERVICE)

    deako = Deako(discoverer.get_address)
    await deako.connect()
    try:
        await deako.find_devices()
        updated = asyncio.Event()
        deako.set_state_callback("uuid-1", updated.set)
        await deako.control_device("uuid-1", True, 60)
        await asyncio.wait_for(updated.wait(), WAIT_S)
    finally:
        await deako.disconnect()

    assert bridge.connections == 1
    assert deako.get_state("uuid-1") == {"power": True, "dim": 60}


@pytest.mark.asyncio
async def test_new_address_replaces_the_old_one(
    zeroconf: Zeroconf,
    discoverer: DeakoDiscoverer,
) -> None:
    """Test a bridge announcing a new address is found there."""
    _announce(zeroconf, 1001)
    await _wait_for_addresses(discoverer, ["127.0.0.1:1001"])

    _announce(zeroconf, 1002)
    await _wait_for_addresses(discoverer, ["127.0.0.1:1002"])

    assert await discoverer.get_address() == ("127.0.0.1:1002", SERVICE)


@pytest.mark.asyncio
async def test_goodbye_removes_the_bridge(
    zeroconf: Zeroconf,
    discoverer: DeakoDiscoverer,
) -> None:
    """Test a bridge that says goodbye is no longer offered."""
    _announce(zeroconf, 1001)
    await _wait_for_addresses(discoverer, ["127.0.0.1:1001"])

    _announce(zeroconf, 1001, ttl=0)
    await _wait_for_addresses(discoverer, [])

    with pytest.raises(DevicesNotFoundError):
        await discoverer.get_address()
