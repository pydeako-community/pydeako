"""
Class that manages a socket connection to a deako device.
"""

import asyncio
import codecs
import json
import logging
import re
from collections.abc import Callable
from enum import Enum
from typing import Any

from ._socket import _SocketConnection

_LOGGER: logging.Logger = logging.getLogger(__package__)

# Messages to and from the device are single-line JSON separated by CRLF.
MESSAGE_DELIMITER = "\r\n"
# Longest incomplete message kept while waiting for the rest of it. It's
# checked after each read, so one read more can pass.
MAX_MESSAGE_LENGTH = 64 * 1024
_JSON_WHITESPACE = " \t\r\n"
_JSON_DECODER = json.JSONDecoder()
# A line that starts a new message, after optional whitespace.
_NEXT_MESSAGE = re.compile(r"\n[ \t\r]*\{")


class UnknownStateException(Exception):
    """Unknown state."""


class ConnectionState(Enum):
    """Enum for connection states."""

    UNKNOWN = -1
    NOT_STARTED = 0
    CONNECTED = 1
    ERROR = 2
    CLOSED = 3


class _Connection:
    """
    Representation of a local socket connection to a Deako device.
    Continuously reads socket for messages async.
    """

    # pylint: disable=too-many-instance-attributes

    address: str
    name: str
    message_buffer: str
    loop: asyncio.AbstractEventLoop
    state: ConnectionState
    socket: _SocketConnection
    tasks: set[asyncio.Task[None]]

    def __init__(
        self,
        address: str,
        name: str,
        on_data_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        """Setup and start a socket connection."""
        self.address = address
        self.name = name
        self.loop = asyncio.get_running_loop()
        self.state = ConnectionState.NOT_STARTED
        self.on_data_callback = on_data_callback
        self.message_buffer = ""
        # Keeps a multi-byte character that is split between reads.
        self.utf8_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.skipping_line = False
        # Bad input is logged as a warning once, then at debug.
        self.bad_input_logged = False
        self.socket = _SocketConnection(address, self.loop)
        self.tasks = set()
        self.init_run()

    async def send_data(self, data_to_send: str) -> None:
        """Send data to socket."""
        _LOGGER.debug("[%s] Sending data: %s", self.address, data_to_send)
        try:
            await self.socket.send_bytes(
                str.encode(data_to_send + MESSAGE_DELIMITER)
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.error("Error sending data: %s", exc)
            self.state = ConnectionState.ERROR

    async def read_socket(self) -> None:
        """Read data from socket."""
        try:
            data = await self.socket.read_bytes()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _LOGGER.error("Error receiving data: %s", exc)
            self.state = ConnectionState.ERROR
            return

        if not data:
            # An empty read means the device closed the connection. Reading
            # again would return at once and spin without yielding.
            _LOGGER.warning("%s closed the connection", self.format_name())
            self.state = ConnectionState.ERROR
            return

        self.parse_data(data)

    def parse_data(self, data: bytes) -> None:
        """
        Parse incoming bytes into JSON messages.

        A message can arrive over several reads, and a read can hold several
        messages. Input that isn't a JSON object is skipped up to the next
        line that starts one, so a bad message doesn't block later ones.
        """
        text = self.utf8_decoder.decode(data)
        _LOGGER.debug(
            "[%s] Raw message received: %r", self.format_name(), text,
        )
        if self.skipping_line:
            line_end = text.find("\n")
            if line_end == -1:
                return
            text = text[line_end + 1:]
            self.skipping_line = False

        buffer = self.message_buffer + text
        while buffer := buffer.lstrip(_JSON_WHITESPACE):
            try:
                message, end = _JSON_DECODER.raw_decode(buffer)
            except (ValueError, RecursionError):
                next_message = _NEXT_MESSAGE.search(buffer)
                if next_message is None:
                    break  # incomplete, wait for more data
                self._log_bad_input(
                    "Skipping invalid message from %s", self.format_name(),
                )
                buffer = buffer[next_message.start() + 1:]
                continue
            buffer = buffer[end:]
            if isinstance(message, dict):
                self.on_data_callback(message)
            else:
                self._log_bad_input(
                    "Skipping non-object message from %s", self.format_name(),
                )

        if len(buffer) > MAX_MESSAGE_LENGTH:
            self._log_bad_input(
                "Dropping a message over %d characters from %s",
                MAX_MESSAGE_LENGTH,
                self.format_name(),
            )
            # Keep what follows the last complete line, unless that part is
            # itself too long; then skip until the current line ends.
            rest = buffer[buffer.rfind("\n") + 1:]
            self.skipping_line = len(rest) > MAX_MESSAGE_LENGTH
            buffer = "" if self.skipping_line else rest
        self.message_buffer = buffer

    def _log_bad_input(self, message: str, *args: object) -> None:
        """Log bad input as a warning the first time, then at debug."""
        level = logging.DEBUG if self.bad_input_logged else logging.WARNING
        self.bad_input_logged = True
        _LOGGER.log(level, message, *args)

    def init_run(self) -> None:
        """Init the run sequence and store run task."""
        # RUF006
        # pylint: disable-next=line-too-long
        # noqa keep reference via: https://stackoverflow.com/questions/71938799/python-asyncio-create-task-really-need-to-keep-a-reference
        # even if we don't care
        task = self.loop.create_task(self.run())

        def remove_task(_task: asyncio.Task[None]) -> None:
            self.tasks.remove(_task)

        task.add_done_callback(remove_task)
        self.tasks.add(task)

    def close(self) -> None:
        """Close our socket and cancel all pending tasks."""
        self.socket.close_socket()
        for task in self.tasks:
            task.cancel()

    def is_connected(self) -> bool:
        """Return whether or not connected."""
        return self.state == ConnectionState.CONNECTED

    async def run(self) -> None:
        """State machine."""
        while True:
            if self.state == ConnectionState.NOT_STARTED:
                try:
                    await self.socket.connect_socket()
                    self.state = ConnectionState.CONNECTED
                    _LOGGER.info(
                        "Connected to Deako local integrations with %s",
                        self.format_name(),
                    )
                # pylint: disable-next=broad-exception-caught
                except Exception as exc:
                    _LOGGER.error(
                        "Failed to connect %s because %s",
                        self.format_name(),
                        exc,
                    )
                    self.state = ConnectionState.ERROR
            elif self.state == ConnectionState.CONNECTED:
                try:
                    await self.read_socket()
                    # A read returns without suspending while data is
                    # waiting, so let other tasks run between reads.
                    await asyncio.sleep(0)
                # pylint: disable-next=broad-exception-caught
                except Exception as exc:
                    _LOGGER.error(
                        "Failed to read socket %s because %s",
                        self.format_name(),
                        exc,
                    )
                    self.state = ConnectionState.ERROR
            elif self.state == ConnectionState.ERROR:
                try:
                    self.close()
                    self.state = ConnectionState.CLOSED
                # pylint: disable-next=broad-exception-caught
                except Exception as exc:
                    _LOGGER.error(
                        "Failed to close socket %s because %s",
                        self.format_name(),
                        exc,
                    )
                    self.state = ConnectionState.CLOSED
            elif self.state == ConnectionState.CLOSED:
                # this socket is toast
                break
            else:
                _LOGGER.error("Unknown state: %s", self.state)
                raise UnknownStateException(f"Unknown state: {self.state}")

    def format_name(self) -> str:
        """Format name."""
        return f"{self.name}@{self.address}"
