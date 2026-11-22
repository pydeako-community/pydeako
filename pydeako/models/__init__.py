"""
Types and functions to build requests for the Deako local API.

The API is described in docs/local-api.md, a copy of
https://github.com/DeakoLights/local-integrations/blob/master/API.md
"""

from ._const import ErrorCode, RequestType, ResponseStatus, ResponseType
from ._request import (
    device_list_request,
    device_ping_request,
    state_change_request,
)

__all__ = [
    "ErrorCode",
    "RequestType",
    "ResponseStatus",
    "ResponseType",
    "device_list_request",
    "device_ping_request",
    "state_change_request",
]
