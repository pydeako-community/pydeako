"""Test the SocketConnection manager."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from pydeako.deako._manager import (
    CONNECTION_TIMEOUT_S,
    _is_device_busy,
    _Manager,
)
from pydeako.deako._request import _Request
from pydeako.discover import DevicesNotFoundError
from pydeako.models import state_change_request


def test_init():
    """Test _Manager.__init__."""
    manager = _Manager(AsyncMock(), Mock())

    assert manager is not None


@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.asyncio")
@pytest.mark.asyncio
async def test_init_connection_already_started(
    asyncio_mock,
    create_connection_mock,
):
    """Test _Manager.init_connection while already connecting."""
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())
    manager.state.connecting = True

    await manager.init_connection()

    asyncio_mock.create_task.assert_not_called()

    create_connection_mock.assert_not_called()


@patch("pydeako.deako._manager._Manager.create_connection_task")
@pytest.mark.asyncio
async def test_init_connection_get_address_no_devices(
    create_connection_mock,
):
    """
    Test _Manager.init_connection with no devices found.

    It schedules another attempt; once there is an address, devices
    should be found.
    """
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())

    get_address.side_effect = DevicesNotFoundError()

    await manager.init_connection()

    create_connection_mock.assert_called_once()

    assert manager.state.connecting is False


@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager._Connection")
@pytest.mark.asyncio
async def test_init_connection_timeout_connecting(
    connection_mock,
    create_connection_mock,
):
    """Test _Manager.init_connection with timeout."""
    address, name = Mock(), Mock()
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())

    get_address.return_value = address, name
    connection_mock_instance = connection_mock.return_value
    connection_mock_instance.wait_connected = AsyncMock(return_value=False)

    await manager.init_connection()

    connection_mock.assert_called_once_with(
        address,
        name,
        manager.incoming_json,
    )

    connection_mock_instance.wait_connected.assert_awaited_once_with(
        CONNECTION_TIMEOUT_S
    )
    connection_mock_instance.close.assert_called_once()
    assert manager.connection is None

    create_connection_mock.assert_called_once()  # this is the retry


@patch(
    "pydeako.deako._manager._Manager.maintain_connection_worker",
    new_callable=Mock,
)
@patch("pydeako.deako._manager._Connection")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_init_connection(
    asyncio_mock,
    connection_mock,
    maintain_connection_worker_mock,
):
    """Test _Manager.init_connection, success."""
    address, name = Mock(), Mock()
    get_address = AsyncMock()

    manager = _Manager(get_address, Mock())

    get_address.return_value = address, name
    connection_mock_instance = connection_mock.return_value
    connection_mock_instance.wait_connected = AsyncMock(return_value=True)

    await manager.init_connection()

    assert asyncio_mock.create_task.call_count == 1
    maintain_connection_worker_mock.assert_called_once()

    connection_mock.assert_called_once_with(
        address,
        name,
        manager.incoming_json,
    )

    assert not manager.state.connecting


def test_close():
    """Test _Manager.close."""
    maintain_worker = Mock()
    connection = Mock()

    manager = _Manager(AsyncMock(), Mock())
    manager.maintain_worker = maintain_worker
    manager.connection = connection

    manager.close()

    maintain_worker.cancel.assert_called_once()
    connection.close.assert_called_once()

    assert manager.state.canceled


@patch("pydeako.deako._manager.asyncio")
@patch(
    "pydeako.deako._manager._Manager._connect_in_background",
    new_callable=Mock,
)
def test_create_connection_task(connect_mock, asyncio_mock):
    """Test _Manager.create_connection_task."""
    manager = _Manager(AsyncMock(), Mock())

    manager.create_connection_task()

    asyncio_mock.create_task.assert_called_once()
    task = asyncio_mock.create_task.return_value
    task.add_done_callback.assert_called_once()
    connect_mock.assert_called_once()
    asyncio_mock.create_task.assert_called_once_with(connect_mock.return_value)
    assert len(manager.tasks) == 1


@patch("pydeako.deako._manager._Manager.close")
@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_maintain_connection_worker_canceled(
    asyncio_mock, create_connection_mock, close_mock
):
    """Test _Manager.maintain_connection_worker stops when canceled."""
    manager = _Manager(AsyncMock(), Mock())
    manager.state.canceled = True

    await manager.maintain_connection_worker()

    asyncio_mock.sleep.assert_called_once_with(10)
    close_mock.assert_not_called()
    create_connection_mock.assert_not_called()


@patch("pydeako.deako._manager._Manager.close")
@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.asyncio", autospec=True)
@pytest.mark.asyncio
async def test_maintain_connection_worker_no_pong(
    asyncio_mock, create_connection_mock, close_mock
):
    """Test _Manager.maintain_connection_worker doesn't receive pong."""
    manager = _Manager(AsyncMock(), Mock())

    await manager.maintain_connection_worker()

    assert len(asyncio_mock.sleep.mock_calls) == 2
    assert asyncio_mock.sleep.mock_calls[0].args[0] == 10
    assert asyncio_mock.sleep.mock_calls[1].args[0] == 10
    close_mock.assert_called_once()
    create_connection_mock.assert_called_once()


