"""Test the discovery client, patching the address pool and zeroconf lib."""

from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from zeroconf import Zeroconf

from pydeako.discover._address_pool import EmptyAddressPoolError, _AddressPool
from pydeako.discover._discover import (
    DeakoDiscoverer,
    DeakoListener,
    DevicesNotFoundError,
)


@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
def test_deako_discoverer_init(
    mock_address_pool, mock_deako_listener, mock_service_browser_init
):
    """Test init of Discoverer.__init__."""
    zc_mock = Mock(Zeroconf)

    deako_discoverer = DeakoDiscoverer(zc_mock)

    assert deako_discoverer is not None

    mock_address_pool.assert_called_once_with()
    mock_deako_listener.assert_called_once_with(
        mock_address_pool.return_value.set_service,
        mock_address_pool.return_value.remove_service,
    )

    mock_service_browser_init.assert_called_once_with(
        zc_mock,
        "_deako._tcp.local.",
        mock_deako_listener.return_value,
    )


@patch("pydeako.discover._discover.Zeroconf", spec=Zeroconf)
@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
def test_deako_discoverer_init_no_zc(
    mock_address_pool, mock_deako_listener, mock_service_browser_init, mock_zc
):
    """Test init of Discoverer.__init__ with no zc passed in."""
    deako_discoverer = DeakoDiscoverer()

    assert deako_discoverer is not None

    mock_zc.assert_called_once()
    mock_address_pool.assert_called_once_with()
    mock_deako_listener.assert_called_once_with(
        mock_address_pool.return_value.set_service,
        mock_address_pool.return_value.remove_service,
    )

    mock_service_browser_init.assert_called_once_with(
        mock_zc.return_value,
        "_deako._tcp.local.",
        mock_deako_listener.return_value,
    )


@patch("pydeako.discover._discover.TIMEOUT_S", 1)
@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
@pytest.mark.asyncio
async def test_deako_discoverer_get_address_success(
    mock_address_pool, mock_deako_listener, mock_service_browser_init
):  # pylint: disable=unused-argument
    """Test Discoverer.get_address success."""
    # use instance as the mock as this is what will be called
    mock_address_pool = mock_address_pool.return_value
    zc_mock = Mock(Zeroconf)
    mock_address, mock_name = str(uuid4()), str(uuid4())

    mock_address_pool.available_addresses.return_value = 1
    mock_address_pool.get_address.return_value = mock_address, mock_name

    deako_discoverer = DeakoDiscoverer(zc_mock)

    assert deako_discoverer is not None

    address, name = await deako_discoverer.get_address()

    assert address == mock_address
    assert name == mock_name


@patch("pydeako.discover._discover.TIMEOUT_S", 1)
@patch("pydeako.discover._discover.ServiceBrowser.__init__")
@patch("pydeako.discover._discover.DeakoListener", spec=DeakoListener)
@patch("pydeako.discover._discover._AddressPool", spec=_AddressPool)
@pytest.mark.asyncio
async def test_deako_discoverer_get_address_timeout(
    mock_address_pool, mock_deako_listener, mock_service_browser_init
):  # pylint: disable=unused-argument
    """Test Discoverer.get_address times out with DevicesNotFoundError."""
    # use instance as the mock as this is what will be called
    mock_address_pool = mock_address_pool.return_value
    zc_mock = Mock(Zeroconf)

    mock_address_pool.available_addresses.return_value = 0
    mock_address_pool.get_address.side_effect = EmptyAddressPoolError()

    deako_discoverer = DeakoDiscoverer(zc_mock)

    assert deako_discoverer is not None

    with pytest.raises(DevicesNotFoundError):
        await deako_discoverer.get_address()


def test_listener_init():
    """Test Listener.__init__."""
    add_callback = Mock()
    remove_callback = Mock()

    listener = DeakoListener(add_callback, remove_callback)
    assert listener is not None
    add_callback.assert_not_called()
    remove_callback.assert_not_called()


