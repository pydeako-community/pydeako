"""End-to-end tests of the client against the fake Deako Connect."""

import asyncio
import json
import socket
from collections.abc import Callable
from typing import Any

import pytest

from pydeako import DevicesNotFoundError
from pydeako.deako import Deako, FindDevicesError, _manager

from .fake_bridge import CRLF, FakeDeakoConnect

WAIT_S = 5


async def _wait_for(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), WAIT_S)


async def _wait_until(condition: Callable[[], bool]) -> None:
    """Poll until the condition holds; fail after WAIT_S."""
    for _ in range(int(WAIT_S / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met in time")


@pytest.mark.asyncio
async def test_find_devices(client: Deako) -> None:
    """Test devices and their state are read from the bridge."""
    assert set(client.get_devices()) == {"uuid-1", "uuid-2", "uuid-3"}
    assert client.get_name("uuid-1") == "Fake device 1"
    assert client.is_dimmable("uuid-1") is True
    assert client.is_dimmable("uuid-2") is False
    assert client.get_state("uuid-1") == {"power": False, "dim": 40}
    assert client.get_state("uuid-2") == {"power": True, "dim": None}


@pytest.mark.asyncio
async def test_messages_end_with_crlf(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test every message sent to the bridge is one CRLF-terminated line."""
    await client.control_device("uuid-1", True, 70)

    lines = bytes(bridge.raw_received).split(CRLF)
    assert lines[-1] == b""
    assert [json.loads(line)["type"] for line in lines[:-1]] == [
        "DEVICE_LIST",
        "CONTROL",
    ]
    assert bridge.received[-1]["src"] == "tests"


@pytest.mark.asyncio
async def test_control_device_state_comes_from_event(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test a control request updates the state via the bridge's event."""
    updated = asyncio.Event()
    client.set_state_callback("uuid-1", updated.set)

    await client.control_device("uuid-1", True, 70)
    await _wait_for(updated)

    assert client.get_state("uuid-1") == {"power": True, "dim": 70}
    assert bridge.devices["uuid-1"]["state"] == {"power": True, "dim": 70}


@pytest.mark.asyncio
async def test_concurrent_controls_are_sent_one_at_a_time(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test concurrent requests wait for the previous response."""
    bridge.response_delay_s = 0.05

    await asyncio.gather(
        *(client.control_device(uuid, True) for uuid in bridge.devices)
    )

    controls = [r for r in bridge.received if r["type"] == "CONTROL"]
    assert len(controls) == 3
    assert bridge.max_outstanding == 1
    assert all(d["state"]["power"] for d in bridge.devices.values())


@pytest.mark.asyncio
async def test_device_busy_is_retried(
    bridge: FakeDeakoConnect,
    client: Deako,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a DEVICE_BUSY answer is retried with a new transaction id."""
    monkeypatch.setattr(_manager, "BUSY_RETRY_DELAY_S", 0.01)
    bridge.busy_replies = 1
    updated = asyncio.Event()
    client.set_state_callback("uuid-3", updated.set)

    await client.control_device("uuid-3", True, 5)
    await _wait_for(updated)

    controls = [r for r in bridge.received if r["type"] == "CONTROL"]
    assert len(controls) == 2
    assert controls[0]["transactionId"] != controls[1]["transactionId"]
    assert client.get_state("uuid-3") == {"power": True, "dim": 5}


@pytest.mark.asyncio
async def test_disconnect_closes_the_connection(
    bridge: FakeDeakoConnect,
) -> None:
    """Test disconnect closes the socket so the bridge sees EOF."""
    deako = Deako(bridge.get_address)
    await deako.connect()
    await deako.find_devices()

    await deako.disconnect()

    await _wait_for(bridge.client_closed)
    assert deako.connection_manager.connection is None


@pytest.mark.asyncio
async def test_bridge_closing_the_connection_does_not_block(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test the event loop keeps running when the bridge closes the socket."""
    connection = client.connection_manager.connection
    assert connection is not None
    ticks = 0

    async def tick() -> None:
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    ticker = asyncio.create_task(tick())
    bridge.drop_clients()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not connection.tasks:
            break
    ticker.cancel()

    assert ticks > 0
    assert not connection.is_connected()
    assert not connection.tasks


@pytest.mark.asyncio
async def test_reconnects_after_the_bridge_drops_the_connection(
    bridge: FakeDeakoConnect,
    client: Deako,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test the client reconnects after a bridge reboot and works again."""
    monkeypatch.setattr(_manager, "PING_WORKER_WAIT_S", 0.05)
    monkeypatch.setattr(_manager, "RESPONSE_TIMEOUT_S", 0.05)
    manager = client.connection_manager
    # restart the ping worker so it uses the short interval
    manager.close()
    await client.connect()
    connections_before = bridge.connections

    bridge.drop_clients()
    for _ in range(int(WAIT_S / 0.01)):
        await asyncio.sleep(0.01)
        if (
            bridge.connections > connections_before
            and manager.connection is not None
            and manager.connection.is_connected()
        ):
            break

    assert bridge.connections > connections_before
    updated = asyncio.Event()
    client.set_state_callback("uuid-2", updated.set)
    await client.control_device("uuid-2", False)
    await _wait_for(updated)
    assert client.get_state("uuid-2") == {"power": False, "dim": None}


@pytest.mark.asyncio
async def test_ping_keeps_the_connection(
    bridge: FakeDeakoConnect,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a bridge that answers pings is not reconnected to."""
    monkeypatch.setattr(_manager, "PING_WORKER_WAIT_S", 0.02)
    deako = Deako(bridge.get_address, client_name="tests")
    await deako.connect()
    try:
        await _wait_until(lambda: len(bridge.requests("PING")) >= 3)
    finally:
        await deako.disconnect()

    assert bridge.connections == 1
    assert bridge.requests("PING")[0]["src"] == "tests"


@pytest.mark.parametrize(
    ("batch", "chunk_size"),
    [(True, 0), (False, 7), (True, 7)],
    ids=["one write", "split messages", "split stream"],
)
@pytest.mark.asyncio
async def test_framing_does_not_depend_on_tcp_segments(
    bridge: FakeDeakoConnect,
    batch: bool,
    chunk_size: int,
) -> None:
    """Test messages are read whether they arrive joined or in pieces."""
    bridge.batch = batch
    bridge.chunk_size = chunk_size
    deako = Deako(bridge.get_address)
    await deako.connect()
    try:
        await deako.find_devices()
        updated = asyncio.Event()
        deako.set_state_callback("uuid-3", updated.set)
        await deako.control_device("uuid-3", True, 30)
        await _wait_for(updated)
    finally:
        await deako.disconnect()

    assert set(deako.get_devices()) == {"uuid-1", "uuid-2", "uuid-3"}
    assert deako.get_state("uuid-3") == {"power": True, "dim": 30}


@pytest.mark.asyncio
async def test_event_from_the_bridge_updates_state(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test a change made at the wall switch reaches the callback."""
    updated = asyncio.Event()
    client.set_state_callback("uuid-1", updated.set)

    await bridge.send_event("uuid-1", {"power": True, "dim": 15})
    await _wait_for(updated)

    assert client.get_state("uuid-1") == {"power": True, "dim": 15}


@pytest.mark.asyncio
async def test_garbage_does_not_block_later_messages(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test non-object JSON and input that isn't JSON are skipped."""
    updated = asyncio.Event()
    client.set_state_callback("uuid-2", updated.set)

    await bridge.send_raw(b"[1, 2]\r\n\x00\xff not json\r\n")
    await bridge.send_event("uuid-2", {"power": False})
    await _wait_for(updated)

    assert client.get_state("uuid-2") == {"power": False, "dim": None}


@pytest.mark.asyncio
async def test_find_devices_fails_on_a_bridge_without_devices(
    bridge: FakeDeakoConnect,
) -> None:
    """Test an answer with no devices is reported like no answer at all."""
    bridge.devices.clear()
    deako = Deako(bridge.get_address)
    await deako.connect()
    try:
        with pytest.raises(FindDevicesError, match="device list response"):
            await deako.find_devices(timeout=0.05)
    finally:
        await deako.disconnect()

    assert len(bridge.requests("DEVICE_LIST")) == 1
    assert deako.get_devices() == {}


@pytest.mark.asyncio
async def test_silent_bridge_does_not_block_later_requests(
    bridge: FakeDeakoConnect,
    client: Deako,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a request the bridge never answers returns after the timeout."""
    monkeypatch.setattr(_manager, "RESPONSE_TIMEOUT_S", 0.05)
    bridge.silent = True

    await asyncio.wait_for(client.control_device("uuid-1", True), WAIT_S)

    assert client.get_state("uuid-1") == {"power": False, "dim": 40}
    bridge.silent = False
    updated = asyncio.Event()
    client.set_state_callback("uuid-1", updated.set)
    await client.control_device("uuid-1", True)
    await _wait_for(updated)
    assert client.get_state("uuid-1") == {"power": True, "dim": 40}


@pytest.mark.asyncio
async def test_unknown_device_error_is_not_retried(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test an error other than DEVICE_BUSY is not sent again."""
    await client.control_device("no-such-device", True)

    assert len(bridge.requests("CONTROL")) == 1


@pytest.mark.asyncio
async def test_float_dim_is_sent_as_an_int(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test a float dim level, as Home Assistant passes, goes out as int."""
    dim: Any = 50.0

    await client.control_device("uuid-1", True, dim)

    state = bridge.requests("CONTROL")[-1]["data"]["state"]
    assert state == {"power": True, "dim": 50}
    assert isinstance(state["dim"], int)


@pytest.mark.asyncio
async def test_connects_to_the_next_address_when_one_refuses(
    bridge: FakeDeakoConnect,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test a refused address is followed by the next one discovery gives."""
    monkeypatch.setattr(_manager, "CONNECTION_TIMEOUT_S", 0.05)
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        refusing = f"127.0.0.1:{unused.getsockname()[1]}"
    addresses = [refusing, bridge.address]

    async def get_address() -> tuple[str, str]:
        address = addresses.pop(0) if len(addresses) > 1 else addresses[0]
        return address, "fake._deako._tcp.local."

    deako = Deako(get_address)
    manager = deako.connection_manager
    await deako.connect()
    try:
        assert manager.connection is None
        await _wait_until(
            lambda: (
                manager.connection is not None
                and manager.connection.is_connected()
            )
        )
        await deako.find_devices()
    finally:
        await deako.disconnect()

    assert bridge.connections == 1
    assert set(deako.get_devices()) == {"uuid-1", "uuid-2", "uuid-3"}


@pytest.mark.asyncio
async def test_find_devices_again_updates_devices_and_keeps_callbacks(
    bridge: FakeDeakoConnect,
    client: Deako,
) -> None:
    """Test asking for the device list again refreshes what is stored."""
    updated = asyncio.Event()
    client.set_state_callback("uuid-1", updated.set)
    bridge.devices["uuid-1"]["name"] = "Fake device 1 renamed"
    bridge.devices["uuid-1"]["state"] = {"power": True, "dim": 80}

    await client.find_devices()
    await _wait_until(
        lambda: client.get_name("uuid-1") == "Fake device 1 renamed"
    )

    assert client.get_state("uuid-1") == {"power": True, "dim": 80}
    await bridge.send_event("uuid-1", {"power": False})
    await _wait_for(updated)


@pytest.mark.asyncio
async def test_disconnect_stops_looking_for_a_bridge() -> None:
    """Test disconnect cancels the retries started when none was found."""
    calls = 0

    async def get_address() -> tuple[str, str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DevicesNotFoundError()
        await asyncio.Event().wait()  # the retry keeps waiting
        raise AssertionError("unreachable")

    deako = Deako(get_address)
    await deako.connect()
    await _wait_until(lambda: calls == 2)
    retries = set(deako.connection_manager.tasks)

    await deako.disconnect()
    await asyncio.gather(*retries, return_exceptions=True)

    assert retries
    assert all(task.cancelled() for task in retries)
    assert calls == 2


@pytest.mark.xfail(
    raises=AssertionError,
    reason="a second connect() leaves the first connection open",
)
@pytest.mark.asyncio
async def test_second_connect_keeps_one_connection(
    bridge: FakeDeakoConnect,
) -> None:
    """Test calling connect again doesn't leave an extra connection open."""
    deako = Deako(bridge.get_address)
    await deako.connect()
    first = deako.connection_manager.connection
    assert first is not None
    await deako.connect()
    try:
        assert (
            deako.connection_manager.connection is first
            or not first.is_connected()
        )
    finally:
        await deako.disconnect()
        first.close()


@pytest.mark.xfail(
    raises=AssertionError,
    reason="disconnect() during connect() doesn't stop the retries",
)
@pytest.mark.asyncio
async def test_disconnect_during_connect_stops_retrying() -> None:
    """Test disconnect ends the retries of a connect still in progress."""
    calls = 0

    async def get_address() -> tuple[str, str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)  # discovery waiting for a device
        raise DevicesNotFoundError()

    deako = Deako(get_address)
    connecting = asyncio.create_task(deako.connect())
    await asyncio.sleep(0)
    await deako.disconnect()
    await connecting
    try:
        calls_at_disconnect = calls
        await asyncio.sleep(0.1)
        assert calls == calls_at_disconnect
    finally:
        await deako.disconnect()
