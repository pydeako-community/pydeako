"""Test Deako."""

import json
from typing import Any
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from pydeako.deako._deako import Deako, FindDevicesError


@patch("pydeako.deako._deako._Manager")
def test_init(manager_mock):
    """Test Deako.__init__."""
    get_address_mock = Mock()
    client_name = Mock()

    deako = Deako(get_address_mock, client_name=client_name)

    manager_mock.assert_called_once_with(
        get_address_mock, deako.incoming_json, client_name=client_name
    )
    assert deako is not None


def test_update_state_uuid_not_found():
    """Test Deako.update_state, device id not found."""
    deako = Deako(Mock())
    pre_update_state_devices = json.dumps(deako.devices)

    deako.update_state(str(uuid4()), False)

    # expect no changes
    assert pre_update_state_devices == json.dumps(deako.devices)


def test_update_state_no_callback():
    """Test Deako.update_state with no callback."""
    device_id = str(uuid4())

    deako = Deako(Mock())
    deako.devices[device_id] = {
        "state": {
            "power": False,
            "dim": 69,
        },
    }

    deako.update_state(device_id, True, 42)

    assert deako.devices[device_id] == {
        "state": {
            "power": True,
            "dim": 42,
        },
    }


def test_update_state_no_callback_no_dim():
    """Test Deako.update_state, no callback, no dim for a dimmable."""
    device_id = str(uuid4())

    deako = Deako(Mock())
    deako.devices[device_id] = {
        "state": {
            "power": False,
            "dim": 69,
        },
    }

    deako.update_state(device_id, True)

    assert deako.devices[device_id] == {
        "state": {
            "power": True,
            "dim": 69,
        },
    }


def test_update_state_with_callback():
    """Test Deako.update_state with callback."""
    device_id = str(uuid4())
    callback = Mock()

    deako = Deako(Mock())
    deako.devices[device_id] = {
        "state": {
            "power": False,
            "dim": 69,
        },
        "callback": callback,
    }

    deako.update_state(device_id, True, 42)

    assert deako.devices[device_id] == {
        "state": {
            "power": True,
            "dim": 42,
        },
        "callback": callback,
    }
    callback.assert_called_once()


def test_set_state_callback_no_device():
    """Test Deako.set_state_callback with device id not found."""
    deako = Deako(Mock())
    pre_update_state_devices = json.dumps(deako.devices)

    callback = Mock()

    deako.set_state_callback(str(uuid4()), callback)

    # expect no changes
    assert pre_update_state_devices == json.dumps(deako.devices)


def test_set_state_callback():
    """Test Deako.set_state_callback."""
    device_id = str(uuid4())
    deako = Deako(Mock())
    deako.devices = {device_id: {}}

    callback = Mock()

    deako.set_state_callback(device_id, callback)

    assert deako.devices[device_id] == {
        "callback": callback,
    }


def test_incoming_json_device_list():
    """Test Deako.incoming_json with device list."""
    deako = Deako(Mock())

    incoming = {
        "type": "DEVICE_LIST",
        "data": {"number_of_devices": 42},
    }

    deako.incoming_json(incoming)

    assert deako.expected_devices == 42


@pytest.mark.parametrize(
    "capabilities_param",
    [("power", False), ("dim+power", True)],
)
@pytest.mark.parametrize("dim", [None, 69])
@patch("pydeako.deako._deako.Deako.record_device")
def test_incoming_json_device_found(
    record_device_mock,
    dim,
    capabilities_param,
):
    """Test deako.incoming_json device with capabilities."""
    [capabilities, dimmable] = capabilities_param
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    incoming: dict[str, Any] = {
        "type": "DEVICE_FOUND",
        "data": {
            "name": device_name,
            "capabilities": capabilities,
            "uuid": device_id,
            "state": {
                "power": True,
            },
        },
    }

    if dim is not None:
        incoming["data"]["state"]["dim"] = dim

    deako.incoming_json(incoming)

    record_device_mock.assert_called_once_with(
        device_name, device_id, dimmable, True, dim
    )


