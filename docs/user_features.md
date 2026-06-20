# User Features

The Frank Energie integration provides both public and authenticated features.

Public features are available without signing in to a Frank Energie account. Authenticated features become available after providing valid Frank Energie credentials during setup.

## Public Features

The following functionality is available without authentication:

- Electricity prices
- Gas prices
- Price statistics
- Lowest price calculations
- Cheapest price windows
- Tomorrow price availability detection
- Frank Energie events
- Manual price refresh controls

### Electricity Price Resolution

The integration supports multiple electricity market resolutions:

- `PT15M` (quarter-hourly pricing)
- `PT60M` (hourly pricing)

Resolution selection is available through Home Assistant entities.

## Authentication

Authentication is optional during setup.

Users can choose:

- Public market data only
- Full account integration with customer-specific data

## Contract Features

Authenticated users gain access to contract-related information such as:

- Contract information
- Contract status
- Contract pricing configuration
- Delivery site information
- Resolution configuration

## Consumption Features

Authenticated users may access:

- Electricity consumption
- Gas consumption
- Consumption summaries
- Historical usage information
- Monthly summaries

## Cost Features

Cost-related entities may include:

- Current costs
- Historical costs
- Energy cost summaries
- Forecast information

## Invoice Features

Authenticated users may access:

- Invoice information
- Invoice summaries
- Billing information
- Historical invoice data

## Multi-Site Support

For customers with multiple delivery sites, the integration supports:

- Automatic delivery site discovery
- Site selection during setup
- Site reconfiguration
- Automatic filtering of active delivery sites

## Smart Charging

Supported accounts may expose Smart Charging information.

Features include:

- Smart Charging status
- Feature availability
- Provider information
- Subscription status information

## Smart Trading

Supported accounts may expose Smart Trading information.

Features include:

- Smart Trading status
- Provider information
- Availability information
- Subscription requirements

## Smart Feed-In

Supported accounts may expose Smart Feed-In information.

Features include:

- Smart Feed-In status
- Program participation status

## Smart HVAC

Supported accounts may expose Smart HVAC information.

Features include:

- Smart HVAC status
- Heat pump optimization participation status

## Smart Solar (PV)

Supported accounts may expose photovoltaic (PV) information.

Features include:

- Smart PV status
- Connected PV systems
- Manufacturer information
- Model information
- Onboarding status
- PV system count

## Smart Batteries

Supported battery systems expose additional functionality.

### Battery Monitoring

Battery-related entities may include:

- Battery status
- Battery sessions
- Battery configuration
- Self-consumption trading status
- Capacity information
- Maximum charge power
- Maximum discharge power
- Trading configuration

### Battery Mode Control

Users can directly control battery operating modes:

- Self Consumption
- Self Consumption Mix
- Imbalance Trading
- Trading

### Battery Trading Strategies

Available trading strategies may include:

- Balanced
- Conservative
- Aggressive
- Imbalance Only

### Self-Consumption Threshold

For batteries using Self Consumption Mix mode, a configurable electricity price threshold is available.

Supported range:

- Minimum: €0.20/kWh
- Maximum: €0.40/kWh
- Step size: €0.05/kWh

## Manual Refresh Controls

The integration provides manual refresh buttons.

Available refresh actions may include:

- Refresh Prices
- Refresh Battery Sessions

These controls are useful when verifying newly available data or troubleshooting synchronization issues.

## Resolution Management

The integration provides entities for managing electricity price resolution.

Available functionality may include:

- Current resolution
- Available resolutions
- Resolution change requests
- Effective dates
- Pending changes

## User Preferences

Additional user preference information may be available.

Examples include:

- Push notification price alerts
- Haptic feedback preference
- CO₂ compensation participation status

## Events

The integration fires Home Assistant events when important actions occur.

See:

- [Events](events.md)

## Notes

- Available features depend on your Frank Energie account.
- Available features depend on enabled Frank Energie services.
- Some entities are created dynamically based on available account data.
- Smart Charging, Smart Trading, Smart Feed-In, Smart HVAC, Smart Solar, and Smart Battery features may not be available for all users.
- New features may automatically appear as Frank Energie expands API capabilities.
