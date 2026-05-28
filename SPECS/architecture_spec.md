# Frank Energie — Integration Design Specification

> **Status:** Draft
> **Target quality scale:** Silver
> **Domain:** `frank_energie`
> **Maintainer:** `@HiDiHo01`
> **HA core version requirement:** `≥ 2025.1.0`
> **Python library:** `python-frank-energie`

---

## 1. Purpose

This document defines the architectural constraints, state model, and execution rules for the Frank Energie Home Assistant integration. The integration exposes dynamic Dutch energy pricing (electricity and gas) sourced from the Frank Energie GraphQL API, enabling automations and dashboards that respond to real-time spot-market tariffs.

Goals:

- Deterministic behaviour under asynchronous execution
- Strict separation between user intent, API state, and runtime state
- Stateless API client design
- Immutable domain state model
- HA Core PR review compatibility (Silver quality scale)

Non-goals:

- Real-time streaming / WebSocket-based synchronisation
- Persistent background worker loops
- Submitting meter readings or managing account settings
- Controlling smart devices on behalf of Frank Energie

---

## 2. Architectural Principles

These principles are **non-negotiable** design constraints. Any PR that violates them must be refactored before review.

### 2.1 Stateless API Layer

`FrankEnergieClient` (from `python-frank-energie`) **must not** maintain internal state beyond what is strictly required to issue the next HTTP request (i.e., the current access token).

| ✔ Allowed | ✗ Not allowed |
|---|---|
| Direct GraphQL request / response mapping | Internal price caches |
| Token storage for auth header injection | Mutation tracking across calls |
| Typed deserialisation into domain objects | Global or class-level mutable state |
| Raising typed exceptions on API errors | Side-effect accumulation between fetches |

> **Rationale:** The coordinator owns the data lifecycle. Leaking state into the client creates two sources of truth and makes unit testing non-deterministic.

---

### 2.2 Coordinator as Projection Layer

`FrankEnergieCoordinator` is a **pure projection** of the external API state onto an immutable snapshot. It has exactly one responsibility: call the API, normalise the response, and publish the result.

Responsibilities:

- Fetch electricity and gas price data from `FrankEnergieClient`.
- Construct and return an immutable `FrankEnergieData` snapshot.
- Map API exceptions to `UpdateFailed` (or trigger reauth on auth errors).
- Expose coordinator-level **command methods** for state mutations (see §2.4).

**Not** the coordinator's responsibility:

- Business logic (price comparisons, threshold decisions).
- Configuration mutations initiated without user action.
- Cross-request caching beyond the current `data` property.
- Writing to `hass` outside of normal coordinator callbacks.

---

### 2.3 Immutable Domain Model

All runtime state **must** be represented as immutable, fully typed objects. No `dict`, no `Any`, no `TypedDict` for runtime state.

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
- No partial mutation of state objects after creation.
- No `None`-driven field updates after creation.

---

### 2.4 Command-Based Mutation Model

All state changes initiated from Home Assistant **must** go through explicit coordinator command methods. Entities must never call the API directly or mutate config entries directly.

#### Command flow

```
UI (Select / Button entity)
    ↓
Coordinator command method   (e.g. async_set_resolution())
    ↓
API client mutation call
    ↓
ConfigEntry options update   (hass.config_entries.async_update_entry)
    ↓
Coordinator.async_request_refresh()
```

Rules:

- No direct API calls from entity `async_press` / `async_select_option` etc.
- No direct `ConfigEntry` mutation from entities.
- All mutations must be serialised and deterministic (see §4.1).

---

### 2.5 No Dual Source of Truth

The system **must** avoid conflicting state sources.

| Source | Purpose |
|---|---|
| `ConfigEntry.options` | User intent — persistent across restarts |
| API response | External authoritative runtime state |
| Coordinator snapshot | Derived, read-only runtime view for entities |

Forbidden:

- `ConfigEntry.options` overwritten by API state without explicit user action.
- API state used as a configuration source.
- Entities holding a local copy of coordinator data.

---

### 2.6 Async-Only Execution

No blocking I/O in the event loop — ever.

- All coordinator and entity methods are `async`.
- `python-frank-energie` uses `aiohttp` internally; the integration must not wrap it with `executor` calls.
- The `aiohttp.ClientSession` is managed by `python-frank-energie`; the integration does not instantiate its own session.

---

## 3. Architecture

### 3.1 Entry Point & Setup Flow

```
ConfigFlow ──► async_setup_entry()
                  │
                  ├── instantiate FrankEnergieClient   (python-frank-energie)
                  ├── await client.async_login()        # validates credentials
                  ├── instantiate FrankEnergieCoordinator(hass, client, entry)
                  ├── await coordinator.async_config_entry_first_refresh()
                  └── async_forward_entry_setups(entry, PLATFORMS)
```

