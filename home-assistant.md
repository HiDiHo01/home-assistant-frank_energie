# Home Assistant Development Guide

This document defines repository-specific development rules for the Frank Energie Home Assistant integration. It complements `AGENTS.md`, `AI_POLICY.md`, and `CLAUDE.md`; it does not replace them.

## Instruction precedence and scope

Apply platform, system, security, and safety requirements first, then explicit task constraints, then repository guidance. A task's explicit scope (for example, documentation-only or do-not-run-tests) must not be overridden by general repository instructions. Treat conversation summaries and generated handoffs as unverified until checked against the repository.

Before changing code or behavior:

1. Read `AGENTS.md`, this file, `AI_POLICY.md`, and `CLAUDE.md`, plus any applicable nested instructions.
2. Inspect the relevant implementation, tests, translations, manifest, dependency declarations, and CI workflows.
3. Verify the current Home Assistant API and deprecations against official documentation or Home Assistant Core source/tests. Do not infer current support from old examples or AI-generated output.
4. Keep changes within the requested scope and use the smallest complete, maintainable solution.

## Supported environment

Use the repository's declared environment as the source of truth. At the time this guide was added:

- `pyproject.toml` declares Python `>=3.14.2,<4.0`.
- `custom_components/frank_energie/manifest.json` declares `python-frank-energie==2026.9.20` and integration version `2026.9.20`.
- The integration is a config-entry-based cloud-polling service integration.
- The repository declares Platinum as its target Quality Scale; do not claim compliance unless every applicable rule has been verified.

When changing versions, check Home Assistant's currently supported Python version and the dependency's compatibility. Keep version declarations consistent where they are intended to match, and document intentional differences.

## Home Assistant architecture

### Config-entry lifecycle

- Use the supported config-entry setup, unload, reauthentication, reconfiguration, options, and migration patterns that apply to the change.
- Keep runtime objects scoped to their config entry; use a precise typed runtime-data structure where supported.
- Forward entity platforms through current Home Assistant APIs and unload them correctly.
- Register listeners and scheduled callbacks with lifecycle-managed cleanup (for example, `entry.async_on_unload` where appropriate).
- Make setup failures explicit and recoverable. Use the appropriate Home Assistant exception for authentication failures, temporary setup failures, and coordinator update failures.
- Preserve multi-site and multiple-entry behavior. Do not merge devices or runtime state across entries accidentally.
- Do not add new legacy YAML setup paths or deprecated platform-forwarding APIs. Treat existing legacy code as technical debt, not a pattern for new work.

### API clients, asynchronous work, and coordinators

- Keep network I/O asynchronous and use Home Assistant's shared `aiohttp` session via `async_get_clientsession` when supported by the client library.
- Prefer dependency APIs that accept an injected session; do not create a new session for every request.
- Put shared polling and data normalization in the relevant coordinator/API layer, rather than making each entity issue its own requests.
- Use `DataUpdateCoordinator` when entities share a data source or refresh lifecycle.
- Configure bounded timeouts and retry only transient failures. Respect rate limits and avoid overlapping refreshes or retry storms.
- Use `asyncio.timeout()` for bounded asynchronous operations where appropriate; do not use blocking sleeps or synchronous network calls on the event loop.
- Propagate cancellation correctly and ensure listeners, sessions, and other resources are cleaned up.
- Do not turn failed requests into valid-looking empty data unless the provider explicitly returned a valid empty result.
- Validate and normalize external payloads at the API/coordinator boundary. Treat optional fields, `null`, missing keys, expired settings, and partial responses as expected cases when the upstream contract permits them.
- Keep the dependency independent of Home Assistant where appropriate; Home Assistant-specific utilities belong in this integration, not in the separate `python-frank-energie` library.

### Entities and devices

- Use current entity-platform APIs, `CoordinatorEntity` for coordinator-backed entities, and typed entity descriptions where they improve consistency.
- Provide stable, entry-safe unique IDs and modern entity naming.
- Only create device-registry entries for real, meaningful devices or services. Every `via_device_id` must reference an existing valid parent device in the appropriate config-entry scope.
- Keep account/site-level services and individual batteries, chargers, vehicles, and PV systems correctly separated. Avoid accidental cross-entry device merging.
- Derive availability from real data freshness and coordinator state. Do not report zero for missing, invalid, stale, or unavailable data.
- Use correct units, device classes, state classes, entity categories, and timestamp semantics. Ensure long-term statistics are valid and stable.
- Expose attributes only when they are useful, serializable, and reasonably bounded. Avoid duplicating large payloads across many entities.
- Create entities dynamically when the upstream account or site can add or remove supported resources after initial setup, and handle stale registry entries appropriately.

