# Smart heating activation sensor

The authenticated User-service binary sensor, **Smart heating** (NL and NL-BE:
**Slim verwarmen**, FR: **Chauffage intelligent**), indicates whether the
Frank Energie **Smart HVAC service is activated** for the selected delivery
site. It is not a measurement of a heat pump's heating, cooling, running,
power, or live steering activity.

## State, availability and presentation

| Source | Home Assistant behavior |
| --- | --- |
| `UserSmartHvac.isActivated = true` | Binary sensor `on`: service activated |
| `UserSmartHvac.isActivated = false` | Binary sensor `off`: service not activated |
| `isActivated` missing, null, or not a Boolean | Entity `unavailable`: activation not known |
| Settings coordinator refresh unsuccessful | Entity `unavailable` |

The existing `smart_hvac` binary sensor intentionally uses
`BinarySensorDeviceClass.RUNNING` **for Home Assistant's localized
active/inactive presentation**, as requested. This is a **display choice**:
`RUNNING` must not be used by automations as proof that physical equipment is
operating. Automations can use it only as a service-activation indicator.

`isAvailableOnSite` is **not** interchangeable with `isActivated`.
The diagnostic attribute `available_on_site` describes whether the provider
offers Smart HVAC at that delivery site; it does not determine the sensor's
`on` or `off` value, and it does not determine Home Assistant entity
availability. Other optional, non-identifying flags include
`available_in_country` and `has_accepted_terms`. User IDs, site references,
EANs and device IDs are never exposed as Smart HVAC state attributes.

## Data path and site selection

1. Config-entry setup discovers delivery sites through the library's
   `UserSites` operation, and the selected site reference remains scoped to
   that specific config entry. Never use a hard-coded or unrelated site's
   reference to query Smart HVAC data.
2. The settings coordinator retains `api.user(site_reference)` for existing
   user/account entities.
3. In the published **python-frank-energie 2026.11.9** client, the separate
   `api.me(site_reference)` request includes the root-level GraphQL field
   `userSmartHvac(siteReference: $siteReference)`.
4. The library parses this field as `Me.user_smart_hvac`. The settings
   coordinator copies it to `User.smartHvac`, preserving the existing
   `DATA_USER` data contract and `smart_hvac` entity identity.
5. The binary sensor strictly reads `User.smartHvac.isActivated`; strings,
   numbers and other truthy values must never be coerced into Boolean states.

A failed optional Smart HVAC request must not invalidate otherwise valid user
data. Handle expected API, network, timeout **and non-required authentication**
(`AuthException`) failures without exposing upstream response details in
logs; retain the existing Smart Charging and Smart Trading data. Propagate
`AuthRequiredException` to Home Assistant reauthentication, because that
indicates the account must authenticate again. Keep cancellation safe and
preserve coordinator-based availability.

## Translations and entity identity

| Locale | Entity name |
| --- | --- |
| `strings.json` / `en.json` | Smart heating |
| `nl.json` / `nl-BE.json` | Slim verwarmen |
| `fr.json` / `fr-BE.json` | Chauffage intelligent |

The translated **name** must be the feature name: do not append *activated*,
*active*, or *geactiveerd*. The Boolean state and the `RUNNING` device class
provide the state presentation. Keep `translation_key="smart_hvac"`, the
entity's existing unique ID and the User service device association unchanged.

## Library dependency and release gate