def test_incoming_json_pong():
    """Test _Manager.incoming_json with ping response."""
    incoming_json = {"type": "PING"}

    incoming_json_callback = Mock()

    manager = _Manager(AsyncMock(), incoming_json_callback)
    manager.pong_received = False

    manager.incoming_json(incoming_json)

    assert manager.pong_received


def test_incoming_json():
    """Test _Manager.incoming_json."""
    incoming_json = {"key": "value"}
    incoming_json_callback = Mock()

    manager = _Manager(AsyncMock(), incoming_json_callback)
    manager.pong_received = False

    manager.incoming_json(incoming_json)

    incoming_json_callback.assert_called_once_with(incoming_json)
    assert manager.pong_received is False


@patch("pydeako.deako._manager.RESPONSE_TIMEOUT_S", 0.01)
@patch("pydeako.deako._manager._Request")
@patch("pydeako.deako._manager.device_list_request")
@pytest.mark.asyncio
async def test_send_get_device_list(device_list_request_mock, request_mock):
    """Test _Manager.send_get_device_list."""
    client_name = Mock()
    request_mock_ret = Mock()

    request_mock.return_value = request_mock_ret

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    # Test with connection
    manager.connection = AsyncMock()
    result = await manager.send_get_device_list()
    device_list_request_mock.assert_called_once_with(source=client_name)
    request_mock.assert_called_once_with(device_list_request_mock.return_value)
    assert result is True

    # Test without connection
    manager.connection = None
    result = await manager.send_get_device_list()
    assert result is False


@patch("pydeako.deako._manager._Request")
@patch("pydeako.deako._manager.state_change_request")
@patch("pydeako.deako._manager._Manager.send_request")
@pytest.mark.asyncio
async def test_send_state_change(
    send_request_mock,
    state_change_request_mock,
    request_mock,
):
    """Test _Manager.send_state_change."""
    client_name = Mock()
    uuid = Mock()
    power = Mock()
    dim = Mock()
    request_mock_ret = Mock()

    request_mock.return_value = request_mock_ret

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    await manager.send_state_change(uuid, power, dim)

    request_mock.assert_called_once_with(
        state_change_request_mock.return_value,
    )
    state_change_request_mock.assert_called_once_with(
        uuid, power, dim, source=client_name
    )
    send_request_mock.assert_called_once_with(request_mock_ret)


@patch("pydeako.deako._manager._Request")
@pytest.mark.asyncio
async def test_send_request(
    request_mock,
):
    """Test _Manager.send_request with connection."""
    client_name = Mock()
    connection_mock = AsyncMock()

    request_mock.get_body_str.return_value = "some message"
    request_mock.get_transaction_id.return_value = None

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    manager.connection = connection_mock

    assert await manager.send_request(request_mock)

    connection_mock.send_data.assert_called_once_with("some message")


@patch("pydeako.deako._manager._Request")
@pytest.mark.asyncio
async def test_send_request_no_connection(
    request_mock,
):
    """Test _Manager.send_request without connection."""
    client_name = Mock()
    connection_mock = AsyncMock()

    request_mock.get_body_str.return_value = "some message"

    manager = _Manager(AsyncMock(), Mock(), client_name=client_name)

    assert not await manager.send_request(request_mock)

    connection_mock.send_data.assert_not_called()


def _device_connection(
    manager: _Manager,
    replies: Sequence[str | None] = (),
    log: list[tuple[str, str]] | None = None,
) -> tuple[AsyncMock, list[tuple[str, str]]]:
    """
    Mock a connection that answers requests like a device.

    replies says how to answer each write in turn: "ok", "busy",
    "malformed" or None for no answer; writes past the end of replies are
    answered "ok".
    """
    pending = list(replies)
    writes: list[tuple[str, str]] = [] if log is None else log

    def answer(response: dict[str, Any]) -> None:
        writes.append(("response", response["transactionId"]))
        manager.incoming_json(response)

    async def send_data(data: str) -> None:
        body = json.loads(data)
        writes.append(("write", body["transactionId"]))
        reply = pending.pop(0) if pending else "ok"
        if reply is None:
            return
        response: dict[str, Any] = {
            "transactionId": body["transactionId"],
            "type": body["type"],
            "status": "ok",
        }
        if reply != "ok":
            code = "DEVICE_BUSY" if reply == "busy" else "REQUEST_MALFORMED"
            response["status"] = "error"
            response["data"] = {"code": code}
        asyncio.get_running_loop().call_later(0.01, answer, response)

    connection = AsyncMock()
    connection.send_data.side_effect = send_data
    return connection, writes


