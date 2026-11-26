"""
A fake Deako Connect bridge on 127.0.0.1, for tests.

It speaks the local API (docs/local-api.md): CRLF-delimited JSON,
DEVICE_LIST followed by one DEVICE_FOUND per device, CONTROL answered
with "ok" (or an error) and then a DEVICE_STATE_CHANGE event, and PING
answered with PING.
"""

import asyncio
import json
from typing import Any

CRLF = b"\r\n"


def default_devices() -> dict[str, dict[str, Any]]:
    """Return two dimmers and a switch, keyed by uuid."""
    return {
        "uuid-1": {
            "name": "Fake device 1",
            "capabilities": "power+dim",
            "state": {"power": False, "dim": 40},
        },
        "uuid-2": {
            "name": "Fake device 2",
            "capabilities": "power",
            "state": {"power": True},
        },
        "uuid-3": {
            "name": "Fake device 3",
            "capabilities": "power+dim",
            "state": {"power": False, "dim": 100},
        },
    }


class FakeDeakoConnect:
    """A Deako Connect bridge listening on a loopback port."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, devices: dict[str, dict[str, Any]]) -> None:
        """Set up the bridge with its devices; call start() to listen."""
        self.devices = devices
        self.received: list[dict[str, Any]] = []
        self.raw_received = bytearray()
        self.busy_replies = 0
        self.response_delay_s = 0.0
        # Read requests but never answer, like a bridge that hangs.
        self.silent = False
        # Close each connection as soon as it is accepted.
        self.close_on_accept = False
        # Send all messages of one answer in a single write.
        self.batch = False
        # Above 0, write in pieces of this many bytes with a pause between.
        self.chunk_size = 0
        self.outstanding = 0
        self.max_outstanding = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.connections = 0
        self.client_closed = asyncio.Event()
        self.server: asyncio.Server | None = None
        self.port = 0
        self._handlers: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Start listening on a free loopback port."""
        self.server = await asyncio.start_server(
            self._handle,
            "127.0.0.1",
            0,
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close all clients and the server, and wait for the handlers."""
        self.drop_clients()
        if self.server is not None:
            self.server.close()
        await asyncio.gather(*self._handlers, return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()

    @property
    def address(self) -> str:
        """Address in the host:port form discovery returns."""
        return f"127.0.0.1:{self.port}"

    async def get_address(self) -> tuple[str, str]:
        """Stand-in for DeakoDiscoverer.get_address."""
        return self.address, "fake._deako._tcp.local."

    def requests(self, request_type: str) -> list[dict[str, Any]]:
        """Return the received requests of one type."""
        return [r for r in self.received if r["type"] == request_type]

    def drop_clients(self) -> None:
        """Close every client connection, like a bridge reboot."""
        for writer in self.writers:
            writer.close()
        self.writers.clear()

    async def send_raw(self, data: bytes) -> None:
        """Send bytes as they are to every client that is still there."""
        for writer in list(self.writers):
            await self._write_or_forget(writer, data)

    async def _write_or_forget(
        self,
        writer: asyncio.StreamWriter,
        data: bytes,
    ) -> None:
        try:
            await self._write(writer, data)
        except ConnectionError:
            self._forget(writer)

    def _forget(self, writer: asyncio.StreamWriter) -> None:
        if writer in self.writers:
            self.writers.remove(writer)

    async def send_event(self, target: str, state: dict[str, Any]) -> None:
        """Change a device's state and tell every client, like a wall tap."""
        self.devices[target]["state"].update(state)
        await self.send_raw(self._encode(self._event(target)))

    def _event(self, target: str) -> dict[str, Any]:
        return {
            "type": "EVENT",
            "src": "deako",
            "timestamp": 1,
            "data": {
                "eventType": "DEVICE_STATE_CHANGE",
                "target": target,
                "state": dict(self.devices[target]["state"]),
            },
        }

    @staticmethod
    def _encode(message: dict[str, Any]) -> bytes:
        return json.dumps(message, ensure_ascii=False).encode() + CRLF

    async def _write(self, writer: asyncio.StreamWriter, data: bytes) -> None:
        step = self.chunk_size or len(data)
        for start in range(0, len(data), step):
            writer.write(data[start : start + step])
            await writer.drain()
            if self.chunk_size:
                # Let the client read this piece before the next arrives.
                await asyncio.sleep(0.001)

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        messages: list[dict[str, Any]],
    ) -> None:
        encoded = [self._encode(message) for message in messages]
        if self.batch:
            encoded = [b"".join(encoded)]
        for data in encoded:
            await self._write(writer, data)

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._handlers.add(task)
        self.connections += 1
        if self.close_on_accept:
            writer.close()
            self._handlers.discard(task)
            return
        self.writers.append(writer)
        try:
            while True:
                line = await reader.readuntil(CRLF)
                self.raw_received += line
                request = json.loads(line)
                self.received.append(request)
                if self.silent:
                    continue
                self.outstanding += 1
                self.max_outstanding = max(
                    self.max_outstanding,
                    self.outstanding,
                )
                await asyncio.sleep(self.response_delay_s)
                self.outstanding -= 1
                await self._answer(writer, request)
        except (asyncio.IncompleteReadError, ConnectionError):
            self.client_closed.set()
        finally:
            self._forget(writer)
            writer.close()
            self._handlers.discard(task)

    async def _answer(
        self,
        writer: asyncio.StreamWriter,
        request: dict[str, Any],
    ) -> None:
        base = {
            "transactionId": request["transactionId"],
            "type": request["type"],
            "dst": request["src"],
            "src": "deako",
            "timestamp": 1,
        }
        if request["type"] == "PING":
            await self._send(writer, [{**base, "status": "ok"}])
        elif request["type"] == "DEVICE_LIST":
            await self._send(
                writer,
                [
                    {
                        **base,
                        "status": "ok",
                        "data": {"number_of_devices": len(self.devices)},
                    },
                    *(
                        {
                            "type": "DEVICE_FOUND",
                            "src": "deako",
                            "timestamp": 1,
                            "data": {"uuid": uuid, **device},
                        }
                        for uuid, device in self.devices.items()
                    ),
                ],
            )
        elif request["type"] == "CONTROL":
            await self._answer_control(writer, base, request)

    async def _answer_control(
        self,
        writer: asyncio.StreamWriter,
        base: dict[str, Any],
        request: dict[str, Any],
    ) -> None:
        target = request["data"]["target"]
        error = None
        if self.busy_replies:
            self.busy_replies -= 1
            error = {"code": "DEVICE_BUSY", "message": "Device is busy"}
        elif target not in self.devices:
            error = {"code": "DEVICE_UNKNOWN", "message": "Unknown device"}
        if error is not None:
            await self._send(
                writer,
                [{**base, "status": "error", "data": error}],
            )
            return
        await self._send(writer, [{**base, "status": "ok"}])
        state = self.devices[target]["state"]
        # A device only has the state keys of its capabilities.
        state.update(
            {
                key: value
                for key, value in request["data"]["state"].items()
                if key in state
            }
        )
        await self.send_raw(self._encode(self._event(target)))
