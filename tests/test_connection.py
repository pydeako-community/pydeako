"""Tests _Connection."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, Mock, call, patch
from uuid import uuid4

import pytest

from pydeako.deako.utils._connection import (
    ConnectionState,
    UnknownStateError,
    _Connection,
)
from pydeako.deako.utils._socket import _SocketConnection


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection",
    spec=_SocketConnection,
)
def test_init(socket_connection_mock, asyncio_mock):
    """Test _Connection.__init__."""
    address, name = Mock(), Mock()
    loop_mock = AsyncMock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    assert conn is not None
    asyncio_mock.get_running_loop.assert_called_once()
    assert conn.state == ConnectionState.NOT_STARTED
    # nothing runs until start()
    loop_mock.create_task.assert_not_called()
    assert not conn.tasks


@pytest.mark.parametrize("raise_error", [True, False])
@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection",
    spec=_SocketConnection,
)
@pytest.mark.asyncio
async def test_send_data(
    socket_connection_mock,
    asyncio_mock,
    raise_error,
):
    """Test _Connection.send_data."""
    data = str(uuid4())
    data_bytes = str.encode(data + "\r\n")
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)
    socket_connection_mock_instance = socket_connection_mock.return_value

    if raise_error:
        socket_connection_mock_instance.send_bytes.side_effect = Exception()

    await conn.send_data(data)

    socket_connection_mock_instance.send_bytes.assert_called_once_with(
        data_bytes
    )
    assert (
        conn.state == ConnectionState.ERROR
        if raise_error
        else ConnectionState.NOT_STARTED
    )


@pytest.mark.parametrize("raise_read_error", [True, False])
@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@patch("pydeako.deako.utils._connection._Connection.parse_data")
@pytest.mark.asyncio
async def test_read_socket(
    parse_data_mock, socket_connection_mock, asyncio_mock, raise_read_error
):
    """Test _Connection.read_socket."""
    read_bytes = str.encode(str(uuid4()))
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)
    socket_connection_mock_instance = socket_connection_mock.return_value
    socket_connection_mock_instance.read_bytes.return_value = read_bytes

    if raise_read_error:
        socket_connection_mock_instance.read_bytes.side_effect = Exception()

    await conn.read_socket()

    socket_connection_mock_instance.read_bytes.assert_called_once()
    if not raise_read_error:
        parse_data_mock.assert_called_once_with(read_bytes)
    assert (
        conn.state == ConnectionState.ERROR
        if raise_read_error
        else ConnectionState.NOT_STARTED
    )


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@patch("pydeako.deako.utils._connection._Connection.parse_data")
@pytest.mark.asyncio
async def test_read_socket_closed_by_device(
    parse_data_mock, socket_connection_mock, asyncio_mock
):
    """Test _Connection.read_socket, device closed the connection (EOF)."""
    asyncio_mock.get_running_loop.return_value = Mock()
    conn = _Connection(Mock(), Mock(), Mock())
    socket_connection_mock.return_value.read_bytes.return_value = b""

    await conn.read_socket()

    parse_data_mock.assert_not_called()
    assert conn.state == ConnectionState.ERROR


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_parse_data_one_item(socket_connection_mock, asyncio_mock):
    """Test _Connection.parse_data, one message."""
    json_message = {"key": "value"}
    one_item = str.encode(json.dumps(json_message))
    address, name = Mock(), Mock()
    loop_mock = Mock()
    on_data_callback = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, on_data_callback)

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.parse_data(one_item)

    on_data_callback.assert_called_once_with(json_message)


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_parse_data_multiple_item(socket_connection_mock, asyncio_mock):
    """Test _Connection.parse_data, multiple messages."""
    json_message = {"key": "value"}
    json_message2 = {"key2": "value2"}

    items = str.encode(
        "\r\n".join(
            [json.dumps(item) for item in [json_message, json_message2]]
        )
    )
    address, name = Mock(), Mock()
    loop_mock = Mock()
    on_data_callback = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, on_data_callback)

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.parse_data(items)

    on_data_callback.assert_has_calls(
        [
            call(json_message),
            call(json_message2),
        ]
    )


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_parse_data_partial(socket_connection_mock, asyncio_mock):
    """Test _Connection.parse_data, one message split up."""
    json_message = {"key": "value"}
    json_message_data = json.dumps(json_message)

    items = str.encode(
        "\r\n".join(
            [
                json_message_data[0 : len(json_message_data) // 2],
                json_message_data[len(json_message_data) // 2 :],
            ]
        )
    )
    address, name = Mock(), Mock()
    loop_mock = Mock()
    on_data_callback = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, on_data_callback)

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.parse_data(items)

    on_data_callback.assert_has_calls(
        [
            call(json_message),
        ]
    )


def _parsed(*chunks: bytes) -> list[object]:
    """Feed chunks to a _Connection's parser and return what it delivered."""
    delivered: list[object] = []
    with (
        patch("pydeako.deako.utils._connection.asyncio"),
        patch(
            "pydeako.deako.utils._connection._SocketConnection",
            spec=_SocketConnection,
        ),
    ):
        conn = _Connection("127.0.0.1:23", "dev", delivered.append)
    for chunk in chunks:
        conn.parse_data(chunk)
    return delivered


