"""Verify the SonarQube issue-export workflow and its offline API handling."""

import importlib.util
import io
import json
import sys
from secrets import token_hex
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "export_sonar_issues.py"
WORKFLOW = ROOT / ".github" / "workflows" / "sonar-issues-export.yml"


@pytest.fixture(scope="module")
def exporter() -> ModuleType:
    """Load the actual exporter rather than duplicating its implementation."""
    specification = importlib.util.spec_from_file_location(
        "export_sonar_issues", SCRIPT
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_workflow_is_manual_read_only_and_uploads_json() -> None:
    """Protect manual inputs, minimal permissions and complete artifact delivery."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    events = workflow.get("on", workflow.get(True))
    assert set(events) == {"workflow_dispatch", "pull_request"}
    assert ".github/scripts/export_sonar_issues.py" in events["pull_request"]["paths"]
    assert events["workflow_dispatch"]["inputs"]["pull_request"]["type"] == "string"
    assert workflow["permissions"] == {"contents": "read"}

    job = workflow["jobs"]["export"]
    assert job["permissions"] == {"contents": "read"}
    assert job["if"] == (
        "github.event_name != 'workflow_dispatch' || github.ref == 'refs/heads/main'"
    )
    steps = {step["name"]: step for step in job["steps"]}
    assert steps["Harden Runner"]["with"]["egress-policy"] == "block"
    assert steps["Export open findings"]["env"] == {
        "SONAR_TOKEN": (
            "${{ github.event_name == 'workflow_dispatch' && "
            "github.ref == 'refs/heads/main' && "
            "secrets.SONAR_TOKEN || '' }}"
        ),
        "PULL_REQUEST": (
            "${{ github.event.pull_request.number || "
            "inputs.pull_request || '' }}"
        ),
    }
    assert '--pull-request "$PULL_REQUEST"' in steps["Export open findings"]["run"]

    artifact = steps["Upload JSON export"]
    assert artifact["uses"] == (
        "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
    )
    assert artifact["with"]["path"] == "sonar-open-issues.json"
    assert artifact["with"]["if-no-files-found"] == "error"


def test_project_key_and_pull_request_validation(exporter: ModuleType) -> None:
    """Read the configured project and reject injection or invalid PR numbers."""
    assert exporter._PROJECT_CONFIG == ROOT / "sonar-project.properties"
    assert exporter._project_key() == ("HiDiHo01_home-assistant-frank_energie")
    assert exporter._pull_request_number("") is None
    assert exporter._pull_request_number(" 433 ") == "433"
    for value in ("0", "-1", "433&ps=500", "PR-433", "１２"):
        with pytest.raises(ValueError, match="positive integer"):
            exporter._pull_request_number(value)


@pytest.mark.parametrize("pull_request", [None, "433"])
def test_fetch_page_builds_correct_query_and_optional_auth(
    exporter: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    pull_request: str | None,
) -> None:
    """Use the precise SonarQube filters and keep tokens in request headers."""
    captured: dict[str, object] = {}
    synthetic_token = token_hex(16)

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        """Return a valid empty issue page while recording safe request metadata."""
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return io.BytesIO(b'{"total":0,"issues":[]}')

    monkeypatch.setattr(exporter, "urlopen", fake_urlopen)
    assert exporter._fetch_page("my_project", pull_request, 1, synthetic_token) == {
        "total": 0,
        "issues": [],
    }
    params = parse_qs(urlsplit(str(captured["url"])).query)
    assert params["componentKeys"] == ["my_project"]
    assert params["issueStatuses"] == ["OPEN,CONFIRMED"]
    assert params["p"] == ["1"]
    assert params["ps"] == ["100"]
    if pull_request:
        assert params["pullRequest"] == ["433"]
        assert params["sinceLeakPeriod"] == ["true"]
    else:
        assert "pullRequest" not in params
        assert "sinceLeakPeriod" not in params
    assert captured["authorization"] == f"Bearer {synthetic_token}"
    assert captured["timeout"] == 20
    assert synthetic_token not in str(captured["url"])


def test_export_aggregates_all_pages_without_truncation(
    exporter: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrieve every issue rather than silently returning only the first page."""
    fetched_pages: list[int] = []

    def fake_fetch(
        project_key: str, pull_request: str | None, page: int, token: str
    ) -> dict[str, object]:
        """Return 103 distinct issues in two API-sized pages."""
        assert project_key == "my_project"
        assert pull_request == "433"
        assert token == ""
        fetched_pages.append(page)
        start = (page - 1) * 100
        return {
            "total": 103,
            "issues": [
                {"key": f"issue-{issue_id}", "rule": "python:S3415"}
                for issue_id in range(start, min(start + 100, 103))
            ],
        }

    monkeypatch.setattr(exporter, "_fetch_page", fake_fetch)
    result = exporter._export_issues("my_project", "433", "")
    assert fetched_pages == [1, 2]
    assert result["total"] == 103
    assert len(result["issues"]) == 103
    assert result["issues"][-1]["key"] == "issue-102"
    assert result["pullRequest"] == "433"
    assert result["sinceLeakPeriod"] is True


@pytest.mark.parametrize("inconsistent", ["changed_total", "duplicate", "short_page"])
def test_export_fails_closed_on_inconsistent_pagination(
    exporter: ModuleType, monkeypatch: pytest.MonkeyPatch, inconsistent: str
) -> None:
    """Never publish partial or duplicate issue inventories as complete results."""

    def fake_fetch(
        project_key: str, pull_request: str | None, page: int, token: str
    ) -> dict[str, object]:
        """Simulate inconsistent Sonar issue pagination."""
        if page == 1:
            if inconsistent == "short_page":
                return {"total": 101, "issues": [{"key": "issue-1"}]}
            return {
                "total": 101,
                "issues": [{"key": f"issue-{index}"} for index in range(100)],
            }
        if inconsistent == "changed_total":
            return {"total": 102, "issues": [{"key": "issue-100"}]}
        return {"total": 101, "issues": [{"key": "issue-0"}]}

    monkeypatch.setattr(exporter, "_fetch_page", fake_fetch)
    with pytest.raises(RuntimeError, match="changed|duplicate|incomplete"):
        exporter._export_issues("my_project", "433", "")


def test_unauthorized_response_never_exposes_token(
    exporter: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return an actionable HTTP error without leaking the Authorization header."""

    def fake_urlopen(request: Request, timeout: int) -> io.BytesIO:
        """Simulate SonarQube rejecting a bearer token."""
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)

    monkeypatch.setattr(exporter, "urlopen", fake_urlopen)
    synthetic_token = token_hex(16)
    with pytest.raises(RuntimeError, match="HTTP 403") as error:
        exporter._fetch_page("my_project", "433", 1, synthetic_token)
    assert synthetic_token not in str(error.value)


def test_cli_writes_valid_json_for_empty_project(
    exporter: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Publish a zero-issue artifact instead of failing a clean project."""
    output = tmp_path / "sonar-open-issues.json"
    monkeypatch.setattr(exporter, "_OUTPUT_PATH", output)
    monkeypatch.setattr(
        exporter, "_fetch_page", lambda *_args: {"total": 0, "issues": []}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["export_sonar_issues.py"],
    )
    assert exporter.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["issues"] == []
    assert payload["total"] == 0
    assert payload["pullRequest"] is None
    assert payload["sinceLeakPeriod"] is False
    assert payload["exportedAt"].endswith("+00:00")
