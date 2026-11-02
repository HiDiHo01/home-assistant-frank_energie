# Quality Readiness

Evidence reviewed on **2 November 2026**. The initial audit used `main`
`a691e3482926dc8dd0bef25dfc7436e2d90f1b82`. The runtime/lifecycle follow-up builds
on PR #369 at `169cfcc306dbdd2e40bc43c705f05524581e8098`, which includes the
contract-support changes merged into `main` at `a90c37ba`.
This is a custom-integration readiness assessment, not a Home Assistant
quality-tier award. **Platinum remains a target.**

The previous manifest declared Platinum without complete evidence. That field
has been removed rather than declaring another unverified tier. The
[rule matrix](../custom_components/frank_energie/quality_scale.yaml) records all
54 current rules, including individual explanations for exemptions. `todo`
means incomplete or not yet fully verified; it does not mean that every listed
feature is absent or defective.

## Verified fixes

- Runtime setup already passed Home Assistant's shared aiohttp session. Login,
  site selection, site retries and options authentication now use it as well.
  Real client context managers are exercised with mocked account endpoints,
  including failures, and leave the shared session open.
- Setup and options logs no longer print stored credential dictionaries. Site
  selection and setup omit delivery addresses and account models. Authentication
  error logs retain useful traceback frames and the exception type without
  echoing private provider messages or chained exception values.
- Coordinator fetch logs omit raw account, vehicle and battery models, and
  resolution changes no longer print all saved options. Event payloads and
  returned models retain their existing values.
- The authentication-failure helper now starts reauthentication through
  `ConfigEntry.async_start_reauth(hass)` through a synchronous HA callback. The removed
  `hass.config_entries.async_start_reauth(...)` API was not available in the
  inspected Home Assistant release. The helper had no active repository callers;
  its repair does not replace the coordinator's existing `ConfigEntryAuthFailed`
  handling.
- Configuration documentation now explains entry removal and price-cache cleanup.

The new session/log regressions failed on unchanged main before the fixes. They
use synthetic account data and mock external endpoints, not production accounts.
The audit does not certify every upstream dependency log or all possible
provider error payloads as safe for public sharing.

## Initial audit baseline

The complete, unchanged-main suite passed: **443 passed, 3 skipped**.

| Environment | Version |
| --- | --- |
| Python | 3.14.2 |
| Home Assistant | 2026.10.4 |
| pytest | 9.0.3 |
| pytest-homeassistant-custom-component | 0.13.374 |
| python-frank-energie | 2026.9.20 |
| coverage.py | 7.16.2 |
| pytest-cov | 7.1.0 |
| Ruff | 0.15.18 |
| Flake8 | 7.3.0 |

Coverage was measured across the entire integration package, including currently
unused modules. No new exclusions or skips were added to obtain these results.

| Baseline metric | Result |
| --- | --- |
| Statements | 2,773 / 4,100, **67.63%** |
| Branches | 596 / 1,224, **48.69%** |
| Combined coverage.py metric | **63.28%** |
| Config flow | 227 / 395 statements, **57.47%** |
| Diagnostics | **100%** statements and branches in the current tests |

These package-wide percentages do not satisfy the official requirement of more
than 95% coverage in **every** module. Full config-flow coverage is also still
required. This historical baseline had three timer-related skips in
`tests/test_init.py`; the runtime/lifecycle follow-up below reactivates them.

Reproduce a measurement in the repository's Python 3.14 environment with
`pytest-cov` available:

```sh
poetry run pytest -q tests \
  --cov=custom_components.frank_energie --cov-branch \
  --cov-report=term-missing --cov-report=json:coverage.json
```

Dependency/test-plugin patch versions can change the result. Record the exact
environment and commit when updating evidence. The measurements above describe
the unchanged baseline, not the complete rule status after future merges.

## Initial session/privacy validation

The updated suite passes: **470 passed, 3 existing skips**, including 27 new
account-flow, session-ownership, log-privacy and reauth-helper cases. The focused
new/existing config-flow suite passes all 37 cases.

| Metric | Baseline | Updated candidate |
| --- | --- | --- |
| Statement coverage | 67.63% | 71.49% |
| Branch coverage | 48.69% | 50.25% |
| Combined coverage.py metric | 63.28% | 66.60% |
| Config-flow statement coverage | 57.47% | 73.99% |

Ruff lint/format, critical Flake8 and whitespace checks pass. The full configured
Flake8 report has the same existing 21 findings as the unchanged baseline:
12 complexity findings, four Ruff-compatible slice-spacing findings and five
long lines. The report remains informational in the existing workflow; this
change neither suppresses those findings nor fixes unrelated lint debt.

## Runtime and lifecycle follow-up

The exact parent, PR #369 at `169cfcc`, passes **533 tests with 3 skips**.
The updated candidate passes **552 tests with no skips**, including 16 new
runtime/lifecycle cases and the three reactivated authentication/title tests.
Both full suites were measured in the environment above, including the offline
Node.js workflow tests. The contract regression suite is part of this actual
parent and candidate, covering single electricity, single gas and dual contracts.
An additional complete run using the lockfile's Home Assistant 2026.10.4 and
pytest-homeassistant-custom-component 0.13.375 also passes all 552 tests.

- `runtime_data.py` defines one typed record and config-entry alias shared across
  setup, flows, coordinators, all six platforms and diagnostics. The entrypoint
  assigns runtime data directly; no legacy entry-ID coordinator dictionary remains.
