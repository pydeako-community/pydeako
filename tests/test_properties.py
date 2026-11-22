"""Property tests for the stream parser, with generated input."""

import itertools
import json
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pydeako.deako.utils import _connection
from pydeako.deako.utils._connection import _Connection

_JSON_VALUES = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(), children, max_size=4)
    ),
    max_leaves=10,
)
_MESSAGES = st.lists(
    st.dictionaries(st.text(), _JSON_VALUES, max_size=4),
    max_size=6,
)


def _connection_collecting(received: list[dict[str, Any]]) -> _Connection:
    return _Connection("127.0.0.1:1", "fake", received.append)


# No deadline: each example goes through the event loop, which can stall.
@settings(deadline=None)
@given(
    messages=_MESSAGES,
    cuts=st.lists(st.integers(min_value=0)),
    ascii_only=st.booleans(),
)
@pytest.mark.asyncio
async def test_messages_survive_any_split(
    messages: list[dict[str, Any]],
    cuts: list[int],
    ascii_only: bool,
) -> None:
    """Test CRLF-separated messages arrive whole however reads split them."""
    stream = b"".join(
        json.dumps(message, ensure_ascii=ascii_only).encode() + b"\r\n"
        for message in messages
    )
    edges = sorted(
        {0, len(stream), *(cut % (len(stream) + 1) for cut in cuts)}
    )
    received: list[dict[str, Any]] = []
    connection = _connection_collecting(received)

    for start, end in itertools.pairwise(edges):
        connection.parse_data(stream[start:end])

    assert received == [json.loads(json.dumps(m)) for m in messages]
    assert connection.message_buffer == ""


@settings(deadline=None)
@given(chunks=st.lists(st.binary(max_size=200), max_size=20))
@pytest.mark.asyncio
async def test_any_bytes_are_handled(chunks: list[bytes]) -> None:
    """Test arbitrary input never raises and never fills the buffer."""
    limit = 64
    received: list[dict[str, Any]] = []
    connection = _connection_collecting(received)

    with patch.object(_connection, "MAX_MESSAGE_LENGTH", limit):
        for chunk in chunks:
            connection.parse_data(chunk)
            assert len(connection.message_buffer) <= limit

    assert all(isinstance(message, dict) for message in received)
