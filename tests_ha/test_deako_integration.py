"""
Run Home Assistant's deako integration on this library and the fake bridge.

Only discovery is replaced; the integration, the library and the TCP
connection to 127.0.0.1 are real. It needs pytest-homeassistant, see
requirements_ha.txt.
"""

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant.common import MockConfigEntry

from pydeako import DevicesNotFoundError
from tests.fake_bridge import FakeDeakoConnect, default_devices

WAIT_S = 5

pytestmark = pytest.mark.usefixtures("mock_async_zeroconf")


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Give Home Assistant a temporary config directory."""
    return hass_tmp_config_dir


@pytest.fixture(name="bridge")
async def fixture_bridge(
    socket_enabled: None,
) -> AsyncIterator[FakeDeakoConnect]:
    """Start the fake bridge; sockets to 127.0.0.1 are allowed."""
    fake = FakeDeakoConnect(default_devices())
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture(name="discoverer")
def fixture_discoverer(bridge: FakeDeakoConnect) -> Iterator[MagicMock]:
    """Replace discovery: the only device found is the fake bridge."""
    with patch(
        "homeassistant.components.deako.DeakoDiscoverer",
        autospec=True,
    ) as discoverer:
        discoverer.return_value.get_address = bridge.get_address
        yield discoverer


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain="deako")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _wait_until(condition: Callable[[], bool]) -> None:
    for _ in range(int(WAIT_S / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


@pytest.mark.usefixtures("discoverer")
async def test_setup_creates_a_light_per_device(hass: HomeAssistant) -> None:
    """Test the devices of the bridge become lights with their state."""
    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert _state(hass, "light.fake_device_1") == "off"
    assert _state(hass, "light.fake_device_2") == "on"
    assert _state(hass, "light.fake_device_3") == "off"
    device_1 = hass.states.get("light.fake_device_1")
    device_2 = hass.states.get("light.fake_device_2")
    assert device_1 is not None
    assert device_2 is not None
    assert device_1.attributes["supported_color_modes"] == ["brightness"]
    assert device_2.attributes["supported_color_modes"] == ["onoff"]


@pytest.mark.usefixtures("discoverer")
async def test_turn_on_with_brightness(
    hass: HomeAssistant,
    bridge: FakeDeakoConnect,
) -> None:
    """Test a brightness reaches the bridge as a whole dim level."""
    await _setup(hass)

    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": "light.fake_device_1", "brightness": 128},
        blocking=True,
    )
    await _wait_until(lambda: _state(hass, "light.fake_device_1") == "on")

    sent = bridge.requests("CONTROL")[-1]["data"]
    assert sent == {"target": "uuid-1", "state": {"power": True, "dim": 50}}
    assert isinstance(sent["state"]["dim"], int)
    device_1 = hass.states.get("light.fake_device_1")
    assert device_1 is not None
    assert device_1.attributes["brightness"] == round(50 * 2.55)


@pytest.mark.usefixtures("discoverer")
async def test_turn_off(hass: HomeAssistant, bridge: FakeDeakoConnect) -> None:
    """Test turning a light off is sent and comes back as its state."""
    await _setup(hass)

    await hass.services.async_call(
        "light",
        "turn_off",
        {"entity_id": "light.fake_device_2"},
        blocking=True,
    )
    await _wait_until(lambda: _state(hass, "light.fake_device_2") == "off")

    assert bridge.devices["uuid-2"]["state"] == {"power": False}


@pytest.mark.usefixtures("discoverer")
async def test_change_at_the_switch_updates_the_light(
    hass: HomeAssistant,
    bridge: FakeDeakoConnect,
) -> None:
    """Test an event from the bridge changes the light's state."""
    await _setup(hass)

    await bridge.send_event("uuid-3", {"power": True, "dim": 20})
    await _wait_until(lambda: _state(hass, "light.fake_device_3") == "on")

    device_3 = hass.states.get("light.fake_device_3")
    assert device_3 is not None
    assert device_3.attributes["brightness"] == round(20 * 2.55)


@pytest.mark.usefixtures("discoverer")
async def test_unload_disconnects(
    hass: HomeAssistant,
    bridge: FakeDeakoConnect,
) -> None:
    """Test unloading the entry closes the connection to the bridge."""
    entry = await _setup(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await asyncio.wait_for(bridge.client_closed.wait(), WAIT_S)

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert bridge.connections == 1


async def test_setup_is_retried_when_no_bridge_is_found(
    hass: HomeAssistant,
    discoverer: MagicMock,
    bridge: FakeDeakoConnect,
) -> None:
    """Test setup ends as "retry" and leaves nothing running."""
    discoverer.return_value.get_address = AsyncMock(
        side_effect=DevicesNotFoundError,
    )

    entry = await _setup(hass)

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert bridge.connections == 0
