"""Guard the CI workflows against download, lint, and release regressions."""

import importlib.util
import json
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read_workflow(name: str) -> dict[object, object]:
    """Parse a repository workflow using the installed YAML loader."""
    result = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(result, dict)
    return result


def _named_step(workflow_name: str, job_name: str, name: str) -> dict[str, object]:
    """Return exactly one matching step from the selected GitHub Actions job."""
    workflow = _read_workflow(workflow_name)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    matches = [step for step in steps if step.get("name") == name]
    assert len(matches) == 1
    return matches[0]


def _load_baseline_checker() -> ModuleType:
    """Import the actual Flake8 baseline checker without executing its entry point."""
    script = ROOT / ".github" / "helpers" / "check_flake8_baseline.py"
    specification = importlib.util.spec_from_file_location(
        "check_flake8_baseline", script
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_link_check_retries_and_verifies_a_pinned_binary() -> None:
    """Prevent transient download errors and unverified release assets."""
    install_step = _named_step(
        "link-check.yml", "link-check", "Install verified Lychee release"
    )
    environment = install_step["env"]
    assert isinstance(environment, dict)
    assert environment["LYCHEE_SHA256"] == (
        "1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a"
    )
    script = install_step["run"]
    assert isinstance(script, str)
    assert "curl " in script
    assert "--retry 3 --retry-all-errors" in script
    assert "--connect-timeout 10 --max-time 35" in script
    assert "api.github.com/repos/lycheeverse/lychee/releases/assets/409959156" in script
    assert '--header "Authorization: Bearer $GITHUB_TOKEN"' in script
    assert "sha256sum --check" in script
    assert 'echo "$RUNNER_TEMP/lychee-bin" >> "$GITHUB_PATH"' in script
    assert install_step.get("continue-on-error", False) is False
    lychee = _named_step("link-check.yml", "link-check", "Run Lychee")
    assert lychee.get("continue-on-error", False) is False
    assert "lychee --verbose" in str(lychee["run"])
    assert "--max-retries 5 --retry-wait-time 2" in str(lychee["run"])
    assert "--exclude-file" not in str(lychee["run"])


def test_releases_link_is_verified_through_its_actual_github_api() -> None:
    """Exclude only unreliable HTML and still block when releases are missing."""
    patterns = [
        line.strip()
        for line in (ROOT / ".lycheeignore").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("^https://github")
    ]
    assert len(patterns) == 1
    assert re.search(
        patterns[0],
        "https://github.com/HiDiHo01/home-assistant-frank_energie/releases/",
    )
    assert not re.search(patterns[0], "https://github.com/another/repo/releases/")
    assert not re.search(
        patterns[0],
        "https://github.com/HiDiHo01/home-assistant-frank_energie/releases/tag/v2026.9.20",
    )
    api_step = _named_step(
        "link-check.yml", "link-check", "Verify GitHub Releases via API"
    )
    assert api_step["env"] == {"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}
    script = str(api_step["run"])
    assert "gh api" in script
    assert "releases?per_page=1" in script
    assert ".[0].html_url" in script
    assert "exit 1" in script
    assert api_step.get("continue-on-error", False) is False


def test_ruff_checks_main_pushes_and_python_pull_requests() -> None:
    """Keep Ruff active for every main push and relevant changed Python files."""
    workflow = _read_workflow("ruff.yml")
    events = workflow.get("on", workflow.get(True))
    assert isinstance(events, dict)
    assert events["push"]["branches"] == ["main"]
    paths = events["pull_request"]["paths"]
    assert "custom_components/**/*.py" in paths
    assert "tests/**/*.py" in paths
    assert "pyproject.toml" in paths


def test_release_uses_supported_python_and_safe_shell_parameters() -> None:
    """Never interpolate untrusted tag names into a shell command."""
    python_setup = _named_step("release.yml", "release_zip_file", "Set up Python")
    assert python_setup["with"]["python-version"] == "3.14.10"
    version_step = _named_step("release.yml", "release_zip_file", "Set version number")
    asset_step = _named_step("release.yml", "release_zip_file", "Upload Release Asset")
    for step in (version_step, asset_step):
        environment = step["env"]
        assert isinstance(environment, dict)
        assert environment["RELEASE_TAG"] == "${{ github.event.release.tag_name }}"
        script = str(step["run"])
        assert '"$RELEASE_TAG"' in script
        assert "${{" not in script
    zip_step = _named_step("release.yml", "release_zip_file", "Combine ZIP")
    assert '"$RUNNER_TEMP/frank_energie.zip"' in str(zip_step["run"])
    assert "--clobber" in str(asset_step["run"])


def test_quality_workflow_performs_json_validation_without_tier_claims() -> None:
    """Make basic validation real while avoiding a false Platinum assertion."""
    json_step = _named_step(
        "quality_scale.yml", "quality-scale", "Validate manifest and localization JSON"
    )
    script = json_step["run"]
    assert isinstance(script, str)
    assert 'manifest.get("domain")' in script
    assert 'manifest.get("config_flow")' in script
    assert 'json.loads(path.read_text(encoding="utf-8"))' in script
    summary = _named_step("quality_scale.yml", "quality-scale", "Summary")
    assert "not Platinum certification" in str(summary["run"])


def test_ci_blocks_new_full_flake8_violations() -> None:
    """Require stable Python while keeping experimental wheels diagnostic."""
    workflow = _read_workflow("ci.yaml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    job = jobs["run"]
    assert isinstance(job, dict)
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["strategy"]["matrix"]["include"] == [
        {"python-version": "3.14.10", "experimental": False},
        {"python-version": "3.15.2", "experimental": True},
    ]
    assert job["continue-on-error"] == "${{ matrix.experimental }}"
    assert job["strategy"]["fail-fast"] is False
    environment_step = _named_step(
        "ci.yaml", "run", "Verify Home Assistant test environment"
    )
    assert environment_step["if"] == "${{ !matrix.experimental }}"
    assert "(3, 14, 10)" in str(environment_step["run"])
    wheel_step = _named_step(
        "ci.yaml", "run", "Diagnose experimental Python wheel availability"
    )
    assert wheel_step["if"] == "${{ matrix.experimental }}"
    assert "parse_wheel_filename" in str(wheel_step["run"])
    assert "sys_tags" in str(wheel_step["run"])
    install_step = _named_step("ci.yaml", "run", "Install dependencies with Poetry")
    assert install_step["if"] == "${{ !matrix.experimental }}"
    assert install_step["timeout-minutes"] == 10
    assert "poetry sync --with dev --no-root" in str(install_step["run"])
    client_step = _named_step(
        "ci.yaml", "run", "Install Python 3.15 upstream client for offline smoke tests"
    )
    assert client_step["if"] == "${{ matrix.experimental }}"
    assert "--only-binary=:all:" in str(client_step["run"])
    assert "manifest.json" in str(client_step["run"])
    smoke_step = _named_step(
        "ci.yaml", "run", "Run Python 3.15 upstream client smoke tests"
    )
    assert smoke_step["if"] == "${{ matrix.experimental }}"
    assert "test_python315_smoke.py" in str(smoke_step["run"])
    assert _named_step("ci.yaml", "run", "Test with pytest")["if"] == (
        "${{ !cancelled() && !matrix.experimental && steps.environment.outcome == 'success' }}"
    )
    summary_step = _named_step(
        "ci.yaml", "run", "Summarize experimental Python compatibility"
    )
    assert summary_step["if"] == "${{ always() && matrix.experimental }}"
    assert "CLIENT_INSTALL_OUTCOME" in summary_step["env"]
    assert "SMOKE_OUTCOME" in summary_step["env"]
    assert "Home Assistant dependencies and pytest: not run" in str(summary_step["run"])

    full_check = _named_step("ci.yaml", "run", "Enforce no new Flake8 violations")
    assert full_check["run"] == (
        "poetry run python .github/helpers/check_flake8_baseline.py"
    )
    assert "|| true" not in full_check["run"]
    assert "--select=E9,F63,F7,F82" in str(
        _named_step("ci.yaml", "run", "Lint with flake8")["run"]
    )


def test_homeassistant_test_dependencies_match_the_official_plugin() -> None:
    """Ensure Core and the official test plugin share the same locked version."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dev_dependencies = project["tool"]["poetry"]["group"]["dev"]["dependencies"]
    assert dev_dependencies["homeassistant"] == "==2026.11.0"
    assert dev_dependencies["pytest-homeassistant"] == "==2026.11.0"
    assert "pytest-homeassistant-custom-component" not in dev_dependencies
    assert (
        "pytest-homeassistant"
        in project["tool"]["pytest"]["ini_options"]["required_plugins"]
    )

    lock_data = tomllib.loads((ROOT / "poetry.lock").read_text(encoding="utf-8"))
    locked_versions = {
        package["name"]: package["version"] for package in lock_data["package"]
    }
    assert locked_versions["homeassistant"] == "2026.11.0"
    assert locked_versions["pytest-homeassistant"] == "2026.11.0"
    assert "pytest-homeassistant-custom-component" not in locked_versions


def test_flake8_parser_recognizes_diagnostics() -> None:
    """Parse structured Flake8 output rather than inspecting status alone."""
    module = _load_baseline_checker()
    report = (
        "./custom_components/frank_energie/sensor.py:17:3: "
        "C901 'async_setup_entry' is too complex (49)\n"
    )
    findings = module._parse_diagnostics(report)
    assert findings == Counter(
        {
            (
                "custom_components/frank_energie/sensor.py",
                "C901",
                "'async_setup_entry' is too complex (49)",
            ): 1
        }
    )


@pytest.mark.parametrize(
    ("extra_report", "expected_status"),
    (
        ("", 0),
        ("./tests/new_test.py:3:1: E501 line too long (160 > 120 characters)\n", 1),
    ),
)
def test_flake8_baseline_rejects_new_findings(
    extra_report: str, expected_status: int
) -> None:
    """Preserve known debt but block every additional lint finding."""
    module = _load_baseline_checker()
    baseline_data = json.loads(
        (ROOT / ".github" / "flake8-baseline.json").read_text(encoding="utf-8")
    )
    known = baseline_data["allowed"][0]
    report = (
        f"./{known['path']}:1:1: {known['code']} {known['message']}\n{extra_report}"
    )
    result = subprocess.CompletedProcess(
        args=["flake8"], returncode=1, stdout=report, stderr=""
    )
    with patch.object(module.subprocess, "run", return_value=result):
        assert module.main() == expected_status


def test_flake8_baseline_fails_when_scanner_errors() -> None:
    """An unrecognized tool failure must never pass the lint gate."""
    module = _load_baseline_checker()
    result = subprocess.CompletedProcess(
        args=["flake8"], returncode=2, stdout="", stderr="Unknown scanner error"
    )
    with patch.object(module.subprocess, "run", return_value=result):
        assert module.main() == 1
