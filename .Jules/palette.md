# Palette's Journal

## 2026-04-18 - Masked Password Input with Native Toggle in Config Flows
**Learning:** Home Assistant configuration and options flows defaulting to `vol.Required(CONF_PASSWORD): str` render standard plain text inputs in the frontend unless explicitly configured with `TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))`. Using `TextSelector` with `TextSelectorType.PASSWORD` provides immediate visual masking to protect privacy while preserving accessibility via the frontend's built-in visibility toggle and standard password field autocomplete attributes.
**Action:** When defining credential schemas across Home Assistant flows (login, options, reconfigure, site error steps), always specify `TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))` rather than bare `str`.