The published [python-frank-energie 2026.11.9 source](https://github.com/HiDiHo01/python-frank-energie/blob/v2026.11.9/python_frank_energie/models.py)
includes `Me.user_smart_hvac`, populated by the site-scoped
`userSmartHvac(siteReference: $siteReference)` query. The integration's
`manifest.json` pins `python-frank-energie==2026.11.9`, `pyproject.toml`
requires `python-frank-energie>=2026.11.9`, and `poetry.lock` resolves
`2026.11.9`. The former **unpublished-client release blocker is cleared**.

Future client upgrades must verify the *published* package, synchronize the
runtime manifest, Poetry declaration and lockfile, and test the actual
Home Assistant integration. Mocked tests alone cannot establish live account
activation or site-specific API availability; evaluate those separately
before claiming deployment compatibility. Do not use an unreleased Git branch
as a production requirement or keep a PR draft solely for this now-resolved
library publication gate.

## Smart heating service device

The integration adds the **Frank Energie - Smart heating** service device,
its sensors, and the account-wide connection request sensor when Frank confirms
`UserSmartHvac.isActivated = true` for the selected delivery site. The
activation binary sensor and the connection request count appear on the
**User** service. The binary sensor shows the site's activation status.

| Group | Data |
| --- | --- |
| Delivery site | Linked device count and the profile creation timestamp with country availability, site availability, and terms acceptance attributes |
| Linked equipment | Display name, brand, model, equipment type, provider, creation and update timestamps, and reauthentication requirement |
| Equipment summary | Frank's operational and steering statuses, the operational status timestamp, first and last session dates, and the provider's total result |
| Account connection requests | Count, brand, Frank's request status, and an indication that Frank reported an error |

Each linked `SmartHvacDevice` in the selected site's inventory appears as a
separate Home Assistant device with its equipment and summary sensors. The
integration uses Frank's display name, brand, and model when available. An
opaque identifier derived from the selected site and Frank's device ID keeps
the device stable across refreshes. Existing equipment sensors keep their
entity IDs and unique IDs when they move from the service to the device. The
service holds the device count and profile information.

The total result uses the numeric value returned by Frank, with Home Assistant's
`MONETARY` sensor class, an `EUR` native unit and a suggested display precision
of two decimals. The sensor retains the unrounded numeric value, including
negative and zero results, and does not define a statistics class. The public
Frank Energie's customer-service article *Hoe zie ik mijn totale opbrengsten?*
describes monetary savings and bonuses. The SDK's `SmartHvacSummary.total_result`
model does **not** explicitly specify a unit or prove that the GraphQL number is
already measured in whole euros; confirm that unit/scale against actual
provider evidence before merging. Frank's status strings describe the provider's
operational and steering states. The separate activation sensor continues to
describe the service.

The HVAC coordinator refreshes `smart_hvacs(site_reference)`,
`smart_hvac_connect_requests()`, and `smart_hvac_summary(device_id)`
every 15 minutes outside the API maintenance window. The inventory provides
the same fields as the device detail query. Summaries are fetched one at a
time. On rate limiting, the coordinator waits at least one polling interval
and honors a longer valid provider retry delay, capped at 24 hours. Invalid
or non-finite retry hints fall back to the polling interval. Site flags and
profile time come
from the settings coordinator's existing site response.

A successful empty inventory produces a count of zero. A successful null
inventory or connection-request response produces an unknown count; a skipped
or failed request makes the corresponding existing count sensor unavailable.
The coordinator retains explicit read-success flags for both collections.
A failed inventory refresh retains the physical devices and registrations.
A successful inventory refresh removes devices and sensors for equipment that
Frank has removed, even after service deactivation; new equipment is created
only while the service is active. A successful null device summary produces
unknown summary values. If rate limiting skips a summary read, previously
retrieved data remains cached but is marked stale, and its sensors remain
unavailable until a successful read. A summary request that was attempted
but failed discards the earlier value and remains unavailable.

Local entities show the equipment's display name. Device and request IDs are
used internally. Downloadable HVAC diagnostics contain counts and update
status. Logs and diagnostics redact personal identifiers and raw errors.
The published client exposes settings changes through a write method with a
Boolean result. Reading current modes and temperature bounds requires a
verified read operation. [Python client Smart HVAC API](https://github.com/HiDiHo01/python-frank-energie/blob/v2026.11.9/docs/smart-hvac.md)

## Developer verification

For changes to this feature, test `true`, `false`, null, missing and
malformed `isActivated`; `isAvailableOnSite` independently; settings
refresh failures; optional API errors and auth-required behavior; redaction;
and the real Home Assistant entity state machine. Check the stable entity ID,
translated names and displayed device class.

Using the repository's Python 3.14 Poetry environment:

```sh
poetry run python -m pytest tests/test_binary_sensor.py tests/test_coordinator.py tests/test_translations.py -q
poetry run python -m pytest tests/test_smart_hvac.py tests/test_diagnostics.py -q
poetry run python -m pytest tests -ra --junitxml=pytest-results.xml
ruff check custom_components tests
ruff format --check --diff custom_components tests
poetry run python .github/helpers/check_flake8_baseline.py
```

See [the developer guide](developer.md), [the integration development
skill](../.agents/skills/frank-energie-development/SKILL.md), and
the [dependency-update skill](../.agents/skills/frank-energie-dependency-update/SKILL.md).
Always report checks not executed.
