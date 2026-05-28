# Frank Energie Integration — Architecture Specification (CORE PR GRADE)

## 1. Purpose

This document defines the architectural constraints, state model, and execution rules for the Frank Energie Home Assistant integration. The integration exposes dynamic Dutch energy pricing (electricity and gas) sourced from the Frank Energie GraphQL API, enabling automations and dashboards that respond to real-time spot-market tariffs. The integration is designed to be compatible with HA Core PR review standards and the Silver quality scale.

The goal is to ensure:

- Deterministic behavior under asynchronous execution
- Strict separation between user intent, API state, and runtime state
- Stateless API client design
- Immutable domain state model
- HA Core PR review compatibility

### 1.2 Product / API

| Attribute            | Value                                                          |
|----------------------|----------------------------------------------------------------|
| Vendor               | Frank Energie B.V.                                             |
| API type             | GraphQL over HTTPS                                             |
| Auth mechanism       | Email + password → JWT access/refresh token pair              |
| Polling interval     | 900 or 3600 s (price data changes once per quarter hour or per hour)             |
| Push / event-driven  | No                                                             |
| Rate limits          | Not officially published; treat as low-tolerance cloud API    |
| Official SDK         | `python-frank-energie` (PyPI)                                 |
| Docs URL             | https://github.com/HiDiHo01/python-frank-energie              |

### 1.3 Scope

What this integration **does**:

- Authenticates with Frank Energie and manages token refresh transparently.
- Exposes today's and tomorrow's electricity and gas price segments as sensor entities.
- Derives convenience sensors (current price, average, lowest, highest) from the raw price list.
- Supports accounts with electricity-only, gas-only, or both commodities.

What this integration **does not** do (explicit non-goals):

- Does not control smart devices or actuators on behalf of Frank Energie.
- Does not submit meter readings or manage Frank Energie account settings.
- Does not implement a local push path (the API has no webhook/event stream).
- Does not cache prices across HA restarts beyond what the Recorder provides.

---
## 2. Architectural Principles

These principles are **non-negotiable** design constraints. Any PR that violates them must be refactored before review.

### 2.1 Stateless API Layer

The `FrankEnergieClient` (provided by `python-frank-energie`) **must not** maintain internal state beyond what is strictly required to issue the next HTTP request (i.e., the current access token).

| ✔ Allowed                                  | ✗ Not allowed                              |
|--------------------------------------------|---------------------------------------------|
| Direct GraphQL request / response mapping  | Internal price caches                       |
| Token storage for auth header injection    | Mutation tracking across calls              |
| Typed deserialization into domain objects  | Global or class-level mutable state         |
| Raising typed exceptions on API errors     | Side-effect accumulation between fetches    |

> **Rationale:** The coordinator owns the data lifecycle. Leaking state into the client creates two sources of truth and makes unit testing non-deterministic.

- No caching
- No mutation tracking
- No derived state storage
- Only request/response mapping

✔ Allowed:
- direct API calls
- serialization/deserialization

❌ Not allowed:
- internal state mutation
- global caches
- side-effect accumulation

---

### 2.2 Coordinator as Projection Layer

The DataUpdateCoordinator is strictly a projection of external state:
`FrankEnergieCoordinator` is a **pure projection** of the external API state onto an immutable snapshot. It has exactly one responsibility: call the API, normalise the response, and publish the result.

Responsibilities:

- Fetch data from API
- Normalize raw responses
- Expose immutable snapshot to entities
- Construct and return an immutable `FrankEnergieData` snapshot.
- Map API exceptions to `UpdateFailed` (or trigger reauth on auth errors).

NOT responsible for:

- Business logic decisions (price comparisons, threshold decisions).
- State mutation
- Configuration updates
- Cross-request caching logic (beyond short-lived fetch cache)
- Writing to `hass` outside of normal coordinator callbacks.
- 
---

### 2.3 Immutable Domain Model

All runtime state MUST be represented as immutable, fully typed objects. No `dict`, no `Any`.