- Failed or cancelled setup removes runtime data. Concurrent initial refreshes
  are cancelled and awaited before the original exception propagates, preserving
  Home Assistant's authentication and setup-retry handling.
- Failed platform forwarding unloads already-created sensors. Real HA reload and
  unload tests verify entry isolation, stable entity IDs, refused-unload retention
  and stopped polling for every resource, including per-battery sessions.
- The three auth/title tests now explicitly unload their entries and verify runtime
  removal. The integration no longer changes the host's Windows event-loop policy
  at import. The unused, broken YAML setup path and its diagnostic class are removed;
  YAML configuration was already unsupported.

| Metric | Exact parent `169cfcc` | Runtime/lifecycle candidate |
| --- | --- | --- |
| Statements | 2,998 / 4,171, **71.88%** | 3,039 / 4,135, **73.49%** |
| Branches | 693 / 1,258, **55.09%** | 703 / 1,256, **55.97%** |
| Combined coverage.py metric | **67.99%** | **69.41%** |
| Setup module statements | **75.00%** | **97.62%** |
| Config-flow statements | **68.69%** | **68.77%** |

Runtime-data and diagnostics modules have 100% statement coverage in this run.
Removing unused legacy setup code changes the package denominator; these numbers
are measured results, not a claim that every module reaches the quality target.
No coverage configuration or test assertions were relaxed.

Ruff lint/format, critical Flake8, JSON/YAML parsing and whitespace checks pass.
The full informational Flake8 report decreases from 21 to 20 existing findings:
12 complexity findings, four slice-spacing findings and four long lines. No
lint thresholds or workflow settings changed. The official Core 2026.10.4
runtime-data assignment helper and 12 typed-entry interface checks pass against
the source; this is separate from full hassfest validation.

The matrix contains all 54 Core 2026.10.4 rule IDs: **8 done, 6 exempt, 40 todo**.
`runtime-data` and `config-entry-unloading` now have code and lifecycle evidence.
The remaining entries include implemented behavior awaiting a complete audit;
they do not represent 40 newly discovered bugs.

## Remaining priorities

| Priority | Work | Acceptance evidence |
| --- | --- | --- |
| High | Review and integrate overlapping runtime/lifecycle PRs | Passing tests on the actual merged head; no conflicting cache, resolution or reconfiguration behavior |
| High | Complete config-flow and per-resource failure coverage | Full flow coverage; more than 95% in each module; no artificial exclusions or relaxed assertions |
| High | Enforce strict typing and type the dependency | A strict checker in CI with no broad ignores, and a released client with appropriate PEP 561 typing support |
| Medium | Finish entity command/error behavior | Explicit failure reporting and translated exceptions for failed control operations |
| Medium | Complete parallel-update and dynamic-device ownership | Declared platform concurrency and tested device addition/removal and subscriptions |
| Medium | Reconcile documentation and entity metadata | Current options/defaults, UTC/local timing, all forwarded platforms, supported capabilities and translations |

The installed `python-frank-energie 2026.9.20` distribution has no `py.typed`
marker. Source annotations alone do not establish dependency typing support.
The integration also has incomplete function annotations and no enforced strict
type-checker configuration. These remain blockers for `strict-typing`.

## Open changes are not merged evidence

PR #368 is merged into `main` at `a90c37ba`. PR #369 remains open at the
follow-up checkpoint; the runtime/lifecycle changes build on its current head,
including the merged contract support. Measurements of this candidate describe
proposed code, not a completed merge or an awarded quality tier. Dedicated
reconfiguration work and the other overlapping open PRs require their own review
before they can count as merged evidence.

## What the existing checks prove

The Python matrix, Ruff, HACS/Hassfest, CodeQL and SonarCloud check specific
properties; a passing check does not certify a quality tier. In particular,
`.github/workflows/quality_scale.yml` currently checks documentation and file
presence. Core's hassfest quality-rule validator skips custom integrations.
Keep that distinction when reporting green CI or editing this matrix.

The full Flake8 pass is currently informational and uses a 120-character line
limit and complexity threshold of 10. The critical `E9,F63,F7,F82` pass is
blocking. Apply the workflow's actual flags when measuring existing lint debt;
Flake8's default 79-character limit produces a different result. No lint or
coverage thresholds were weakened by these changes.

## Official references

- [All 54 quality rules](https://contributors.home-assistant.io/core/quality-scale/rules/)
- [Tier prerequisites](https://contributors.home-assistant.io/core/quality-scale/tiers/)
- [Quality scale file format](https://contributors.home-assistant.io/core/quality-scale/quality-scale-file/)
- [Typed runtime data](https://contributors.home-assistant.io/core/quality-scale/runtime-data/)
- [Config-entry unloading](https://contributors.home-assistant.io/core/quality-scale/config-entry-unloading/)
- [Shared web sessions](https://contributors.home-assistant.io/core/quality-scale/inject-websession/)
- [Strict typing](https://contributors.home-assistant.io/core/quality-scale/strict-typing/)
- [Per-module test coverage](https://contributors.home-assistant.io/core/quality-scale/test-coverage/)
- [Core 2026.10.4 quality validator](https://github.com/home-assistant/core/blob/2026.10.4/script/hassfest/quality_scale.py)
- [Platinum tracker #173](https://github.com/HiDiHo01/home-assistant-frank_energie/issues/173)
