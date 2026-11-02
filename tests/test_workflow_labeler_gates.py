"""Check CI event guards, checkout recovery, and exact link exclusions."""

import json
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


def _named_step(workflow_name: str, job_name: str, step_name: str) -> dict[str, object]:
    """Require exactly one step with the requested name."""
    matching_steps = [
        step
        for step in _workflow_steps(workflow_name, job_name)
        if step.get("name") == step_name
    ]
    assert len(matching_steps) == 1
    return matching_steps[0]


def _step_script(step: dict[str, object]) -> str:
    """Extract an inline GitHub script from validated action inputs."""
    action_inputs = step["with"]
    assert isinstance(action_inputs, dict)
    script = action_inputs["script"]
    assert isinstance(script, str)
    return script


def _matches_event(condition: str, event_context: dict[str, str]) -> bool:
    """Evaluate the equality conjunctions used by these workflow guards."""
    comparisons: list[bool] = []
    for clause in condition.split("&&"):
        comparison = re.fullmatch(
            r"\s*((?:github|steps)\.[\w.]+)\s*(==|!=)\s*'([^']*)'\s*", clause
        )
        assert comparison is not None, f"Unsupported workflow condition: {clause}"
        context_key, comparison_operator, expected_value = comparison.groups()
        actual_value = event_context.get(context_key, "")
        values_equal = actual_value == expected_value
        comparisons.append(
            values_equal if comparison_operator == "==" else not values_equal
        )
    return all(comparisons)


def _run_github_script(
    script: str,
    payload_kind: str = "pull_request",
    title: str = "",
    live_pr_state: str = "open",
) -> dict[str, object]:
    """Execute an actual workflow script offline with recorded GitHub API calls."""
    context = {
        "payload": {payload_kind: {"title": title, "number": 357, "state": "open"}},
        "repo": {"owner": "example", "repo": "integration"},
        "issue": {"number": 357},
    }
    program = f"""
const context = {json.dumps(context)};
const calls = {{ labels: [], pulls: [] }};
const github = {{
  rest: {{
    issues: {{
      addLabels: async (parameters) => {{ calls.labels.push(parameters); }},
    }},
    pulls: {{
      get: async (parameters) => {{
        calls.pulls.push(parameters);
        return {{ data: {{ state: {json.dumps(live_pr_state)} }} }};
      }},
    }},
  }},
}};
async function runWorkflowScript() {{
{script}
}}
const result = await runWorkflowScript();
process.stdout.write(JSON.stringify({{ result, calls }}));
"""
    execution = subprocess.run(
        ["node", "--input-type=module"],
        input=program,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert execution.returncode == 0, execution.stderr
    output = json.loads(execution.stdout)
    assert isinstance(output, dict)
    return output


def test_issue_title_labeling_is_still_enabled() -> None:
    """Require real issue triggers and an unconditional title-labeling step."""
    workflow = _load_workflow("label.yml")
    # PyYAML's YAML 1.1 loader represents the unquoted on key as True.
    events = workflow.get("on", workflow.get(True))
    assert isinstance(events, dict)
    issue_events = events["issues"]
    assert isinstance(issue_events, dict)
    issue_types = issue_events["types"]
    assert isinstance(issue_types, list)
    assert {"opened", "edited"}.issubset(issue_types)
    title_step = _named_step("label.yml", "labeler", "Label by title")
    assert "if" not in title_step


@pytest.mark.parametrize("payload_kind", ("issue", "pull_request"))
@pytest.mark.parametrize(
    ("title", "expected_labels"),
    (
        (
            "DEPS CHORE FIX FEAT ENODE GRAPHQL PRICE SENSOR NUMBER BREAKING",
            [
                "dependencies",
                "chore",
                "fixed",
                "feature",
                "enode",
                "graphql",
                "prices",
                "sensor",
                "number",
                "breaking-change",
            ],
        ),
        ('fix: sensor with "quotes" and a newline\n', ["fixed", "sensor"]),
        ("fix: '; process.exit(1); //", ["fixed"]),
        ("Update documentation", []),
    ),
)
def test_title_script_invokes_label_api(
    payload_kind: str, title: str, expected_labels: list[str]
) -> None:
    """Check actual label API calls, including titles containing code-like text."""
    title_step = _named_step("label.yml", "labeler", "Label by title")
    output = _run_github_script(
        _step_script(title_step), payload_kind=payload_kind, title=title
    )
    calls = output["calls"]
    assert isinstance(calls, dict)
    expected_calls = []
    if expected_labels:
        expected_calls.append(
            {
                "owner": "example",
                "repo": "integration",
                "issue_number": 357,
                "labels": expected_labels,
            }
        )
    assert calls["labels"] == expected_calls
    assert calls["pulls"] == []


@pytest.mark.parametrize(
    ("step_name", "state_step_name"),
    (
        ("Apply labels", "Check current PR state before file labels"),
        ("Size label", "Check current PR state before size labels"),
    ),
)
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
    state_step_name: str,
    event_name: str,
    pr_state: str,
    event_action: str,
    expected: bool,
) -> None:
    """Only run PR actions after an eligible event and a fresh open-state check."""
    state_step = _named_step("label.yml", "labeler", state_step_name)
    label_step = _named_step("label.yml", "labeler", step_name)
    event_context = {
        "github.event_name": event_name,
        "github.event.pull_request.state": pr_state,
        "github.event.action": event_action,
    }
    state_condition = state_step["if"]
    label_condition = label_step["if"]
    assert isinstance(state_condition, str)
    assert isinstance(label_condition, str)
    if _matches_event(state_condition, event_context):
        output = _run_github_script(_step_script(state_step), live_pr_state=pr_state)
        event_context[f"steps.{state_step['id']}.outputs.result"] = json.dumps(
            output["result"]
        )
    assert _matches_event(label_condition, event_context) is expected


