---
name: frank-energie-dependency-update
description: Safely update python-frank-energie and other runtime or development dependencies in the Frank Energie Home Assistant integration. Use for library version bumps, manifest and Poetry synchronization, dependency security updates, Smart HVAC release gating, compatibility verification, and dependency-related CI failures.
---

# Frank Energie dependency upgrade

## Confirm scope

Read `AGENTS.md`, `AI_POLICY.md`, `home-assistant.md`, `pyproject.toml`, `custom_components/frank_energie/manifest.json`, `poetry.lock` if present, relevant workflows, and affected tests. Establish the actual dependency version, target version, Python requirement, and installed Home Assistant compatibility.

The integration depends on the separately maintained [python-frank-energie library](https://github.com/HiDiHo01/python-frank-energie); review its changelog, API and data-model changes, and relevant tests before updating. Do not assume that its Python minimum or API contract equals this integration's minimum.

## Change workflow

1. Identify the exact provider-library or dependency change needed; verify the target release exists using trustworthy package/release metadata. Never fabricate a tag, release note, or comparison URL.
2. Inspect the current use sites and API changes: authentication, nullable vehicle/charger/battery fields, price resolution, invoice rate limits, timestamp conversions, and upstream exceptions.
3. Update the runtime requirement in `manifest.json` and the compatible constraint in `pyproject.toml` according to existing repository policy. Keep dependency files consistent; refresh the lockfile only if the project uses one, via the declared dependency manager.
4. Avoid introducing a second API client, speculative compatibility shims, or unrelated feature changes. Separate broad feature work from a dependency bump where feasible.
5. Update tests/fixtures and code only for demonstrated compatibility changes. Use sanitized, mocked responses and preserve electricity-only, gas-only, dual-contract, cache, and config-entry behavior.
6. Validate against the configured Python/Home Assistant CI matrix; include focused regression tests, the full pytest suite when feasible, Ruff/Flake8, HACS/hassfest, and dependency consistency checks as applicable.
7. Inspect the final diff for accidental dependency upgrades, new permissions, changes to public behavior, and secrets.

## Smart HVAC release gate

For the authenticated Smart heating / Slim verwarmen entity, follow
[the documented data contract](../../../docs/smart_hvac_activation.md):

- Verify the **published version pinned in `manifest.json`**, not just
  `python-frank-energie/main`, exposes the site-specific `api.me(site_reference)`
  `Me.user_smart_hvac` response populated from `userSmartHvac(siteReference:)`.
  The released `2026.11.9` client contains the Smart HVAC additions, but
  future version changes require fresh contract and compatibility checks.
- Do not declare this feature production-ready solely because the
  integration's mocked pytest suite passes. Before merge, verify a **released**
  library version exposes `Me.user_smart_hvac` and the expected
  `isActivated`, `isAvailableOnSite`, `hasAcceptedTerms`, and
  `isAvailableInCountry` fields.
- Update `custom_components/frank_energie/manifest.json`,
  `pyproject.toml`, and `poetry.lock` together for the real published
  version. Never guess a version or pin an unreleased branch in production.
- Test both activated and deactivated services, missing data, site isolation,
  authentication/reauthentication, optional-request failures, and the real
  Home Assistant entity presentation. Keep the `smart_hvac` entity's stable
  ID and its deliberate `RUNNING` display class; this class does not mean
  the physical heat pump is running.
- Recheck release availability when revisiting a draft PR; keep the draft
  release gate visible until deployment compatibility is demonstrated.

## Reporting

Document old and new versions, verified source release/change evidence, all changed dependency declarations, compatibility impact, exact executed checks/results, and any unknowns. Never claim CI or an upstream release was verified without evidence.

## Adapted learning sources

Adapted from [Core's dependency-bump skill](https://github.com/HiDiHo01/core/blob/dev/.claude/skills/bump-dependency/SKILL.md) and [python-frank-energie's repository instructions](https://github.com/HiDiHo01/python-frank-energie/blob/main/AGENTS.md). This repository uses its own Poetry, manifest, PR, and CI conventions rather than Core's requirement-generation scripts.
