# AGENTS.md — Frank Energie tests

## Scope and authority

These instructions apply to all files in `tests/`. Read the repository
[AGENTS.md](../AGENTS.md), [AI_POLICY.md](../AI_POLICY.md),
[home-assistant.md](../home-assistant.md) and the local
[AI_POLICY.md](AI_POLICY.md) before changing tests. The parent requirements
remain mandatory; this file supplements them. Report conflicting or
unavailable instructions rather than inventing exceptions.

## Test environment

- Use the committed dev container or the Python release in
  [.python-version](../.python-version), with the Poetry version and
  installation commands in the [developer guide](../docs/developer.md).
  Read [pyproject.toml](../pyproject.toml) and
  [poetry.lock](../poetry.lock) for actual dependency constraints.
- Use the official `pytest-homeassistant` plugin matching Home Assistant
  Core. Keep asyncio auto mode and function-scoped fixture loops. Never
  co-install the obsolete custom-component plugin.
- The experimental Python 3.15 client smoke job is not a full Home Assistant
  test environment. Do not present its result as equivalent to the required
  Home Assistant suite.
- Inspect [conftest.py](conftest.py) and [utils.py](utils.py) before creating
  fixtures. Reuse `frank_energie_setup`, `MockConfigEntry`,
  `ResponseMocks`, and the existing provider mock fixtures where suitable.
  Keep fixture changes narrowly scoped to avoid affecting unrelated tests.

## Test design

- Name tests after observable behavior. Give new and modified functions,
  including fixtures and helpers, descriptive docstrings and precise type
  hints. Use two blank lines between module-level definitions, built-in
  generic types, snake_case, and pytest parametrization for meaningful cases.
- Reproduce a bug before or alongside the fix. Assert expected state,
  attributes, registry identity, events, exceptions, side effects and
  absence of unwanted changes as applicable. Do not only assert call counts.
- Exercise actual SDK model parsing and Home Assistant validation when
  relevant. Mock network and hardware boundaries, not the implementation
  under test. Use `AsyncMock` for awaited calls and assert awaited arguments,
  including site/device selection where relevant.
- Use deterministic synthetic records and timezone-aware fixed dates. Use
  the freezer and Home Assistant event/time helpers instead of arbitrary
  sleeps. Await scheduled work, cancel and await outstanding tasks, and
  verify listener/resource cleanup.
- Keep fixtures isolated. Use temporary directories for storage and
  subprocess tests, bounded subprocess timeouts, scoped patches, and
  independent config entries. Never depend on test order or a real account.

## Regression coverage

Choose cases relevant to each behavior change:

| Area | Required evidence when affected |
| --- | --- |
| Setup and unload | Real config-entry lifecycle, rollback, cancellation, reload and cleanup |
| Config flows | Authentication, reauth, reconfigure, site mismatch, migration and preserved options |
| Coordinators | Successful refresh, partial/missing data, typed errors, rate limits, cache expiry and recovery |
| Entities | Correct device/state class, units, enum options, availability, attributes and stable IDs |
| Hardware | Discovery, reordered inventory, verified removal, rediscovery, registry ownership and cross-entry isolation |
| Contract variants | Electricity-only, gas-only, combined and public mode where supported |
| Prices and costs | Negative prices, genuine zero versus missing data, PT15M/PT60M, Amsterdam day boundaries and DST |
| Smart features | Independent activity, steering, connection and other resource states; missing summary versus explicit unknown |
| Actions and diagnostics | Validated parameters, entry isolation, failure paths, no unintended requests and redaction |
| Translations | Matching keys, enum options and placeholders across all shipped locales |

Use real Home Assistant registry and config-flow fixtures for lifecycle
claims; lightweight mocks alone cannot establish registry compatibility.
Preserve existing entity IDs and customizations during migrations. Include
disabled entities, collisions and repeated migration where relevant.

## CI and workflow tests

- Use the existing `test_workflow_*.py` and `test_release_manifest.py`
  coverage when changing workflows, authorization, manifest releases or
  dependency automation.
- Verify actual workflow YAML and executable behavior, not just copied
  strings. Run inline GitHub scripts with Node.js 24 and mocked GitHub APIs
  where appropriate. Use synthetic local Git repositories for Git operations;
  never require network access, a live token or repository write permissions.
- Keep stable Python checks blocking and experimental checks explicitly
  non-blocking as configured in
  [ci.yaml](../.github/workflows/ci.yaml).
- Use the pinned Ruff version from `pyproject.toml` and
  [ruff.yml](../.github/workflows/ruff.yml). Preserve the existing Flake8
  baseline; fix new violations rather than expanding it.

## Validation and reporting

Run from the repository root in the locked environment:

```sh
poetry check --lock
poetry run python -m pip check
poetry run python -m pytest tests -ra --junitxml=pytest-results.xml
poetry run flake8 . --jobs=1 --extend-exclude=.venv --count --select=E9,F63,F7,F82 --show-source --statistics
poetry run python .github/helpers/check_flake8_baseline.py
ruff check custom_components tests
ruff format --check --diff custom_components tests
```

Run focused tests first, then the full suite and applicable CI checks.
Install Ruff at the repository's configured version in the development
environment if it is not already available. For workflow tests, ensure
Node.js 24 is available. Report exact commit, Python/Core/plugin versions,
commands, pass/fail/skip counts, warnings and unavailable checks.

If local execution is blocked, use the existing manual CI workflow or the
PR checks and inspect results for the actual candidate commit. Do not claim
a successful local test or clone when only remote execution succeeded.
Do not weaken assertions, add unjustified skips/xfails, suppress diagnostics
or alter dependencies to hide a defect. Verify alleged pre-existing failures
on the unchanged target under the same locked environment.
