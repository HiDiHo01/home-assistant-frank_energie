---
name: frank-energie-api-reliability
description: Diagnose and test Frank Energie API errors, coordinator refresh failures, rate limits, cache freshness, partial GraphQL responses, site contracts, provider fixtures, Smart HVAC status and equipment, account connection requests, and cancellation in the Home Assistant integration.
---

# Frank Energie API and coordinator reliability

## Boundaries

Read `AGENTS.md`, `AI_POLICY.md`, `home-assistant.md`, `SPECS/update_frequency_policy.md`, `docs/update_frequency.md`, relevant coordinator/client/entity code, and regression tests. Confirm the actual upstream `python-frank-energie` response and error contracts; don't infer GraphQL response shapes or expose raw live payloads.

Keep the API library responsible for GraphQL transport, authentication, parsing, and domain models; keep the Home Assistant integration responsible for config entries, resource refresh policies, availability and entity presentation. Coordinate cross-repository fixes rather than duplicating unsafe workarounds.

## Failure-analysis procedure

1. Reconstruct the relevant request path, update schedule, shared state, in-flight tasks, cache source, and resource-specific success/failure flags.
2. Categorize provider outcomes: valid empty response; nullable/partial field; authentication expiry; rate limit; transient timeout/connection error; validation error; and unexpected schema change.
3. Verify that an invoice, battery, charger, vehicle, PV, or account subresource failure does not mark unrelated fresh price data unavailable or erase valid cached information.
4. Confirm bounded retries and `Retry-After` where supported, no overlapping refresh storms, proper cancellation propagation, entry/site isolation, and correct Home Assistant setup/re-auth/error transitions.
5. Verify cache aging and response ordering; a fallback must not be labeled live/current, and an old request must not overwrite newer data.
6. For price-related fixes, verify hourly/quarter-hour resolution, PT15M/PT60M effective dates, negative prices, UTC versus Amsterdam local dates, and DST days.

## Public tomorrow-price cache and partial-response lessons (issue #473 / PR #474)

