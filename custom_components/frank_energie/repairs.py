"""User-confirmed repairs for Frank Energie devices absent from live inventories."""

import re
from typing import TYPE_CHECKING

import probatio

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .device_inventory import (
    INVENTORY_SPECS,
    async_identify_inventory_device,
    async_persist_inventory_device,
    validated_inventory_ids,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from . import FrankEnergieEntryData


_REQUIRED_ABSENT_FETCHES = 2


def _issue_id(kind: str, entry_id: str, device_id: str) -> str:
    """Return a stable, config-entry-scoped repair identifier."""
    return f"stale_{kind}_{entry_id}_{device_id}"


@callback
def async_process_device_inventory(
    hass: HomeAssistant,
    entry: "ConfigEntry[FrankEnergieEntryData]",
    kind: str,
    inventory: object,
) -> set[str] | None:
    """Update repairs and return the IDs verified by this live inventory.

    Missing devices require two complete, independent successful fetches.
    Failed, skipped, or malformed fetches never change or clear repair issues.
    """
    if kind not in INVENTORY_SPECS or entry.runtime_data is None:
        return None
    inventory_ids = validated_inventory_ids(
        INVENTORY_SPECS[kind].collection_key, inventory
    )
    if inventory_ids is None:
        return None

    absences = entry.runtime_data.stale_device_absences
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
            candidate = async_persist_inventory_device(
                hass, entry, device, kind, inventory_ids
            )
            if candidate is None:
                # The scoped refresh retires issues for unidentifiable devices.
                continue

            key = f"{kind}:{device.id}"
            observed.add(key)
            if candidate[1] in inventory_ids:
                absences.pop(key, None)
                continue

            issue_id = _issue_id(kind, entry.entry_id, device.id)
            count = absences.get(key, 0) + 1
            absences[key] = count
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

    for key in tuple(absences):
        if key.startswith(f"{kind}:") and key not in observed:
            absences.pop(key)
    return inventory_ids


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
            or kind not in INVENTORY_SPECS
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

        return await self._async_confirm_device_removal(entry, kind, device_id)

    async def _async_confirm_device_removal(
        self,
        entry: "ConfigEntry[FrankEnergieEntryData]",
        kind: str,
        device_id: str,
    ) -> RepairsFlowResult:
        """Revalidate the device and require a newly verified live inventory."""
        registry = dr.async_get(self.hass)
        device = registry.async_get(
            device_id, include_child_devices=False, include_composite_devices=False
        )
        if device is None:
            return self.async_create_entry(data={})
        candidate = async_identify_inventory_device(
            self.hass, entry, device, expected_kind=kind
        )
        if candidate is None:
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
            return self.async_abort(reason="cannot_verify")

        runtime_data = entry.runtime_data
        coordinator = getattr(
            runtime_data, INVENTORY_SPECS[kind].coordinator_attribute, None
        )
        if coordinator is None:
            return self.async_abort(reason="cannot_verify")
        previous_inventory = coordinator.verified_inventory_ids
        site_reference = coordinator.site_reference
        await coordinator.async_refresh()

        # A skipped refresh can still report success for cached display data.
        inventory = coordinator.verified_inventory_ids
        if (
            inventory is None
            or inventory is previous_inventory
            or not coordinator.last_update_success
            or entry.state is not ConfigEntryState.LOADED
            or entry.runtime_data is not runtime_data
            or coordinator.site_reference != site_reference
        ):
            return self.async_abort(reason="cannot_verify")

        current_device = registry.async_get(
            device_id, include_child_devices=False, include_composite_devices=False
        )
        if current_device is None:
            return self.async_create_entry(data={})
        if (
            current_device.config_entry_id != device.config_entry_id
            or async_identify_inventory_device(
                self.hass, entry, current_device, expected_kind=kind
            )
            != candidate
        ):
            return self.async_abort(reason="cannot_verify")
        if candidate[1] in inventory:
            return self.async_abort(reason="device_restored")

        from . import _is_device_removal_safe

        # The Repairs flow already requested and verified a fresh inventory.
        if not _is_device_removal_safe(self.hass, entry, current_device):
            return self.async_abort(reason="cannot_verify")

        registry.async_remove_device(device_id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the repair flow registered for a stale Frank Energie device."""
    return StaleDeviceRepairFlow()
