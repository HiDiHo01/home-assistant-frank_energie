# Smart Battery device and entity naming specification

**Status:** Normative repository requirement
**Scope:** All Frank Energie Smart Battery hardware and aggregate battery services, across all current and future entity platforms.

## 1. Authoritative identity

- Use the complete Frank Energie `SmartBattery.id` (Battery ID) as the authoritative provider identity and for API requests. A currently observed length of 25 characters is not an API contract: accept non-empty valid provider IDs without a fixed-length assumption. Do not truncate, case-fold or otherwise normalize the ID used for identity.
- `external_reference` (External Reference) is optional provider metadata only. Never use it as a replacement for the Battery ID, a registry key, a default naming suffix or a serial number without a separately verified provider contract. Do not disclose real identifiers in logs, examples or public issues.
- Scope device and entity identities to the owning config entry and selected delivery site. Use the existing typed inventory identity helpers; preserve legacy identifiers only where ownership is unambiguous. Do not merge hardware across entries.
- Preserve existing sensor unique IDs from `battery_sensor_unique_id`: entry ID, full SHA-256 digest of the original Battery ID and stable description key. For new battery entities use the same entry-scoped identity principle. Migrate other legacy identities only in place with verified ownership and uniqueness; never change existing entity IDs or delete history as a naming shortcut.

## 2. Physical battery device name

The default for every individual battery, including a single battery, MUST be:

```text
{brand} {short_id}
```

- Compute `short_id` from the first eight lowercase hexadecimal characters of `sha256(battery_id.encode("utf-8")).hexdigest()`; display those characters uppercase in the device name.
- Use the verified battery brand from the entry-owned inventory. Use the translated generic “Battery” label when the brand is absent; do not invent a manufacturer. A partial refresh must not overwrite previously verified naming metadata.
- Check shortened digest collisions against the entry's known battery identities, including existing registered devices. On collision extend the new identifier by two hexadecimal characters at a time until distinct; never rename an established device solely because another battery is added. For initially discovered colliding IDs use deterministic ordering by complete digest, independent of API response order. Persist or recover assigned suffixes from verified registry metadata so reloads, partial inventories and removal/rediscovery do not change assignments. If identities cannot be disambiguated safely, do not register a conflicting device.
- All battery platforms MUST use one shared device naming/metadata helper, with the same authoritative entry-owned battery inventory and registry identity. No platform-specific name construction, inventory index, full provider ID in the display name, or brand-only name is allowed.
- Keep manufacturer as verified metadata and use a genuine model or serial number only when verified. Preserve the user's device name override and existing entity IDs. The aggregate Batteries service remains separate from individual hardware; do not add its device-name prefix to entity names.

Examples (fictional digest fragments):

| Device name | Entity name | Possible generated entity ID |
| --- | --- | --- |
| Sessy A1B2C3D4 | State of charge | `sensor.sessy_a1b2c3d4_state_of_charge` |
| Sessy E5F6A7B8 | State of charge | `sensor.sessy_e5f6a7b8_state_of_charge` |
| Sessy A1B2C3D4 | Mode | `select.sessy_a1b2c3d4_mode` |
| Frank Energie - Batteries | Current capacity | `sensor.frank_energie_batteries_current_capacity` |

The examples are illustrative, not guaranteed entity IDs.

## 3. Home Assistant entity names

- Set `has_entity_name = True`. Use short, localized function/property names, standard device-class naming where applicable and existing stable translation keys. Avoid redundant battery context in a name attached to a battery device (e.g. Capacity, Status, Mode, Strategy, ID).
- Keep canonical `strings.json` and all supported translations synchronized. Do not change translation keys or state/attribute semantics for cosmetic reasons.
- Let Home Assistant generate entity IDs according to the installation's configured format, locale, device/area context and collision handling. Do not impose a prefix, set a hard-coded `entity_id` or use `suggested_object_id` to bypass the user's format. Never depend on a specific generated entity ID internally.
- Keep the aggregate service name “Frank Energie - Batteries” localized using the established service translation. Use plural service context and property names appropriate to the aggregate. Do not aggregate identity across delivery sites.

References: [Home Assistant entity naming](https://developers.home-assistant.io/docs/core/entity/#entity-naming), [entity registry](https://developers.home-assistant.io/docs/entity_registry_index/), [device registry](https://developers.home-assistant.io/docs/device_registry_index/).

## 4. Mandatory implementation and regression criteria

Every change affecting battery entities, naming, inventory, translations or registry identity MUST be reviewed against this specification. A naming implementation or change must add or retain meaningful automated tests in the normal pytest CI suite:

- One battery and two identical-brand batteries; distinguishable default names and the same naming/identity across sensors, daily results, session/period results, selects, numbers and any other applicable platform.
- Exact UTF-8 SHA-256 suffix derivation, non-empty IDs of different lengths, optional external reference (missing, duplicate or changed), absent brand and partial inventory.
- Synthetic short-hash collisions, reordered inventory, adding/removing batteries, reload/restart and rediscovery; existing names/suffix assignments remain stable and no duplicate identities are created.
- Multiple entries and delivery sites, ambiguous legacy registry ownership, disabled entries, preserved unique IDs or safe in-place migration, existing entity IDs and custom names, and no accidental cross-entry association.
- Translated names without redundant context, correct aggregate service association and no forced entity IDs. Verify actual device/entity registry behavior with the supported Home Assistant test framework, not just string assertions.

Reviewers MUST reject newly introduced deviations and require explicit, tested compatibility handling when changing existing identity or naming behavior. Existing deviations are migration work, not evidence that the convention is already implemented. Do not mark a naming implementation complete until the relevant regression tests and CI checks have passed on the candidate head.

## 5. Initial adoption

At adoption, the existing code constructs device names independently in `sensor.py` (regular and session sensors), `battery_daily.py`, `select.py` and `number.py`. Some expose the complete provider ID or omit a distinguishing suffix. Legacy session identities also differ from the entry-scoped sensor identity. The initial specification establishes the required target, not a claim of existing compliance. A focused implementation must consolidate these paths and validate registry compatibility before claiming runtime enforcement.
