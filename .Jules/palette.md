# Palette's Design Journal

Critical UX/accessibility learnings and patterns for Frank Energie Home Assistant integration.

## 2026-03-31 - Semantic username and password selectors

**Learning:** Home Assistant's frontend already masks string fields whose names include `password`; plain string fields are therefore not automatically exposed as visible passwords. Explicit `TextSelector` configuration makes the intended input type and autocomplete hint clear. For an account username that is an email address, `TextSelectorType.EMAIL` with `autocomplete="username"` offers appropriate keyboard and password-manager hints. The existing password selector uses `TextSelectorType.PASSWORD` with `autocomplete="current-password"`.

**Action:** Keep the Home Assistant-supported password selectors introduced by PR #422 and use an email selector for Frank Energie account usernames in login, retry, reauthentication, reconfiguration and options forms. Preserve the existing credential encryption, site-isolation checks and account identity during updates.
