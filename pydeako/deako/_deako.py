"""Deako client: tracks devices and sends them control requests."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeGuard

from ..models import ResponseType
from ._manager import _Manager

_LOGGER: logging.Logger = logging.getLogger(__package__)


class FindDevicesError(Exception):
    """Unable to find devices."""

    def __init__(self, reason: str = "unknown") -> None:
        """Initialize with optional reason string."""
        self.reason = reason
        super().__init__()

    def __str__(self) -> str:
        return f"Failed to find devices: {self.reason}"


# 2s per device to be received to be conservative
DEVICE_FOUND_TIME_FACTOR_S = 2
DEFAULT_DEVICE_LIST_TIMEOUT_S = 10
DEVICE_LIST_POLLING_INTERVAL_S = 1
DEVICE_FOUND_POLLING_INTERVAL_S = 1

CAPABILITY_DIMMABLE = "dim"
# Upper bound on devices accepted from one bridge.
MAX_DEVICES = 1000
MAX_DIM = 100
# Longest value from the bridge that goes into a log message.
MAX_LOGGED_CHARS = 80


def _short(value: object) -> str:
    """Return a one-line repr of value, cut to MAX_LOGGED_CHARS."""
    text = repr(value)
    if len(text) <= MAX_LOGGED_CHARS:
        return text
    return text[:MAX_LOGGED_CHARS] + "..."


def _is_int(value: object) -> TypeGuard[int]:
    """Return whether value is an int (bool doesn't count)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_dim(dim: object) -> int | None:
    """Return dim as an int from 0 to 100, or None if missing or invalid."""
    # Home Assistant sends whole floats such as 50.0.
    if isinstance(dim, bool) or not isinstance(dim, (int, float)):
        return None
    if not 0 <= dim <= MAX_DIM:  # also rejects NaN
        return None
    return round(dim)


def _parse_state(state: object) -> tuple[bool, int | None] | None:
    """
    Return (power, dim) from a device state, or None if power is invalid.

    An invalid dim is ignored, so the power change still applies.
    """
    if not isinstance(state, dict):
        return None
    power = state.get("power")
    if _is_int(power) and power in (0, 1):
        power = bool(power)
    if not isinstance(power, bool):
        return None
    return power, _parse_dim(state.get("dim"))


class Deako:
    """
    Client for the Deako local API.

    Keeps the devices the Deako Connect reports, with their state, and
    sends control requests. State changes arrive as events and are passed
    on through the callbacks set with set_state_callback.
    """

    connection_manager: _Manager
    devices: dict[str, Any]
    expected_devices: int

    def __init__(
        self,
        get_address: Callable[[], Awaitable[tuple[str, str]]],
        client_name: str | None = None,
    ) -> None:
        """
        Set up the client; nothing is sent until connect() is called.

        get_address returns the (address, name) of a device to connect to,
        the address as "host:port", like DeakoDiscoverer.get_address does.
        client_name is sent as the source of every request.
        """
        self.connection_manager = _Manager(
            get_address,
            self.incoming_json,
            client_name=client_name,
        )
        self.devices: dict[str, Any] = {}
        self.expected_devices = 0

    def update_state(
        self,
        uuid: str,
        power: bool,
        dim: int | None = None,
    ) -> None:
        """Update an in memory device's state."""
        if uuid not in self.devices:
            return

        self.devices[uuid]["state"]["power"] = power
        # dimmables don't always send dim; 0 is a valid level
        if dim is not None:
            self.devices[uuid]["state"]["dim"] = dim

        if "callback" in self.devices[uuid]:
            self.devices[uuid]["callback"]()

    def set_state_callback(
        self, uuid: str, callback: Callable[[], None]
    ) -> None:
        """
        Call callback, without arguments, when the device's state changes.

        A device has one callback; setting another replaces it. Unknown
        uuids are ignored.
        """
        if uuid in self.devices:
            self.devices[uuid]["callback"] = callback

    def incoming_json(self, in_data: dict[str, Any]) -> None:
        """Parse incoming socket data which is json."""
        try:
            if in_data["type"] == ResponseType.DEVICE_LIST:
                self._handle_device_list(in_data["data"])
            elif in_data["type"] == ResponseType.DEVICE_FOUND:
                self._handle_device_found(in_data["data"])
            elif in_data["type"] == ResponseType.EVENT:
                self._handle_event(in_data["data"])
        # A bad message must not reach the connection's read loop.
        # pylint: disable-next=broad-exception-caught
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to parse a %s message: %s",
                _short(in_data.get("type")),
                _short(exc),
            )

    def _handle_device_list(self, data: dict[str, Any]) -> None:
        """Store how many devices the bridge is about to report."""
        count = data.get("number_of_devices")
        if not _is_int(count) or not 0 <= count <= MAX_DEVICES:
            _LOGGER.error("Ignoring invalid device count %s", _short(count))
            return
        self.expected_devices = count

    def _handle_device_found(self, data: dict[str, Any]) -> None:
        """Record a device the bridge reported."""
        name = data.get("name")
        uuid = data.get("uuid")
        capabilities = data.get("capabilities")
        state = _parse_state(data.get("state"))
        if (
            not isinstance(name, str)
            or not isinstance(uuid, str)
            or not isinstance(capabilities, (str, list, type(None)))
            or state is None
        ):
            _LOGGER.error("Ignoring invalid device %s", _short(uuid))
            return
        if uuid not in self.devices and len(self.devices) >= MAX_DEVICES:
            _LOGGER.error(
                "Ignoring device %s: over %d devices",
                _short(uuid),
                MAX_DEVICES,
            )
            return
        power, dim = state
        if capabilities is not None:
            dimmable = CAPABILITY_DIMMABLE in capabilities
        else:
            # support older local api versions
            dimmable = dim is not None
        self.record_device(name, uuid, dimmable, power, dim)

    def _handle_event(self, data: dict[str, Any]) -> None:
        """Apply a device state change event."""
        target = data.get("target")
        state = _parse_state(data.get("state"))
        if not isinstance(target, str) or state is None:
            _LOGGER.error(
                "Ignoring invalid state change for %s",
                _short(target),
            )
            return
        self.update_state(target, *state)

    # pylint: disable-next=too-many-arguments
    def record_device(
        self,
        name: str,
        uuid: str,
        dimmable: bool,
        power: bool,
        dim: int | None = None,
    ) -> None:
        """Store a device in local memory."""
        if uuid not in self.devices:
            self.devices[uuid] = {"state": {}}

        self.devices[uuid]["name"] = name
        self.devices[uuid]["uuid"] = uuid
        self.devices[uuid]["dimmable"] = dimmable
        self.devices[uuid]["state"]["power"] = power
        self.devices[uuid]["state"]["dim"] = dim

    async def connect(self) -> None:
        """
        Connect to a device from get_address.

        Returns without a connection when no device is found or it doesn't
        answer in time; the client then keeps trying in the background.
        A connection that stops answering pings is replaced.
        """
        await self.connection_manager.init_connection()

    async def disconnect(self) -> None:
        """Close the connection and stop reconnecting."""
        self.connection_manager.close()

    def get_devices(self) -> dict[str, Any]:
        """
        Get the devices found so far, by uuid.

        Each device is a dict with "name", "uuid", "dimmable" and "state";
        the state holds "power" and "dim" (0 to 100, or None).
        """
        return self.devices

    async def find_devices(
        self,
        timeout: float = DEFAULT_DEVICE_LIST_TIMEOUT_S,
    ) -> None:
        """
        Ask for the device list and wait until every device has arrived.

        timeout is how long to wait for the first answer, in seconds.
        Raises FindDevicesError when the request can't be sent, no answer
        comes in time, or fewer devices arrive than announced. A list
        without devices counts as no answer.
        """
        _LOGGER.info("Finding devices")
        success = await self.connection_manager.send_get_device_list()
        if not success:
            raise FindDevicesError("Failed to send device list request")
        remaining = timeout
        # wait for device list
        while self.expected_devices == 0 and remaining > 0:
            _LOGGER.debug(
                "waiting for device list... time remaining: %is",
                remaining,
            )
            await asyncio.sleep(DEVICE_LIST_POLLING_INTERVAL_S)
            remaining -= DEVICE_LIST_POLLING_INTERVAL_S

        # still 0: no answer, or a list without devices
        if self.expected_devices == 0:
            raise FindDevicesError(
                "Timed out waiting for device list response",
            )

        remaining = self.expected_devices * DEVICE_FOUND_TIME_FACTOR_S
        while len(self.devices) != self.expected_devices and remaining > 0:
            _LOGGER.debug(
                "waiting for devices... expected: %i, received: "
                "%i, time remaining: %is",
                self.expected_devices,
                len(self.devices),
                remaining,
            )
            await asyncio.sleep(DEVICE_FOUND_POLLING_INTERVAL_S)
            remaining -= DEVICE_FOUND_POLLING_INTERVAL_S
        _LOGGER.debug("found %i devices", len(self.devices))

        if len(self.devices) != self.expected_devices and remaining == 0:
            raise FindDevicesError(
                f"Timed out waiting for devices to be found. Expected "
                f"{self.expected_devices} devices but only found "
                f"{len(self.devices)}",
            )

    async def control_device(
        self,
        uuid: str,
        power: bool,
        dim: int | None = None,
    ) -> None:
        """
        Ask the device to change its state.

        dim is a level from 0 to 100. The new state arrives later as an
        EVENT from the bridge. A request that can't be sent or isn't
        acknowledged is logged, not raised.
        """
        await self.connection_manager.send_state_change(uuid, power, dim)

    def get_name(self, uuid: str) -> str | None:
        """Get a device's name by uuid, or None if it is unknown."""
        device_data = self.devices.get(uuid)
        if device_data is None:
            return None

        # name should exist if we have data on this device
        name: str = device_data["name"]
        return name

    def get_state(self, uuid: str) -> dict[str, Any] | None:
        """
        Get a device's state by uuid, or None if it is unknown.

        The state is {"power": bool, "dim": int | None}.
        """
        device_data = self.devices.get(uuid)
        if device_data is None:
            return None

        # state should exist if we have data on this device
        state: dict[str, Any] = device_data["state"]
        return state

    def is_dimmable(self, uuid: str) -> bool | None:
        """Get whether a device is dimmable, or None if it is unknown."""
        device_data = self.devices.get(uuid)
        if device_data is None:
            return None

        # dimmable should exist if we have data on this device
        dimmable: bool = device_data["dimmable"]
        return dimmable
