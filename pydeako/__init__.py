"""
Discover and control Deako smart switches on the local network.

Deako is the client, DeakoDiscoverer finds the bridge over mDNS, and the
request functions build messages for the local API.
"""

# pylint: disable=duplicate-code
from .deako import Deako, FindDevicesError
from .discover import (
    DeakoDiscoverer,
    DevicesNotFoundError,
    DevicesNotFoundException,
)
from .models import (
    RequestType,
    ResponseType,
    device_list_request,
    device_ping_request,
    state_change_request,
)

__all__ = [
    "Deako",
    "DeakoDiscoverer",
    "DevicesNotFoundError",
    "DevicesNotFoundException",
    "FindDevicesError",
    "RequestType",
    "ResponseType",
    "device_list_request",
    "device_ping_request",
    "state_change_request",
]
