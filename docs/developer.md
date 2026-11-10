# Developer Guide

This document provides information for contributors and developers working on the Frank Energie Home Assistant integration.

## Home Assistant test environment

Open the repository in GitHub Codespaces or use **Dev Containers: Reopen in Container** in VS Code. The committed container provides Python 3.14, Poetry 2.5.1, and the locked runtime and development dependencies in `.venv`. VS Code uses that interpreter and discovers the `tests` directory with pytest.

For a host installation, use Python 3.14.2 or a newer 3.14 patch and run:

```sh
uv tool install "poetry==2.5.1"
poetry env use python3.14
poetry sync --with dev --no-root
poetry check --lock
poetry run python -m pip check
```

If Python 3.14 is missing, install it with your interpreter manager or use the dev container before running these commands. Keep this environment separate from a production Home Assistant installation.

Run the complete suite against the real Home Assistant test framework:

```sh
poetry run python -m pytest tests -ra --junitxml=pytest-results.xml
```

`pytest-homeassistant` is the official, required pytest plugin. Pin it to the same version as Home Assistant Core (currently `2026.11.0`) and do not install it alongside the superseded `pytest-homeassistant-custom-component`. Its Home Assistant fixtures exercise setup, config flows, entities, coordinator behavior, and cleanup. The suite mocks provider calls and requires no production credentials. Isolated checks with substitute Home Assistant objects are useful for diagnosis, but do not count as a complete Home Assistant test run.

The existing **Flake8 and pytest** workflow runs the full suite on Python 3.14.2 and the latest 3.14 patch. It also supports **Run workflow** once this workflow revision is on the default branch. Each matrix job uploads its own JUnit report, including when pytest fails. Critical Flake8 errors remain blocking. A separate full-Flake8 gate checks every diagnostic against the measured, reviewable `.github/flake8-baseline.json`: new or increased findings fail CI. The existing C901 and E501 debt must be reduced and removed from this baseline; the targeted E203 exclusion matches Ruff-compatible slice formatting. The full pytest suite still runs after a lint failure when dependency installation and environment verification succeed.

`poetry.lock` is authoritative for reproducibility. Update Home Assistant and the matching `pytest-homeassistant-custom-component` version together, regenerate the lockfile with the pinned Poetry version, and rerun both matrix entries. Record the actual interpreter and dependency versions with the results. The existing three skipped teardown cases remain visible in pytest's summary.

## Repository Structure

```text
custom_components/frank_energie/
├── __init__.py
├── binary_sensor.py
├── button.py
├── config_flow.py
├── constants.py
├── coordinator.py
├── diagnostics.py
├── number.py
├── select.py
├── sensor.py
├── services.yaml
├── strings.json
└── translations/
```

The integration follows Home Assistant architecture patterns and uses a central DataUpdateCoordinator.

## Architecture Overview

The integration consists of four primary layers:

### Home Assistant Integration Layer

Responsible for:

- Config entries
- Entity platforms
- Services
- Diagnostics
- Events

### Coordinator Layer

`coordinator.py` acts as the central source of truth.

Responsibilities include:

- API communication
- Update scheduling
- Data caching
- Event generation
- Entity refresh coordination
- Error handling

All entities read data from the coordinator.

### API Layer

The integration uses:

```text
python-frank-energie
```

The API library handles:

- Authentication
- Token renewal
- GraphQL requests
- Data parsing
- Data models

### Entity Layer

Entity platforms expose API data to Home Assistant.

Current platforms include:

- Sensor
- Binary Sensor
- Button
- Number
- Select

## Data Flow

```text
Frank Energie API
        │
        ▼
python-frank-energie
        │
        ▼
DataUpdateCoordinator
        │
        ▼
Home Assistant Entities
```

Entities should not communicate directly with the API.

All data should flow through the coordinator.

## Coordinator Design

The coordinator is responsible for:

- Fetching market prices
- Fetching authenticated user data
- Managing update intervals
- Handling maintenance windows
- Detecting tomorrow price availability
- Firing Home Assistant events

The coordinator minimizes API requests by using intelligent polling intervals and caching.

## Event System

The integration generates Home Assistant events.

Event type:

```text
frank_energie_event
```

Examples include:

- tomorrow_prices_available
- lowest_price
- lowest_4p_price
- lowest_8p_price
- lowest_16p_price

See:

- events.md

## Entity Design Guidelines

### Dynamic Entity Creation

Entities should be created dynamically when data is available.

Examples:

- Smart Battery entities
- Smart Charging entities
- Smart Trading entities
- PV entities

Do not create unavailable entities.