def _control_requests(count: int) -> list[_Request]:
    """Build real state change requests for count devices."""
    return [
        _Request(state_change_request(f"uuid {i}", True, None))
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_send_request_waits_for_response():
    """Test concurrent requests are each sent after the previous response."""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(manager)
    requests = _control_requests(3)

    results = await asyncio.gather(
        *(manager.send_request(req) for req in requests)
    )

    assert results == [True, True, True]
    expected = []
    for req in requests:
        transaction_id = req.get_transaction_id()
        expected += [("write", transaction_id), ("response", transaction_id)]
    assert log == expected
    assert not manager.pending_responses


@patch("pydeako.deako._manager.RESPONSE_TIMEOUT_S", 0.05)
@pytest.mark.asyncio
async def test_send_request_no_response():
    """Test requests the device doesn't answer don't block the next one."""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager,
        replies=[None, None],
    )

    results = await asyncio.gather(
        *(manager.send_request(req) for req in _control_requests(2))
    )

    assert results == [True, True]
    assert [event for event, _ in log] == ["write", "write"]
    assert not manager.pending_responses


@patch("pydeako.deako._manager.BUSY_RETRY_DELAY_S", 0.01)
@pytest.mark.asyncio
async def test_send_request_retries_when_busy():
    """Test a request answered with DEVICE_BUSY is sent again."""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    connection, log = _device_connection(manager, replies=["busy", "ok"])
    manager.connection = connection
    request = _control_requests(1)[0]

    assert await manager.send_request(request)

    first, resent = (
        json.loads(call.args[0])
        for call in connection.send_data.call_args_list
    )
    first_id = first.pop("transactionId")
    resent_id = resent.pop("transactionId")
    assert first_id == request.get_transaction_id()
    assert resent_id != first_id
    assert resent == first
    assert log == [
        ("write", first_id),
        ("response", first_id),
        ("write", resent_id),
        ("response", resent_id),
    ]


@patch("pydeako.deako._manager.BUSY_RETRY_DELAY_S", 0.01)
@patch("pydeako.deako._manager.MAX_BUSY_RETRIES", 2)
@pytest.mark.asyncio
async def test_send_request_gives_up_when_busy():
    """Test a request is given up after MAX_BUSY_RETRIES busy answers."""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager,
        replies=["busy", "busy", "busy", "ok"],
    )

    assert not await manager.send_request(_control_requests(1)[0])

    assert [event for event, _ in log].count("write") == 3


@pytest.mark.asyncio
async def test_send_request_other_error_not_retried():
    """Test a request answered with an error other than busy isn't resent."""
    manager = _Manager(AsyncMock(), Mock(), client_name=Mock())
    manager.connection, log = _device_connection(
        manager,
        replies=["malformed"],
    )

    assert await manager.send_request(_control_requests(1)[0])

    assert [event for event, _ in log].count("write") == 1


@pytest.mark.asyncio
async def test_incoming_json_resolves_pending_response():
    """Test _Manager.incoming_json hands a response to its waiting request."""
    callback = Mock()
    manager = _Manager(AsyncMock(), callback, client_name=Mock())
    pending = asyncio.get_running_loop().create_future()
    manager.pending_responses["some id"] = pending
    message = {"transactionId": "some id", "type": "CONTROL", "status": "ok"}

    manager.incoming_json(message)

    assert pending.result() == message
    callback.assert_called_once_with(message)


@pytest.mark.parametrize("data", ["x", 1, ["DEVICE_BUSY"], None])
def test_is_device_busy_without_object_data(data):
    """Test an error response whose data isn't an object."""
    assert not _is_device_busy({"status": "error", "data": data})


def test_incoming_json_unhashable_transaction_id():
    """Test a message whose transactionId can't be looked up."""
    callback = Mock()
    manager = _Manager(AsyncMock(), callback)
    message = {"transactionId": ["x"], "type": "EVENT"}

    manager.incoming_json(message)

    callback.assert_called_once_with(message)


@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager.PING_WORKER_WAIT_S", 0)
@pytest.mark.asyncio
async def test_maintain_connection_worker_error_reconnects(
    create_connection_mock,
):
    """Test an unexpected error while pinging leads to a reconnect."""
    manager = _Manager(AsyncMock(), Mock())
    manager.send_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom"),
    )
    worker = asyncio.create_task(manager.maintain_connection_worker())
    manager.maintain_worker = worker

    await asyncio.wait([worker])

    assert manager.maintain_worker is None
    create_connection_mock.assert_called_once()


