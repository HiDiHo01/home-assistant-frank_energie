---
name: frank-energie-quality-scale
description: Assess and improve Frank Energie Home Assistant Integration Quality Scale compliance. Use for Platinum audits, quality_scale or manifest claims, HACS and hassfest verification, coverage gaps, strict typing, config flows, diagnostics, and quality-gate findings.
---

# Evidence-based Home Assistant quality verification

## Authority

Read `AGENTS.md`, `AI_POLICY.md`, `home-assistant.md`, the integration manifest, test configuration, and the current official [Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/) rules before assessing compliance. Use the actual custom-integration repository and its CI. Do not assume the Home Assistant Core layout or its test commands apply here.

## Decide the audit scope

- **Requested tier audit:** Enumerate every applicable rule through the target tier, including prerequisites and allowable exemptions.
- **Rule-specific request:** Verify only the named rule and necessary dependencies.
- **PR review:** Map the diff to affected rules and check for regressions; a manifest-only declaration is not proof of implementation.

For each rule record: requirement, applicability, declared status (if this repository maintains a rule status file), concrete evidence, execution/inspection method, and result (`pass`, `fail`, `exempt`, or `unverified`). Examine any exemption justification; never invent a `quality_scale.yaml` file that the repository does not maintain.

## Verify with evidence

1. Read the official rule text and distinguish objectively measurable thresholds from review-based requirements. Do not copy thresholds from a Core-only skill without checking the current rule.
2. Inspect `custom_components/frank_energie/`, `tests/`, `docs/`, `SPECS/`, and `.github/workflows/` for the specific evidence.
3. Verify config-entry setup, cleanup, duplicate handling, reauthentication/reconfiguration and migrations; entity availability, stable unique IDs, translations, diagnostics redaction, documented behavior and async resource handling.
4. For test-coverage rules, execute the project's configured coverage tooling. Record exact totals and module-level results if the rule requires them. A passing `pytest` run or Sonar Quality Gate does **not** by itself prove coverage compliance.
5. Check HACS/hassfest results for the current commit and review what each check actually covers. Passing validation is necessary where applicable but is not sufficient for every Platinum rule.
6. For typing claims, use the project's configured static type checker and actual target environment; do not infer strict typing from annotations alone.
7. Assess documentation and translation correctness across `strings.json` and every locale in `translations/` when relevant.

## Change workflow

If authorized to implement improvements, address independently confirmed gaps with focused changes, regression tests, and applicable checks. Do not change version support, entity IDs, or add irrelevant features merely to satisfy a checklist. Respect review-only authorization.

## Output

Provide a concise rule-by-rule evidence matrix, measured results and exact commands (if run), reasons for valid exemptions, missing evidence, and a prioritized remediation list. Say **not verified** instead of claiming Platinum when any applicable rule remains unverified.

## Adapted learning sources

This workflow draws on the verification method in [Home Assistant Core's quality-scale skill](https://github.com/HiDiHo01/core/blob/dev/.claude/skills/ha-quality-scale-verify/SKILL.md), adapted for a HACS custom integration. The official current rules and this repository's policy take precedence over that fork's scripts or layouts.