@patch("socket.inet_ntoa")
def test_listener_add_service(mock_inet_ntoa):
    """Test Listener.add_service."""
    address, name = str(uuid4()), str(uuid4())
    converted_address = str(uuid4())
    _type = str(uuid4())
    name = str(uuid4())
    port = 69

    mock_inet_ntoa.return_value = converted_address

    add_callback = Mock()
    remove_callback = Mock()
    zc_mock = Mock(Zeroconf)
    mock_info = Mock()
    mock_info.addresses = [address]
    mock_info.port = port

    zc_mock.get_service_info.return_value = mock_info

    listener = DeakoListener(add_callback, remove_callback)

    listener.add_service(zc_mock, _type, name)

    mock_inet_ntoa.assert_called_with(address)
    zc_mock.get_service_info.assert_called_with(_type, name)
    add_callback.assert_called_once_with(
        name,
        [f"{converted_address}:{port}"],
    )
    remove_callback.assert_not_called()


def test_listener_remove_service():
    """Test Listener.remove_service removes by name without a lookup."""
    _type = str(uuid4())
    name = str(uuid4())
    add_callback = Mock()
    remove_callback = Mock()
    zc_mock = Mock(Zeroconf)
    # after a goodbye packet the service can no longer be resolved
    zc_mock.get_service_info.return_value = None

    listener = DeakoListener(add_callback, remove_callback)

    listener.remove_service(zc_mock, _type, name)

    zc_mock.get_service_info.assert_not_called()
    add_callback.assert_not_called()
    remove_callback.assert_called_once_with(name)


@patch("socket.inet_ntoa")
def test_listener_update_service(mock_inet_ntoa):
    """Test Listener.update_service."""
    address = str(uuid4())
    converted_address = str(uuid4())
    _type = str(uuid4())
    name = str(uuid4())
    port = 69

    mock_inet_ntoa.return_value = converted_address

    add_callback = Mock()
    remove_callback = Mock()
    zc_mock = Mock(Zeroconf)
    mock_info = Mock()
    mock_info.addresses = [address]
    mock_info.port = port

    zc_mock.get_service_info.return_value = mock_info

    listener = DeakoListener(add_callback, remove_callback)

    listener.update_service(zc_mock, _type, name)

    mock_inet_ntoa.assert_called_with(address)
    zc_mock.get_service_info.assert_called_with(_type, name)
    # one call that swaps the addresses, so the pool is never empty between
    add_callback.assert_called_once_with(
        name,
        [f"{converted_address}:{port}"],
    )
    remove_callback.assert_not_called()


@patch("pydeako.discover._discover.ServiceBrowser.__init__", return_value=None)
@pytest.mark.asyncio
async def test_deako_discoverer_get_address_again(service_browser_init):
    """Test a second get_address, as on reconnect, finds the device again."""
    discoverer = DeakoDiscoverer(Mock(Zeroconf))
    discoverer.address_pool.add_address("10.0.0.2:23", "sw._deako._tcp.local.")

    first = await discoverer.get_address()
    second = await discoverer.get_address()

    service_browser_init.assert_called_once()
    assert first == second == ("10.0.0.2:23", "sw._deako._tcp.local.")


@patch("pydeako.discover._discover.ServiceBrowser.__init__", return_value=None)
@pytest.mark.asyncio
async def test_deako_discoverer_get_address_removed_meanwhile(
    service_browser_init,
):
    """Test the pool emptying between the check and the read."""
    discoverer = DeakoDiscoverer(Mock(Zeroconf))
    pool = discoverer.address_pool
    pool.add_address("10.0.0.2:23", "sw")
    real_count = pool.available_addresses

    def count_then_remove() -> int:
        count = real_count()
        pool.remove_service("sw")  # zeroconf thread removes it right after
        return count

    with (
        patch.object(pool, "available_addresses", count_then_remove),
        pytest.raises(DevicesNotFoundError),
    ):
        await discoverer.get_address()
    service_browser_init.assert_called_once()


def test_listener_ignores_unresolved_service():
    """Test add and update do nothing when the service can't be resolved."""
    add_callback = Mock()
    zc_mock = Mock(Zeroconf)
    zc_mock.get_service_info.return_value = None
    listener = DeakoListener(add_callback, Mock())

    listener.add_service(zc_mock, "type", "name")
    listener.update_service(zc_mock, "type", "name")

    assert zc_mock.get_service_info.call_count == 2
    add_callback.assert_not_called()


@patch("pydeako.discover._discover.ServiceBrowser.__init__")
def test_deako_discoverer_stop(mock_service_browser_init):
    """Test DeakoDiscoverer.stop closes the zeroconf instance it uses."""
    zc_mock = Mock(Zeroconf)

    DeakoDiscoverer(zc_mock).stop()

    mock_service_browser_init.assert_called_once()
    zc_mock.close.assert_called_once_with()
