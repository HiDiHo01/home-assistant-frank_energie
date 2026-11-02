# Developer Guide

This document provides information for contributors and developers working on the Frank Energie Home Assistant integration.

## Repository Structure

```text
custom_components/frank_energie/
├── __init__.py
├── binary_sensor.py
├── button.py
├── config_flow.py
├── constants.py
├── coordinator.py
├── diagnostics.py
├── number.py
├── select.py
├── sensor.py
├── services.yaml
├── strings.json
└── translations/
```

The integration follows Home Assistant architecture patterns and uses a central DataUpdateCoordinator.

## Architecture Overview

The integration consists of four primary layers:

### Home Assistant Integration Layer

Responsible for:

- Config entries
- Entity platforms
- Services
- Diagnostics
- Events

### Coordinator Layer

`coordinator.py` acts as the central source of truth.

Responsibilities include:

- API communication
- Update scheduling
- Data caching
- Event generation
- Entity refresh coordination
- Error handling

All entities read data from the coordinator.

### API Layer

The integration uses:

```text
python-frank-energie
```

The API library handles:

- Authentication
- Token renewal
- GraphQL requests
- Data parsing
- Data models

### Entity Layer

Entity platforms expose API data to Home Assistant.

Current platforms include:

- Sensor
- Binary Sensor
- Button
- Number
- Select

## Data Flow

```text
Frank Energie API
        │
        ▼
python-frank-energie
        │
        ▼
DataUpdateCoordinator
        │
        ▼
Home Assistant Entities
```

Entities should not communicate directly with the API.

All data should flow through the coordinator.

## Coordinator Design

The coordinator is responsible for:

- Fetching market prices
- Fetching authenticated user data
- Managing update intervals
- Handling maintenance windows
- Detecting tomorrow price availability
- Firing Home Assistant events

The coordinator minimizes API requests by using intelligent polling intervals and caching.

## Event System

The integration generates Home Assistant events.

Event type:

```text
frank_energie_event
```

Examples include:

- tomorrow_prices_available
- lowest_price
- lowest_4p_price
- lowest_8p_price
- lowest_16p_price

See:

- events.md

## Entity Design Guidelines

### Dynamic Entity Creation

Entities should be created dynamically when data is available.

Examples:

- Smart Battery entities
- Smart Charging entities
- Smart Trading entities
- PV entities

Do not create unavailable entities.

### Entity Descriptions

Use entity descriptions whenever possible.

Benefits:

- Consistent metadata
- Reduced boilerplate
- Easier maintenance

### CoordinatorEntity

Entities should inherit from:

```python
CoordinatorEntity
```

when appropriate.

## Home Assistant Development Standards

The integration targets modern Home Assistant best practices.

### Async First

Use asynchronous APIs whenever available.

Examples:

```python
async def async_update(self) -> None:
```

Avoid blocking I/O.

### Config Entries

Use config entries exclusively.

Do not support YAML configuration.

### Diagnostics

Sensitive information must be redacted.

Examples:

- Tokens
- Passwords
- Personal identifiers

### Reauthentication

Support reauthentication flows through:

```text
async_step_reauth
```

### Reconfiguration

Support configuration updates through:

```text
async_step_reconfigure
```

## Python Standards

The project follows modern Python practices.

### Type Hints

Use explicit type hints.

### Dataclasses

Prefer dataclasses for structured data.

### Time Handling

Use timezone-aware datetimes.

Examples:

```python
from datetime import timezone

now(timezone.utc)
```

Avoid:

```python
utcnow()
```

### Logging

Use lazy logging formatting.

Preferred:

```python
LOGGER.debug("Received %s prices", count)
```

Avoid:

```python
LOGGER.debug(f"Received {count} prices")
```

### Internal Helpers

Internal helper methods should use a leading underscore.

Example:

```python
def _process_prices(self) -> None:
```

## Testing

The integration uses pytest-based testing.

Typical areas covered:

- Config flows
- Coordinator updates
- Entity creation
- Diagnostics
- Event generation
- Reauthentication
- Reconfiguration

### Snapshot Testing

Snapshot testing may be used for:

- Diagnostics
- API responses
- Entity structures

## Quality Scale

The project aims to comply with the Home Assistant Quality Scale.

Reference:

https://www.home-assistant.io/docs/quality_scale/

Contributions should support movement toward Platinum quality.

## Debug Logging

Enable debug logging with:

```yaml
logger:
  logs:
    custom_components.frank_energie: debug
    python_frank_energie: debug
```

## Development Workflow

Typical workflow:

1. Create a feature branch.
2. Implement changes.
3. Add or update tests.
4. Run linting.
5. Run test suite.
6. Run Hassfest.
7. Open a pull request.

## Documentation

When adding features:

- Update entities.md when entities change.
- Update events.md when events change.
- Update examples.md when new automation scenarios become available.
- Update user_features.md when user-facing functionality changes.

## Related Documentation

- configuration.md
- diagnostics.md
- entities.md
- events.md
- examples.md
- faq.md
- smart_battery.md
- smart_charging.md
- troubleshooting.md
- update_frequency.md
- user_features.md