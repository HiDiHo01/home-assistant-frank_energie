# Update Frequency and Cache-Driven Refresh Specification

## Goals

- Minimize API traffic.
- Prefer cached data whenever possible.
- Separate API refreshes from sensor state updates.
- Update sensors exactly when underlying values change.
- Avoid polling immutable data.
- Support Home Assistant Platinum Quality Scale principles.

## Core Principles

### Cache First

Data must be served from cache whenever possible.

API calls are only allowed when:

- Cache is missing.
- Cache is incomplete.
- New data is expected but not yet available.
- Manual refresh is requested.
- Recovery after reauthentication is required.

### Sensor Updates

Sensor updates must not trigger API requests.

Sensors should derive their state from cached data.

### Maintenance Window

No API requests between:

- 00:00 UTC
- 01:00 UTC

During this window:

- No authentication.
- No token refresh.
- No electricity price fetches.
- No gas price fetches.
- No charger refreshes.
- No battery refreshes.
- No vehicle refreshes.

Cached sensor state transitions remain allowed.

## Electricity Prices

### Today Prices

Load today's prices only when:

- Cache is missing.
- Cache is invalid.
- Cache is incomplete.
- Startup recovery requires it.

Once loaded:

- Store in cache.
- Do not fetch again.

### Tomorrow Prices

Before 11:00 UTC:

- Never request tomorrow prices.

Release window:

- 11:00 UTC until 13:00 UTC.
- Poll every 5 minutes.

After 13:00 UTC:

- Continue polling every 5 minutes only when tomorrow prices are still unavailable.

Once tomorrow prices are loaded:

- Cache them.
- Stop polling immediately.
- Do not fetch tomorrow prices again that day.

### Midnight Rollover

At day transition:

- Promote tomorrow cache to today cache.
- Clear tomorrow cache.

No API call should be required.

### Price Resolution

PT60M:

- Update sensors exactly on whole-hour boundaries.

PT15M:

- Update sensors exactly on quarter-hour boundaries.

These updates must use cached data only.

## Gas Prices

### Discovery Window

Poll only during:

- 04:00 UTC
- 07:00 UTC

Frequency:

- Every 5 minutes.

### Stop Condition

Once today's gas price is available:

- Cache it.
- Stop polling immediately.
- Do not request gas prices again that day.

Outside the discovery window:

- Use cached gas price.

## Realtime Energy Data

### PV Systems

Refresh interval:

- 30 seconds.

Includes:

- Current production.
- Inverter power.
- Grid export.

### Home Batteries

Refresh interval:

- 30 seconds.

Includes:

- State of charge.
- Charge power.
- Discharge power.
- Operating state.

### EV Chargers

Charging:

- 30 seconds.

Idle:

- 5 minutes.

### Electric Vehicles

Vehicle awake:

- 1 minute.

Vehicle sleeping:

- 15 minutes.

## Statistics Data

Refresh interval:

- 1 hour.

Examples:

- Monthly consumption.
- Yearly consumption.
- Charge history.
- Battery statistics.

## Configuration and Metadata

Refresh interval:

- Once every 24 hours.

Examples:

- User profile.
- Contract details.
- Site information.
- Device metadata.
- Battery configuration.
- Charger configuration.
- Vehicle capabilities.
- Supported features.
- Tariff information.

Event-driven refreshes remain allowed during:

- Startup.
- Reauthentication.
- Entry reload.
- Manual refresh.

## Coordinator Separation

Recommended coordinator groups:

### Price Coordinator

Responsible for:

- Electricity prices.
- Gas prices.
- Price cache management.

### Realtime Energy Coordinator

Responsible for:

- PV.
- Batteries.
- Chargers.
- Grid power.

### Vehicle Coordinator

Responsible for:

- Vehicle state.
- Vehicle charging.
- Vehicle telemetry.

### Statistics Coordinator

Responsible for:

- Historical data.
- Aggregations.
- Reporting.

### Settings Coordinator

Responsible for:

- Configuration.
- Metadata.
- Capabilities.
- Contract information.

## Final Rule

Once valid data is loaded and cached:

- Stop polling.
- Serve data from cache.
- Refresh only when new information is expected or cache recovery is required.
