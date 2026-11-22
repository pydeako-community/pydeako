"""Client for Deako devices on the local network."""

from ._deako import Deako, FindDevicesError

__all__ = [
    "Deako",
    "FindDevicesError",
]
