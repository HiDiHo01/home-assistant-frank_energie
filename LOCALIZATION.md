# Localization Guide

This document defines how to maintain translations for the Frank Energie Home Assistant integration.

## Supported locales and files

The integration currently maintains these locales:

- English: `custom_components/frank_energie/translations/en.json`
- French: `custom_components/frank_energie/translations/fr.json`
- Dutch: `custom_components/frank_energie/translations/nl.json`

The integration's `custom_components/frank_energie/strings.json` is the canonical translation-key structure. Keep every locale structurally aligned with it. The repository's `tests/test_translations.py` explicitly requires `strings.json`, `en.json`, `fr.json`, and `nl.json`.

Use `fr.json` for French-language users; keep labels idiomatic in French while preserving technical identifiers, API enum values, placeholders, and Home Assistant shared translation references.

## What must be localized

Translation content includes, where present:

- Config-flow and reauthentication/reconfiguration forms, including titles, descriptions, fields, abort reasons, and errors.
- Options-flow forms and settings.
- Device names.
- Entity names for supported platforms, including sensors, binary sensors, buttons, numbers, and selects.
- Entity state labels and translated state attributes.
- Any other user-facing text exposed through Home Assistant's translation system.

Prefer Home Assistant's native translation mechanisms. Do not hard-code user-facing entity names in Python when an entity description's `translation_key` should provide the localized name.

## Workflow for changing translations

1. **Inspect the implementation.** Identify the relevant config-flow step, entity description, state attribute, option, or other user-facing behavior before editing translation files.
2. **Update the canonical structure.** Add or change translation keys in `strings.json`.
3. **Update every supported locale.** Apply the same key and nesting changes to both `translations/en.json` and `translations/nl.json`. Translate values naturally; do not copy English text into Dutch merely to satisfy key parity.
4. **Keep the schema identical.** All nested keys and their paths must match `strings.json` exactly in every locale. Do not add locale-specific keys or leave unused/orphaned keys.
5. **Check the code-to-translation mapping.** New entity descriptions must use the appropriate translation key. If code emits state attributes, ensure each user-visible attribute has a corresponding translation.
6. **Validate the JSON and tests.** Run the focused translation tests and then the full test suite when practical (see below).

If a key is renamed or removed, update the implementation and all locale files in the same change. Avoid changing translation keys solely to improve wording; change the translated value instead.

## Translation conventions

- Keep translation **keys** stable, lowercase, and machine-oriented. Keys are identifiers, not display text.
- Translate **values**, not keys. Preserve the exact key spelling and nesting across all locales.
- Use clear, concise language appropriate to Home Assistant forms and entity names.
- Use consistent Frank Energie terminology across the integration. Use the domain's established Dutch energy terminology in `nl.json` and clear, idiomatic English in `en.json`.
- Preserve placeholders, interpolation tokens, and Home Assistant shared-translation references exactly. Do not translate or alter tokens such as `[%key:common::config_flow::abort::already_configured%]`.
- Preserve technical identifiers and machine values, including API field names, enum values, entity IDs, and configuration keys. Translate only their displayed labels.
- State translations must match the actual values produced by the entity. Keep enum translation keys consistent with the values and options defined in code; do not infer or invent states.
- Keep units, currencies, dates, durations, and energy terminology accurate. Do not put units into names if Home Assistant already renders them separately.
- Avoid duplicate or contradictory translations for the same concept. Prefer consistent phrasing for repeated concepts such as electricity prices, market prices, taxes, markups, battery modes, and charging settings.
- Do not add a translation for text that is not exposed to users through Home Assistant's translation system.

## Automated validation

Run these commands from the repository root:

```bash
pytest tests/test_translations.py
pytest
```

The translation tests check, among other things:

- The required translation files exist.
- Every locale has exactly the same nested key paths as `strings.json`.
- Entity state-attribute keys emitted by the resolution select are represented in `strings.json`.
- Translation keys are used by the code and are not orphaned.
- Translation keys follow the repository's lowercase-key convention.
- Entity descriptions use translation keys appropriately and state translations correspond to enum-style entities.

Do not remove or weaken these checks just to make a translation change pass. Fix the translation structure or implementation instead.

## Pull request checklist

Before submitting a localization change, confirm that:

- [ ] `strings.json`, `en.json`, and `nl.json` contain matching key paths.
- [ ] English and Dutch values are complete, natural, and consistent.
- [ ] Shared Home Assistant translation references and placeholders are unchanged.
- [ ] New or changed user-facing code is connected to the correct translation keys.
- [ ] State labels and attribute translations match the values actually emitted by the integration.
- [ ] `pytest tests/test_translations.py` passes.
- [ ] The full test suite and relevant lint/validation checks have been run, or any checks not run are stated clearly.
