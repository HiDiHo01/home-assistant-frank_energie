"""Check CI event guards, retry behavior, and narrowly scoped link exclusions."""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"


def _load_workflow(workflow_name: str) -> dict[object, object]:
    """Read a workflow as YAML instead of matching unrelated text fragments."""
    workflow = yaml.safe_load((WORKFLOWS / workflow_name).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _workflow_steps(workflow_name: str, job_name: str) -> list[dict[str, object]]:
    """Return the validated step mappings for a named workflow job."""
    jobs = _load_workflow(workflow_name)["jobs"]
    assert isinstance(jobs, dict)
    job = jobs[job_name]
    assert isinstance(job, dict)
    steps = job["steps"]
    assert isinstance(steps, list)
    assert all(isinstance(step, dict) for step in steps)
    return steps


def _matches_event(condition: str, event_context: dict[str, str]) -> bool:
    """Evaluate the equality conjunctions used by these workflow guards."""
    comparisons: list[bool] = []
    for clause in condition.split("&&"):
        comparison = re.fullmatch(
            r"\s*(github\.[\w.]+)\s*(==|!=)\s*'([^']*)'\s*", clause
        )
        assert comparison is not None, f"Unsupported workflow condition: {clause}"
        context_key, comparison_operator, expected_value = comparison.groups()
        actual_value = event_context.get(context_key, "")
        values_equal = actual_value == expected_value
        comparisons.append(
            values_equal if comparison_operator == "==" else not values_equal
        )
    return all(comparisons)


def test_issue_title_labeling_is_still_enabled() -> None:
    """Require real issue triggers and an unconditional title-labeling step."""
    workflow = _load_workflow("label.yml")
    # PyYAML's YAML 1.1 loader represents the unquoted `on` key as True.
    events = workflow.get("on", workflow.get(True))
    assert isinstance(events, dict)
    issue_events = events["issues"]
    assert isinstance(issue_events, dict)
    issue_types = issue_events["types"]
    assert isinstance(issue_types, list)
    assert {"opened", "edited"}.issubset(issue_types)

    title_steps = [
        step
        for step in _workflow_steps("label.yml", "labeler")
        if str(step.get("uses", "")).startswith("actions/github-script@")
    ]
    assert len(title_steps) == 1
    title_step = title_steps[0]
    assert "if" not in title_step
    action_inputs = title_step["with"]
    assert isinstance(action_inputs, dict)
    title_script = action_inputs["script"]
    assert isinstance(title_script, str)
    assert "github.rest.issues.addLabels" in title_script


@pytest.mark.parametrize("step_name", ("Apply labels", "Size label"))
@pytest.mark.parametrize(
    ("event_name", "pr_state", "event_action", "expected"),
    (
        ("issues", "", "opened", False),
        ("issues", "", "edited", False),
        ("pull_request_target", "open", "opened", True),
        ("pull_request_target", "open", "edited", True),
        ("pull_request_target", "open", "synchronize", True),
        ("pull_request_target", "open", "reopened", True),
        ("pull_request_target", "closed", "closed", False),
        ("pull_request_target", "closed", "edited", False),
    ),
)
def test_pr_only_actions_skip_issue_and_closed_pr_events(
    step_name: str,
    event_name: str,
    pr_state: str,
    event_action: str,
    expected: bool,
) -> None:
    """Reject issue events and edited closed PRs while retaining open PR events."""
    matching_steps = [
        step
        for step in _workflow_steps("label.yml", "labeler")
        if step.get("name") == step_name
    ]
    assert len(matching_steps) == 1
    condition = matching_steps[0]["if"]
    assert isinstance(condition, str)
    event_context = {
        "github.event_name": event_name,
        "github.event.pull_request.state": pr_state,
        "github.event.action": event_action,
    }
    assert _matches_event(condition, event_context) is expected


@pytest.mark.parametrize(
    ("event_action", "expected"), (("synchronize", True), ("closed", False))
)
def test_closing_pr_preserves_active_labeling_run(
    event_action: str, expected: bool
) -> None:
    """Avoid canceling a labeling run with a closing event that skips labelers."""
    concurrency = _load_workflow("label.yml")["concurrency"]
    assert isinstance(concurrency, dict)
    condition = concurrency["cancel-in-progress"]
    assert isinstance(condition, str)
    expression = condition.removeprefix("${{").removesuffix("}}").strip()
    assert _matches_event(expression, {"github.event.action": event_action}) is expected


def test_ruff_cancels_superseded_commits_for_the_same_pr() -> None:
    """Keep all commits of one PR in the same cancelable concurrency group."""
    concurrency = _load_workflow("ruff.yml")["concurrency"]
    assert isinstance(concurrency, dict)
    assert concurrency["cancel-in-progress"] is True
    assert concurrency["group"] == "${{ github.workflow }}-${{ github.ref }}"


@pytest.mark.parametrize(
    ("failure_count", "expected_attempts", "expected_returncode"),
    ((0, 1, 0), (2, 3, 0), (12, 12, 1)),
)
def test_checkout_preflight_retries_http_errors_and_requires_https(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_count: int,
    expected_attempts: int,
    expected_returncode: int,
) -> None:
    """Execute the preflight offline with curl HTTP errors and a no-op sleep."""
    preflight = next(
        step
        for step in _workflow_steps("link-check.yml", "link-check")
        if step.get("name") == "Wait for GitHub checkout connectivity"
    )
    script = preflight["run"]
    assert isinstance(script, str)
    request_count_path = tmp_path / "request_count"
    arguments_path = tmp_path / "curl_arguments"
    request_count_path.write_text("0", encoding="utf-8")
    curl_path = tmp_path / "curl"
    curl_path.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$@" > "$CURL_ARGUMENTS"\n'
        'attempt=$(cat "$CURL_REQUEST_COUNT")\n'
        "attempt=$((attempt + 1))\n"
        'printf "%s" "$attempt" > "$CURL_REQUEST_COUNT"\n'
        "if (( attempt <= CURL_FAILURE_COUNT )); then\n"
        '  for argument in "$@"; do\n'
        '    if [[ "$argument" == "--fail" ]]; then exit 22; fi\n'
        "  done\n"
        "fi\n",
        encoding="utf-8",
    )
    sleep_path = tmp_path / "sleep"
    sleep_path.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    curl_path.chmod(0o700)
    sleep_path.chmod(0o700)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    monkeypatch.setenv("CURL_FAILURE_COUNT", str(failure_count))
    monkeypatch.setenv("CURL_REQUEST_COUNT", str(request_count_path))
    monkeypatch.setenv("CURL_ARGUMENTS", str(arguments_path))

    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == expected_returncode, result.stderr
    assert int(request_count_path.read_text(encoding="utf-8")) == expected_attempts
    curl_arguments = arguments_path.read_text(encoding="utf-8").splitlines()
    for protocol_flag in ("--proto", "--proto-redir"):
        assert curl_arguments[curl_arguments.index(protocol_flag) + 1] == "=https"
    assert "--fail" in curl_arguments


@pytest.mark.parametrize(
    ("link_url", "expected"),
    (
        ("https://www.paypal.me/royarx", True),
        ("https://www.paypal.me/royarx-typo", False),
        ("https://wwwXpaypalXme/royarx", False),
        ("https://example.com/https://www.paypal.me/royarx", False),
    ),
)
def test_paypal_exclusion_matches_only_the_intended_url(
    link_url: str, expected: bool
) -> None:
    """Keep malformed donation URLs visible to the documentation link gate."""
    exclusion_patterns = [
        line.strip()
        for line in (REPOSITORY_ROOT / ".lycheeignore")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert (
        any(re.search(pattern, link_url) for pattern in exclusion_patterns) is expected
    )
