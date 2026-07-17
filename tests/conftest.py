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


@pytest.fixture
def mock_coordinator():
    from unittest.mock import AsyncMock, MagicMock

    coordinator = MagicMock()
    coordinator.data = {}
    coordinator.api = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_update_enode_charge_settings = AsyncMock()
    return coordinator


@pytest.fixture
def mock_config_entry():
    from unittest.mock import MagicMock
    from homeassistant.config_entries import ConfigEntry

    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.unique_id = "test_unique_id"
    return entry


@pytest.fixture
def create_mock_charge_settings():
    """Fixture returning a function to create mock charge settings."""
    from unittest.mock import MagicMock

    def _create(
        id="set_123",
        deadline=None,
        is_smart_charging_enabled=False,
        is_solar_charging_enabled=False,
        min_charge_limit=20,
        max_charge_limit=80,
        hour_monday=420,
        hour_tuesday=420,
        hour_wednesday=420,
        hour_thursday=420,
        hour_friday=420,
        hour_saturday=420,
        hour_sunday=420,
    ):
        settings = MagicMock()
        settings.id = id
        settings.deadline = deadline
        settings.is_smart_charging_enabled = is_smart_charging_enabled
        settings.is_solar_charging_enabled = is_solar_charging_enabled
        settings.min_charge_limit = min_charge_limit
        settings.max_charge_limit = max_charge_limit
        settings.hour_monday = hour_monday
        settings.hour_tuesday = hour_tuesday
        settings.hour_wednesday = hour_wednesday
        settings.hour_thursday = hour_thursday
        settings.hour_friday = hour_friday
        settings.hour_saturday = hour_saturday
        settings.hour_sunday = hour_sunday
        settings.calculated_deadline = None
        return settings

    return _create


@pytest.fixture
def create_mock_vehicle(create_mock_charge_settings):
    """Fixture returning a function to create a mock Enode vehicle."""
    from unittest.mock import MagicMock

    def _create(
        vehicle_id="veh_123",
        brand="Tesla",
        model="Model 3",
        charge_settings_kwargs=None,
    ):
        vehicle = MagicMock()
        vehicle.id = vehicle_id
        if brand or model:
            vehicle.information = MagicMock()
            vehicle.information.brand = brand
            vehicle.information.model = model
        else:
            vehicle.information = None

        if charge_settings_kwargs is not None:
            vehicle.charge_settings = create_mock_charge_settings(
                **charge_settings_kwargs
            )
        else:
            vehicle.charge_settings = create_mock_charge_settings()
        return vehicle

    return _create


@pytest.fixture
def create_mock_charger(create_mock_charge_settings):
    """Fixture returning a function to create a mock Enode charger."""
    from unittest.mock import MagicMock

    def _create(
        charger_id="chg_123",
        brand="Wallbox",
        model="Copper",
        charge_settings_kwargs=None,
    ):
        charger = MagicMock()
        charger.id = charger_id
        if brand or model:
            charger.information = {"brand": brand, "model": model}
        else:
            charger.information = None

        if charge_settings_kwargs is not None:
            charger.charge_settings = create_mock_charge_settings(
                **charge_settings_kwargs
            )
        else:
            charger.charge_settings = create_mock_charge_settings()
        return charger

    return _create


@pytest.fixture(autouse=True)
def mock_core_uuid():
    """Mock core.uuid for tests by patching _get_fernet_key directly."""
    with patch(
        "custom_components.frank_energie.helpers._get_fernet_key",
        return_value=b"test-key-1234567890123456789012345678901234=",
    ) as mock_key:
        yield mock_key