- `entry.runtime_data` holds a typed `FrankEnergieRuntimeData` dataclass.
- No global state. No module-level mutable variables.
- `async_unload_entry` calls `async_forward_entry_unload_platforms` and `client.close()`.

```python
@dataclass(slots=True)
class FrankEnergieRuntimeData:
    client: FrankEnergieClient
    coordinator: FrankEnergieCoordinator
```

### 3.2 Config Flow

| Step | Fields | Validation |
|---|---|---|
| `user` | `username`, `password` | `client.async_login()` → `InvalidAuth` / `CannotConnect` |
| `reauth` | `password` only | Re-uses stored `username` |
| `options` | _(reserved)_ | Future: scan-interval override |

- Credentials stored in `entry.data` under `CONF_USERNAME` / `CONF_PASSWORD`.
- Access token is **not** persisted; obtained fresh on every `async_setup_entry`.
- Duplicate prevention: `self._abort_if_unique_id_configured()` with `username` as unique ID.

### 3.3 DataUpdateCoordinator

```python
class FrankEnergieCoordinator(DataUpdateCoordinator[FrankEnergieData]):
    """Coordinator for Frank Energie price data."""
```

| Attribute | Contract |
|---|---|
| `update_interval` | `timedelta(minutes=15)` or `timedelta(hours=1)` — quarter-hour or hourly segments, account-dependent; default `timedelta(hours=1)` |
| `_async_update_data()` | Fetches today + tomorrow prices; returns a new immutable `FrankEnergieData` |
| Cached snapshot on error | Returns previous `self.data` on transient network errors (see §6.2) |
| Auth error | Calls `entry.async_start_reauth(hass)`; raises `UpdateFailed` |

#### Update flow

```
_async_update_data()
    ↓
client.async_fetch_prices_today()
    ↓
client.async_fetch_prices_tomorrow()   (tolerates NotAvailable)
    ↓
_merge_to_snapshot()                   # pure function, no side effects
    ↓
return FrankEnergieData(frozen)
```

No side effects in fetch functions. No config updates inside update flow. No mutation calls inside update flow.

### 3.4 Platform Breakdown

| Platform | Entity class | Sensors exposed |
|---|---|---|
| `sensor` | `FrankEnergieSensorEntity` | Current price, average, lowest, highest, tomorrow average (elec + gas) |
| `sensor` | `FrankEnergiePriceSegmentSensor` | Per-hour price segments (today + tomorrow), disabled by default |

No `binary_sensor`, `switch`, or `button` platforms in current scope.

### 3.5 Entity Base Class

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

- State is **always** derived from `self.coordinator.data` in a `@property`. Never cached locally.
- `available` returns `False` when the relevant commodity is `None`.
- `async_write_ha_state()` is **never** called manually.
- Entities must never call API methods directly (see §2.4).
- Entities must never mutate `ConfigEntry` directly.

---

## 4. Mutation Handling

### 4.1 Serialised Execution

All API mutations **must** be serialised to prevent race conditions.

Requirements:

- Single-flight execution per mutation type, OR a global coordinator-level command queue.
- No concurrent mutation calls for the same domain.
- No overlapping API write calls.

Implementation pattern:

```python
async def async_set_resolution(self, resolution: str) -> None:
    """Set price resolution. Serialised via coordinator lock."""
    async with self._command_lock:
        await self.client.async_set_resolution(resolution)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={**self.config_entry.options, CONF_RESOLUTION: resolution},
        )
    await self.async_request_refresh()
```

### 4.2 Retry Strategy

Mutations must implement:

- Bounded retry (max attempts explicitly defined, not infinite).
- Exponential back-off is optional but recommended for 5xx errors.
- Explicit failure propagation: raise `HomeAssistantError` to surface in UI.

### 4.3 Idempotency

All mutation methods must be idempotent where possible:

- Setting resolution to the same value must be safe.
- Repeated calls must not corrupt state.
- Methods should check current value before issuing API call when cheap to do so.

---

## 5. State Model

### 5.1 Resolution State

Resolution (price granularity) is:

- User-configurable via `ConfigEntry.options`.
- Applied to the API via an explicit coordinator command.
- Read back from API only for reconciliation / logging — never to overwrite `options`.

Source priority:

```
User ConfigEntry.options  →  API mutation input  →  API response (read-only)
```

### 5.2 Configuration Model

`ConfigEntry.options` is the single source of truth for persistent user settings.

Rules:

- Must **never** be overwritten by API state alone.
- Must only be updated via user action or explicit command execution (see §4.1).
- Must be versioned; breaking changes require `async_migrate_entry`.

---

## 6. Coordinator Error Handling

### 6.1 Error Mapping

