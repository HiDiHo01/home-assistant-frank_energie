# Smart Charging

The Frank Energie integration exposes Smart Charging information through Home Assistant entities.

Smart Charging helps optimize electric vehicle charging based on electricity prices and account configuration.

## Overview

Smart Charging is managed by Frank Energie and may use third-party providers to communicate with supported vehicles and charging hardware.

The integration exposes Smart Charging status and configuration information available through the Frank Energie API.

## Availability

Smart Charging entities are only created when:

- Smart Charging is available for your account.
- The feature is enabled by Frank Energie.
- Supported charging hardware is connected.
- Required providers are configured correctly.

Not all Frank Energie customers have access to Smart Charging.

## Smart Charging Status

The integration exposes status information including:

- Smart Charging enabled state
- Smart Charging availability
- Provider information
- Subscription requirements
- Feature configuration state

These entities can be used in dashboards, automations, and troubleshooting.

## Vehicle and charger binary statuses

For each connected Enode vehicle, the following **read-only** statuses use
`binary_sensor` rather than `sensor`:

- Can smart charge (`can_smart_charge`)
- Vehicle reachable (`is_reachable`)
- Charging (`is_charging`)
- Fully charged (`is_fully_charged`)
- Plugged in (`is_plugged_in`)
- Smart charging enabled (`smart_charging_enabled`)
- Solar charging enabled (`solar_charging_enabled`)

For each Enode charger, the six `binary_sensor` status keys are
`can_smart_charge`, `is_plugged_in`, `is_reachable`, `is_charging`,
`is_smart_charging_enabled`, and `is_solar_charging_enabled`.

A genuine `True` API value means **on**, and `False` means **off**. A missing
or malformed boolean is **unknown**, not off. If the hardware disappears from
a successful inventory refresh, its binary entities become unavailable.

### Entity migration for existing installations

On the first authenticated setup after upgrading, the integration migrates
legacy Enode boolean `sensor.*` registry entries for **currently discovered**
vehicles and chargers into `binary_sensor.*`. It retains the underlying
provider-based unique ID, physical device association, user-disabled state,
and user-defined name or icon where possible. Cleanup is scoped to the
correct Frank Energie config entry and will not remove other accounts'
entities. For devices that are not currently discovered, legacy registry
entries are left intact until a later discovery.

**The domain change is not an automatic automation migration.** Review
dashboards, scripts, template entities and automations that refer to the
former `sensor.*` IDs, and replace those references with the corresponding
`binary_sensor.*` IDs. Home Assistant may also choose a new object-ID suffix
if the preferred new ID is already occupied. Confirm the actual new entity
IDs in **Settings → Devices & Services → Entities** before changing
automations. Old `sensor.*` statistics/history are not automatically
rewritten as binary-sensor history.

## Provider Information

Frank Energie may use external providers to manage Smart Charging functionality.

The integration exposes available provider information reported by the API, allowing users to verify:

- Connected services
- Provider status
- Integration availability

## Home Assistant Automations

Smart Charging entities can be used in automations.

Example use cases:

- Notify when Smart Charging becomes unavailable.
- Alert when provider connectivity changes.
- Track Smart Charging activation status.
- Display Smart Charging information on dashboards.

## Related Price Sensors

Smart Charging works alongside the Frank Energie price entities.

Useful entities include:

- Current electricity price
- Lowest electricity price
- Average electricity price
- Cheapest consecutive price periods
- Tomorrow electricity prices

These entities can be used to create custom charging automations.

## Troubleshooting

### Smart Charging Entities Not Available

Possible causes:

- Smart Charging is not enabled for your account.
- No supported charger is connected.
- The provider connection is unavailable.
- Frank Energie services are temporarily unavailable.

### Smart Charging Became Unavailable

Check:

- Home Assistant logs
- Frank Energie account status
- Provider availability
- Integration diagnostics

Temporary API issues may cause entities to become unavailable.

### Missing Provider Information

Provider information is only available when reported by the Frank Energie API.

If no provider information is available, the corresponding entities may not be created.

## Diagnostics

When troubleshooting Smart Charging issues:

1. Download diagnostics from the integration.
2. Enable debug logging.
3. Verify provider status entities.
4. Check Home Assistant logs for API errors.

## Notes

- Smart Charging functionality is managed by Frank Energie.
- Available features may vary between accounts.
- Provider availability depends on supported hardware and services.
- Entity availability is determined dynamically from the API response.
