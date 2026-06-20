# Configuration

This page describes how to configure the Frank Energie integration.

## Requirements

Before configuring the integration, ensure:

- Home Assistant is running and accessible.
- The Frank Energie integration is installed.
- Internet access is available.

Authentication is optional.

## Adding the Integration

1. Open Home Assistant.
2. Navigate to **Settings → Devices & Services**.
3. Select **Add Integration**.
4. Search for **Frank Energie**.
5. Select the integration.

## Public Mode

The integration can operate without authentication.

Public mode provides:

- Electricity prices
- Gas prices
- Price statistics
- Lowest price calculations
- Price events

Public mode is useful when only market prices are required.

## Authenticated Mode

Authenticated mode provides access to account-specific features.

Examples include:

- Contract information
- Consumption data
- Cost information
- Invoice information
- Smart Charging
- Smart Battery
- Smart Trading
- Smart Feed-In
- PV system information

During setup, provide your Frank Energie account credentials when prompted.

## Multi-Site Accounts

Some Frank Energie accounts have multiple delivery sites.

When multiple eligible sites are detected:

1. Select the desired site.
2. Complete setup.

The integration retrieves data for the selected site only.

The active site can be changed later through reconfiguration.

## Resolution Selection

The integration supports multiple electricity market resolutions.

Available resolutions may include:

- `PT15M` (quarter-hourly pricing)
- `PT60M` (hourly pricing)

Resolution management is available through Home Assistant entities when supported by the account.

See:

- [Entities](entities.md)
- [Update Frequency](update_frequency.md)

## Reconfiguration

To change configuration settings:

1. Open **Settings → Devices & Services**.
2. Select the Frank Energie integration.
3. Select **Configure**.

Examples:

- Change the selected delivery site.
- Update account information.
- Review available configuration options.

## Reauthentication

If authentication expires or credentials change:

1. Open **Settings → Devices & Services**.
2. Select the Frank Energie integration.
3. Complete the reauthentication flow.

The integration automatically attempts token renewal before requiring user intervention.

## Dynamic Entities

The integration dynamically creates entities based on:

- Account features
- Connected devices
- Smart services
- Available API data

Not all users will see the same entities.

See:

- [Entities](entities.md)
- [User Features](user_features.md)

## Smart Features

Additional entities may appear when enabled on the account.

Examples include:

- Smart Charging
- Smart Battery
- Smart Trading
- Smart Feed-In
- Smart HVAC
- PV Systems

Availability depends on Frank Energie account capabilities.

## Troubleshooting Setup Issues

### No Sites Available

Possible causes:

- No eligible delivery sites found.
- Temporary API issue.
- Account configuration issue.

### Authentication Failed

Verify:

- Email address
- Password
- Frank Energie account access

### Entities Missing

Many entities are created dynamically.

Missing entities may indicate:

- Feature not enabled on the account.
- Device not connected.
- API data not available.

See:

- [Troubleshooting](troubleshooting.md)

## Diagnostics

If setup problems occur:

1. Download diagnostics.
2. Enable debug logging.
3. Review Home Assistant logs.
4. Include diagnostics when reporting issues.

## Related Documentation

- [Entities](entities.md)
- [Events](events.md)
- [Examples](examples.md)
- [Update Frequency](update_frequency.md)
- [User Features](user_features.md)
- [Troubleshooting](troubleshooting.md)