| Situation | Behaviour |
|---|---|
| Network unreachable (transient) | Return cached `self.data` snapshot if available; log at `WARNING` |
| `FrankEnergieAuthError` | `entry.async_start_reauth(hass)` + raise `UpdateFailed` |
| HTTP 429 / rate limited | `UpdateFailed`; no special back-off (interval is already 1 h) |
| Tomorrow prices not yet published | `prices_tomorrow = None`; relevant sensors report `unavailable` |
| Partial commodity data | Missing commodity → `None` field; other commodity unaffected |
| Config flow connection failure | `CannotConnect` → form error `"cannot_connect"` |
| Config flow auth failure | `InvalidAuth` → form error `"invalid_auth"` |
| Entity action failure | Raise `HomeAssistantError` from command method |

### 6.2 Degraded Mode

When `_async_update_data` encounters a transient network error and a previous snapshot exists:

```python
except FrankEnergieConnectionError as err:
    if self.data is not None:
        _LOGGER.warning("Frank Energie unreachable, serving stale data: %s", err)
        return self.data
    raise UpdateFailed(f"Connection error: {err}") from err
```

This prevents unnecessary `unavailable` flapping on short outages.

---

## 7. API Client Contract

The `FrankEnergieClient` from `python-frank-energie` is treated as an opaque, injected dependency.

| Method | Returns | Called by | Notes |
|---|---|---|---|
| `async_login()` | `None` | Config flow, setup | Raises `FrankEnergieAuthError` on failure |
| `async_fetch_prices()` | `FrankEnergieData` | Coordinator only | Raises `FrankEnergieConnectionError` on failure |
| `async_refresh_token()` | `None` | Client-internal | Transparent to the integration |
| `close()` | `None` | `async_unload_entry` | Must be called to close the aiohttp session |

The integration **must not** call internal methods of `python-frank-energie` directly. Only the public API surface is used.

---

## 8. Sensor Definitions

All sensors are defined as `SensorEntityDescription` instances in `sensor.py` and iterated in `async_setup_entry`. Descriptions are declarative only — no logic.

| Key | Unit | `device_class` | `state_class` | Notes |
|---|---|---|---|---|
| `electricity_current_price` | EUR/kWh | `monetary` | `measurement` | Can be negative (feed-in tariffs) |
| `electricity_average_price` | EUR/kWh | `monetary` | `measurement` | |
| `electricity_lowest_price` | EUR/kWh | `monetary` | `measurement` | |
| `electricity_highest_price` | EUR/kWh | `monetary` | `measurement` | |
| `electricity_tomorrow_average` | EUR/kWh | `monetary` | `measurement` | `unavailable` when tomorrow unknown |
| `gas_current_price` | EUR/m³ | `monetary` | `measurement` | |
| `gas_average_price` | EUR/m³ | `monetary` | `measurement` | |
| `gas_tomorrow_average` | EUR/m³ | `monetary` | `measurement` | `unavailable` when tomorrow unknown |

`state_class = measurement` (not `total`) is correct for price sensors: values can go negative and do not accumulate over time.

Per-hour price segment sensors use `entity_category = EntityCategory.DIAGNOSTIC` and are **disabled by default**.

---

## 9. File Layout

