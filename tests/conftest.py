import sys
from os.path import abspath, dirname
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

root_dir = abspath(dirname(__file__) + "/../custom_components/")
sys.path.append(root_dir)


@pytest.fixture
def mock_setup_entry():
    """Mock setting up an entry."""
    with patch(
        "custom_components.frank_energie.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def mock_setup_entry_success():
    """Mock setting up an entry with success."""
    with patch(
        "custom_components.frank_energie.async_setup_entry", return_value=True
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def mock_auth_success():
    """Mock successful authentication and UserSites fetch."""
    mock_auth = MagicMock()
    mock_auth.authToken = "mock_auth_token"
    mock_auth.refreshToken = "mock_refresh_token"

    mock_address = MagicMock()
    mock_address.street = "Main Street"
    mock_address.houseNumber = "123"
    mock_address.houseNumberAddition = ""

    mock_site = MagicMock()
    mock_site.reference = "site_ref_123"
    mock_site.address = mock_address
    mock_site.status = "IN_DELIVERY"

    mock_user_sites = MagicMock()
    mock_user_sites.deliverySites = [mock_site]

    with patch("custom_components.frank_energie.config_flow.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.__aenter__.return_value = api_instance
        api_instance.login = AsyncMock(return_value=mock_auth)
        api_instance.UserSites = AsyncMock(return_value=mock_user_sites)
        yield mock_api


@pytest.fixture
def mock_auth_failure():
    """Mock authentication failure."""
    from python_frank_energie.exceptions import AuthException

    with patch("custom_components.frank_energie.config_flow.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.__aenter__.return_value = api_instance
        api_instance.login = AsyncMock(side_effect=AuthException("invalid_auth"))
        yield mock_api


@pytest.fixture
def mock_auth_exception():
    """Mock connection exception during login."""
    from python_frank_energie.exceptions import ConnectionException

    with patch("custom_components.frank_energie.config_flow.FrankEnergie") as mock_api:
        api_instance = mock_api.return_value
        api_instance.__aenter__.return_value = api_instance
        api_instance.login = AsyncMock(
            side_effect=ConnectionException("connection_error")
        )
        yield mock_api


@pytest.fixture
def config_entry(hass):
    """Create a mock config entry."""
    entry = MockConfigEntry(
        domain="frank_energie",
        title="Frank Energie",
        data={
            "username": "user@example.com",
            "access_token": "token123",
            "token": "refresh123",
        },
        entry_id="123",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def config_entry_with_site(hass):
    """Create a mock config entry with a site selected."""
    entry = MockConfigEntry(
        domain="frank_energie",
        title="Frank Energie",
        data={
            "username": "user@example.com",
            "access_token": "token123",
            "token": "refresh123",
            "site_reference": "site_ref_123",
        },
        entry_id="1234",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def aioclient_responses(aioclient_mock, socket_enabled):
    from custom_components.frank_energie import const
    from tests.utils import ResponseMocks

    responses = ResponseMocks()

    async def next_response(*_):
        return next(responses)

    aioclient_mock.post(const.DATA_URL, side_effect=next_response)
    aioclient_mock.post(
        "https://frank-graphql-prod.graphcdn.app/", side_effect=next_response
    )

    return responses