```python
@dataclass(frozen=True, slots=True)
class FrankEnergieData:
    electricity: ElectricityData | None
    gas: GasData | None

@dataclass(frozen=True, slots=True)
class ElectricityData:
    prices_today: tuple[PriceSegment, ...]
    prices_tomorrow: tuple[PriceSegment, ...] | None
    current_price: Decimal | None
    average_price: Decimal | None
    lowest_price: Decimal | None
    highest_price: Decimal | None

@dataclass(frozen=True, slots=True)
class PriceSegment:
    start: datetime
    end: datetime
    price: Decimal          # incl. VAT, EUR/kWh or EUR/m³
    market_price: Decimal   # excl. surcharges
```

Rules:
- All monetary values use `Decimal`, never `float`.
- All timestamps are timezone-aware (`datetime` with `tzinfo`).
- `frozen=True` + `slots=True` on every dataclass; no post-init mutation.
- `tuple` for ordered price sequences, never `list`.

### 2.4 Async-Only Execution

No blocking I/O in the event loop — ever.

- All coordinator and entity methods are `async`.
- `python-frank-energie` uses `aiohttp` internally; the integration must not wrap it with `executor` calls.
- The `aiohttp.ClientSession` is managed by `python-frank-energie`; the integration does not instantiate its own session.

### 2.5 No Cross-Entity Side Effects

Entities are **read-only projections** of coordinator data. An entity must never:

- Write to `hass.data`.
- Call coordinator methods that trigger a fetch.
- Communicate with another entity directly.
- Fire events outside of standard HA entity state writes.

---

## 3. Architecture

### 3.1 Entry point & setup flow

```
ConfigFlow ──► async_setup_entry()
                  │
                  ├── instantiate FrankEnergieClient (from python-frank-energie)
                  ├── await client.async_login()           # validates credentials
                  ├── instantiate FrankEnergieCoordinator(hass, client, entry)
                  ├── await coordinator.async_config_entry_first_refresh()
                  └── async_forward_entry_setups(entry, PLATFORMS)
```

- `entry.runtime_data` holds a typed `FrankEnergieRuntimeData` dataclass with `client` and `coordinator`.
- No global state. No module-level mutable variables.
- `async_unload_entry` calls `async_forward_entry_unload_platforms` and closes the client session.

```python
@dataclass(slots=True)
class FrankEnergieRuntimeData:
    client: FrankEnergieClient
    coordinator: FrankEnergieCoordinator
```

### 3.2 Config flow

| Step      | Fields                   | Validation                                              |
|-----------|--------------------------|---------------------------------------------------------|
| `user`    | `username`, `password`   | `client.async_login()` → `InvalidAuth` / `CannotConnect` |
| `reauth`  | `password` only          | Re-uses stored `username`                               |
| `options` | _(none currently)_       | Reserved for future scan-interval override              |

- Credentials are stored in `entry.data` under `CONF_USERNAME` / `CONF_PASSWORD`.
- The access token is **not** persisted; it is obtained fresh on every `async_setup_entry`.
- Duplicate entry prevention uses `self._abort_if_unique_id_configured()` with `username` as unique ID.

### 3.3 DataUpdateCoordinator

```python
class FrankEnergieCoordinator(DataUpdateCoordinator[FrankEnergieData]):
    """Coordinator for Frank Energie price data."""
```

| Attribute              | Contract                                                                                  |
|------------------------|-------------------------------------------------------------------------------------------|
| `update_interval`      | `timedelta(hours=1)` — aligns with hourly price segment changes                          |
| `_async_update_data()` | Calls `client.async_fetch_prices()`; returns a new `FrankEnergieData` instance each time |
| Error propagation      | `FrankEnergieConnectionError` → `UpdateFailed`; `FrankEnergieAuthError` → `async_start_reauth()` |
| Data freshness         | The coordinator does **not** diff against previous data; it always publishes the full new snapshot |

