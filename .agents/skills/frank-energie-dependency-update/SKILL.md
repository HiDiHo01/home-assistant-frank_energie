---
name: frank-energie-dependency-update
description: Safely update python-frank-energie and other runtime or development dependencies in the Frank Energie Home Assistant integration. Use for library version bumps, manifest and Poetry synchronization, dependency security updates, compatibility verification, and dependency-related CI failures.
---

# Frank Energie dependency upgrade

## Confirm scope

Read `AGENTS.md`, `AI_POLICY.md`, `home-assistant.md`, `pyproject.toml`, `custom_components/frank_energie/manifest.json`, `poetry.lock` if present, relevant workflows, and affected tests. Establish the actual dependency version, target version, Python requirement, and installed Home Assistant compatibility.

The integration depends on the separately maintained [python-frank-energie library](https://github.com/HiDiHo01/python-frank-energie); review its changelog, API and data-model changes, and relevant tests before updating. Do not assume that its Python minimum or API contract equals this integration's minimum.

## Mandatory migration when official test plugin is available

On every Home Assistant test-dependency upgrade, **check if a stable published `pytest-homeassistant` release supports the minimum Home Assistant/Python versions declared by this repository**. The official [Home Assistant pytest package](https://github.com/home-assistant/core/tree/2026.11.0/packages/pytest-homeassistant) supersedes `pytest-homeassistant-custom-component`. Once a compatible official release is verified, **prioritize migration**: replace the old development dependency in `pyproject.toml`, replace all `pytest_homeassistant_custom_component` imports with `pytest_homeassistant`, update fixtures and pytest plugin configuration using the official migration guide, regenerate `poetry.lock` through Poetry, and update the CI runtime version checks. The two plugins must never be installed together.

Verify published package metadata and Core compatibility rather than assuming the package exists; never invent a version or edit the generated lockfile by hand. Validate the lockfile, actual installed Core version, focused and full tests, Ruff, Flake8, and CI before considering the migration complete. The official stable `pytest-homeassistant==2026.11.0` release is available; use it with matching Home Assistant 2026.11.0 and validate the generated lockfile before merging. Do not downgrade the Home Assistant API or disable tests to make checks green. This implements the mandatory rule in `AGENTS.md`.

## Change workflow

1. Identify the exact provider-library or dependency change needed; verify the target release exists using trustworthy package/release metadata. Never fabricate a tag, release note, or comparison URL.
2. Inspect the current use sites and API changes: authentication, nullable vehicle/charger/battery fields, price resolution, invoice rate limits, timestamp conversions, and upstream exceptions.
3. Update the runtime requirement in `manifest.json` and the compatible constraint in `pyproject.toml` according to existing repository policy. Keep dependency files consistent; refresh the lockfile only if the project uses one, via the declared dependency manager.
4. Avoid introducing a second API client, speculative compatibility shims, or unrelated feature changes. Separate broad feature work from a dependency bump where feasible.
5. Update tests/fixtures and code only for demonstrated compatibility changes. Use sanitized, mocked responses and preserve electricity-only, gas-only, dual-contract, cache, and config-entry behavior.
6. Validate against the configured Python/Home Assistant CI matrix; include focused regression tests, the full pytest suite when feasible, Ruff/Flake8, HACS/hassfest, and dependency consistency checks as applicable.
7. Inspect the final diff for accidental dependency upgrades, new permissions, changes to public behavior, and secrets.

## Reporting

Document old and new versions, verified source release/change evidence, all changed dependency declarations, compatibility impact, exact executed checks/results, and any unknowns. Never claim CI or an upstream release was verified without evidence.

## Adapted learning sources

Adapted from [Core's dependency-bump skill](https://github.com/HiDiHo01/core/blob/dev/.claude/skills/bump-dependency/SKILL.md) and [python-frank-energie's repository instructions](https://github.com/HiDiHo01/python-frank-energie/blob/main/AGENTS.md). This repository uses its own Poetry, manifest, PR, and CI conventions rather than Core's requirement-generation scripts.