### Frank Energie-specific behavior

- Preserve correct behavior for multiple config entries and selected site references; never silently use one entry's site or coordinator for another entry.
- Respect the different data lifecycles for prices, settings, statistics, batteries and battery sessions, chargers, vehicles, and PV systems.
- Do not assume optional vehicle, charger, battery, or account fields are always populated. In particular, a nullable upstream field must not make an entire coordinator or unrelated entities unavailable when a safe, documented fallback is possible.
- Keep price timestamps timezone-aware and distinguish UTC instants from Amsterdam-local calendar dates and publication windows. Use the repository's timezone policy and test daylight-saving transitions and midnight boundaries where relevant.
- Preserve cache integrity and freshness rules. A cache must not make stale data appear current, and failed refreshes must not overwrite valid cached data with fabricated values.
- The README currently states that this integration does not register Home Assistant services. Do not document or claim service actions unless the implementation actually registers them. The integration exposes behavior through entities, events, buttons, selects, and sensors as implemented.

## Configuration, security, and diagnostics

- Keep credentials and tokens out of logs, exception messages, diagnostics, test snapshots, fixtures, and issue examples.
- Redact sensitive fields from diagnostics and debug output. Verify redaction when new fields or nested payloads are added.
- Store setup configuration in `ConfigEntry.data` and user-adjustable configuration in `ConfigEntry.options`, following the current Home Assistant API.
- Provide useful config-flow errors for authentication, connection, invalid input, missing sites, and recoverable upstream failures.
- Do not expose account-specific data unless authentication and the feature's availability justify it.
- Do not introduce hidden network calls, undocumented provider behavior, or unnecessary polling.

## Tests and validation

Add or update tests for every behavior change. Use the repository's existing pytest fixtures and mocking conventions rather than creating a parallel test framework.

Cover the applicable cases:

- successful setup, failed setup, unload/reload, migration, and multiple config entries;
- config-flow success, authentication/connection failures, retry/recovery, duplicate-entry handling, and options or reauthentication flows affected by the change;
- coordinator refresh success, temporary failures, authentication expiry, malformed or partial payloads, and nullable upstream fields;
- entity availability, units, state classes, unique IDs, device relationships, and entity creation/removal;
- timezone boundaries, daylight-saving changes, price publication timing, and cache freshness when relevant;
- diagnostics redaction and translation-key consistency when relevant.

Run the narrowest relevant tests first, then the applicable broader checks from CI. Inspect workflow definitions before claiming which checks are required or passed. Report commands and results accurately; do not claim a test, linter, validation, or live API check was run unless it actually was.

## Documentation, translations, and Quality Scale

- Update the relevant files under `docs/` and the README when user-visible behavior, configuration, entities, events, troubleshooting, or update timing changes.
- Keep entity names, descriptions, selectors, and exception messages translated consistently across every shipped translation file. Follow the repository's translation tests; do not update only a subset of locales.
- Keep the integration manifest accurate, valid JSON, and consistent with the actual implementation and dependency requirements.
- Use the official Home Assistant Integration Quality Scale as a checklist, not as an unsupported claim of compliance. Verify the current rule and its applicability before marking it complete.
- Do not add custom services, dependencies, compatibility shims, abstractions, or documentation claims without a concrete implementation need.

## Official references

Use current official documentation as the primary source:

- [Home Assistant Developer Documentation](https://developers.home-assistant.io/)
- [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
- [Quality Scale rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
- [Config entries](https://developers.home-assistant.io/docs/config_entries_index/)
- [Integration manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/)
- [Home Assistant Core source](https://github.com/home-assistant/core)

For this repository, also consult `docs/architecture.md`, `docs/developer.md`, `docs/update_frequency.md`, the relevant files under `SPECS/`, and tests covering the behavior being changed.