def test_parse_data_utf8_split_across_reads():
    """Test a multi-byte character split between two reads."""
    message = json.dumps(
        {"name": "Fake device é"}, ensure_ascii=False
    ).encode()
    cut = message.index("é".encode()) + 1

    assert _parsed(message[:cut], message[cut:] + b"\r\n") == [
        {"name": "Fake device é"},
    ]


def test_parse_data_skips_invalid_line():
    """Test a line that isn't JSON doesn't block later messages."""
    assert _parsed(b"not json\r\n", b'{"a": 1}\r\n', b'{"b": 2}\r\n') == [
        {"a": 1},
        {"b": 2},
    ]


def test_parse_data_skips_invalid_object_line():
    """Test a broken object on its own line doesn't block later messages."""
    assert _parsed(b'{"a": }\r\n{"b": 2}\r\n') == [{"b": 2}]


def test_parse_data_resyncs_on_indented_message():
    """Test recovery when the next message starts after whitespace."""
    assert _parsed(b'{"a": }\r\n  {"b": 2}\r\n\t{"c": 3}\r\n') == [
        {"b": 2},
        {"c": 3},
    ]


def test_parse_data_skips_non_objects():
    """Test JSON that isn't an object is not delivered."""
    assert _parsed(b'[1, 2]\r\n"text"\r\n7\r\n{"a": 1}\r\n') == [{"a": 1}]


def test_parse_data_skips_unparsable_values():
    """Test a number too long to convert doesn't break the connection."""
    huge_number = b'{"a": ' + b"9" * 5000 + b"}\r\n"

    assert _parsed(huge_number, b'{"b": 2}\r\n') == [{"b": 2}]


def test_parse_data_survives_deep_nesting():
    """Test very deep nesting doesn't break the connection."""
    deep = b'{"a": ' + b"[" * 100_000 + b"]" * 100_000 + b"}\r\n"

    delivered = _parsed(deep, b'{"b": 2}\r\n')

    # Whether Python can parse this depth depends on the platform's stack
    # (it raises RecursionError on macOS, parses on Linux); either way the
    # next message must still arrive.
    assert delivered[-1] == {"b": 2}
    assert len(delivered) in (1, 2)


def test_parse_data_back_to_back_messages():
    """Test two objects in one read without a delimiter between them."""
    assert _parsed(b'{"a": 1}{"b": 2}\r\n') == [{"a": 1}, {"b": 2}]


def test_parse_data_drops_oversized_message():
    """Test an endless message is dropped instead of growing forever."""
    endless = [b'{"a": "' + b"x" * 2040] * 64  # 128 KiB, never closed

    assert _parsed(*endless, b'"}\r\n{"b": 2}\r\n') == [{"b": 2}]


