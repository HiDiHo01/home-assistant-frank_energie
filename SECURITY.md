# Security Policy

## Supported Versions

Security fixes are generally applied to the latest released version of this integration.

Because this is a custom Home Assistant integration, support for older releases depends on the current Home Assistant and dependency compatibility requirements. Users should keep the integration and Home Assistant up to date before reporting a vulnerability.

| Version | Security Support |
| --- | --- |
| Latest release | :white_check_mark: |
| Older releases | :warning: Best effort |

## Reporting a Vulnerability

Please **do not report security vulnerabilities through public GitHub issues, discussions, or pull requests**.

Use GitHub's **Report a vulnerability** / private vulnerability reporting feature for this repository when it is available. This keeps sensitive security details private while the issue is investigated.

If private vulnerability reporting is not available, contact the repository maintainer privately through the GitHub account [@HiDiHo01](https://github.com/HiDiHo01). Do not include secrets or sensitive personal information in a public issue.

### What to include

Please provide enough information to reproduce and assess the issue, such as:

- A clear description of the vulnerability and its security impact.
- The affected integration version and Home Assistant version.
- The affected component, module, or feature.
- Reproduction steps or a minimal proof of concept, where appropriate.
- Any relevant error messages, logs, or configuration details with secrets and personal data removed.
- Whether the issue requires authentication, a specific configuration, or local network access.

### Sensitive information

Never include any of the following in a security report, log, issue, or pull request unless it has been properly redacted:

- Frank Energie account credentials.
- API tokens, access tokens, session cookies, or other authentication secrets.
- Home Assistant credentials or long-lived access tokens.
- Personal information or customer-specific data.
- Unredacted diagnostics or configuration containing secrets.

## Response Process

Security reports are reviewed by the repository maintainer. The investigation may include reproduction, impact assessment, identification of affected versions, and preparation of a fix or mitigation.

For confirmed vulnerabilities, the maintainer will determine an appropriate remediation and release process based on severity, exploitability, affected users, and the availability of a safe fix.

Please allow reasonable time for investigation before publicly disclosing a vulnerability or publishing exploit details.

## Security Best Practices for Users

Users can reduce risk by:

- Keeping Home Assistant, this integration, and its dependencies up to date.
- Installing the integration only from trusted sources.
- Using the minimum credentials and access required for the integration.
- Protecting Home Assistant accounts and long-lived access tokens.
- Reviewing and redacting diagnostics before sharing them publicly.
- Avoiding exposure of Home Assistant or integration endpoints directly to the internet unless appropriate security controls are in place.

## Scope

This policy covers security issues in the `home-assistant-frank_energie` integration itself, including its authentication handling, API communication, data processing, and Home Assistant integration code.

Vulnerabilities in Home Assistant, Frank Energie services, third-party dependencies, HACS, or other external systems should be reported to their respective maintainers. Please still mention relevant upstream dependencies when they are directly involved in an issue affecting this integration.
