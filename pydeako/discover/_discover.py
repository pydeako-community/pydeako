"""Deako device discovery via zeroconf."""

import logging
import socket
from asyncio import sleep
from collections.abc import Callable

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

from ._address_pool import EmptyAddressPoolError, _AddressPool

DEAKO_TYPE = "_deako._tcp.local."
TIMEOUT_S = 10
POLLING_INTERVAL_S = 0.5

_LOGGER = logging.getLogger(__package__)


class DevicesNotFoundError(Exception):
    """No devices found."""

    def __init__(
        self,
        *args: object,
        msg: str = f"No devices found via {DEAKO_TYPE}",
    ) -> None:
        super().__init__(msg, *args)


# The name before 0.7, kept so existing imports keep working.
DevicesNotFoundException = DevicesNotFoundError


class DeakoDiscoverer(ServiceBrowser):
    """
    Browse for Deako devices with zeroconf and keep their addresses.

    Browsing starts when the object is created and runs on its own
    thread. cancel() ends it and waits for that thread, so asyncio code
    should run it with asyncio.to_thread.
    """

    address_pool: _AddressPool
    zeroconf: Zeroconf

    def __init__(self, zeroconf: Zeroconf | None = None) -> None:
        """Start browsing, on the given zeroconf or on a new instance."""
        self.zeroconf = zeroconf or Zeroconf()
        self.address_pool = _AddressPool()
        # Note that this begins discovering on init
        super().__init__(
            self.zeroconf,
            DEAKO_TYPE,
            DeakoListener(
                self.address_pool.set_service,
                self.address_pool.remove_service,
            ),
        )

    async def get_address(self) -> tuple[str, str]:
        """
        Get the address and service name of a discovered device.

        The address is "host:port". With several addresses, each call
        returns the next one. Waits up to TIMEOUT_S for a device and
        raises DevicesNotFoundError if none is discovered.
        """
        total_time = 0.0
        while (
            total_time < TIMEOUT_S
            and self.address_pool.available_addresses() < 1
        ):
            total_time += POLLING_INTERVAL_S
            await sleep(POLLING_INTERVAL_S)
        try:
            address, name = self.address_pool.get_address()
        except EmptyAddressPoolError as exc:
            raise DevicesNotFoundError() from exc
        _LOGGER.debug("Got address %s, with device name %s", address, name)
        return address, name

    def stop(self) -> None:
        """
        Close the zeroconf instance.

        Don't call this on a shared instance, such as Home Assistant's.
        The browser thread keeps running; cancel() ends it.
        """
        self.zeroconf.close()


class DeakoListener(ServiceListener):
    """Listener class that satisfies the interface for zeroconf browsers."""

    def __init__(
        self,
        device_addresses_callback: Callable[[str, list[str]], None],
        device_removed_callback: Callable[[str], None],
    ) -> None:
        """Save callbacks: a service's addresses set, and a service removed."""
        self.device_addresses_callback = device_addresses_callback
        self.device_removed_callback = device_removed_callback

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Add the addresses of a newly discovered service."""
        _LOGGER.debug("Add service with address type %s, name %s", type_, name)
        addresses = self.__get_addresses(zc, type_, name)
        if addresses:
            self.device_addresses_callback(name, addresses)

    def remove_service(
        self,
        zc: Zeroconf,  # noqa: ARG002 (zeroconf's listener interface)
        type_: str,
        name: str,
    ) -> None:
        """Remove addresses associated with service."""
        _LOGGER.debug(
            "Remove service with address type %s, name %s",
            type_,
            name,
        )
        # The service can't be resolved any more after a goodbye packet or
        # expiry, so remove it by name.
        self.device_removed_callback(name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """Replace the addresses of a service that changed."""
        _LOGGER.debug(
            "Update for service with address type %s, name %s",
            type_,
            name,
        )
        addresses = self.__get_addresses(zc, type_, name)
        if addresses:
            # The device may have a new address, e.g. after a DHCP change.
            self.device_addresses_callback(name, addresses)

    def __get_addresses(
        self, zeroconf: Zeroconf, address_type: str, name: str
    ) -> list[str]:
        """
        Get a service's addresses.

        Returns each IPv4 address as host:port.
        """
        info = zeroconf.get_service_info(address_type, name)
        if info is not None:
            addresses = info.addresses
            port = info.port
            return [
                f"{socket.inet_ntoa(address)}:{port}" for address in addresses
            ]
        return []
