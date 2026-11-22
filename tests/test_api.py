"""Test the public API that Home Assistant and other users rely on."""

# The public names are repeated here on purpose.
# pylint: disable=duplicate-code

import functools
import importlib
import inspect
import pkgutil
from importlib import resources
from typing import Any

import pydeako
from pydeako import deako, discover, models

# The calls users make, without annotations. Home Assistant's deako
# integration uses most of them, so a change here breaks it: keep the old
# form working, or plan the change together with Home Assistant.
PUBLIC_API = {
    "Deako": "(get_address, client_name=None)",
    "Deako.connect": "async (self)",
    "Deako.disconnect": "async (self)",
    "Deako.find_devices": "async (self, timeout=10)",
    "Deako.control_device": "async (self, uuid, power, dim=None)",
    "Deako.get_devices": "(self)",
    "Deako.get_name": "(self, uuid)",
    "Deako.get_state": "(self, uuid)",
    "Deako.is_dimmable": "(self, uuid)",
    "Deako.set_state_callback": "(self, uuid, callback)",
    "DeakoDiscoverer": "(zeroconf=None)",
    "DeakoDiscoverer.get_address": "async (self)",
    "DeakoDiscoverer.stop": "(self)",
    "FindDevicesError": "(reason='unknown')",
    "DevicesNotFoundError": (
        "(*args, msg='No devices found via _deako._tcp.local.')"
    ),
    "device_ping_request": "(**kwargs)",
    "device_list_request": "(**kwargs)",
    "state_change_request": "(device_uuid, power, dim, **kwargs)",
}


def _shape(name: str) -> str:
    """Return a public callable's signature without annotations."""
    target: Any = functools.reduce(getattr, name.split("."), pydeako)
    signature = inspect.signature(target)
    plain = signature.replace(
        parameters=[
            parameter.replace(annotation=inspect.Parameter.empty)
            for parameter in signature.parameters.values()
        ],
        return_annotation=inspect.Signature.empty,
    )
    prefix = "async " if inspect.iscoroutinefunction(target) else ""
    return f"{prefix}{plain}"


def test_home_assistant_imports() -> None:
    """Test the names Home Assistant's deako integration imports."""
    for name in (
        "Deako",
        "DeakoDiscoverer",
        "DevicesNotFoundException",
        "FindDevicesError",
    ):
        assert name in pydeako.__all__
        assert hasattr(pydeako, name)


def test_exceptions_follow_pep8_names() -> None:
    """Test exception names end in Error, keeping the old name working."""
    assert issubclass(pydeako.DevicesNotFoundError, Exception)
    assert pydeako.DevicesNotFoundException is pydeako.DevicesNotFoundError
    assert discover.DevicesNotFoundError is pydeako.DevicesNotFoundError
    assert discover.DevicesNotFoundException is pydeako.DevicesNotFoundError
    assert "DevicesNotFoundError" in pydeako.__all__


def test_all_names_exist() -> None:
    """Test every name in each __all__ can be imported."""
    for module in (pydeako, deako, discover, models):
        for name in module.__all__:
            assert hasattr(module, name), f"{module.__name__}.{name}"


def test_public_names_are_kept() -> None:
    """Test no public name was removed; new ones may be added."""
    assert set(pydeako.__all__) >= {
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
    }
    assert set(deako.__all__) >= {"Deako", "FindDevicesError"}
    assert set(discover.__all__) >= {
        "DeakoDiscoverer",
        "DevicesNotFoundError",
        "DevicesNotFoundException",
    }
    assert set(models.__all__) >= {
        "ErrorCode",
        "RequestType",
        "ResponseStatus",
        "ResponseType",
        "device_list_request",
        "device_ping_request",
        "state_change_request",
    }


def test_public_signatures() -> None:
    """Test public calls keep their parameters, defaults and async-ness."""
    assert {name: _shape(name) for name in PUBLIC_API} == PUBLIC_API


def test_exceptions_need_no_arguments() -> None:
    """Test the exceptions can be raised bare, as callers and mocks do."""
    assert str(pydeako.FindDevicesError()) == "Failed to find devices: unknown"
    assert pydeako.FindDevicesError("x").reason == "x"
    assert "No devices found" in str(pydeako.DevicesNotFoundException())


def test_loggers_are_under_pydeako() -> None:
    """Test all loggers sit below "pydeako", which Home Assistant lists."""
    names = set()
    for module_info in pkgutil.walk_packages(pydeako.__path__, "pydeako."):
        module = importlib.import_module(module_info.name)
        if hasattr(module, "_LOGGER"):
            # pylint: disable-next=protected-access
            names.add(module._LOGGER.name)

    assert names
    assert all(name.startswith("pydeako.") for name in names)


def test_package_is_marked_as_typed() -> None:
    """Test py.typed ships with the package (PEP 561)."""
    assert resources.files(pydeako).joinpath("py.typed").is_file()