```
custom_components/frank_energie/
├── __init__.py            # async_setup_entry, async_unload_entry, async_migrate_entry
├── coordinator.py         # FrankEnergieCoordinator, FrankEnergieData dataclasses
├── config_flow.py         # ConfigFlow + OptionsFlow (stub)
├── entity.py              # FrankEnergieEntity base class, FrankEnergieRuntimeData
├── sensor.py              # Sensor entities + SensorEntityDescription definitions
├── const.py               # DOMAIN, PLATFORMS, DEFAULT_UPDATE_INTERVAL, CONF_*
├── strings.json           # All user-facing strings (source of truth)
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

`api.py` is omitted: the integration uses `python-frank-energie`'s public interface directly. If a thin adapter is ever needed, it lives in `api.py` with a strict public surface.

---

## 10. manifest.json

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

## 11. Quality Scale Checklist

### Bronze (required baseline)

- [ ] Config flow — no YAML configuration
- [ ] Unique ID on all config entries (`username`) and entities (`frank_energie_{key}`)
- [ ] `_attr_has_entity_name = True` on base entity
- [ ] Translations present for all config flow strings (`en` + `nl`)
- [ ] `async_unload_entry` implemented; closes client session; unloads platforms
- [ ] No blocking I/O in the event loop

### Silver (target)

- [ ] All coordinator data fully typed — no `Any`, no bare `dict`
- [ ] Options flow stub implemented (forward compatible)
- [ ] Re-auth flow implemented and tested
- [ ] `device_info` with `DeviceEntryType.SERVICE` on all entities
- [ ] Diagnostics platform: `async_get_config_entry_diagnostics` redacts tokens
- [ ] `strings.json` complete and mirrored in `translations/en.json`
- [ ] Coordinator command methods serialised via lock (§4.1)
- [ ] Degraded mode implemented: stale snapshot served on transient network error (§6.2)

### Gold (aspirational)

- [ ] Test coverage ≥ 80 % via `pytest-homeassistant-custom-component`
- [ ] `recorder` exclusions declared for per-hour price segment sensors
- [ ] `entity_category = EntityCategory.DIAGNOSTIC` on segment sensors (disabled by default)
- [ ] `async_migrate_entry` covered by dedicated migration tests
- [ ] Mutation serialisation covered by concurrency tests

---

## 12. Logging Policy

| Level | When |
|---|---|
| `ERROR` | Unrecoverable failure requiring user action |
| `WARNING` | Auth failure triggering reauth; stale data served; partial data |
| `INFO` | Setup and teardown lifecycle events only |
| `DEBUG` | Per-poll response summary, token refresh events, command execution |

Rules:

- No f-string interpolation in `_LOGGER` calls — use `%s` lazy formatting.
- Credentials, tokens, and email addresses are **never** logged at any level.
- Log only state transitions and failures. Avoid noisy per-refresh logs.
- Logger: `logging.getLogger(__name__)` in every module.

---

## 13. Performance Constraints

- No blocking calls anywhere in the coordinator or entity layer.
- No sequential API calls where a batch fetch is possible.
- Cache only within a single update cycle unless explicitly defined in this spec.
- No persistent background workers outside the coordinator update loop.

---

## 14. Testing Strategy

### 14.1 Fixtures

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

### 14.2 Test Categories

| Category | File | Key tooling |
|---|---|---|
| Config flow | `tests/test_config_flow.py` | `hass`, `MockConfigEntry`, `AsyncMock` |
| Coordinator update | `tests/test_coordinator.py` | `freezegun` (hour boundaries), mock client |
| Coordinator degraded mode | `tests/test_coordinator.py` | Inject `FrankEnergieConnectionError` with existing `data` |
| Mutation serialisation | `tests/test_coordinator.py` | Concurrent `asyncio.gather` calls |
| Sensor state | `tests/test_sensor.py` | `MockConfigEntry.add_to_hass`, state assertions |
| Reauth | `tests/test_config_flow.py` | Inject `FrankEnergieAuthError` from mock client |
| Diagnostics | `tests/test_diagnostics.py` | `get_diagnostics_for_config_entry` |
| Migration | `tests/test_migration.py` | v1 entry fixture → assert v2 structure |

### 14.3 Required Test Cases (minimum)

- Config flow: successful setup, `CannotConnect`, `InvalidAuth`, duplicate username rejection.
- Coordinator: successful update publishes new snapshot; `UpdateFailed` on connection error with no prior data; stale data returned on connection error with prior data; reauth triggered on `FrankEnergieAuthError`.
- Sensors: correct `state` and `unit_of_measurement` after coordinator update; `unavailable` when commodity is `None`; `unavailable` when tomorrow prices are `None`.
- Mutations: two concurrent `async_set_resolution` calls produce exactly one API call and one consistent final state.
- Unload: `async_unload_entry` returns `True`, `client.close()` is called, `runtime_data` is cleared.

---

## 15. Versioning & Migration

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

## 16. Open Questions / Decisions Pending

| # | Question | Owner | Due |
|---|---|---|---|
| 1 | Should per-hour price segment sensors be enabled by default? | @HiDiHo01 | — |
| 2 | Is a `select` entity for price resolution useful / feasible? | @HiDiHo01 | — |
| 3 | Which sensors warrant `recorder` exclusion? | @HiDiHo01 | — |
| 4 | Should `options` flow expose the polling interval (15 min vs 60 min) as a user choice, or derive it from account contract type? | @HiDiHo01 | — |
| 5 | Does the API support push-based price updates (WebSocket)? | @HiDiHo01 | — |
| 6 | Should degraded-mode stale data have a max-age limit? | @HiDiHo01 | — |

---

## 17. Final Rule

If a design choice introduces any of the following, it is **invalid under this specification** and must be refactored:

- Hidden state (state that is not observable via `coordinator.data` or `ConfigEntry`)
- Dual write paths (two code paths that can modify the same domain)
- Implicit mutation (state change without an explicit coordinator command)
- Non-deterministic refresh behaviour
- Blocking I/O anywhere in the async execution path

---

## 18. Changelog

| Version | Date | Change |
|---|---|---|
| 0.1 | 2025-05-28 | Initial combined spec (generic template + FE arch principles + mutation model) |
