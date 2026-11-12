"""Register Smart Heating sensors on service and physical devices."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from python_frank_energie.models import SmartHvacDevice, SmartHvacSummary

from .const import DATA_USER, DOMAIN, SERVICE_NAME_SMART_HVAC, SERVICE_NAME_USER
from .coordinator import FrankEnergieSettingsCoordinator
from .device_inventory import (
    inventory_device_identifiers,
    smart_hvac_device_identifier,
)
from .helpers import service_device_info
from .smart_hvac import (
    FrankEnergieSmartHvacCoordinator,
    smart_hvac_activation,
    smart_hvac_site_attributes,
)

if TYPE_CHECKING:
    from . import FrankEnergieEntryData


type _SummaryValue = str | float | date | datetime | None
_EQUIPMENT_ID_PREFIX: Final = "smart_hvac_equipment"
_STEERING_STATES: Final = ("active", "inactive")


@dataclass(frozen=True, kw_only=True)
class _DeviceSensorDescription(SensorEntityDescription):
    """Describe a sensor backed by equipment metadata or a device summary."""

    summary_value: Callable[[SmartHvacSummary], _SummaryValue] | None = None


def _steering_state(summary: SmartHvacSummary) -> str | None:
    """Map known HVAC steering statuses to translatable enum state keys."""
    value = summary.steering_status
    if isinstance(value, str) and value.casefold() in _STEERING_STATES:
        return value.casefold()
    return value


_DEVICE_SENSORS = (
    _DeviceSensorDescription(
        key="smart_hvac_equipment",
        translation_key="smart_hvac_equipment",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _DeviceSensorDescription(
        key="smart_hvac_operational_status",
        translation_key="smart_hvac_operational_status",
        summary_value=lambda summary: summary.operational_status,
    ),
    _DeviceSensorDescription(
        key="smart_hvac_steering_status",
        translation_key="smart_hvac_steering_status",
        device_class=SensorDeviceClass.ENUM,
        options=list(_STEERING_STATES),
        summary_value=_steering_state,
    ),
    _DeviceSensorDescription(
        key="smart_hvac_operational_status_timestamp",
        translation_key="smart_hvac_operational_status_timestamp",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        summary_value=lambda summary: summary.operational_status_timestamp,
    ),
    _DeviceSensorDescription(
        key="smart_hvac_first_session_date",
        translation_key="smart_hvac_first_session_date",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        summary_value=lambda summary: summary.first_session_date,
    ),
    _DeviceSensorDescription(
        key="smart_hvac_last_session_date",
        translation_key="smart_hvac_last_session_date",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        summary_value=lambda summary: summary.last_session_date,
    ),
    _DeviceSensorDescription(
        key="smart_hvac_total_result",
        translation_key="smart_hvac_total_result",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY_EURO,
        suggested_display_precision=2,
        summary_value=lambda summary: summary.total_result,
    ),
)


class _SmartHvacCountSensor(
    CoordinatorEntity[FrankEnergieSmartHvacCoordinator], SensorEntity
):
    """Count the site's devices or the account's connection requests."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FrankEnergieSmartHvacCoordinator,
        entry_id: str,
        *,
        account_scope: bool = False,
    ) -> None:
        """Bind the count to its corresponding Frank Energie service."""
        super().__init__(coordinator)
        self._account_scope = account_scope
        if account_scope:
            self._attr_translation_key = "smart_hvac_connection_requests"
        else:
            self._attr_translation_key = "smart_hvac_device_count"
        self._attr_unique_id = f"{entry_id}_{self._attr_translation_key}"
        self._attr_device_info = service_device_info(
            entry_id, SERVICE_NAME_USER if account_scope else SERVICE_NAME_SMART_HVAC
        )

    @property
    def available(self) -> bool:
        """Distinguish successful null values from skipped or failed reads."""
        data = self.coordinator.data
        fetched = data.requests_fetched if self._account_scope else data.devices_fetched
        return super().available and fetched

    @property
    def native_value(self) -> int | None:
        """Return the record count or ``None`` when the collection is unknown."""
        data = self.coordinator.data
        records = data.requests if self._account_scope else data.devices
        return len(records) if records is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose safe connection details with the account-level count."""
        requests = self.coordinator.data.requests
        if self._account_scope and self.available and requests is not None:
            return {"requests": [dict(request) for request in requests]}
        return {}


class _SmartHvacSiteSensor(
    CoordinatorEntity[FrankEnergieSettingsCoordinator], SensorEntity
):
    """Expose the profile creation timestamp and the selected site's flags."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_translation_key = "smart_hvac_profile_created_at"

    def __init__(
        self, coordinator: FrankEnergieSettingsCoordinator, entry_id: str
    ) -> None:
        """Attach the site's profile sensor to the Smart Heating service."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_{self._attr_translation_key}"
        self._attr_device_info = service_device_info(entry_id, SERVICE_NAME_SMART_HVAC)

    @property
    def available(self) -> bool:
        """Require a successful settings refresh with Smart Heating site data."""
        return (
            super().available
            and getattr(self.coordinator.data.get(DATA_USER), "smartHvac", None)
            is not None
        )

    @property
    def native_value(self) -> datetime | None:
        """Return the profile creation timestamp in UTC when its offset is known."""
        feature = getattr(self.coordinator.data.get(DATA_USER), "smartHvac", None)
        value = getattr(feature, "userCreatedAt", None)
        if not isinstance(value, str):
            return None
        try:
            timestamp = datetime.fromisoformat(value)
            return timestamp.astimezone(UTC) if timestamp.tzinfo is not None else None
        except (ValueError, OverflowError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Show site flags independently of the optional creation timestamp."""
        if self.available:
            return smart_hvac_site_attributes(self.coordinator.data)
        return {}


