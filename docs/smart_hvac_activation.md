# Smart HVAC activation sensor

The existing `smart_hvac` binary sensor is a **service activation** indicator.
It does not report whether a heat pump is heating, cooling, running, or currently
being steered. Do not use `BinarySensorDeviceClass.RUNNING` for this state.

## Data flow

1. The settings coordinator uses the config entry's selected site reference.
2. The existing `api.user(site_reference)` call continues to populate the
   user service and its unrelated settings, contracts, and smart features.
3. The `api.me(site_reference)` call reads the root-level
   `userSmartHvac(siteReference:)` field from **python-frank-energie main**.
4. `Me.user_smart_hvac` is transferred to `User.smartHvac`, leaving the
   existing `DATA_USER` shape and `smart_hvac` entity unique ID intact.

The Home Assistant sensor reads `User.smartHvac.isActivated` without Boolean
coercion. `True` means activated, `False` means not activated; absent, null,
or malformed values mean unavailable. The separate `is_available_on_site`
field is exposed as a diagnostic attribute and is **not** substituted for
activation. No user ID, address, EAN, or device ID is exposed as an attribute.

The settings coordinator continues to isolate an unsupported or failed
optional Smart HVAC request from unrelated user entities, while preserving
authentication failure behavior and avoiding raw upstream error logging.
`CoordinatorEntity.available` is respected on failed refreshes.

## Dependency and release requirement

The required `Me.user_smart_hvac` support exists in the
[python-frank-energie main branch](https://github.com/HiDiHo01/python-frank-energie/blob/main/python_frank_energie/models.py).
The currently pinned PyPI release `2026.9.20` does **not** contain this
model field. Until a new PyPI release is published and the integration
manifest, Poetry dependency, and lock file are updated together, the new
sensor cannot obtain activation state using a standard Home Assistant install.

The pull request must remain **draft** until that release/update is ready.
Do not merge a Git-branch requirement into the production manifest. Local
integration testing against main is possible using Home Assistant's documented
`--skip-pip-packages` development workflow.

This change intentionally does not introduce device inventory, device
runtime state, or additional polling coordinators. See
[issue #414](https://github.com/HiDiHo01/home-assistant-frank_energie/issues/414)
for the original semantic and availability defects.
