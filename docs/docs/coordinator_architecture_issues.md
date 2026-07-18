# Coordinator architecture follow-up issues

These issue drafts convert the coordinator architecture review findings into actionable backlog items. They are written to be copied into GitHub issues using the existing backlog template.

## Issue 1: Make setup runtime-data registration failure-safe

**Labels:** `type: backlog`, `area: setup`, `area: coordinator`, `priority: high`

### Description

The integration currently saves coordinator references into `hass.data` and `entry.runtime_data` before the initial coordinator refreshes complete. This makes coordinators visible earlier during setup, but if a first refresh fails with `ConfigEntryAuthFailed`, `ConfigEntryNotReady`, or another setup exception, Home Assistant can be left with partially initialized runtime data for an entry that did not finish setup.

### Context / motivation

Home Assistant config entry setup should be transactional from the integration's perspective: either setup completes and runtime data is valid, or setup fails and no partially initialized coordinators remain registered. Early runtime-data registration is useful only if failure cleanup is guaranteed.

Relevant code paths:

- `FrankEnergieComponent.setup()` creates all coordinators, calls `_save_coordinator_to_hass_data()`, then performs initial refreshes.
- `_save_coordinator_to_hass_data()` writes both `hass.data[DOMAIN][entry_id]` and `entry.runtime_data`.
- `_remove_entry_from_hass_data()` exists but is not used to clean up failed first-refresh setup.

### Proposed solution

Either:

1. Move `_save_coordinator_to_hass_data()` back to after all required initial refreshes succeed; or
2. Keep early registration, but wrap the initial refresh block in `try/except` and call `_remove_entry_from_hass_data()` before re-raising setup exceptions.

Option 2 preserves early runtime-data visibility while making failed setup safe.

### Acceptance criteria

- If any required `async_config_entry_first_refresh()` raises during setup, the integration removes `hass.data[DOMAIN][entry_id]` before re-raising.
- Failed setup does not leave stale `entry.runtime_data` or stale coordinator references reachable by services/diagnostics.
- Successful setup still forwards platforms only after required first refreshes complete.
- Tests cover both successful setup and first-refresh failure cleanup.

---

## Issue 2: Replace API-client monkey-patched auth lock with explicit auth manager

**Labels:** `type: backlog`, `area: auth`, `area: coordinator`, `priority: high`

### Description

The coordinators currently share token-renewal serialization by dynamically attaching a private `_frank_energie_auth_lock` attribute to the third-party `FrankEnergie` API client. This hidden coupling works only when every coordinator uses the same client instance and makes integration-owned auth state implicit.

### Context / motivation

Token renewal is an integration-level concern. It should be represented explicitly rather than by mutating a third-party API object. Explicit auth coordination will make the code easier to reason about, test, and extend to coordinators that do not inherit from `FrankEnergieCoordinator`.

### Proposed solution

Introduce a small integration-owned auth helper, for example `FrankEnergieAuthManager`, that owns:

- the shared `asyncio.Lock`,
- token renewal,
- silent re-login fallback,
- config entry token persistence,
- conversion of terminal auth failures into `ConfigEntryAuthFailed`.

Pass this helper to all coordinators that can perform authenticated API calls.

### Acceptance criteria

- No integration-specific attributes are added to the third-party `FrankEnergie` API client.
- All coordinator token-renewal paths use one shared auth manager or equivalent explicit shared object.
- Concurrent token-renewal attempts are serialized in tests.
- Renewed access and refresh tokens are persisted to the config entry in one place.

---

## Issue 3: Bring battery session updates under the shared auth-renewal path

**Labels:** `type: backlog`, `area: auth`, `area: battery`, `area: coordinator`, `priority: high`

### Description

`FrankEnergieBatterySessionCoordinator` inherits directly from `DataUpdateCoordinator`, not from the main authenticated coordinator base. On `AuthException`, it calls `api.renew_token()` directly, bypassing the shared auth lock and config entry token persistence used by the other coordinators.

### Context / motivation

This creates inconsistent auth behavior and can race with token renewal from other coordinators. It can also renew tokens without saving the updated tokens back to the config entry.

### Proposed solution

Refactor battery session updates to use the same auth-renewal helper as all other authenticated coordinator paths. This could be done by:

- passing the explicit auth manager from Issue 2 into `FrankEnergieBatterySessionCoordinator`, or
- extracting a small shared authenticated-coordinator base used by both `FrankEnergieCoordinator` and `FrankEnergieBatterySessionCoordinator`.

### Acceptance criteria

