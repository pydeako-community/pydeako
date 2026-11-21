"""
Manager to SocketConnection, ensuring that there's always an
active connection. Uses two workers, one to check connectivity
through pinging, and one to check for messages to send.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..discover import DevicesNotFoundException
from ..models import (
    device_list_request,
    device_ping_request,
    state_change_request,
    ErrorCode,
    ResponseStatus,
    ResponseType,
)
from .utils import _Connection
from ._request import _Request

_LOGGER: logging.Logger = logging.getLogger(__package__)

CONNECTED_POLLING_INTERVAL_S = 1
CONNECTION_TIMEOUT_S = 10
# Wait before trying again after connecting failed unexpectedly.
CONNECTION_RETRY_DELAY_S = 10
WORKER_WAIT_S = 0.5
PING_WORKER_WAIT_S = 10
# Max time to wait for the device to answer a request before sending the next.
RESPONSE_TIMEOUT_S = 1.0
# A request answered with DEVICE_BUSY is sent again after this delay.
BUSY_RETRY_DELAY_S = 0.25
MAX_BUSY_RETRIES = 3


def _is_device_busy(response: dict[str, Any] | None) -> bool:
    """Return whether a response is a DEVICE_BUSY error."""
    if not isinstance(response, dict):
        return False
    if response.get("status") != ResponseStatus.ERROR:
        return False
    data = response.get("data")
    return isinstance(data, dict) and data.get("code") == ErrorCode.DEVICE_BUSY


@dataclass
class _ManagerState:
    """State for _Manager, used by workers."""

    connecting: bool = False
    canceled: bool = False
    logged_send_error = False


# pylint: disable-next=too-many-instance-attributes
class _Manager:
    """Manage the socket connection to Deako local integrations."""

    maintain_worker: asyncio.Task[None] | None = None
    worker: asyncio.Task[None] | None = None
    connection: _Connection | None = None
    tasks: set[asyncio.Task[None]]
    client_name: str | None
    state: _ManagerState
    send_lock: asyncio.Lock
    pending_responses: dict[str, asyncio.Future[dict[str, Any]]]

    def __init__(
        self,
        get_address: Callable[[], Awaitable[tuple[str, str]]],
        incoming_json_callback: Callable[[dict[str, Any]], None],
        client_name: str | None = None,
    ) -> None:
        """Initialize with get address function and incoming json callback."""
        self.get_address = get_address
        self.incoming_json_callback = incoming_json_callback
        self.pong_received = False
        self.tasks = set()
        self.client_name = client_name
        self.state = _ManagerState()
        self.send_lock = asyncio.Lock()
        self.pending_responses = {}

    async def init_connection(self) -> None:
        """Initialize the connection process."""
        if self.state.connecting:
            _LOGGER.error("Already attempting to connect")
            return
        self.state.connecting = True
        try:
            await self._connect()
        finally:
            self.state.connecting = False

    async def _connect(self) -> None:
        """Connect to a discovered device; retry later if that fails."""
        try:
            address, name = await self.get_address()
        except DevicesNotFoundException:
            _LOGGER.warning("No devices to connect to")
            self.create_connection_task()
            return
        connection = _Connection(address, name, self.incoming_json)
        timeout = 0
        try:
            while (
                not connection.is_connected()
                and timeout < CONNECTION_TIMEOUT_S
            ):
                await asyncio.sleep(CONNECTED_POLLING_INTERVAL_S)
                timeout += CONNECTED_POLLING_INTERVAL_S
        except BaseException:
            # e.g. cancelled by close(): don't leave the socket open
            connection.close()
            raise
        if not connection.is_connected():
            _LOGGER.error("Timeout attempting to connect. Trying again")
            connection.close()
            self.create_connection_task()
            return
        self.connection = connection
        # init connection watching
        if self.maintain_worker is None:
            self.state.canceled = False
            self.maintain_worker = asyncio.create_task(
                self.maintain_connection_worker()
            )

    def close(self) -> None:
        """Close connection."""
        _LOGGER.debug("Closing connection and canceling workers")
        self.state.canceled = True

        # Cancel and clear all pending tasks
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()

        if self.worker is not None:
            self.worker.cancel()
            self.worker = None
        if self.maintain_worker is not None:
            self.maintain_worker.cancel()
            self.maintain_worker = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def create_connection_task(self) -> None:
        """Create an async task to initiate connection."""
        # RUF006
        # pylint: disable-next=line-too-long
        # noqa keep reference via: https://stackoverflow.com/questions/71938799/python-asyncio-create-task-really-need-to-keep-a-reference
        # even if we don't care
        task = asyncio.create_task(self._connect_in_background())
        self.tasks.add(task)

        def remove_task(_task: asyncio.Task[None]) -> None:
            try:
                self.tasks.remove(_task)
            except KeyError:
                pass  # already removed

        task.add_done_callback(remove_task)

    async def _connect_in_background(self) -> None:
        """Connect from a task; try again later if that fails unexpectedly."""
        try:
            await self.init_connection()
        # An unexpected error must not end the reconnect attempts for good.
        except Exception:  # pylint: disable=broad-exception-caught
            _LOGGER.exception(
                "Connecting failed, trying again in %s s",
                CONNECTION_RETRY_DELAY_S,
            )
            if self.state.canceled:
                return
            await asyncio.sleep(CONNECTION_RETRY_DELAY_S)
            self.create_connection_task()

    async def maintain_connection_worker(self) -> None:
        """Monitor connection and restart if there's a failure."""
        await asyncio.sleep(PING_WORKER_WAIT_S)
        while not self.state.canceled:
            self.pong_received = False
            _LOGGER.debug("Pinging for responsiveness")
            try:
                await self.send_request(
                    _Request(device_ping_request(source=self.client_name)),
                )
                await asyncio.sleep(PING_WORKER_WAIT_S)
            # An unexpected error must not stop the liveness check for good;
            # treat it like a missing pong and reconnect.
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Pinging the device failed")
                self.pong_received = False
            if self.pong_received:
                _LOGGER.debug("Pong received")
            else:
                _LOGGER.warning("Never received pong! Dumping this connection")
                self.close()
                # close() marks the manager canceled, but this is a
                # reconnect: a failed attempt must still be retried.
                self.state.canceled = False
                self.create_connection_task()
                break

    def incoming_json(self, incoming_json: dict[str, Any]) -> None:
        """Handle incoming json."""
        transaction_id = incoming_json.get("transactionId")
        pending = (
            self.pending_responses.get(transaction_id)
            if isinstance(transaction_id, str)
            else None
        )
        if pending is not None and not pending.done():
            pending.set_result(incoming_json)
        response_type = incoming_json.get("type")
        if response_type == ResponseType.PONG:
            self.pong_received = True
        else:
            self.incoming_json_callback(incoming_json)

    async def send_get_device_list(self) -> bool:
        """Send the device list request."""
        return await self.send_request(
            _Request(device_list_request(source=self.client_name)),
        )

    async def send_state_change(
        self,
        uuid: str,
        power: bool,
        dim: int | None = None,
        completed_callback: Callable[[], None] | None = None,
    ) -> None:
        """Send a state change request."""
        await self.send_request(
            _Request(
                state_change_request(
                    uuid, power, dim, source=self.client_name,
                ),
                completed_callback=completed_callback,
            )
        )

    async def send_request(self, req: _Request) -> bool:
        """
        Send a request. Requests go out one at a time: the next one is sent
        once the device answers (or RESPONSE_TIMEOUT_S passes). Requests
        answered with DEVICE_BUSY are retried.
        """
        async with self.send_lock:
            for attempt in range(MAX_BUSY_RETRIES + 1):
                if attempt:
                    _LOGGER.debug("Device busy, retrying request")
                    await asyncio.sleep(BUSY_RETRY_DELAY_S)
                    req = req.with_new_transaction_id()
                if self.connection is None:
                    _LOGGER.warning("No connection to send data to")
                    return False
                response = await self._send_and_wait(self.connection, req)
                if not _is_device_busy(response):
                    return True

        _LOGGER.warning("Device still busy after %d retries", MAX_BUSY_RETRIES)
        return False

    async def _send_and_wait(
        self, connection: _Connection, req: _Request,
    ) -> dict[str, Any] | None:
        """Send a request and return its response, or None if none came."""
        transaction_id = req.get_transaction_id()
        if transaction_id is None:
            await connection.send_data(req.get_body_str())
            return None

        response = asyncio.get_running_loop().create_future()
        self.pending_responses[transaction_id] = response
        try:
            await connection.send_data(req.get_body_str())
            return await asyncio.wait_for(response, RESPONSE_TIMEOUT_S)
        except asyncio.TimeoutError:
            _LOGGER.debug("No response to request %s", transaction_id)
            return None
        finally:
            self.pending_responses.pop(transaction_id, None)
