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
3. In the current **python-frank-energie main branch**, the separate
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
| `fr.json` | Chauffage intelligent |

The translated **name** must be the feature name: do not append *activated*,
*active*, or *geactiveerd*. The Boolean state and the `RUNNING` device class
provide the state presentation. Keep `translation_key="smart_hvac"`, the
entity's existing unique ID and the User service device association unchanged.

## Library dependency and release gate

This workflow requires `Me.user_smart_hvac` from
[python-frank-energie main](https://github.com/HiDiHo01/python-frank-energie/blob/main/python_frank_energie/models.py).
The currently pinned published dependency `python-frank-energie==2026.9.20`
does **not** supply that field. With this dependency the sensor cannot obtain
the new activation value, even when an account has activated the service.

Keep the implementation PR **draft** until the library change is published
and the integration's `manifest.json`, `pyproject.toml` and `poetry.lock`
are updated consistently. Do not pin a production requirement to an
unreleased Git branch. The passing Home Assistant suite with the old
dependency does **not** demonstrate compatibility with the new released
model; verify the actual package before merge.

This integration does not yet expose Smart HVAC inventory, operational
status, steering status, or actual heat-pump running state as a separate
entity. See [issue #414](https://github.com/HiDiHo01/home-assistant-frank_energie/issues/414)
for the original semantics issue.

## Developer verification

For changes to this feature, test `true`, `false`, null, missing and
malformed `isActivated`; `isAvailableOnSite` independently; settings
refresh failures; optional API errors and auth-required behavior; redaction;
and the real Home Assistant entity state machine. Check the stable entity ID,
translated names and displayed device class.

Using the repository's Python 3.14 Poetry environment:

```sh
poetry run python -m pytest tests/test_binary_sensor.py tests/test_coordinator.py tests/test_translations.py -q
poetry run python -m pytest tests -ra --junitxml=pytest-results.xml
ruff check custom_components tests
ruff format --check --diff custom_components tests
poetry run python .github/helpers/check_flake8_baseline.py
```

See [the developer guide](developer.md), [the integration development
skill](../.agents/skills/frank-energie-development/SKILL.md), and
the [dependency-update skill](../.agents/skills/frank-energie-dependency-update/SKILL.md).
Always report checks not executed.