@pytest.mark.parametrize("dim", [None, 69])
@patch("pydeako.deako._deako.Deako.record_device")
def test_incoming_json_device_found_no_capabilities(
    record_device_mock,
    dim,
):
    """Test deako.incoming_json device, no capabilities."""
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    incoming: dict[str, Any] = {
        "type": "DEVICE_FOUND",
        "data": {
            "name": device_name,
            "uuid": device_id,
            "state": {
                "power": True,
            },
        },
    }

    if dim is not None:
        incoming["data"]["state"]["dim"] = dim

    deako.incoming_json(incoming)

    record_device_mock.assert_called_once_with(
        device_name, device_id, dim is not None, True, dim
    )


@pytest.mark.parametrize("dim", [None, 69])
@patch("pydeako.deako._deako.Deako.update_state")
def test_incoming_json_event(update_state_mock, dim):
    """Test Deako.incoming_json state event."""
    device_id = str(uuid4())
    deako = Deako(Mock())

    incoming: dict[str, Any] = {
        "type": "EVENT",
        "data": {
            "target": device_id,
            "state": {
                "power": True,
            },
        },
    }

    if dim is not None:
        incoming["data"]["state"]["dim"] = dim

    deako.incoming_json(incoming)

    update_state_mock.assert_called_once_with(device_id, True, dim)


@patch("pydeako.deako._deako.Deako.update_state")
def test_incoming_json_exception_ignored(update_state_mock):
    """Test Deako.incoming_json parse failure ignored."""
    device_id = str(uuid4())
    deako = Deako(Mock())

    incoming = {
        "type": "EVENT",
        "data": {
            "target": device_id,
            "state": {
                "power": True,
            },
        },
    }

    update_state_mock.side_effect = Exception()

    deako.incoming_json(incoming)

    update_state_mock.assert_called_once_with(device_id, True, None)


@pytest.mark.parametrize("dimmable", [True, False])
@pytest.mark.parametrize("dim", [None, 69])
def test_incoming_record_device(dim, dimmable):
    """Test Deako.incoming_json record device."""
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    deako.record_device(device_name, device_id, dimmable, False, dim)

    assert deako.devices[device_id] == {
        "name": device_name,
        "uuid": device_id,
        "dimmable": dimmable,
        "state": {
            "power": False,
            "dim": dim,
        },
    }


@patch("pydeako.deako._deako._Manager.init_connection")
@pytest.mark.asyncio
async def test_connect(manager_init_connection_mock):
    """Test Deako.connect."""
    deako = Deako(Mock())

    await deako.connect()

    manager_init_connection_mock.assert_called_once()


@patch("pydeako.deako._deako._Manager.close")
@pytest.mark.asyncio
async def test_disconnect(manager_close_mock):
    """Test Deako.disconnect."""
    deako = Deako(Mock())

    await deako.disconnect()

    manager_close_mock.assert_called_once()


def test_get_devices():
    """Test Deako.get_devices."""
    deako = Deako(Mock())
    devices_mock = Mock()
    deako.devices = devices_mock

    assert deako.get_devices() == devices_mock


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices_no_expected_devices(asyncio_mock, manager_mock):
    """Test Deako.find_devices no expected devices raises."""
    deako = Deako(Mock())

    with pytest.raises(FindDevicesError) as exc:
        await deako.find_devices()

    assert str(exc.value) == (
        "Failed to find devices: Timed out waiting for device list response"
    )
    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices_devices_timeout(asyncio_mock, manager_mock):
    """Test Deako.find_devices times out and raises."""
    deako = Deako(Mock())

    deako.expected_devices = 42
    with pytest.raises(FindDevicesError) as exc:
        await deako.find_devices()

    assert str(exc.value) == (
        "Failed to find devices: Timed out waiting for devices to be found. "
        "Expected 42 devices but only found 0"
    )
    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices(asyncio_mock, manager_mock):
    """Test Deako.find_devices."""
    deako = Deako(Mock())

    deako.expected_devices = 42
    deako.devices = {}
    for i in range(42):
        deako.devices[str(i)] = i

    await deako.find_devices()

    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@patch("pydeako.deako._deako.asyncio", autospec=True)
