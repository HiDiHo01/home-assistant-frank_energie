# Quality Readiness

Evidence reviewed on **2 November 2026**, using `main` commit
`a691e3482926dc8dd0bef25dfc7436e2d90f1b82` and the accompanying session/privacy
changes. This is a custom-integration readiness assessment, not a Home Assistant
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

## Reproducible baseline

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
required. The three existing `tests/test_init.py` skips cite lingering timers;
they remain visible and need a specific resolution or justification.

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

## Validation after the fixes

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

The rule matrix matches all 54 rule IDs in Core 2026.10.4's validator: six
verified `done` entries, six justified `exempt` entries and 42 `todo` entries.
The latter include both implemented-but-incompletely-verified behavior and
known remaining work, rather than representing 42 newly discovered bugs.

Typed `entry.runtime_data` is already present, but the price coordinator is also
stored in the legacy `hass.data[DOMAIN][entry_id]` dictionary. The `runtime-data`
rule remains `todo` until that alias and its consumers are removed and lifecycle
cleanup is verified.

The patch also applies cleanly to PR #368 at `3378bd3`. An isolated combination
of both changes passes **533 tests, with the same 3 skips**, plus Ruff lint and
format checks. This is local compatibility evidence, not a merged-main result.

## Remaining priorities

| Priority | Work | Acceptance evidence |
| --- | --- | --- |
| High | Review and integrate overlapping runtime/lifecycle PRs | Passing tests on the actual merged head; no conflicting cache, resolution or reconfiguration behavior |
| High | Complete config-flow and per-resource failure coverage | Full flow coverage; more than 95% in each module; no artificial exclusions or relaxed assertions |
| High | Enforce strict typing and type the dependency | A strict checker in CI with no broad ignores, and a released client with appropriate PEP 561 typing support |
| Medium | Finish entity command/error behavior | Explicit failure reporting and translated exceptions for failed control operations |
| Medium | Complete unload, parallel-update and dynamic-device ownership | Verified cancellation/cleanup, declared platform concurrency, and tested device addition/removal |
| Medium | Reconcile documentation and entity metadata | Current options/defaults, UTC/local timing, all forwarded platforms, supported capabilities and translations |

The installed `python-frank-energie 2026.9.20` distribution has no `py.typed`
marker. Source annotations alone do not establish dependency typing support.
The integration also has incomplete function annotations and no enforced strict
type-checker configuration. These remain blockers for `strict-typing`.

## Open changes are not merged evidence

At the review checkpoint, main was `a691e348` and these PRs were still open:

| PR | Scope | Assessment treatment |
| --- | --- | --- |
| [#368](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/368) | Single/dual contract entities, cache and retries | Candidate had 506 passing tests and a clean SonarCloud analysis; not counted as main behavior |
| [#355](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/355) | Regional prices, provenance and monthly finance | Review current regional/source behavior before merging |
| [#354](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/354) | Tomorrow-price availability binary sensor | Review against contract completeness after #368 |
| [#352](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/352) | Migration and reconfiguration lifecycle tests | Retest against its actual target branch and current main |
| [#351](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/351) | Electricity connection selection | Reconcile with resolution/contract changes |
| [#350](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/350) | Resolution/cache behavior | Verify remaining changes against current main |
| [#349](https://github.com/HiDiHo01/home-assistant-frank_energie/pull/349) | Rate limits and invoice isolation | Verify the released dependency gate and backoff behavior |

No PR was merged or closed as part of this assessment. Closed historical Bronze
or Silver trackers do not replace current rule evidence.

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
- [Shared web sessions](https://contributors.home-assistant.io/core/quality-scale/inject-websession/)
- [Strict typing](https://contributors.home-assistant.io/core/quality-scale/strict-typing/)
- [Per-module test coverage](https://contributors.home-assistant.io/core/quality-scale/test-coverage/)
- [Core 2026.10.4 quality validator](https://github.com/home-assistant/core/blob/2026.10.4/script/hassfest/quality_scale.py)
- [Platinum tracker #173](https://github.com/HiDiHo01/home-assistant-frank_energie/issues/173)
