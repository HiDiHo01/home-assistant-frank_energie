"""Verify active workflow configuration matches the repository and its events."""

import fnmatch
import json
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"


def _read_configuration(relative_path: str) -> dict[object, object]:
    """Read a YAML configuration as a validated mapping."""
    contents = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
    configuration = yaml.safe_load(contents)
    assert isinstance(configuration, dict)
    return configuration


def _changed_file_patterns(label_name: str) -> list[str]:
    """Extract the file patterns used by a label's changed-file matcher."""
    rules = _read_configuration(".github/labeler.yml")[label_name]
    assert isinstance(rules, list)
    assert rules
    patterns: list[str] = []
    for rule in rules:
        assert isinstance(rule, dict)
        changed_files = rule["changed-files"]
        assert isinstance(changed_files, list)
        for matcher in changed_files:
            assert isinstance(matcher, dict)
            file_globs = matcher["any-glob-to-any-file"]
            assert isinstance(file_globs, list)
            assert all(isinstance(pattern, str) for pattern in file_globs)
            patterns.extend(file_globs)
    return patterns


def test_labeler_rules_use_recognized_matchers() -> None:
    """Prevent a label rule from being silently ignored by actions/labeler."""
    configuration = _read_configuration(".github/labeler.yml")
    allowed_keys = {"changed-files", "head-branch", "base-branch", "any", "all"}
    for label_name, rules in configuration.items():
        if label_name in {"changed-files-labels-limit", "max-files-changed"}:
            continue
        assert isinstance(rules, list), label_name
        assert rules, label_name
        for rule in rules:
            assert isinstance(rule, dict), label_name
            assert rule, label_name
            assert rule.keys() <= allowed_keys, (label_name, rule)


@pytest.mark.parametrize(
    ("label_name", "changed_path", "expected"),
    (
        ("translation", "custom_components/frank_energie/strings.json", True),
        ("translation", "custom_components/frank_energie/translations/nl.json", True),
        ("tests", "tests/test_config_flow.py", True),
        ("tests", "custom_components/frank_energie/sensor.py", False),
        (
            "blueprint",
            "config/blueprints/automation/Frank_Energie/color_from_sensor.yaml",
            True,
        ),
        ("blueprint", "docs/examples.md", False),
        ("dependencies", "pyproject.toml", True),
        ("dependencies", "poetry.lock", True),
    ),
)
def test_label_patterns_match_real_repository_paths(
    label_name: str, changed_path: str, expected: bool
) -> None:
    """Cover root tests, blueprint locations, translations, and Python dependencies."""
    patterns = _changed_file_patterns(label_name)
    assert (
        any(fnmatch.fnmatchcase(changed_path, pattern) for pattern in patterns)
        is expected
    )


def test_dependabot_references_only_defined_registries_and_existing_projects() -> None:
    """Reject undefined registry names and npm updates without a Node project."""
    configuration = _read_configuration(".github/dependabot.yml")
    registry_definitions = configuration.get("registries", {})
    assert isinstance(registry_definitions, dict)
    updates = configuration["updates"]
    assert isinstance(updates, list)
    for update in updates:
        assert isinstance(update, dict)
        registry_names = update.get("registries", [])
        if registry_names != "*":
            assert isinstance(registry_names, list)
            assert set(registry_names) <= registry_definitions.keys()
        if update["package-ecosystem"] == "npm":
            directory = str(update["directory"]).lstrip("/")
            assert (REPOSITORY_ROOT / directory / "package.json").is_file()


def test_pages_deployment_requires_a_complete_build_project() -> None:
    """Prevent deployment jobs from referencing an absent Docusaurus project."""
    for workflow_path in WORKFLOWS.iterdir():
        if workflow_path.suffix not in {".yml", ".yaml"}:
            continue
        configuration = _read_configuration(
            str(workflow_path.relative_to(REPOSITORY_ROOT))
        )
        jobs = configuration["jobs"]
        assert isinstance(jobs, dict)
        for job in jobs.values():
            assert isinstance(job, dict)
            steps = job.get("steps", [])
            assert isinstance(steps, list)
            if any(
                str(step.get("uses", "")).startswith("actions/deploy-pages@")
                for step in steps
            ):
                for required_path in (
                    "package.json",
                    "package-lock.json",
                    "sidebars.js",
                    "src/css/custom.css",
                ):
                    assert (REPOSITORY_ROOT / required_path).is_file(), required_path


@pytest.mark.parametrize(
    ("actor_type", "association", "comment_body", "expected"),
    (
        ("User", "OWNER", "@claude review this change", True),
        ("User", "MEMBER", "@claude review this change", True),
        ("User", "COLLABORATOR", "@claude review this change", True),
        ("User", "CONTRIBUTOR", "@claude review this change", False),
        ("User", "FIRST_TIME_CONTRIBUTOR", "@claude review this change", False),
        ("User", "NONE", "@claude review this change", False),
        ("Bot", "OWNER", "@claude review this change", False),
        ("Bot", "COLLABORATOR", "@claude review this change", False),
        ("User", "OWNER", "Thanks for the update", False),
        ("User", "MEMBER", "No command in this comment", False),
    ),
)
def test_claude_job_requires_an_authorized_command(
    actor_type: str, association: str, comment_body: str, expected: bool
) -> None:
    """Exercise the job guard before any runner step can be scheduled."""
    configuration = _read_configuration(".github/workflows/claude.yml")
    jobs = configuration["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["run-claude"]
    assert isinstance(job, dict)
    condition = job["if"]
    assert isinstance(condition, str)
    event = {
        "comment": {
            "user": {"type": actor_type},
            "author_association": association,
            "body": comment_body,
        }
    }
    # The guard uses JS-compatible boolean comparisons and string contains only.
    program = f"""
const github = {{ event: {json.dumps(event)} }};
function contains(value, fragment) {{
  return String(value).toLowerCase().includes(String(fragment).toLowerCase());
}}
process.stdout.write(JSON.stringify(Boolean({condition})));
"""
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=program,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is expected