class _SmartHvacDeviceSensor(
    CoordinatorEntity[FrankEnergieSmartHvacCoordinator], SensorEntity
):
    """Expose one equipment or summary value on the corresponding device."""

    _attr_has_entity_name = True
    entity_description: _DeviceSensorDescription

    def __init__(
        self,
        coordinator: FrankEnergieSmartHvacCoordinator,
        entry_id: str,
        device_id: str,
        fingerprint: str,
        description: _DeviceSensorDescription,
        device_identifier: str,
    ) -> None:
        """Preserve the sensor's unique ID and attach it to the physical device."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._attr_unique_id = (
            f"{entry_id}_{_EQUIPMENT_ID_PREFIX}_{fingerprint}_{description.key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers=inventory_device_identifiers(
                entry_id, "hvac", device_identifier
            )
        )

    @property
    def _device(self) -> SmartHvacDevice | None:
        """Return the device only while its inventory data is available."""
        if not self.coordinator.last_update_success:
            return None
        return (self.coordinator.data.devices or {}).get(self._device_id)

    @property
    def available(self) -> bool:
        """Require inventory and a fresh, successful summary read."""
        data = self.coordinator.data
        return self._device is not None and (
            self.entity_description.summary_value is None
            or (
                self._device_id in data.summaries
                and self._device_id not in data.stale_summaries
            )
        )

    @property
    def options(self) -> list[str] | None:
        """Expose known states and preserve unrecognized provider enum values."""
        supported = self.entity_description.options
        if supported is None:
            return None
        value = self.native_value
        if isinstance(value, str) and value not in supported:
            return [*supported, value]
        return supported

    @property
    def native_value(self) -> _SummaryValue:
        """Return the equipment display name or the provider's summary value."""
        value_fn = self.entity_description.summary_value
        if value_fn is None:
            return device.display_name if (device := self._device) is not None else None
        summary = self.coordinator.data.summaries.get(self._device_id)
        return value_fn(summary) if summary is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose only the non-identifying inventory fields on the metadata entity."""
        if (
            self.entity_description.summary_value is not None
            or (device := self._device) is None
        ):
            return {}
        attributes = {
            "brand": device.brand,
            "model": device.model,
            "device_type": device.device_type,
            "provider": device.provider,
            "created_at": device.created_at.isoformat() if device.created_at else None,
            "updated_at": device.updated_at.isoformat() if device.updated_at else None,
            "needs_reauthentication": device.needs_reauthentication,
        }
        return {key: value for key, value in attributes.items() if value is not None}


def _site_has_active_smart_hvac(settings: FrankEnergieSettingsCoordinator) -> bool:
    """Require confirmed Smart Heating activation for this delivery site."""
    return settings.last_update_success and smart_hvac_activation(settings.data) is True


@callback
def _async_sync_equipment(
    hass: HomeAssistant,
    entry_id: str,
    coordinator: FrankEnergieSmartHvacCoordinator,
    devices: dict[str, SmartHvacDevice],
    known_devices: dict[str, SmartHvacDevice] | None,
) -> tuple[dict[str, SmartHvacDevice], list[_SmartHvacDeviceSensor]]:
    """Register discovered HVAC hardware without deleting absent equipment.

    Keep previously registered identities so a temporarily disconnected
    device is not re-added under a duplicate entity on reconnection.
    Only the user-confirmed Repairs flow may remove a missing device.
    """
    current = {
        sha256(f"{entry_id}:{device_id}".encode()).hexdigest(): device
        for device_id, device in devices.items()
    }
    previous_devices = known_devices or {}
    device_registry = dr.async_get(hass)
    sensors: list[_SmartHvacDeviceSensor] = []
    for fingerprint, device in sorted(current.items()):
        identifier = smart_hvac_device_identifier(
            entry_id, coordinator.site_reference, device.id
        )
        previous_device = previous_devices.get(fingerprint)
        registered_device = device_registry.async_get_device_by_identifier(
            (DOMAIN, identifier), config_entry_id=entry_id
        )
        if device == previous_device and registered_device is not None:
            continue

        device_registry.async_get_or_create(
            config_entry_id=entry_id,
            **DeviceInfo(
                identifiers=inventory_device_identifiers(
                    entry_id, "hvac", identifier
                ),
                name=device.display_name or device.model or device.brand or "HVAC",
                manufacturer=device.brand,
                model=device.model,
                entry_type=None,
            ),
        )
        if previous_device is None or registered_device is None:
            sensors.extend(
                _SmartHvacDeviceSensor(
                    coordinator,
                    entry_id,
                    device.id,
                    fingerprint,
                    description,
                    identifier,
                )
                for description in _DEVICE_SENSORS
            )

    return {**previous_devices, **current}, sensors


@callback
def async_setup_smart_hvac_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry[FrankEnergieEntryData],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smart Heating discovery for an authenticated config entry.

    Add entities after settings confirm site activation, both at setup and
    on later refreshes. Remove the settings and inventory listeners on unload.
    """
    settings = entry.runtime_data.settings_coordinator
    if (
        not settings.api.is_authenticated
        or (coordinator := entry.runtime_data.smart_hvac_coordinator) is None
    ):
        return

    entry_id = entry.entry_id
    service_added = requests_added = False
    known_devices: dict[str, SmartHvacDevice] | None = None

    @callback
    def _async_sync_sensors() -> None:
        """Add newly available sensors after the site activation check."""
        nonlocal known_devices, requests_added, service_added
        devices = coordinator.data.devices if coordinator.last_update_success else None
        if not _site_has_active_smart_hvac(settings):
            # Deactivation or an unreadable inventory must not delete hardware.
            return

        sensors: list[SensorEntity] = []
        if not service_added:
            sensors.append(_SmartHvacCountSensor(coordinator, entry_id))
            sensors.append(_SmartHvacSiteSensor(settings, entry_id))
            service_added = True
        if (
            not requests_added
            and coordinator.last_update_success
            and coordinator.data.requests is not None
        ):
            sensors.append(
                _SmartHvacCountSensor(coordinator, entry_id, account_scope=True)
            )
            requests_added = True
        if devices is not None:
            known_devices, equipment_sensors = _async_sync_equipment(
                hass, entry_id, coordinator, devices, known_devices
            )
            sensors.extend(equipment_sensors)
        if sensors:
            async_add_entities(sensors)

    _async_sync_sensors()
    entry.async_on_unload(coordinator.async_add_listener(_async_sync_sensors))
    entry.async_on_unload(settings.async_add_listener(_async_sync_sensors))
