---
name: frank-energie-ha-compatibility
description: Check the Frank Energie custom integration against Home Assistant API changes and deprecations. Use for Core version upgrades, Python compatibility, ConfigEntry and config-flow changes, entity APIs, DataUpdateCoordinator updates, migration risks, and Home Assistant release-note impact.
---

# Home Assistant compatibility and deprecation assessment

## Sources and scope

Read the repository's `AGENTS.md`, `AI_POLICY.md`, `home-assistant.md`, manifest, Python constraints, workflow matrix, and relevant integration code. Compare against **current official** Home Assistant release notes, developer documentation, and, when needed, the implementation in [Home Assistant Core](https://github.com/home-assistant/core). Distinguish the stable supported release from a development-branch preview and name the exact versions being compared.

Do not apply obsolete guidance, Core-only `dev` tooling, or general dependency-bump routines without verifying this repository's actual compatibility baseline.

**Required API selection:** For any new or materially refactored integration capability, prefer the public API in the *latest stable* Home Assistant Core release (verify its exact release tag). If that API requires a newer Core release than `hacs.json` declares, **raise the minimum version**, document the breaking compatibility impact in the PR, and update tests, installation requirements, and any other version assertions. Do not substitute legacy compatibility paths merely to avoid raising the minimum. Do not raise the minimum unless a concrete API or other compatibility requirement justifies it. This implements the rule in `AGENTS.md`.

## Compatibility workflow

1. Identify the requested Home Assistant/Python target, the project's declared minimums, and the changed or deprecated API's documented removal/version deadline.
2. Search the integration's real call sites for affected APIs, imports, config flows, entry runtime data, platform forwarding, coordinator calls, event listeners, entity properties, and serialization.
3. Trace the data and lifecycle impact: initial setup, auth failure, reauth, reconfigure, migration, unload/reload, background task cancellation, entity/device registry stability, and contract/site isolation.
4. Identify any provider library compatibility changes separately; do not fix a library defect by introducing unverified duplicate parsing inside the integration.
5. Categorize each finding: `confirmed break`, `due deprecation`, `future risk`, `unaffected`, or `unverified`. Do not remove code solely because a deprecation was mentioned; confirm it is applicable to the supported release.
6. For approved changes, preserve existing config entries and unique IDs or provide explicit migrations and tests. Prefer current supported APIs and the smallest complete fix.

## Verify

Add focused tests that exercise the actual Home Assistant path and failure behavior. Choose relevant `tests/test_init.py`, `tests/test_config_flow.py`, `tests/test_coordinator.py`, and entity-platform tests as appropriate. Run the configured Python and Home Assistant CI matrix, lint/format, and HACS/hassfest checks where relevant. Report commands not executed.

## Output

For each relevant change, provide the official reference, version where it applies, affected file/call site, user-visible consequence, minimal correction, tests, and confidence level. Do not describe a compatibility check as a live Home Assistant test unless one actually ran.

## Adapted learning sources

Learned from [Home Assistant Core's integration-knowledge and deprecation-cleanup skills](https://github.com/HiDiHo01/core/tree/dev/.claude/skills) and [the project's Home Assistant guidance](https://github.com/HiDiHo01/home-assistant-frank_energie/blob/main/home-assistant.md). Core-specific branch, worktree, and PR rules are not inherited.
