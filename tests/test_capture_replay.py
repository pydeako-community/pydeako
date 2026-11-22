"""
Replay messages recorded from a real Deako Connect.

tests/fixtures/bridge_capture.json holds one session: the device list,
two CONTROL requests with the events they caused, and pings. The tests
only read that file; nothing connects to a real device.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from pydeako.deako import Deako

from .fake_bridge import CRLF, FakeDeakoConnect

CAPTURE = Path(__file__).parent / "fixtures" / "bridge_capture.json"
WAIT_S = 5


def _exchanges() -> list[tuple[dict[str, Any], list[str]]]:
    """Pair each recorded request with the socket reads that followed it."""
    exchanges: list[tuple[dict[str, Any], list[str]]] = []
    messages = json.loads(CAPTURE.read_text(encoding="utf-8"))["messages"]
    for message in messages:
        if message["direction"] == "sent":
            exchanges.append((json.loads(message["data"]), []))
        else:
            exchanges[-1][1].append(message["data"])
    return exchanges


def _without_id(request: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in request.items() if k != "transactionId"}


def _shape(value: Any) -> Any:
    """Reduce a message to its keys and value types."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    return type(value).__name__


class ReplayBridge:
    """Answers each request with what the real bridge sent for it."""

    def __init__(self) -> None:
        """Load the recording; call start() to listen."""
        self.pending = _exchanges()
        # Requests that differ from the recorded request of their type.
        self.unexpected: list[dict[str, Any]] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        """Start listening on a free loopback port."""
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Stop listening."""
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def get_address(self) -> tuple[str, str]:
        """Stand-in for DeakoDiscoverer.get_address."""
        return f"127.0.0.1:{self.port}", "replay._deako._tcp.local."

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                request = json.loads(await reader.readuntil(CRLF))
                exchange = next(
                    (
                        e
                        for e in self.pending
                        if e[0]["type"] == request["type"]
                    ),
                    None,
                )
                if exchange is None:
                    continue
                self.pending.remove(exchange)
                recorded, reads = exchange
                if _without_id(request) != _without_id(recorded):
                    self.unexpected.append(request)
                for read in reads:
                    writer.write(
                        read.replace(
                            recorded["transactionId"],
                            request["transactionId"],
                        ).encode()
                    )
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest_asyncio.fixture(name="replay")
async def fixture_replay() -> AsyncIterator[ReplayBridge]:
    """Start a bridge that replays the recording."""
    bridge = ReplayBridge()
    await bridge.start()
    yield bridge
    await bridge.stop()


@pytest.mark.asyncio
async def test_client_understands_the_recorded_session(
    replay: ReplayBridge,
) -> None:
    """Test the client reads a real bridge's device list and events."""
    target = next(
        request["data"]["target"]
        for request, _ in _exchanges()
        if request["type"] == "CONTROL"
    )
    deako = Deako(replay.get_address)
    await deako.connect()
    try:
        await deako.find_devices()
        devices = deako.get_devices()
        updated = asyncio.Event()
        deako.set_state_callback(target, updated.set)
        await deako.control_device(target, True)
        await asyncio.wait_for(updated.wait(), WAIT_S)
        # get_state returns the live dict; keep a copy.
        state_on = dict(deako.get_state(target) or {})
        updated.clear()
        await deako.control_device(target, False)
        await asyncio.wait_for(updated.wait(), WAIT_S)
    finally:
        await deako.disconnect()

    assert [d["name"] for d in devices.values()] == [
        f"Fake device {number}" for number in range(1, 8)
    ]
    assert not any(deako.is_dimmable(uuid) for uuid in devices)
    assert state_on == {"power": True, "dim": None}
    assert deako.get_state(target) == {"power": False, "dim": None}
    # The requests are still the ones the real bridge answered.
    assert replay.unexpected == []


@pytest.mark.asyncio
async def test_fake_bridge_answers_like_the_recording() -> None:
    """Test the fake's messages have the fields and types of real ones."""
    recorded: dict[str, list[Any]] = {}
    for _, reads in _exchanges():
        for line in "".join(reads).splitlines():
            message = json.loads(line)
            recorded.setdefault(message["type"], []).append(_shape(message))
    device = {
        "name": "Fake device 1",
        "capabilities": "power",
        "state": {"power": False},
    }
    fake = FakeDeakoConnect({"uuid-1": device})
    await fake.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", fake.port)
    try:
        for request, _ in _exchanges()[:3]:
            if request["type"] == "CONTROL":
                request["data"]["target"] = "uuid-1"
            writer.write(json.dumps(request).encode() + CRLF)
        # DEVICE_LIST, DEVICE_FOUND, CONTROL, EVENT and PING
        answers = [
            json.loads(await asyncio.wait_for(reader.readuntil(CRLF), WAIT_S))
            for _ in range(5)
        ]
    finally:
        writer.close()
        await fake.stop()

    assert {answer["type"] for answer in answers} == set(recorded)
    for answer in answers:
        assert _shape(answer) in recorded[answer["type"]], answer["type"]
