# Frank Energie Home Assistant Integration

## Python Requirements

- Python 3.14+
- Use timezone-aware datetimes
- Use datetime.UTC
- Use type hints everywhere
- Use object for internal helpers
- Use Any only for Home Assistant public APIs

## Home Assistant Standards

- Use CoordinatorEntity
- Use DataUpdateCoordinator
- Use ConfigEntry.runtime_data
- Use extra_state_attributes
- Never use device_state_attributes
- Use async APIs whenever available

## Logging

- Use lazy formatting

GOOD:
LOGGER.debug("Site reference: %s", site_reference)

BAD:
LOGGER.debug(f"Site reference: {site_reference}")

## Dataclasses

- Required fields before default fields
- Validate incoming API data
- Use timezone-aware datetime objects

## Entity Patterns

- Prefer dynamic entity generation
- Avoid duplicate entity classes
- Use entity descriptions

## Testing

- pytest
- syrupy snapshots
- aresponses for API mocking

## Architecture Goals

- Reduce duplication
- Minimize lambda complexity
- Improve type safety
- Follow HA Core patterns