@pytest.mark.asyncio
# pylint: disable-next=unused-argument
async def test_find_devices_send_request_fails(asyncio_mock, manager_mock):
    """Test Deako.find_devices when sending device list request fails."""
    deako = Deako(Mock())

    # Make send_get_device_list return False to simulate failure
    manager_mock.return_value.send_get_device_list.return_value = False

    with pytest.raises(FindDevicesError) as exc:
        await deako.find_devices()
    assert str(exc.value) == (
        "Failed to find devices: Failed to send device list request"
    )
    manager_mock.return_value.send_get_device_list.assert_called_once()


@patch("pydeako.deako._deako._Manager", autospec=True)
@pytest.mark.asyncio
async def test_control_device(manager_mock):
    """Test Deako.control_device."""
    device_id = Mock()
    power = Mock()
    dim = Mock()
    deako = Deako(Mock())

    await deako.control_device(device_id, power, dim)

    manager_mock.return_value.send_state_change.assert_called_once_with(
        device_id,
        power,
        dim,
    )


@pytest.mark.parametrize("device_exists", [True, False])
@pytest.mark.asyncio
async def test_get_name(device_exists):
    """Test Deako.get_name."""
    device_id = str(uuid4())
    device_name = str(uuid4())
    deako = Deako(Mock())

    if device_exists:
        deako.devices[device_id] = {
            "name": device_name,
        }

    name = deako.get_name(device_id)

    if device_exists:
        assert name == device_name
    else:
        assert name is None


@pytest.mark.parametrize("device_exists", [True, False])
@pytest.mark.asyncio
async def test_get_state(device_exists):
    """Test Deako.get_state."""
    device_id = str(uuid4())
    device_state = Mock()
    deako = Deako(Mock())

    if device_exists:
        deako.devices[device_id] = {
            "state": device_state,
        }

    state = deako.get_state(device_id)

    if device_exists:
        assert state == device_state
    else:
        assert state is None


@pytest.mark.parametrize("dimmable", [True, False])
@pytest.mark.parametrize("device_exists", [True, False])
@pytest.mark.asyncio
async def test_get_dimmable(device_exists, dimmable):
    """Test Deako.get_state."""
    device_id = str(uuid4())
    deako = Deako(Mock())

    if device_exists:
        deako.devices[device_id] = {
            "dimmable": dimmable,
        }

    is_dimmable = deako.is_dimmable(device_id)

    if device_exists:
        assert is_dimmable == dimmable
    else:
        assert is_dimmable is None


def test_update_state_dim_zero():
    """Test a dim of 0 is stored, not mistaken for 'no dim'."""
    deako = Deako(Mock())
    deako.record_device("Fake device 1", "uuid", True, True, 40)

    deako.update_state("uuid", True, 0)

    assert deako.get_state("uuid") == {"power": True, "dim": 0}


def test_update_state_without_dim_keeps_dim():
    """Test an event without dim keeps the last known dim."""
    deako = Deako(Mock())
    deako.record_device("Fake device 1", "uuid", True, True, 40)

    deako.update_state("uuid", False)

    assert deako.get_state("uuid") == {"power": False, "dim": 40}


@pytest.mark.parametrize("count", ["5", -1, True, 1.5, 10**9, None])
def test_incoming_json_device_list_invalid_count(count):
    """Test a device count that isn't a sensible integer is ignored."""
    deako = Deako(Mock())

    deako.incoming_json(
        {"type": "DEVICE_LIST", "data": {"number_of_devices": count}},
    )

    assert deako.expected_devices == 0


@patch("pydeako.deako._deako.DEVICE_LIST_POLLING_INTERVAL_S", 0.01)
@pytest.mark.asyncio
async def test_find_devices_invalid_count_raises_find_devices_error():
    """Test an invalid device count ends in FindDevicesError."""
    deako = Deako(Mock())
    manager = Mock()

    async def send_get_device_list() -> bool:
        deako.incoming_json(
            {"type": "DEVICE_LIST", "data": {"number_of_devices": "5"}},
        )
        return True

    manager.send_get_device_list = send_get_device_list
    deako.connection_manager = manager

    with pytest.raises(FindDevicesError):
        await deako.find_devices(timeout=0.05)