```python
async def _async_update_data(self) -> FrankEnergieData:
    try:
        return await self.client.async_fetch_prices()
    except FrankEnergieAuthError as err:
        self.config_entry.async_start_reauth(self.hass)
        raise UpdateFailed(f"Authentication error: {err}") from err
    except FrankEnergieConnectionError as err:
        raise UpdateFailed(f"Connection error: {err}") from err
```

### 3.4 Platform breakdown

| Platform  | Entity class                      | Sensors exposed                                                             |
|-----------|-----------------------------------|-----------------------------------------------------------------------------|
| `sensor`  | `FrankEnergieSensorEntity`        | current price, average, lowest, highest, tomorrow average (elec + gas)     |
| `sensor`  | `FrankEnergiePriceSegmentSensor`  | per-hour price segments (today + tomorrow, if available)                   |

No `binary_sensor`, `switch`, or `button` platforms in the current scope.

### 3.5 Entity base class

```python
class FrankEnergieEntity(CoordinatorEntity[FrankEnergieCoordinator]):
    """Base entity for Frank Energie."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FrankEnergieCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"frank_energie_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name="Frank Energie",
            manufacturer="Frank Energie B.V.",
            entry_type=DeviceEntryType.SERVICE,
        )
```

Rules:
- Entity state is **always** derived from `self.coordinator.data` in a `@property`. Never cached locally.
- `available` returns `False` when the relevant commodity data is `None` (account has no contract for it).
- `async_write_ha_state()` is **never** called manually; `CoordinatorEntity` handles propagation.

---

## 4. API Client Contract

The `FrankEnergieClient` from `python-frank-energie` is treated as an opaque, injected dependency.

| Method                    | Returns              | Called by        | Notes                                         |
|---------------------------|----------------------|------------------|-----------------------------------------------|
| `async_login()`           | `None`               | Config flow, setup | Raises `FrankEnergieAuthError` on failure   |
| `async_fetch_prices()`    | `FrankEnergieData`   | Coordinator only | Raises `FrankEnergieConnectionError` on failure |
| `async_refresh_token()`   | `None`               | Client-internal  | Transparent to the integration               |
| `close()`                 | `None`               | `async_unload_entry` | Must be called to close the aiohttp session |

The integration **must not** call internal methods of `python-frank-energie` directly. Only the public API surface is used.

---

## 5. Error Handling Contract

| Situation                       | Behaviour                                                              |
|---------------------------------|------------------------------------------------------------------------|
| Network unreachable             | `UpdateFailed` → coordinator retries on next interval (1 h)           |
| `FrankEnergieAuthError`         | `entry.async_start_reauth(hass)` + `UpdateFailed`; entities go unavailable |
| HTTP 429 / rate limited         | `UpdateFailed`; no special back-off (interval is already 1 h)        |
| API returns partial data        | Partial commodity set is acceptable; missing commodity → `None` field  |
| Tomorrow prices not yet available | `prices_tomorrow = None`; relevant sensors report `unavailable`     |
| Config flow connection failure  | `CannotConnect` → form error `"cannot_connect"`                       |
| Config flow auth failure        | `InvalidAuth` → form error `"invalid_auth"`                           |

---

## 6. File Layout

```
custom_components/frank_energie/
├── __init__.py            # async_setup_entry, async_unload_entry, async_migrate_entry
├── api.py                 # Re-exports / thin wrappers only; main client lives in python-frank-energie
├── coordinator.py         # FrankEnergieCoordinator + FrankEnergieData dataclasses
├── config_flow.py         # ConfigFlow + OptionsFlow (stub)
├── entity.py              # FrankEnergieEntity base class + FrankEnergieRuntimeData
├── sensor.py              # All sensor entities + SensorEntityDescription definitions
├── const.py               # DOMAIN, PLATFORMS, DEFAULT_UPDATE_INTERVAL
├── strings.json           # All user-facing strings (nl + en via translations/)
├── translations/
│   ├── en.json
│   └── nl.json
├── manifest.json
└── hacs.json
```

**Import dependency order** (no circular imports permitted):

```
const  →  coordinator  →  entity  →  sensor
                ↑
           (python-frank-energie)
config_flow  →  const, coordinator
__init__     →  all of the above
```

