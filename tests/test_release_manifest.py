"""Verify that release manifest failures block packaging with a nonzero status."""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import TextIO
from unittest.mock import patch

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "helpers" / "update_manifest.py"
)
MANIFEST_RELATIVE_PATH = Path("custom_components") / "frank_energie" / "manifest.json"


def _load_manifest_updater() -> ModuleType:
    """Load the standalone helper without invoking its command-line entry point."""
    specification = importlib.util.spec_from_file_location(
        "release_manifest_updater", SCRIPT_PATH
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _write_manifest(directory: Path, contents: str) -> Path:
    """Create an isolated integration manifest for a release command."""
    manifest_path = directory / MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(contents, encoding="utf-8")
    return manifest_path


def _run_release_command(
    directory: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    """Execute the same standalone helper used by the release workflow."""
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("version_flag", "requested_version", "expected_version"),
    (
        ("--version", "v2026.11.2", "2026.11.2"),
        ("-V", "2026.11.2", "2026.11.2"),
        ("--version", "v2026.11.2b1", "2026.11.2b1"),
    ),
)
def test_release_manifest_updates_only_the_version(
    tmp_path: Path,
    version_flag: str,
    requested_version: str,
    expected_version: str,
) -> None:
    """Preserve manifest metadata while accepting supported version flags."""
    original_manifest = {
        "domain": "frank_energie",
        "name": "Frank Energie",
        "version": "2026.1.1",
    }
    manifest_path = _write_manifest(tmp_path, json.dumps(original_manifest))

    result = _run_release_command(tmp_path, [version_flag, requested_version])

    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == {
        **original_manifest,
        "version": expected_version,
    }


@pytest.mark.parametrize(
    ("manifest_contents", "expected_error"),
    (
        (None, "manifest.json"),
        ("{", "invalid JSON"),
        ("[]", "JSON object"),
        ("null", "JSON object"),
        ('{"value": NaN}', "invalid JSON constant"),
        ('{"value": Infinity}', "invalid JSON constant"),
        ('{"value": -Infinity}', "invalid JSON constant"),
        ('{"value": 1e999}', "Out of range float values"),
    ),
)
def test_release_manifest_data_errors_return_failure(
    tmp_path: Path, manifest_contents: str | None, expected_error: str
) -> None:
    """Reject missing or malformed manifests instead of reporting build success."""
    manifest_path = tmp_path / MANIFEST_RELATIVE_PATH
    manifest_path.parent.mkdir(parents=True)
    if manifest_contents is not None:
        manifest_path.write_text(manifest_contents, encoding="utf-8")

    result = _run_release_command(tmp_path, ["--version", "v2026.11.2"])

    assert result.returncode == 1
    assert not result.stdout
    assert expected_error in result.stderr
    if manifest_contents is not None:
        assert manifest_path.read_text(encoding="utf-8") == manifest_contents


@pytest.mark.parametrize("requested_version", ("", " ", "v"))
def test_empty_release_versions_leave_the_manifest_unchanged(
    tmp_path: Path, requested_version: str
) -> None:
    """Reject empty version input before overwriting a valid manifest."""
    original_contents = '{"version": "2026.1.1"}'
    manifest_path = _write_manifest(tmp_path, original_contents)

    result = _run_release_command(tmp_path, ["--version", requested_version])

    assert result.returncode == 1
    assert "release version must not be empty" in result.stderr
    assert manifest_path.read_text(encoding="utf-8") == original_contents


@pytest.mark.parametrize("version_flag", ("--version", "-V"))
def test_missing_version_argument_is_a_command_line_error(
    tmp_path: Path, version_flag: str
) -> None:
    """Fail argument parsing instead of silently using the default date."""
    result = _run_release_command(tmp_path, [version_flag])

    assert result.returncode == 2
    assert "expected one argument" in result.stderr


@pytest.mark.parametrize("failure_stage", ("write", "replace"))
def test_release_manifest_write_errors_return_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_stage: str,
) -> None:
    """Preserve the original manifest on partial writes or failed replacements."""
    original_contents = '{"version": "2026.1.1"}'
    manifest_path = _write_manifest(tmp_path, original_contents)
    module = _load_manifest_updater()
    monkeypatch.chdir(tmp_path)

    def fail_after_partial_write(
        _manifest: object, output: TextIO, **_options: object
    ) -> None:
        """Simulate the destination becoming unwritable after truncation."""
        output.write('{"version":')
        raise OSError("Disk is full")

    if failure_stage == "write":
        with patch.object(module.json, "dump", side_effect=fail_after_partial_write):
            result = module.main(["--version", "2026.11.2"])
    else:
        with patch.object(Path, "replace", side_effect=OSError("Disk is full")):
            result = module.main(["--version", "2026.11.2"])

    assert result == 1
    assert "Disk is full" in capsys.readouterr().err
    assert manifest_path.read_text(encoding="utf-8") == original_contents
    assert list(manifest_path.parent.iterdir()) == [manifest_path]


def test_release_manifest_default_uses_a_timezone_aware_utc_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the date fallback deterministic and explicitly based on UTC."""
    manifest_path = _write_manifest(tmp_path, '{"version": "2026.1.1"}')
    module = _load_manifest_updater()
    monkeypatch.chdir(tmp_path)

    with patch.object(module, "datetime") as clock:
        clock.now.return_value = datetime(2026, 11, 2, tzinfo=UTC)
        result = module.main([])
        clock.now.assert_called_once_with(UTC)

    assert result == 0
    assert (
        json.loads(manifest_path.read_text(encoding="utf-8"))["version"] == "2026.11.02"
    )
