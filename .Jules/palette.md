# Palette's UX Journal

## 2026-03-31 - Password masking and autocomplete in Home Assistant config flow forms
**Learning:** Using raw `str` type (`vol.Required(CONF_PASSWORD): str`) in Home Assistant config flow schemas renders an unmasked `<input type="text">` HTML element without password management metadata. Using `TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))` generates an accessible `<input type="password">` that prevents visual leakage and integrates with password managers and screen readers.
**Action:** When creating or reviewing config or options flows in Home Assistant integrations, always configure credential and sensitive inputs using `TextSelector` with `TextSelectorType.PASSWORD` and appropriate autocomplete attributes.