- `FrankEnergieBatterySessionCoordinator` no longer calls `api.renew_token()` directly.
- Battery session token renewal is serialized with other coordinator token renewals.
- Renewed tokens are persisted to the config entry.
- Tests cover battery-session auth failure and successful renewal behavior.

---

## Issue 4: Make price projection fully typed and non-mutating

**Labels:** `type: backlog`, `area: prices`, `area: coordinator`, `priority: medium`

### Description

Price aggregation and fallback logic should produce a coordinator-owned projection without mutating API response models. `_combine_price_data()` currently accepts `Any` and uses shallow copy plus `+=`; `_fetch_prices_with_fallback()` mutates the `user_prices` model by assigning fallback gas/electricity data into it.

### Context / motivation

`DataUpdateCoordinator.data` should be a stable snapshot derived from API state. Mutating API-returned model objects can make cache ownership unclear and can leak fallback data into later operations if the same objects are reused.

### Proposed solution

Create typed projection helpers for price data, for example:

- `combine_price_data(today: PriceData | None, tomorrow: PriceData | None) -> PriceData | None`
- `build_market_price_projection(user_prices, public_prices, enabled_segments) -> MarketPrices`

These helpers should construct new projected objects or use explicitly non-mutating model APIs. Avoid `Any`, shallow-copy assumptions, and in-place assignment to API-returned models.

### Acceptance criteria

- Price aggregation helper is typed with `PriceData | None`, not `Any`.
- Aggregating today and tomorrow prices does not mutate `cached_prices_today`, `_static_prices_today`, or API-returned price objects.
- Fallback gas/electricity insertion does not mutate the original `user_prices` object.
- Tests assert original price objects are unchanged after aggregation and fallback.

---

## Issue 5: Split the monolithic base coordinator into feature-owned coordinators

**Labels:** `type: backlog`, `area: architecture`, `area: coordinator`, `priority: medium`

### Description

The codebase defines separate settings, price, battery, charger, PV, vehicle, and statistics coordinators, but the base `FrankEnergieCoordinator` still contains a monolithic `_fetch_today_data()` path that fetches data across many of those feature domains.

### Context / motivation

A Home Assistant coordinator should have clear ownership over its data lifecycle. Keeping full-domain fetching in the base coordinator makes update frequency, API load, and data ownership harder to reason about. It also risks duplicate API calls when feature-specific coordinators fetch the same endpoints independently.

### Proposed solution

Turn `FrankEnergieCoordinator` into a true shared base containing only common helpers, or rename the monolithic coordinator if it is still intentionally used. Move feature-specific fetch orchestration into the corresponding feature coordinators:

- settings: user and sites,
- prices: electricity/gas prices and resolution state,
- statistics: month summary, invoices, usage,
- battery: smart batteries, details, sessions,
- charger: Enode chargers,
- vehicle: Enode vehicles,
- PV: PV systems and summaries.

### Acceptance criteria

- The base coordinator no longer orchestrates all feature-domain API calls.
- Each feature coordinator owns its refresh interval and API calls.
- Duplicate endpoint calls between base and feature coordinators are eliminated or explicitly documented.
- Tests cover each coordinator's update data independently.

---

## Issue 6: Narrow broad exception handling in coordinator fetch helpers

**Labels:** `type: backlog`, `area: error-handling`, `area: coordinator`, `priority: medium`

### Description

Several coordinator helpers catch broad `Exception` and convert failures to `None` or generic `UpdateFailed` results. This can hide programming errors, schema/model regressions, or auth failures wrapped by the API client.

### Context / motivation

Home Assistant integrations should generally let unexpected bugs fail loudly during development while converting expected network/API/auth failures into the appropriate Home Assistant exceptions. Broad catches make diagnostics harder and can silently turn real bugs into unavailable entities.

### Proposed solution

Audit coordinator fetch helpers and replace broad exception handling with targeted handling for known exception types:

- `AuthException` / `AuthRequiredException`,
- `ConfigEntryAuthFailed`,
- `RequestException`,
- `NetworkError`,
- `ClientError`,
- `asyncio.TimeoutError`,
- `asyncio.CancelledError` re-raised immediately.

Unexpected exceptions should either propagate or be logged with full context at the outer coordinator boundary.

### Acceptance criteria

- Auth failures consistently trigger token renewal or `ConfigEntryAuthFailed`.
- `asyncio.CancelledError` is re-raised anywhere it can be caught.
- Unexpected model/programming errors are not silently converted to `None` in low-level helpers.
- Tests cover representative auth, network, and unexpected-error paths.