### Entity Descriptions

Use entity descriptions whenever possible.

Benefits:

- Consistent metadata
- Reduced boilerplate
- Easier maintenance

### CoordinatorEntity

Entities should inherit from:

```python
CoordinatorEntity
```

when appropriate.

## Home Assistant Development Standards

The integration targets modern Home Assistant best practices.

### Async First

Use asynchronous APIs whenever available.

Examples:

```python
async def async_update(self) -> None:
```

Avoid blocking I/O.

### Config Entries

Use config entries exclusively.

Do not support YAML configuration.

### Diagnostics

Sensitive information must be redacted.

Examples:

- Tokens
- Passwords
- Personal identifiers

### Reauthentication

Support reauthentication flows through:

```text
async_step_reauth
```

### Reconfiguration

Support configuration updates through:

```text
async_step_reconfigure
```

## Python Standards

The project follows modern Python practices.

### Type Hints

Use explicit type hints.

### Dataclasses

Prefer dataclasses for structured data.

### Time Handling

Use timezone-aware datetimes.

Examples:

```python
from datetime import timezone

now(timezone.utc)
```

Avoid:

```python
utcnow()
```

### Logging

Use lazy logging formatting.

Preferred:

```python
LOGGER.debug("Received %s prices", count)
```

Avoid:

```python
LOGGER.debug(f"Received {count} prices")
```

### Internal Helpers

Internal helper methods should use a leading underscore.

Example:

```python
def _process_prices(self) -> None:
```

## Testing

The integration uses pytest-based testing.

Typical areas covered:

- Config flows
- Coordinator updates
- Entity creation
- Diagnostics
- Event generation
- Reauthentication
- Reconfiguration

### Snapshot Testing

Snapshot testing may be used for:

- Diagnostics
- API responses
- Entity structures

## Quality Scale

The project aims to comply with the Home Assistant Quality Scale.

Reference:

https://www.home-assistant.io/docs/quality_scale/

Contributions should support movement toward Platinum quality.

## Debug Logging

Enable debug logging with:

```yaml
logger:
  logs:
    custom_components.frank_energie: debug
    python_frank_energie: debug
```

## Development Workflow

Typical workflow:

1. Create a feature branch.
2. Implement changes.
3. Add or update tests.
4. Run linting.
5. Run test suite.
6. Run Hassfest.
7. Open a pull request.

## SonarQube Cloud issue export (JSON)

The read-only [Export SonarQube Issues](../.github/workflows/sonar-issues-export.yml)
GitHub Actions workflow downloads open and confirmed SonarQube Cloud findings.

1. After this workflow is merged into the default branch, open **Actions →
   Export SonarQube Issues → Run workflow**.
2. Enter a **pull_request** number (for example, `433`) to export the open
   findings on that PR's new code, matching the SonarQube PR issues page.
   Leave the field empty to export all open and confirmed project findings.
3. Download the **sonar-open-issues** artifact from the completed workflow run.
   It contains `sonar-open-issues.json` with the project key, optional PR,
   applied filters, UTC export time, total count, and all matching issues.

Changes to the exporter workflow or script also run this read-only check on PRs,
without exposing `SONAR_TOKEN`. PR-triggered artifacts are snapshots taken
at execution time; run the manual export after Sonar's analysis has finished
when you need the latest findings.

The exporter reads only this repository's fixed `sonar-project.properties`
and writes only the fixed `sonar-open-issues.json` filename; no CLI paths
are accepted, preventing arbitrary file reads or writes. The export follows
API pagination beyond the first 100 issues. It fails instead of publishing
an incomplete, malformed or inconsistent result. No issues are modified and
no Sonar token is printed. Public projects may work anonymously; if the
project needs authentication, add a repository Actions secret named
`SONAR_TOKEN` containing a SonarQube Cloud token with **Browse** access.
Never put that token in a URL, workflow input, or committed file. A missing
or insufficient token yields a failed run rather than a misleading empty export.
The artifact is retained for 14 days.

For local, offline regression checks, run
`poetry run pytest -q tests/test_sonar_issues_export.py`.
The actual API download is exercised only when the manual workflow is run
with access to SonarQube Cloud.

## Documentation

When adding features:

- Update entities.md when entities change.
- Update events.md when events change.
- Update examples.md when new automation scenarios become available.
- Update user_features.md when user-facing functionality changes.

## Related Documentation

- configuration.md
- diagnostics.md
- entities.md
- events.md
- examples.md
- faq.md
- smart_battery.md
- smart_charging.md
- troubleshooting.md
- update_frequency.md
- user_features.md
