"""Button platform for Frank Energie integration."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    API_CONF_URL,
    COMPONENT_TITLE,
    DOMAIN,
    DATA_USER,
    DATA_USER_SMART_FEED_IN,
    SERVICE_NAME_USER,
)
from .coordinator import FrankEnergieCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Frank Energie buttons."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: FrankEnergieCoordinator = entry_data["coordinator"]

    entities: list[ButtonEntity] = []

    # --- Refresh buttons (one per coordinator) ---
    if "coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                entry_id=entry.entry_id,
                coordinator=entry_data["coordinator"],
                name="Refresh Frank Energie Prices",
            )
        )

    if "battery_session_coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                entry_id=entry.entry_id,
                coordinator=entry_data["battery_session_coordinator"],
                name="Refresh Battery Sessions",
            )
        )

    if "charger_coordinator" in entry_data:
        entities.append(
            FrankEnergieRefreshButton(
                entry_id=entry.entry_id,
                coordinator=entry_data["charger_coordinator"],
                name="Refresh Chargers",
            )
        )

    # --- Disable action buttons (only when authenticated) ---
    if coordinator.api.is_authenticated:
        entities.append(
            FrankEnergieDisableSmartTradingButton(coordinator, entry)
        )
        entities.append(
            FrankEnergieDisableSmartFeedInButton(coordinator, entry)
        )
        entities.append(
            FrankEnergieDisableSmartHvacButton(coordinator, entry)
        )

    if entities:
        async_add_entities(entities)


class FrankEnergieRefreshButton(ButtonEntity):
    """Button to manually refresh a Frank Energie coordinator."""

    _attr_icon = "mdi:refresh"

    def __init__(
        self,
        entry_id: str,
        coordinator: object,  # at runtime any coordinator type
        name: str,
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = f"{entry_id}_{name.lower().replace(' ', '_')}"
        self._coordinator = coordinator

    async def async_press(self) -> None:
        """Handle button press to trigger manual refresh."""
        _LOGGER.debug("Manual refresh requested: %s", self._attr_name)
        await self._coordinator.async_request_refresh()


class _FrankEnergieDisableButton(
    CoordinatorEntity[FrankEnergieCoordinator], ButtonEntity
):
    """Base class for disable-action buttons.

    These buttons are always present in the entity registry but are marked
    unavailable when the feature is already inactive (HA best practice:
    unavailable, not hidden).
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:power-off"

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
        key: str,
        name: str,
        translation_key: str,
    ) -> None:
        """Initialize the disable button."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = name
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{config_entry.entry_id}_{SERVICE_NAME_USER}")},
            name=f"{COMPONENT_TITLE} - {SERVICE_NAME_USER}",
            manufacturer=COMPONENT_TITLE,
            model=SERVICE_NAME_USER,
            configuration_url=API_CONF_URL,
            entry_type=DeviceEntryType.SERVICE,
        )

    def _is_feature_active(self) -> bool:
        """Return True if the feature is currently active. Subclasses implement this."""
        return False

    @property
    def available(self) -> bool:
        """Button is available (pressable) only when the feature is active."""
        return self._is_feature_active()

    async def _do_disable(self) -> bool:
        """Call the API mutation. Subclasses implement this."""
        return False

    async def async_press(self) -> None:
        """Disable the Frank Energie smart feature."""
        _LOGGER.debug("Pressing disable button: %s", self._attr_name)
        success = await self._do_disable()
        if success:
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to disable feature via button: %s", self._attr_name)


class FrankEnergieDisableSmartTradingButton(_FrankEnergieDisableButton):
    """Button to disable Smart Trading.

    Calls the DisableSmartTrading mutation. Becomes unavailable when
    Smart Trading is already inactive.
    """

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="disable_smart_trading",
            name="Disable Smart Trading",
            translation_key="disable_smart_trading",
        )

    def _is_feature_active(self) -> bool:
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return False
        smart_trading = user_data.smartTrading
        if isinstance(smart_trading, dict):
            return bool(smart_trading.get("isActivated", False))
        return bool(getattr(smart_trading, "isActivated", False))

    async def _do_disable(self) -> bool:
        return await self.coordinator.api.disable_smart_trading()


class FrankEnergieDisableSmartFeedInButton(_FrankEnergieDisableButton):
    """Button to disable Smart Feed-In.

    Calls the SmartFeedInDisable mutation. Becomes unavailable when
    Smart Feed-In is already inactive.
    """

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="disable_smart_feed_in",
            name="Disable Smart Feed-In",
            translation_key="disable_smart_feed_in",
        )

    def _is_feature_active(self) -> bool:
        feed_in_status = self.coordinator.data.get(DATA_USER_SMART_FEED_IN)
        if not feed_in_status:
            return False
        if isinstance(feed_in_status, dict):
            return bool(feed_in_status.get("isActivated", False))
        return bool(getattr(feed_in_status, "is_activated", False))

    async def _do_disable(self) -> bool:
        return await self.coordinator.api.disable_smart_feed_in()


class FrankEnergieDisableSmartHvacButton(_FrankEnergieDisableButton):
    """Button to disable Smart HVAC.

    Calls the SmartHvacDisable mutation. Becomes unavailable when
    Smart HVAC is already inactive.
    """

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        super().__init__(
            coordinator,
            config_entry,
            key="disable_smart_hvac",
            name="Disable Smart HVAC",
            translation_key="disable_smart_hvac",
        )

    def _is_feature_active(self) -> bool:
        user_data = self.coordinator.data.get(DATA_USER)
        if not user_data:
            return False
        smart_hvac = getattr(user_data, "smartHvac", None)
        if smart_hvac is None:
            return False
        if isinstance(smart_hvac, dict):
            return bool(smart_hvac.get("isActivated", False))
        return bool(getattr(smart_hvac, "isActivated", False))

    async def _do_disable(self) -> bool:
        return await self.coordinator.api.disable_smart_hvac()
