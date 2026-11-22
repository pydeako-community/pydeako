"""Test the README's usage examples run against the fake bridge."""

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import pydeako
from pydeako import (
    Deako,
    DevicesNotFoundError,
    FindDevicesError,
    deako,
    discover,
)

from .fake_bridge import FakeDeakoConnect

README = Path(__file__).parent.parent / "README.md"


def _python_blocks(markdown: str) -> list[str]:
    """Return the fenced code blocks marked python or left unmarked."""
    blocks = []
    lines: list[str] = []
    language = None
    for line in markdown.splitlines():
        if not line.startswith("```"):
            lines.append(line)
        elif language is None:
            lines = []
            language = line[3:].strip()
        else:
            if language in ("", "python"):
                blocks.append("\n".join(lines) + "\n")
            language = None
    return blocks


# The code blocks that use the library.
EXAMPLES = [
    block
    for block in _python_blocks(README.read_text(encoding="utf-8"))
    if "pydeako" in block
]


def test_readme_has_examples() -> None:
    """Test the examples were found, so the test below runs something."""
    assert len(EXAMPLES) >= 2


@pytest.mark.parametrize("found", [True, False], ids=["found", "not found"])
@pytest.mark.parametrize("source", EXAMPLES, ids=range(len(EXAMPLES)))
@pytest.mark.asyncio
async def test_readme_example_runs(
    source: str,
    found: bool,
    bridge: FakeDeakoConnect,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test an example runs to its end, with and without a device found."""
    clients: list[deako.Deako] = []
    started: list[Coroutine[Any, Any, Any]] = []

    def make_client(*args: Any, **kwargs: Any) -> deako.Deako:
        clients.append(Deako(*args, **kwargs))
        return clients[-1]

    async def no_address() -> tuple[str, str]:
        await asyncio.sleep(0.01)
        raise DevicesNotFoundError()

    def make_discoverer(*_args: object) -> SimpleNamespace:
        # No zeroconf: the examples must not reach the network in tests.
        return SimpleNamespace(
            get_address=bridge.get_address if found else no_address,
            stop=lambda: None,
        )

    for module in (pydeako, deako):
        monkeypatch.setattr(module, "Deako", make_client)
    for module in (pydeako, discover):
        monkeypatch.setattr(module, "DeakoDiscoverer", make_discoverer)

    with monkeypatch.context() as patched:
        # Collect what the example passes to asyncio.run and await it here.
        patched.setattr(asyncio, "run", started.append)
        # pylint: disable-next=exec-used
        exec(compile(source, str(README), "exec"), {"__name__": "__main__"})
    assert len(started) == 1
    try:
        await started[0]
    except (DevicesNotFoundError, FindDevicesError):
        # An example may let the library's own errors end it, but only
        # when there is no device.
        assert not found
    finally:
        for client in clients:
            await client.disconnect()

    assert bridge.connections == (len(clients) if found else 0)
    # An example that shows control_device really controls a device.
    assert bool(bridge.requests("CONTROL")) == (
        found and "control_device" in source
    )
