"""User-confirmed repairs for Frank Energie devices absent from live inventories."""

import re
from typing import TYPE_CHECKING

import probatio

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .const import DATA_BATTERIES, DATA_ENODE_CHARGERS, DATA_ENODE_VEHICLES, DOMAIN

if TYPE_CHECKING:
    from . import FrankEnergieEntryData
    from homeassistant.config_entries import ConfigEntry


_INVENTORIES: dict[str, tuple[str, str]] = {
    "battery": (DATA_BATTERIES, "batteries"),
    "charger": (DATA_ENODE_CHARGERS, "chargers"),
    "vehicle": (DATA_ENODE_VEHICLES, "vehicles"),
}
_REQUIRED_ABSENT_FETCHES = 2


def _issue_id(kind: str, entry_id: str, device_id: str) -> str:
    """Return a stable, config-entry-scoped repair identifier."""
    return f"stale_{kind}_{entry_id}_{device_id}"


def _inventory_ids(kind: str, inventory: object) -> set[str] | None:
    """Validate the complete API inventory before trusting absence."""
    collection = getattr(inventory, _INVENTORIES[kind][1], None)
    if not isinstance(collection, list):
        return None

    identifiers: set[str] = set()
    for item in collection:
        item_id = (
            item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        )
        if not isinstance(item_id, str) or not item_id or item_id in identifiers:
            return None
        identifiers.add(item_id)
    return identifiers


def _candidate_identifier(
    hass: HomeAssistant,
    entry: "ConfigEntry[FrankEnergieEntryData]",
    device: dr.DeviceEntry,
    kind: str,
) -> str | None:
    """Identify a device using the same guarded classification as PR #405."""
    from . import _device_inventory

    if device.config_entry_id != entry.entry_id or device.entry_type is not None:
        return None
    identifiers = {
        identifier
        for domain, identifier in device.identifiers
        if domain == DOMAIN and identifier
    }
    if len(identifiers) != 1:
        return None
    identifier = next(iter(identifiers))
    unique_ids = {
        entity.unique_id
        for entity in er.async_entries_for_device(
            er.async_get(hass), device.id, include_disabled_entities=True
        )
        if entity.config_entry_id == entry.entry_id
    }
    selected_inventory = _device_inventory(
        entry.runtime_data, device, unique_ids, identifier
    )
    if selected_inventory is None or selected_inventory[1] != _INVENTORIES[kind][0]:
        return None
    return identifier


@callback
def async_process_device_inventory(
    hass: HomeAssistant,
    entry: "ConfigEntry[FrankEnergieEntryData]",
    kind: str,
    inventory: object,
) -> None:
    """Observe one fresh inventory without additional polling or silent removal.

    Missing devices require two complete, independent successful fetches.
    Failed, skipped, or malformed fetches never change or clear repair issues.
    """
    if kind not in _INVENTORIES or entry.runtime_data is None:
        return
    inventory_ids = _inventory_ids(kind, inventory)
    if inventory_ids is None:
        return

    runtime_data = entry.runtime_data
    runtime_data.stale_inventory_epochs[kind] = (
        runtime_data.stale_inventory_epochs.get(kind, 0) + 1
    )
    observed: set[str] = set()
    with ir.async_refresh_issues(
        hass,
        DOMAIN,
        issue_id_pattern=rf"stale_{re.escape(kind)}_",
        config_entry_id=entry.entry_id,
    ) as refresh:
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        ):
            issue_id = _issue_id(kind, entry.entry_id, device.id)
            identifier = _candidate_identifier(hass, entry, device, kind)
            if identifier is None:
                # The device still exists, but its kind can no longer be
                # verified. Preserve any existing issue, including ignore state.
                refresh.async_keep([issue_id])
                continue

            key = f"{kind}:{device.id}"
            observed.add(key)
            if identifier in inventory_ids:
                runtime_data.stale_device_absences.pop(key, None)
                continue

            count = runtime_data.stale_device_absences.get(key, 0) + 1
            runtime_data.stale_device_absences[key] = count
            if count < _REQUIRED_ABSENT_FETCHES:
                # Existing persistent issues survive a restart: one new
                # valid fetch is not enough to change their state.
                refresh.async_keep([issue_id])
                continue

            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                config_entry_id=entry.entry_id,
                is_fixable=True,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="stale_device",
                translation_placeholders={
                    "device": (
                        device.name_by_user
                        or device.name
                        or device.model
                        or "Frank Energie device"
                    )
                },
                data={
                    "entry_id": entry.entry_id,
                    "device_id": device.id,
                    "kind": kind,
                },
            )

    for key in tuple(runtime_data.stale_device_absences):
        if key.startswith(f"{kind}:") and key not in observed:
            runtime_data.stale_device_absences.pop(key, None)


class StaleDeviceRepairFlow(RepairsFlow):
    """Confirm removal only after a newly completed, valid API inventory fetch."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Begin with the explicit deletion confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Re-fetch the relevant inventory and delete only if still absent."""
        if user_input is None:
            issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.issue_id)
            return self.async_show_form(
                step_id="confirm",
                data_schema=probatio.Schema({}),
                description_placeholders=(
                    issue.translation_placeholders if issue is not None else None
                ),
            )

        data = self.data or {}
        kind = data.get("kind")
        entry_id = data.get("entry_id")
        device_id = data.get("device_id")
        if (
            not isinstance(kind, str)
            or kind not in _INVENTORIES
            or not isinstance(entry_id, str)
            or not isinstance(device_id, str)
            or self.issue_id != _issue_id(kind, entry_id, device_id)
        ):
            return self.async_abort(reason="cannot_verify")

        entry = self.hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or entry.runtime_data is None
        ):
            return self.async_abort(reason="cannot_verify")

        registry = dr.async_get(self.hass)
        device = registry.async_get(device_id)
        if device is None:
            return self.async_create_entry(data={})
        if _candidate_identifier(self.hass, entry, device, kind) is None:
            return self.async_abort(reason="cannot_verify")

        runtime_data = entry.runtime_data
        coordinator = {
            "battery": runtime_data.battery_coordinator,
            "charger": runtime_data.charger_coordinator,
            "vehicle": runtime_data.vehicle_coordinator,
        }[kind]
        previous_epoch = runtime_data.stale_inventory_epochs.get(kind, 0)
        await coordinator.async_request_refresh()

        # last_update_success alone is insufficient: a skipped API fetch can
        # return cached coordinator data while reporting a successful update.
        if runtime_data.stale_inventory_epochs.get(kind, 0) == previous_epoch:
            return self.async_abort(reason="cannot_verify")

        if registry.async_get(device_id) != device:
            return self.async_abort(reason="cannot_verify")

        from . import async_remove_config_entry_device

        if not await async_remove_config_entry_device(self.hass, entry, device):
            return self.async_abort(reason="device_restored")

        registry.async_remove_device(device.id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the repair flow registered for a stale Frank Energie device."""
    return StaleDeviceRepairFlow()