`api.py` exists only if the integration needs to extend or adapt the library's public interface. If it is empty or merely re-exports, it must be removed.

---

## 7. manifest.json

```json
{
  "domain": "frank_energie",
  "name": "Frank Energie",
  "version": "2.0.0",
  "config_flow": true,
  "documentation": "https://github.com/HiDiHo01/home-assistant-frank_energie",
  "issue_tracker": "https://github.com/HiDiHo01/home-assistant-frank_energie/issues",
  "requirements": ["python-frank-energie==1.x.x"],
  "dependencies": [],
  "codeowners": ["@HiDiHo01"],
  "iot_class": "cloud_polling",
  "quality_scale": "silver"
}
```

`hacs.json` must **not** contain a `homeassistant` version key; that belongs in `manifest.json` only.

---

## 8. Sensor Definitions

All sensors are defined as `SensorEntityDescription` instances in `sensor.py` and iterated in `async_setup_entry`. No sensor logic lives in the description; the description is declarative only.

| Key                              | Unit       | `device_class`         | `state_class`      | Notes                                    |
|----------------------------------|------------|------------------------|--------------------|------------------------------------------|
| `electricity_current_price`      | EUR/kWh    | `monetary`             | `measurement`      | Can be negative (feed-in tariffs)        |
| `electricity_average_price`      | EUR/kWh    | `monetary`             | `measurement`      |                                          |
| `electricity_lowest_price`       | EUR/kWh    | `monetary`             | `measurement`      |                                          |
| `electricity_highest_price`      | EUR/kWh    | `monetary`             | `measurement`      |                                          |
| `electricity_tomorrow_average`   | EUR/kWh    | `monetary`             | `measurement`      | `unavailable` when tomorrow unknown      |
| `gas_current_price`              | EUR/m³     | `monetary`             | `measurement`      |                                          |
| `gas_average_price`              | EUR/m³     | `monetary`             | `measurement`      |                                          |
| `gas_tomorrow_average`           | EUR/m³     | `monetary`             | `measurement`      | `unavailable` when tomorrow unknown      |

`state_class = measurement` (not `total`) is correct for price sensors because values can go negative and do not accumulate over time.

Price segment sensors (one per hour) use `entity_category = EntityCategory.DIAGNOSTIC` and are disabled by default to avoid polluting the default entity list.

---

## 9. Quality Scale Checklist

### Bronze (required baseline)

- [ ] Config flow — no YAML configuration
- [ ] Unique ID on all config entries (`username`) and entities (`frank_energie_{key}`)
- [ ] `_attr_has_entity_name = True` on base entity
- [ ] Translations present for all config flow strings (en + nl)
- [ ] `async_unload_entry` implemented, closes client session, unloads platforms
- [ ] No blocking I/O in the event loop

### Silver (target)

- [ ] All coordinator data fully typed — no `Any`, no bare `dict`
- [ ] Options flow stub implemented (even if empty, for forward compatibility)
- [ ] Re-auth flow implemented and tested
- [ ] `device_info` with `DeviceEntryType.SERVICE` on all entities
- [ ] Diagnostics platform: `async_get_config_entry_diagnostics` redacts tokens
- [ ] `strings.json` complete and mirrored in `translations/en.json`

### Gold (aspirational)

- [ ] Test coverage ≥ 80 % via `pytest-homeassistant-custom-component`
- [ ] `recorder` exclusions declared for per-hour price segment sensors
- [ ] `entity_category = EntityCategory.DIAGNOSTIC` on segment sensors (disabled by default)
- [ ] `async_migrate_entry` covered by dedicated migration tests

---

## 10. Testing Strategy

### 10.1 Fixtures

