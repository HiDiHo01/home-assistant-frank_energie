---
name: frank-energie-api-reliability
description: Diagnose and test Frank Energie API errors, coordinator refresh failures, rate limiting, invoice isolation, price cache freshness, partial GraphQL responses, site-scoped Smart HVAC status and availability, and asynchronous cancellation in the Home Assistant integration.
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

## Smart HVAC status and optional-request isolation

See [Smart heating activation](../../../docs/smart_hvac_activation.md).
The User-service `smart_hvac` entity is driven by the strictly Boolean
`UserSmartHvac.isActivated` for the selected delivery site. Do not
substitute `isAvailableOnSite`, an equipment operational status, or a
truthy string. `RUNNING` is an intentional UI device class for the
requested active/inactive wording, not physical operating telemetry.

When the settings coordinator fetches `api.me(site_reference)` as an
optional Smart HVAC request, preserve already-valid `api.user()` data
on expected network, request, or timeout failures. Do not swallow
`asyncio.CancelledError` or suppress authentication-required failures
that should trigger Home Assistant reauthentication. Never log raw upstream
payloads, personal identifiers, GraphQL variables or tokens.

Check that the selected config-entry site's reference is used consistently
and originates from the established `UserSites` discovery/selection flow,
not a hard-coded or environment-provided fallback. Verify unknown values
are unavailable rather than silently treated as inactive, and that
`CoordinatorEntity.available` follows the settings refresh state.

## Regression tests

Use existing tests such as `tests/test_coordinator.py`, `tests/test_price_cache.py`, `tests/test_price_coordinator_liveness.py`, `tests/test_contract_support.py`, and `tests/test_market_price_resolution_validation.py` for focused changes; extend scenarios with deterministic `AsyncMock` responses and synthetic identifiers. Confirm both preserved unrelated data and accurate failed-resource availability. Check that `caplog`, diagnostics and exception messages never contain credentials, customer-specific identifiers, or raw sensitive API responses.

## Verify and report

Run focused pytest, then relevant project lint/format and full-suite checks per `frank-energie-development` when feasible. Report the root cause, ownership boundary (library versus integration), tests/results and resource-specific behavior. Do not claim live API validation unless explicitly performed under an authorized test environment.

## Adapted learning sources

Derived from the reliability and privacy boundaries in [python-frank-energie](https://github.com/HiDiHo01/python-frank-energie), [frank-energie-mcp](https://github.com/HiDiHo01/frank-energie-mcp), and this integration's `home-assistant.md`. Those projects have different runtime responsibilities and do not supply binding instructions for this repository.