@pytest.mark.asyncio
async def test_init_connection_unexpected_error_allows_retry():
    """Test an unexpected error doesn't leave the manager 'connecting'."""
    get_address = AsyncMock(side_effect=RuntimeError("boom"))
    manager = _Manager(get_address, Mock())

    with pytest.raises(RuntimeError):
        await manager.init_connection()

    assert not manager.state.connecting
    with pytest.raises(RuntimeError):
        await manager.init_connection()
    assert get_address.await_count == 2


@patch("pydeako.deako._manager._Connection")
@pytest.mark.asyncio
async def test_init_connection_cancelled_closes_connection(connection_mock):
    """Test cancelling while waiting to connect closes the connection."""

    async def never_connects(_timeout: float) -> bool:
        await asyncio.Event().wait()
        return True

    connection_mock.return_value.wait_connected = never_connects
    manager = _Manager(AsyncMock(return_value=("1.2.3.4:23", "dev")), Mock())
    task = asyncio.create_task(manager.init_connection())
    await asyncio.sleep(0.05)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    connection_mock.return_value.close.assert_called_once()
    assert not manager.state.connecting


@patch("pydeako.deako._manager.CONNECTION_RETRY_DELAY_S", 0)
@patch("pydeako.deako._manager._Manager.maintain_connection_worker")
@patch("pydeako.deako._manager._Connection")
@pytest.mark.asyncio
async def test_background_connect_error_retries(connection_mock, worker_mock):
    """Test an unexpected error in a background connect is retried."""
    connection_mock.return_value.wait_connected = AsyncMock(return_value=True)
    get_address = AsyncMock(
        side_effect=[RuntimeError("boom"), ("1.2.3.4:23", "dev")],
    )
    manager = _Manager(get_address, Mock())

    manager.create_connection_task()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if manager.connection is not None:
            break

    assert get_address.await_count == 2
    assert manager.connection is connection_mock.return_value
    worker_mock.assert_called_once()
    manager.close()


@patch("pydeako.deako._manager.CONNECTION_RETRY_DELAY_S", 0)
@patch("pydeako.deako._manager._Manager.send_request")
@patch("pydeako.deako._manager._Connection")
@pytest.mark.asyncio
async def test_reconnect_error_after_lost_connection_retries(
    connection_mock,
    send_request_mock,
):
    """Test an unexpected error is also retried after a lost connection."""
    connection_mock.return_value.wait_connected = AsyncMock(return_value=True)
    get_address = AsyncMock(
        side_effect=[RuntimeError("boom"), ("1.2.3.4:23", "dev")],
    )
    manager = _Manager(get_address, Mock())

    # no pong arrives, so the worker closes the connection and reconnects
    with patch("pydeako.deako._manager.PING_WORKER_WAIT_S", 0):
        await manager.maintain_connection_worker()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if manager.connection is not None:
            break

    send_request_mock.assert_awaited_once()  # the ping
    assert get_address.await_count == 2
    assert manager.connection is connection_mock.return_value
    manager.close()


@patch("pydeako.deako._manager._Manager.maintain_connection_worker")
@patch("pydeako.deako._manager._Manager.create_connection_task")
@patch("pydeako.deako._manager._Connection")
@pytest.mark.asyncio
async def test_init_connection_dropped_at_once_is_kept(
    connection_mock,
    create_connection_mock,
    worker_mock,
):
    """
    Test a connection that drops right after connecting is still kept.

    Retrying at once could loop against a bridge that accepts and closes;
    the ping worker replaces the connection after PING_WORKER_WAIT_S.
    """
    connection_mock.return_value.wait_connected = AsyncMock(return_value=True)
    connection_mock.return_value.is_connected.return_value = False
    manager = _Manager(AsyncMock(return_value=("1.2.3.4:23", "dev")), Mock())

    await manager.init_connection()

    connection_mock.return_value.close.assert_not_called()
    create_connection_mock.assert_not_called()
    worker_mock.assert_called_once()
    assert manager.connection is connection_mock.return_value


@patch("pydeako.deako._manager.CONNECTION_RETRY_DELAY_S", 0)
@pytest.mark.asyncio
async def test_background_connect_error_not_retried_when_closed():
    """Test a closed manager doesn't retry a failed background connect."""
    get_address = AsyncMock(side_effect=RuntimeError("boom"))
    manager = _Manager(get_address, Mock())
    manager.state.canceled = True

    manager.create_connection_task()
    for _ in range(10):
        await asyncio.sleep(0.01)

    assert get_address.await_count == 1
    assert not manager.tasks
