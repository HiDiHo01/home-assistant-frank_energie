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
