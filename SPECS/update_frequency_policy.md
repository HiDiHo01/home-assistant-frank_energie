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

### Timezone Awareness

All market publication windows must be evaluated using timezone-aware datetimes.

Rules:

- Use Europe/Amsterdam for market-related schedules.
- Use timezone-aware datetimes exclusively.
- Never hardcode CET or CEST offsets.
- Let DST transitions be handled automatically.

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
- Stop polling.
- Do not fetch again that day.

### Tomorrow Prices

Before the publication window:

- Never request tomorrow prices.

Publication window:

- 11:00 UTC until 13:00 UTC.
- Poll every 5 minutes.

After 13:00 UTC:

If tomorrow prices are still unavailable:

- Poll every 15 minutes.

After 16:00 UTC:

If tomorrow prices are still unavailable:

- Poll every 30 minutes.

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

Poll only during the gas publication discovery window.

Observed publication behavior:

- Winter: approximately 07:00 local time.
- Summer: approximately 06:00 local time.

Implementation policy:

- Open a discovery window around the expected publication time.
- Poll every 5 minutes while today's gas price is unavailable.

### Stop Condition

Once today's gas price is available:

- Cache it.
- Stop polling immediately.
- Do not request gas prices again that day.

Outside the discovery window:

- Use cached gas price.

## Realtime Energy Data

### Configurable Refresh Intervals

Realtime refresh intervals should be configurable through the Options Flow.

Recommended options:

- 30 seconds (default)
- 60 seconds

The default should favor responsiveness while allowing users to reduce API traffic when necessary.

### PV Systems

Default refresh interval:

- 5 minutes (15 minutes if no PV systems are detected).

Includes:

- Current production.
- Inverter power.
- Grid export.

### Home Batteries

Default refresh interval:

- 5 minutes (15 minutes if no batteries are detected).

Includes:

- State of charge.
- Charge power.
- Discharge power.
- Operating state.

### EV Chargers

Charging:

- 2 minutes.

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

### Price Coordinator

Responsible for:

- Electricity prices.
- Gas prices.
- Price cache management.

### Battery Coordinator

Responsible for:

- Home batteries.
- Battery sessions.

### Charger Coordinator

Responsible for:

- EV chargers.

### PV Coordinator

Responsible for:

- PV systems.
- Grid power (Smart feed in).

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

## Data Classification Matrix

| Class | Examples | Strategy |
|---------|---------|---------|
| Immutable | Electricity prices | Cache until rollover |
| Daily | Gas prices | Fetch once per day |
| Realtime | PV, batteries, chargers | 2-5 minutes |
| Operational | Vehicle state | Adaptive |
| Historical | Statistics | Hourly |
| Configuration | Settings, contracts | Daily |
| Recovery | Startup, reauth | On demand |

## Final Rule

Once valid data is loaded and cached:

- Stop polling.
- Serve data from cache.
- Refresh only when new information is expected.
- Refresh only when cache recovery is required.