[Issue #473](https://github.com/HiDiHo01/home-assistant-frank_energie/issues/473) exposed a real failure after reloading: the disk cache contained **24 electricity and 24 gas periods for today**, but **0 electricity and 6 gas periods for tomorrow**. A same-day `last_fetch_tomorrow` was present; the integration incorrectly reported the tomorrow cache as fresh, and even a manual refresh skipped the API. One API call can legitimately produce a partially populated result: receiving gas is **not** evidence that electricity was also received.

### Contract requirements and cache validation

- For the **unauthenticated public feed**, require **both electricity and gas** in the cached day being validated; the public feed exposes both commodities regardless of `DeliverySite.segments`. Do **not** use `_static_prices_today` (which may include historical carry-forward entries) to infer contract availability or decide that a public cache is complete.
- For an **authenticated** account, rely on the selected site's verified energy segments: `ELECTRICITY`, `GAS`, or both. A legitimate known single-contract site must not wait for the other commodity. Preserve existing explicit handling of unknown site metadata; never silently treat a missing contract as a verified single-contract result.
- Cache completeness is not the same as a non-null `MarketPrices` wrapper, a non-empty **single** series, a same-day fetch marker, or successful coordinator execution. Check the intended **Amsterdam-local date** and a usable price series for **each required commodity**. Verify the actual price entries and their resolution instead of trusting only the saved timestamp.
- `has_cached_tomorrow` in diagnostics is derived from `cached_prices_tomorrow is not None`; it can be `true` for a **partial** cache. Never interpret this flag alone as proof that both tomorrow price series are available. Keep `last_fetch_tomorrow` for a successful **complete** tomorrow fetch only.

### Refresh, persistence and midnight rollover

- On a **partial** response, keep the already available commodity in `cached_prices_tomorrow`, retry the missing one on the **next eligible** price-coordinator refresh, and merge the new series with the retained one. Do not fabricate missing prices or substitute today's stale prices as tomorrow. Respect the configured release window, API rate limits, maintenance window and adaptive update interval; do not trigger extra requests from individual sensors.
- If a disk-restored tomorrow cache has a success timestamp but fails the current completeness check, **clear `last_fetch_tomorrow` before attempting another request**. An empty/error response must not re-persist that old success marker, while the still-valid partial series remain available. The marker may only be set again after complete valid data has been assembled.
- At **00:00 Europe/Amsterdam**, promote applicable tomorrow price entries to the combined today/history data, then clear both `cached_prices_tomorrow` and `last_fetch_tomorrow`, even for multi-day API windows. Historical/multi-day entries may remain in the combined price series; don't repopulate the separate tomorrow cache from old records. Keep this distinct from the API maintenance window expressed in UTC.

### Required regressions for future price-cache changes

1. An unauthenticated disk restore with **gas-only** tomorrow (and the inverse), a same-day saved marker, and today's prices both present, missing, partial, or containing **only yesterday's electricity**. Check that the cache is rejected as incomplete and the next eligible refresh actually calls the API.
2. A partial initial response followed by a response with only the **missing** commodity. Assert that merging retains previously valid prices, `last_fetch_tomorrow` stays `None` until completion, and a complete cache suppresses unnecessary subsequent calls.
3. A partial restored cache followed by an **empty/failed retry**. Assert the old marker is cleared before the API attempt, remains `None`, and valid partial prices are not deleted. Check the saved cache and retry cadence where applicable.
4. Authenticated `GAS`-only, `ELECTRICITY`-only and dual-contract sites. Use the selected delivery site's typed segments rather than speculative model attributes.
5. **CET and CEST** local-midnight rollover, incomplete or absent sources, multi-day prices, and diagnostics: the next-day cache and marker reset without destroying valid combined history. Verify `has_cached_tomorrow` reflects cache presence, not completion.

Add these cases to the existing coordinator/diagnostics tests (e.g. `tests/test_coordinator.py`, `tests/test_diagnostics.py`), using synthetic values and deterministic `AsyncMock` sequences. Run focused tests and the current-head Ruff, Flake8/pytest and integration checks; do not claim CI green until the relevant runs finish. Avoid posting real API tokens, site references, entry IDs or unredacted diagnostic payloads.

## Smart Feed-In API outcome semantics

`user_smart_feed_in()` returns `None` after a **successful** GraphQL
`userSmartFeedIn: null` response when the account has no feed-in contract.
The PV coordinator must also record whether this request completed successfully:
a confirmed null maps to the inactive binary sensor state, while authentication
skips, failed requests, or missing coordinator data remain unknown. Do not
turn network failures into an inactive subscription status. Test null,
active/inactive subscriptions, failure, and subsequent recovery.

## Smart PV status, summary, and verified-inventory lessons (2026-11-11)

A sanitized, read-only API observation returned one `SmartPvSystem` with
`onboarding_status=ACTIVE`, `steering_status=INACTIVE`, and
`deleted_at=None`; its `SmartPvSystemSummary` simultaneously reported
`operational_status=ON`, `steering_status=INACTIVE`, a UTC-aware
`operational_status_timestamp`, and separate numeric `total_bonus` and
`total_result`. **These fields are not interchangeable:**

- `onboarding_status` is the provider's onboarding state, not proof of
  electrical generation or real-time connectivity. `operational_status=ON`
  reports the provider's operating state, not measured PV production.
  `steering_status=INACTIVE` does **not** mean disconnected, unavailable,
  deactivated at the contract level, or safe to remove. `deleted_at=None`
  means the inventory record carries no deletion timestamp; it does not
  replace a fresh inventory check.
- In the pinned client API, `smart_pv_systems()` takes **no site-reference
  argument** and returns the equipment list under `SmartPvSystems.systems`.
  For each returned system, `smart_pv_system_summary(system.id)` supplies
  the operational/steering summary; the inventory record's `panel_groups`
  belong to the same physical PV device, not separate removable devices.
  `inverter_serial_numbers` can be empty. Preserve each method's separate
  request, availability, and failure semantics.
- Treat `total_bonus` and `total_result` as distinct numeric provider
  fields. Do not infer a measurement period, payment, or production from a
  single summary sample. Keep the provider values unrounded internally.
  Convert timezone-aware UTC timestamps to Europe/Amsterdam only for
  presentation, accounting for daylight saving time.
- For **stale-device Repairs**, identify presence from a **complete, fresh,
  validated system inventory**, never from steering, onboarding, operational
  status, summary availability, or a panel group's status. Never log raw
  `SmartPvSystem` representations: they may include EANs, external
  references, device IDs, or inverter serial numbers. Use synthetic or
  redacted values for examples and regression tests.
- **Published Smart PV inventory contract:** `python-frank-energie==2026.11.12`
  includes the fix for [library issue #243](https://github.com/HiDiHo01/python-frank-energie/issues/243)
  ([PR #245](https://github.com/HiDiHo01/python-frank-energie/pull/245)).
  The merged [Repairs PR #454](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/454)
  depends on the fix. A successful explicit `smartPvSystems: []` is
  verified absence; missing, null, malformed, duplicate or feature-disabled
  responses must fail closed, and `SmartFeedInNotEnabledException` must not
  advance absent-device evidence. For [integration issue #455](https://github.com/HiDiHo01/home-assistant-frank_energie/issues/455),
  exercise **raw GraphQL responses through the real pinned client's public
  `smart_pv_systems()` parser**, then coordinator refresh, Repairs
  confirmation, manual removal and recovery. A mocked
  `SmartPvSystems(systems=[])` alone cannot detect parser data loss.
  Preserve registered and disabled panel-group entities when the service
  is disabled.

## Smart HVAC status and optional-request isolation

See [Smart heating activation](../../../docs/smart_hvac_activation.md).
The User-service `smart_hvac` entity is driven by the strictly Boolean
`UserSmartHvac.isActivated` for the selected delivery site. Do not
substitute `isAvailableOnSite`, an equipment operational status, or a
truthy string. `RUNNING` is an intentional UI device class for the
requested active/inactive wording, not physical operating telemetry.

When the settings coordinator fetches `api.me(site_reference)` as an
optional Smart HVAC request, preserve already-valid `api.user()` data
on expected network, request, timeout, or optional `AuthException`
failures. Do not swallow `asyncio.CancelledError` or
`AuthRequiredException`, which must trigger Home Assistant reauthentication.
Regression-test that optional `AuthException` keeps Smart Charging/Smart
Trading data while avoiding sensitive exception logs. Never log raw upstream
payloads, personal identifiers, GraphQL variables or tokens.

Check that the selected config-entry site's reference is used consistently
and originates from the established `UserSites` discovery/selection flow,
not a hard-coded or environment-provided fallback. Verify unknown values
are unavailable rather than silently treated as inactive, and that
`CoordinatorEntity.available` follows the settings refresh state.

### Smart HVAC equipment and connection requests

Register the Smart Heating service and its related sensors only after the
settings coordinator confirms `UserSmartHvac.isActivated is True` for the
selected delivery site. Test that an eligible site with linked equipment
adds the entities only when activation arrives on a later settings refresh.

1. Confirm the typed read methods and selected fields in the published client version pinned in `manifest.json`. Reuse the settings coordinator's `Me.user_smart_hvac` response. The inventory and detail queries select the same device metadata in the current client, so use `smart_hvacs(site_reference)` for discovery and verify a specific need before issuing a detail request. [Smart heating data contract](../../../docs/smart_hvac_activation.md)
2. Poll equipment independently. Fetch `smart_hvac_summary(device_id)` once per active inventory record per poll, one device at a time. Keep valid inventory and other summaries when a summary fails. Check the maintenance window and retry deadline before each provider call, including calls later in the same refresh. Cap valid provider retry hints at 24 hours, use the normal polling interval for invalid hints, and test both boundaries.
3. Treat `smart_hvac_connect_requests()` as account data and place it on the User service. Preserve Frank's raw operational and steering status values. Assign units, runtime meanings, or current setting values only when the released read contract confirms them.
4. Test that a confirmed empty inventory counts as zero, a null summary yields unknown values, and a failed read makes the affected data unavailable. Use opaque entity identities and verify that diagnostics and logs protect account and device IDs, EANs, external references, and raw request errors. [Smart heating regression tests](../../../tests/test_smart_hvac.py)

## Delivery-site contracts and provider fixtures

- Verify the GraphQL selection and parsed model in the dependency version pinned in `manifest.json`. `UserSites.segments` describes the first returned site. Match the config entry's site reference in `UserSites.deliverySites` and read that `DeliverySite.segments`. Treat a missing match or unusable segments as unknown.
- Mock the API boundary and parse sanitized payloads with the actual `User.from_dict()` and `UserSites.from_dict()` models. `MagicMock(spec=User)` allows assignments to attributes absent from the real model, which can make a regression test pass with an invented field such as `userSegments`. If a model double is necessary, apply `spec_set` to a real instance.
- Reproduce the failure with those models before applying the fix. Test electricity-only, gas-only, and dual contracts with the selected site after another site that has different segments. Verify the existing entity ID, contract-dependent entity setup, and sensor recovery when the selected site reappears. [Contract regression tests](../../../tests/test_contract_support.py)
- Check the device registry and include refresh buttons in contract tests. Their `device_info` can create a price device independently of sensor setup. Verify that moving a button to another price device preserves its registered entity ID.
- For a dual contract, verify a distinct refresh button on each price device and use the Home Assistant button action to check that each requests a refresh from the shared price coordinator. Test switching to one segment and back to verify that the extra button is restored with the same entity ID.

## Regression tests

Use existing tests such as `tests/test_coordinator.py`, `tests/test_price_cache.py`, `tests/test_price_coordinator_liveness.py`, `tests/test_contract_support.py`, and `tests/test_market_price_resolution_validation.py` for focused changes; extend scenarios with deterministic `AsyncMock` responses and synthetic identifiers. Confirm both preserved unrelated data and accurate failed-resource availability. Check that `caplog`, diagnostics and exception messages never contain credentials, customer-specific identifiers, or raw sensitive API responses.

## Verify and report

Run focused pytest, then relevant project lint/format and full-suite checks per `frank-energie-development` when feasible. Report the root cause, ownership boundary (library versus integration), tests/results and resource-specific behavior. Do not claim live API validation unless explicitly performed under an authorized test environment.

## Adapted learning sources

Derived from the reliability and privacy boundaries in [python-frank-energie](https://github.com/HiDiHo01/python-frank-energie), [frank-energie-mcp](https://github.com/HiDiHo01/frank-energie-mcp), and this integration's `home-assistant.md`. Those projects have different runtime responsibilities and do not supply binding instructions for this repository.