@pytest.mark.parametrize(
    ("step_name", "state_step_name"),
    (
        ("Apply labels", "Check current PR state before file labels"),
        ("Size label", "Check current PR state before size labels"),
    ),
)
def test_live_state_rejects_pr_closed_after_open_event(
    step_name: str, state_step_name: str
) -> None:
    """Ignore a stale open payload when the current GitHub state is closed."""
    steps = _workflow_steps("label.yml", "labeler")
    state_step = _named_step("label.yml", "labeler", state_step_name)
    label_step = _named_step("label.yml", "labeler", step_name)
    assert steps.index(state_step) + 1 == steps.index(label_step)
    output = _run_github_script(_step_script(state_step), live_pr_state="closed")
    assert output["result"] is False
    assert output["calls"] == {
        "labels": [],
        "pulls": [{"owner": "example", "repo": "integration", "pull_number": 357}],
    }
    condition = label_step["if"]
    assert isinstance(condition, str)
    event_context = {
        "github.event_name": "pull_request_target",
        "github.event.pull_request.state": "open",
        f"steps.{state_step['id']}.outputs.result": "false",
    }
    assert not _matches_event(condition, event_context)


def test_pr_closed_between_labelers_is_checked_again() -> None:
    """Refresh state again before size labeling after file labeling was allowed."""
    file_state = _named_step(
        "label.yml", "labeler", "Check current PR state before file labels"
    )
    size_state = _named_step(
        "label.yml", "labeler", "Check current PR state before size labels"
    )
    file_output = _run_github_script(_step_script(file_state), live_pr_state="open")
    size_output = _run_github_script(_step_script(size_state), live_pr_state="closed")
    assert file_output["result"] is True
    assert size_output["result"] is False


@pytest.mark.parametrize(
    ("pr_state", "event_action", "expected"),
    (
        ("", "edited", True),
        ("open", "opened", True),
        ("open", "edited", True),
        ("open", "synchronize", True),
        ("closed", "closed", False),
        ("closed", "edited", False),
    ),
)
def test_closed_pr_events_preserve_active_labeling_run(
    pr_state: str, event_action: str, expected: bool
) -> None:
    """Preserve active runs for both close and edited-closed PR events."""
    concurrency = _load_workflow("label.yml")["concurrency"]
    assert isinstance(concurrency, dict)
    condition = concurrency["cancel-in-progress"]
    assert isinstance(condition, str)
    expression = condition.removeprefix("${{").removesuffix("}}").strip()
    event_context = {
        "github.event.pull_request.state": pr_state,
        "github.event.action": event_action,
    }
    assert _matches_event(expression, event_context) is expected


def test_ruff_cancels_superseded_commits_for_the_same_pr() -> None:
    """Keep all commits of one PR in the same cancelable concurrency group."""
    concurrency = _load_workflow("ruff.yml")["concurrency"]
    assert isinstance(concurrency, dict)
    assert concurrency["cancel-in-progress"] is True
    assert concurrency["group"] == "${{ github.workflow }}-${{ github.ref }}"


@pytest.mark.parametrize(
    ("checkout_outcome", "expected_retry"),
    (("success", False), ("failure", True), ("cancelled", False)),
)
def test_checkout_retries_failure_before_link_validation(
    checkout_outcome: str, expected_retry: bool
) -> None:
    """Retry checkout itself once and keep a repeated failure blocking."""
    steps = _workflow_steps("link-check.yml", "link-check")
    checkout = _named_step("link-check.yml", "link-check", "Checkout repository")
    retry = _named_step("link-check.yml", "link-check", "Retry repository checkout")
    lychee = _named_step("link-check.yml", "link-check", "Run Lychee")
    assert steps.index(checkout) + 1 == steps.index(retry)
    assert steps.index(retry) < steps.index(lychee)
    assert checkout["id"] == "checkout"
    assert checkout["continue-on-error"] is True
    assert retry.get("continue-on-error", False) is False
    assert checkout["uses"] == retry["uses"]
    assert str(checkout["uses"]).startswith("actions/checkout@")
    assert checkout["with"] == retry["with"] == {"persist-credentials": False}
    condition = retry["if"]
    assert isinstance(condition, str)
    assert (
        _matches_event(condition, {"steps.checkout.outcome": checkout_outcome})
        is expected_retry
    )


def test_ci_executes_workflow_regressions_with_node() -> None:
    """Keep the existing full pytest gate and explicitly provide its JS runtime."""
    workflow = _load_workflow("ci.yaml")
    events = workflow.get("on", workflow.get(True))
    assert isinstance(events, dict)
    assert "pull_request" in events
    steps = _workflow_steps("ci.yaml", "run")
    node_step = _named_step(
        "ci.yaml", "run", "Set up Node.js for workflow script regression tests"
    )
    dependencies = _named_step("ci.yaml", "run", "Install dependencies with Poetry")
    pytest_step = _named_step("ci.yaml", "run", "Test with pytest")
    node_inputs = node_step["with"]
    assert isinstance(node_inputs, dict)
    assert node_inputs["node-version"] == "24"
    assert steps.index(node_step) < steps.index(pytest_step)
    assert steps.index(dependencies) < steps.index(pytest_step)
    assert "if" not in pytest_step
    assert pytest_step.get("continue-on-error", False) is False
    assert str(pytest_step["run"]).strip() == "poetry run pytest -v --capture=no"


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
