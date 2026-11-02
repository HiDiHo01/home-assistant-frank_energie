"""Typed, entry-owned runtime data for the Frank Energie integration."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import (
        FrankEnergieBatteryCoordinator,
        FrankEnergieBatterySessionCoordinator,
        FrankEnergieChargerCoordinator,
        FrankEnergieCoordinator,
        FrankEnergiePriceCoordinator,
        FrankEnergiePVCoordinator,
        FrankEnergieSettingsCoordinator,
        FrankEnergieStatisticsCoordinator,
        FrankEnergieVehicleCoordinator,
    )


@dataclass
class FrankEnergieEntryData:
    """Own the coordinators for exactly one config entry and delivery site."""

    coordinator: FrankEnergieCoordinator
    settings_coordinator: FrankEnergieSettingsCoordinator
    price_coordinator: FrankEnergiePriceCoordinator
    battery_coordinator: FrankEnergieBatteryCoordinator
    charger_coordinator: FrankEnergieChargerCoordinator
    pv_coordinator: FrankEnergiePVCoordinator
    vehicle_coordinator: FrankEnergieVehicleCoordinator
    statistics_coordinator: FrankEnergieStatisticsCoordinator
    battery_session_coordinators: dict[str, FrankEnergieBatterySessionCoordinator] = (
        field(default_factory=dict)
    )


type FrankEnergieConfigEntry = ConfigEntry[FrankEnergieEntryData]
