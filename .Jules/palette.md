# Palette's UX Journal

## 2026-03-31 - Password masking and autocomplete in Home Assistant config flow forms
**Learning:** Using raw `str` type (`vol.Required(CONF_PASSWORD): str`) in Home Assistant config flow schemas renders an unmasked `<input type="text">` HTML element without password management metadata. Using `TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password"))` generates an accessible `<input type="password">` that prevents visual leakage and integrates with password managers and screen readers.
**Action:** When creating or reviewing config or options flows in Home Assistant integrations, always configure credential and sensitive inputs using `TextSelector` with `TextSelectorType.PASSWORD` and appropriate autocomplete attributes.

## 2026-04-12 - Email input semantics and autocomplete in Home Assistant credential forms
**Learning:** In Home Assistant config and options flows, configuring username fields that accept email addresses with a raw `str` type causes frontend clients to render a generic `<input type="text">` without virtual keyboard hints or password manager association. Using `TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username"))` renders `<input type="email">` with proper inputMode, mobile keyboard layouts containing `@` and top-level domain shortcuts, and pairs with `autocomplete="current-password"` for seamless browser and password manager autofill.
**Action:** When defining config or options flows with username or e-mail fields in Home Assistant, always use `TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username"))` to optimize mobile UX, accessibility, and password manager interoperability.
