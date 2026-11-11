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

## Smart Feed-In API outcome semantics

`user_smart_feed_in()` returns `None` after a **successful** GraphQL
`userSmartFeedIn: null` response when the account has no feed-in contract.
The PV coordinator must also record whether this request completed successfully:
a confirmed null maps to the inactive binary sensor state, while authentication
skips, failed requests, or missing coordinator data remain unknown. Do not
turn network failures into an inactive subscription status. Test null,
active/inactive subscriptions, failure, and subsequent recovery.

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