@pytest.mark.parametrize(
    "data",
    [
        {"name": 1, "uuid": "u", "state": {"power": True}},
        {"name": "Fake device 1", "uuid": None, "state": {"power": True}},
        {"name": "Fake device 1", "uuid": "u", "state": {"power": "on"}},
        {"name": "Fake device 1", "uuid": "u", "state": {"power": 2}},
        {"name": "Fake device 1", "uuid": "u", "state": "on"},
        {
            "name": "Fake device 1",
            "uuid": "u",
            "capabilities": 3,
            "state": {"power": True},
        },
    ],
)
def test_incoming_json_device_found_invalid(data):
    """Test a device with invalid fields isn't recorded."""
    deako = Deako(Mock())

    deako.incoming_json({"type": "DEVICE_FOUND", "data": data})

    assert not deako.get_devices()


@pytest.mark.parametrize("state", [{"power": "on"}, {"dim": 5}, "on"])
def test_incoming_json_event_invalid_state(state):
    """Test an event with an invalid state doesn't change the device."""
    deako = Deako(Mock())
    deako.record_device("Fake device 1", "u", True, False, 40)

    deako.incoming_json(
        {"type": "EVENT", "data": {"target": "u", "state": state}},
    )

    assert deako.get_state("u") == {"power": False, "dim": 40}


@patch("pydeako.deako._deako.MAX_DEVICES", 3)
def test_incoming_json_device_found_capped():
    """Test devices beyond the limit aren't recorded."""
    deako = Deako(Mock())

    for i in range(5):
        deako.incoming_json(
            {
                "type": "DEVICE_FOUND",
                "data": {
                    "name": "Fake device 1",
                    "uuid": f"u{i}",
                    "state": {"power": True},
                },
            }
        )

    assert len(deako.get_devices()) == 3


@pytest.mark.parametrize(
    ("dim", "stored"),
    [
        (50.0, 50),
        (49.6, 50),
        (0, 0),
        (101, None),
        (-1, None),
        ("5", None),
        (True, None),
        (float("nan"), None),
    ],
)
def test_incoming_json_device_found_dim(dim, stored):
    """Test dim is read as an int from 0 to 100; anything else is ignored."""
    deako = Deako(Mock())

    deako.incoming_json(
        {
            "type": "DEVICE_FOUND",
            "data": {
                "name": "Fake device 1",
                "uuid": "u",
                "capabilities": "power+dim",
                "state": {"power": True, "dim": dim},
            },
        }
    )

    assert deako.get_state("u") == {"power": True, "dim": stored}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"power": True, "dim": 50.0}, {"power": True, "dim": 50}),
        ({"power": 1, "dim": 60}, {"power": True, "dim": 60}),
        ({"power": 0}, {"power": False, "dim": 40}),
        # an invalid dim doesn't lose the power change
        ({"power": True, "dim": 101}, {"power": True, "dim": 40}),
        ({"power": True, "dim": "5"}, {"power": True, "dim": 40}),
    ],
)
def test_incoming_json_event_lenient_values(state, expected):
    """Test events with values Home Assistant or older firmware send."""
    deako = Deako(Mock())
    deako.record_device("Fake device 1", "u", True, False, 40)

    deako.incoming_json(
        {"type": "EVENT", "data": {"target": "u", "state": state}},
    )

    assert deako.get_state("u") == expected


@pytest.mark.parametrize(
    "message",
    [
        # handler raises: data isn't an object
        {"type": "EVENT", "data": None, "junk": "x" * 10_000},
        # invalid device with a huge, multi-line uuid
        {
            "type": "DEVICE_FOUND",
            "data": {
                "name": 1,
                "uuid": "u\r\n" * 5_000,
                "state": {"power": True},
            },
        },
        # invalid event target
        {
            "type": "EVENT",
            "data": {"target": ["t"] * 5_000, "state": {"power": "on"}},
        },
    ],
)
def test_incoming_json_logs_are_bounded(message, caplog):
    """Test logging an invalid message stays short and on one line."""
    deako = Deako(Mock())

    deako.incoming_json(message)

    assert caplog.records
    for record in caplog.records:
        text = record.getMessage()
        assert len(text) < 300
        assert "\n" not in text
        assert "\r" not in text