```python
# tests/conftest.py

MOCK_ELECTRICITY = ElectricityData(
    prices_today=(
        PriceSegment(
            start=datetime(2025, 1, 1, 0, 0, tzinfo=UTC),
            end=datetime(2025, 1, 1, 1, 0, tzinfo=UTC),
            price=Decimal("0.2450"),
            market_price=Decimal("0.1800"),
        ),
        # …
    ),
    prices_tomorrow=None,
    current_price=Decimal("0.2450"),
    average_price=Decimal("0.2312"),
    lowest_price=Decimal("0.1900"),
    highest_price=Decimal("0.3100"),
)

MOCK_DATA = FrankEnergieData(electricity=MOCK_ELECTRICITY, gas=None)

@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    with patch(
        "custom_components.frank_energie.coordinator.FrankEnergieClient",
        autospec=True,
    ) as mock:
        mock.return_value.async_fetch_prices.return_value = MOCK_DATA
        yield mock
```

### 10.2 Test Categories

| Category        | File                         | Key tooling                                      |
|-----------------|------------------------------|--------------------------------------------------|
| Config flow     | `tests/test_config_flow.py`  | `hass`, `MockConfigEntry`, `AsyncMock`           |
| Coordinator     | `tests/test_coordinator.py`  | `freezegun` (hour boundaries), mock client       |
| Sensor state    | `tests/test_sensor.py`       | `MockConfigEntry.add_to_hass`, state assertions  |
| Reauth          | `tests/test_config_flow.py`  | Inject `FrankEnergieAuthError` from mock client  |
| Diagnostics     | `tests/test_diagnostics.py`  | `get_diagnostics_for_config_entry`               |
| Migration       | `tests/test_migration.py`    | Version 1 → 2 entry fixture                      |

### 10.3 Required Test Cases (minimum)

- Config flow: successful setup, `CannotConnect`, `InvalidAuth`, duplicate username rejection.
- Coordinator: successful update publishes new snapshot; `UpdateFailed` on `FrankEnergieConnectionError`; reauth triggered on `FrankEnergieAuthError`.
- Sensors: correct `state` and `unit_of_measurement` after coordinator update; `unavailable` when commodity is `None`; `unavailable` when tomorrow prices are `None`.
- Unload: `async_unload_entry` returns `True`, `client.close()` is called, `runtime_data` is cleared.
- Tomorrow sensors: available after coordinator returns data with `prices_tomorrow` set.

---

## 11. Logging Conventions

| Level     | When                                                                   |
|-----------|------------------------------------------------------------------------|
| `ERROR`   | Unrecoverable failure requiring user action (should be extremely rare) |
| `WARNING` | Auth failure triggering reauth; partially degraded data                |
| `INFO`    | Setup and teardown lifecycle events only                               |
| `DEBUG`   | Per-poll raw response summary, token refresh events                    |

- Credentials, tokens, and email addresses are **never** logged at any level.
- Logger: `logging.getLogger(__name__)` in every module.
- Do **not** use `logging.getLogger("homeassistant.components.frank_energie")`.

---

## 12. Versioning & Migration

```python
# config_flow.py
class ConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 1
```

```python
# __init__.py
async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to current version."""
    if entry.version < 2:
        # v1 stored the access token in entry.data; v2 removes it
        new_data = {k: v for k, v in entry.data.items() if k != "access_token"}
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info("Migrated Frank Energie config entry to version 2")
    return True
```

All migration paths must have a corresponding test that constructs a v1 entry and asserts the v2 structure.

---

## 13. Open Questions / Decisions Pending

| # | Question                                                                 | Owner       | Due |
|---|--------------------------------------------------------------------------|-------------|-----|
| 1 | Should per-hour price segment sensors be enabled by default?             | @HiDiHo01   | —   |
| 2 | Is a `select` entity for manually forcing a price date feasible / useful?| @HiDiHo01   | —   |
| 3 | Which sensors warrant `recorder` exclusion (per-segment sensors)?        | @HiDiHo01   | —   |
| 4 | Should `options` flow expose a configurable polling interval?            | @HiDiHo01   | —   |
| 5 | Does the API support WebSocket for push-based price updates?             | @HiDiHo01   | —   |

---

## 14. Changelog

| Version | Date       | Change                                             |
|---------|------------|----------------------------------------------------|
| 0.1     | 2025-05-28 | Initial combined spec (generic template + FE arch) |