def test_parse_data_warns_once_about_bad_input(caplog):
    """Test bad input is logged as a warning once, then at debug."""
    bad_lines = b"7\n" * 50 + b"{\n" * 50
    endless = [b'{"a": "' + b"x" * 2040] * 64  # 128 KiB, never closed

    with caplog.at_level(logging.DEBUG, logger="pydeako"):
        delivered = _parsed(bad_lines, *endless, b'"}\r\n{"b": 2}\r\n')

    assert delivered == [{"b": 2}]
    levels = [
        record.levelno
        for record in caplog.records
        if record.getMessage().startswith(("Skipping", "Dropping"))
    ]
    assert len(levels) == 101  # 50 non-objects, 50 bad lines, 1 too long
    assert levels[0] == logging.WARNING
    assert levels.count(logging.WARNING) == 1


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection",
    spec=_SocketConnection,
)
@patch("pydeako.deako.utils._connection._Connection.run", new_callable=Mock)
def test_start(run_mock, socket_connection_mock, asyncio_mock):
    """Test _Connection.__init__."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    task_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    loop_mock.create_task.return_value = task_mock

    conn = _Connection(address, name, Mock())
    conn.start()

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    run_mock.assert_called_once()
    loop_mock.create_task.assert_called_once_with(run_mock.return_value)
    task_mock.add_done_callback.assert_called_once()
    assert task_mock in conn.tasks


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_close(socket_connection_mock, asyncio_mock):
    """Test _Connection.close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    task1 = Mock()
    task2 = Mock()

    conn.tasks = {task1, task2}

    conn.close()

    socket_connection_mock.return_value.close_socket.assert_called_once()
    task1.cancel.assert_called_once()
    task2.cancel.assert_called_once()


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ConnectionState.CONNECTED, True),
        (ConnectionState.CLOSED, False),
        (ConnectionState.ERROR, False),
        (ConnectionState.NOT_STARTED, False),
    ],
)
@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
def test_is_connected(socket_connection_mock, asyncio_mock, state, expected):
    """Test _Connection.is_connected."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    conn.state = state

    assert conn.is_connected() == expected


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_connect_failure(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, connect exception calls close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    socket_connection_instance = socket_connection_mock.return_value

    socket_connection_instance.connect_socket.side_effect = Exception()

    await conn.run()

    socket_connection_instance.close_socket.assert_called_once()


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_read_failure(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, read exception calls close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock
    asyncio_mock.sleep = AsyncMock()  # run() yields after each read

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.CONNECTED

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    socket_connection_instance = socket_connection_mock.return_value

    socket_connection_instance.read_bytes.side_effect = Exception()

    await conn.run()

    socket_connection_instance.close_socket.assert_called_once()


@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_yields_between_reads(socket_connection_mock):
    """Test other tasks get a turn while the device keeps sending."""
    reads = 5
    data = [b'{"a": 1}\r\n'] * reads + [b""]
    # like sock_recv with data waiting, these reads return without suspending
    socket_connection_mock.return_value.read_bytes.side_effect = data
    conn = _Connection("1.2.3.4:23", "dev", Mock())
    conn.state = ConnectionState.CONNECTED
    turns = 0

    async def other_task() -> None:
        nonlocal turns
        while True:
            turns += 1
            await asyncio.sleep(0)

    task = asyncio.create_task(other_task())
    await conn.run()
    task.cancel()

    assert turns >= reads


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_close_failure(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, error calling close."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.ERROR

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    socket_connection_instance = socket_connection_mock.return_value

    socket_connection_instance.close_socket.side_effect = Exception()

    await conn.run()

    socket_connection_instance.close_socket.assert_called_once()


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_closed(socket_connection_mock, asyncio_mock):
    """Test _Connection.run, error state results in closing."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.ERROR

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    await conn.run()

    assert conn.state == ConnectionState.CLOSED


@patch("pydeako.deako.utils._connection.asyncio")
@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_unknown_state_raises(
    socket_connection_mock, asyncio_mock
):
    """Test _Connection.run, unknown state raises."""
    address, name = Mock(), Mock()
    loop_mock = Mock()
    asyncio_mock.get_running_loop.return_value = loop_mock

    conn = _Connection(address, name, Mock())
    conn.state = ConnectionState.UNKNOWN

    socket_connection_mock.assert_called_once_with(address, loop_mock)

    with pytest.raises(UnknownStateError):
        await conn.run()


@patch(
    "pydeako.deako.utils._connection._SocketConnection", spec=_SocketConnection
)
@pytest.mark.asyncio
async def test_state_machine_callback_error_closes(socket_connection_mock):
    """Test _Connection.run closes when handling a message raises."""
    on_data = Mock(side_effect=RuntimeError("boom"))
    socket_connection_instance = socket_connection_mock.return_value
    socket_connection_instance.read_bytes.return_value = b'{"type": "X"}\r\n'
    conn = _Connection("1.2.3.4:23", "dev", on_data)
    conn.state = ConnectionState.CONNECTED

    await conn.run()

    on_data.assert_called_once_with({"type": "X"})
    assert conn.state == ConnectionState.CLOSED
    socket_connection_instance.close_socket.assert_called_once()
