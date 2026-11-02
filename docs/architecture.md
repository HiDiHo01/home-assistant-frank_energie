# Architecture

This document describes the architecture of the Frank Energie Home Assistant integration.

The integration uses separate resource coordinators for prices, settings,
statistics, batteries, chargers, PV systems, vehicles and battery sessions.
Their data and update intervals belong to one config entry and selected site.

## High-Level Architecture

```text
┌─────────────────────┐
│ Frank Energie API   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ python-frank-energie│
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│ DataUpdateCoordinator
│ coordinator.py      │
└──────────┬──────────┘
           │
           ├───────────── Sensors
           ├───────────── Binary Sensors
           ├───────────── Buttons
           ├───────────── Numbers
           └───────────── Selects
```

## Core Components

### Config Entry

The integration uses Home Assistant config entries.

Responsibilities:

- Setup
- Unload
- Migration
- Reauthentication
- Reconfiguration

The integration does not support YAML configuration.

### Runtime Ownership and Lifecycle

`runtime_data.py` defines `FrankEnergieEntryData` and the typed
`FrankEnergieConfigEntry` alias used by setup, platforms, coordinators, flows and
diagnostics. `async_setup_entry` assigns the runtime directly to the entry.
There is no additional entry-ID coordinator dictionary in `hass.data`.

`FrankEnergieComponent.async_prepare()` creates coordinators and selects the
delivery site. `async_setup()` refreshes settings first, then starts the six
independent resource refreshes concurrently. If a refresh fails or setup is
cancelled, pending refresh tasks are cancelled and awaited before the original
exception propagates. Failed setup removes the unpublished runtime.

Sensors register parent devices before dependent platforms load. A forwarding
failure rolls back already-created platforms. On successful unload, Home
Assistant removes the runtime and runs registered coordinator and aligned-timer
cleanup callbacks. A refused platform unload retains the active runtime.
Reloading replaces only that entry's runtime and preserves entity identifiers.
The shared HTTP session remains owned by Home Assistant.

Home Assistant also owns the process event loop. Importing the integration does
not change the Windows event-loop policy.

### Coordinator

The coordinator is the central component of the integration.

File:

```text
custom_components/frank_energie/coordinator.py
```

Responsibilities include:

- Authentication management
- Token renewal
- Market data retrieval
- User data retrieval
- Smart feature retrieval
- Dynamic polling intervals
- Event generation
- Cache management
- Entity updates

All entities obtain their state from coordinator data.

Entities should never communicate directly with the API.

## API Layer

The integration uses:

```text
python-frank-energie
```

The API library handles:

- Login
- Token refresh
- GraphQL communication
- Data parsing
- Data models
- Validation

The Home Assistant integration should remain thin and rely on the library for API-specific behavior.

## Data Flow

### Public Data

```text
Market API
    │
    ▼
Coordinator
    │
    ▼
Price Sensors
```

Examples:

- Electricity prices
- Gas prices
- Price statistics

### Authenticated Data

```text
Authentication
       │
       ▼
User API
       │
       ▼
Coordinator
       │
       ▼
Authenticated Entities
```

Examples:

- Contracts
- Consumption
- Costs
- Invoices
- Smart features

## Dynamic Entity Model

The integration creates entities dynamically.

Entity availability depends on:

- Account features
- Connected devices
- API responses
- Service availability

Examples:

- Smart Battery entities
- Smart Charging entities
- Smart Trading entities
- Smart Feed-In entities
- PV entities

This allows the integration to adapt automatically to account capabilities.

## Entity Platforms

### Sensor Platform

Provides:

- Prices
- Statistics
- Consumption
- Costs
- Contract information
- Smart feature metadata

### Binary Sensor Platform

Provides:

- Feature availability
- Status indicators
- Configuration state

### Button Platform

Provides:

- Manual refresh actions
- Battery session refresh

### Number Platform

Provides:

- Battery threshold configuration

### Select Platform

Provides:

- Battery modes
- Trading strategies
- Resolution selection

## Authentication Architecture

Authentication is optional.

### Public Mode

No authentication required.

Provides:

- Market prices
- Price statistics
- Events

### Authenticated Mode

Provides:

- Account-specific data
- Smart features
- Battery functionality
- Consumption information

The coordinator automatically handles token renewal when possible.

## Update Architecture

The integration uses dynamic polling.

### Normal Operation

Polling frequency is adjusted based on:

- Data type
- Market publication windows
- API availability

### Tomorrow Price Publication Window

Tomorrow prices are checked beginning around:

```text
11:00 UTC
```

Polling frequency increases during the publication window to detect newly available prices.

### Maintenance Window

The integration respects the Frank Energie maintenance window:

```text
00:00 UTC - 01:00 UTC
```

During this period:

- API calls are skipped
- Cached data is used

## Event Architecture

The integration generates Home Assistant events.

Event type:

```text
frank_energie_event
```

Examples:

- tomorrow_prices_available
- lowest_price
- lowest_4p_price
- lowest_8p_price
- lowest_16p_price

Events are generated by coordinator state transitions.

See:

- events.md

## Resolution Handling

The integration supports multiple electricity resolutions.

Examples:

```text
PT15M
PT60M
```

The coordinator:

- Detects available resolutions
- Handles fallback behavior
- Detects resolution drift
- Updates related entities

## Smart Battery Architecture

Smart Battery functionality is discovered dynamically.

The coordinator retrieves:

- Battery configuration
- Battery sessions
- Battery summaries
- Trading information
- Self-consumption settings

Battery entities are created only when battery information is available.

## Smart Charging Architecture

Smart Charging functionality is exposed when available from the API.

Examples:

- Availability status
- Provider information
- Configuration state

## Diagnostics Architecture

Diagnostics are generated through:

```text
diagnostics.py
```

Responsibilities:

- Data collection
- Redaction
- Support reporting

Sensitive information is removed before diagnostics are exported.

## Reauthentication Flow

The integration supports:

```text
async_step_reauth
```

Used when:

- Credentials change
- Authentication expires
- Login is required again

## Reconfiguration Flow

The integration supports:

```text
async_step_reconfigure
```

Used for:

- Site changes
- Configuration updates

## Design Principles

The integration follows these principles:

- Async-first design
- Coordinator-centric architecture
- Dynamic entity creation
- Minimal API requests
- Event-driven automation support
- Robust authentication handling
- Home Assistant Quality Scale compliance

## Related Documentation

- developer.md
- configuration.md
- diagnostics.md
- entities.md
- events.md
- smart_battery.md
- smart_charging.md
- update_frequency.md
