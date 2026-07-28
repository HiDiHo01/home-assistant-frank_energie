# AI Policy

## Purpose

This repository contains the Home Assistant integration for Frank Energie. AI may assist development, but all changes must comply with Home Assistant development standards, preserve user trust, and maintain compatibility with supported Home Assistant releases.

## Guiding principles

- Human review is mandatory for every AI-assisted contribution.
- AI accelerates development but does not replace engineering judgement.
- Code quality, correctness, security, privacy, and maintainability take priority over development speed.

## Acceptable AI use

AI may be used to:

- write and refactor Python code
- generate documentation, translations, release notes, and tests
- explain existing code and propose architectural improvements
- improve logging, validation, typing, async patterns, and error handling
- assist with GitHub issues, pull requests, and code reviews

## Unacceptable AI use

Do not use AI to:

- fabricate Home Assistant APIs or Frank Energie API behavior
- introduce undocumented functionality or hidden logic
- expose API tokens, credentials, user data, or personal information
- bypass CI, quality checks, reviews, or test requirements
- merge code without understanding its behavior

## Code quality requirements

AI-generated code should:

- follow Home Assistant architecture and entity guidelines
- use modern Python with complete type hints
- use asynchronous APIs where appropriate
- use timezone-aware datetimes
- include meaningful docstrings for public modules, classes, and functions
- use lazy `%` logging formatting
- provide descriptive validation and error messages
- avoid dead code, duplicate logic, and unnecessary dependencies
- maintain backwards compatibility unless a breaking change is intentional and documented

## Home Assistant specific requirements

AI-assisted changes should:

- comply with the Home Assistant Integration Quality Scale
- respect DataUpdateCoordinator patterns where applicable
- avoid blocking the event loop
- preserve translation quality and localization support
- include or update tests for new functionality and bug fixes
- avoid introducing unnecessary polling or API requests

## Review checklist

Before merging, verify:

- functionality is correct
- tests pass
- linting passes
- documentation is updated where required
- AI-generated code has been manually reviewed and understood
- changes do not negatively impact existing users

## Privacy and security

Never include production credentials, authentication tokens, personally identifiable information, or customer data in AI prompts, issues, commits, or pull requests. Use sanitized examples for debugging and testing.

## Transparency

Contributors are encouraged to mention when AI substantially assisted with implementation, refactoring, documentation, or testing. Responsibility for the final code always remains with the contributor.
