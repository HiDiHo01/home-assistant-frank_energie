# Frank Energie Integration-Specific Guidance

This guide records behavior and invariants specific to this integration. General Home Assistant, Python, security, testing, and review standards remain in `AGENTS.md`, `AI_POLICY.md`, and `CLAUDE.md`.

## Integration invariants

- **Config-entry and site isolation:** Keep coordinators, runtime state, and entities scoped to the correct config entry and selected site reference. Never reuse another entry's site or coordinator, or accidentally merge devices across entries.
- **Distinct data lifecycles:** Preserve the separate refresh, availability, and expiry behavior of prices, settings, statistics, batteries and battery sessions, chargers, vehicles, and PV systems. Do not make unrelated entities unavailable because one resource failed.
- **Nullable upstream data:** Treat missing, `null`, expired, and partial fields as possible when the upstream contract permits them. In particular, nullable vehicle, charger, battery, or account fields must not crash parsing or take down an entire coordinator when a safe, semantically correct fallback exists. Do not fabricate valid-looking values.
- **Time semantics:** Keep timestamps timezone-aware. Distinguish UTC instants from Amsterdam-local calendar dates and price publication windows. Preserve the repository's timezone policy; consider daylight-saving transitions and midnight boundaries when changing time-sensitive logic.
- **Cache integrity:** Preserve cache freshness and validation. Do not present stale cached data as current, overwrite valid cached data with fabricated values after a failed refresh, or confuse a cache fallback with a successful live update.
- **Service claims:** The integration README states that no Home Assistant services are registered. Do not document or introduce service actions unless the implementation actually registers them. Describe existing behavior through the entities, events, buttons, selects, and sensors implemented by the repository.

## Repository references

Consult the documentation and specifications relevant to the change before modifying behavior:

- [Architecture](docs/architecture.md) — integration components and data flow.
- [Developer guide](docs/developer.md) — repository development and validation workflow.
- [Update frequency](docs/update_frequency.md) — polling and data refresh expectations.
- `SPECS/` — detailed feature and provider behavior specifications.
- Tests for the affected API parsing, coordinator, entity, time, cache, or config-entry behavior.

For Home Assistant API signatures, lifecycle requirements, and deprecations, use the [official developer documentation](https://developers.home-assistant.io/) and verify against [Home Assistant Core](https://github.com/home-assistant/core) when necessary. For quality-tier requirements, consult the [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/). The manifest's declared quality tier is a target, not proof that every applicable rule is satisfied.

Do not duplicate general coding rules or maintain version snapshots here. Treat `pyproject.toml`, `custom_components/frank_energie/manifest.json`, and the implementation as the sources of truth for current versions and behavior.
