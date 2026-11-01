"""Static regression guards for issue/PR labeler event ownership.

The job handles both issues and pull requests, but actions/labeler and
pr-size-labeler require an *open pull request*. Live event behavior still
needs a post-merge GitHub Actions check, because pull_request_target reads
the workflow from the base/default branch.
"""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "label.yml"
ACTIVE_PR_ONLY = (
    "if: github.event_name == 'pull_request_target' && github.event.action != 'closed'"
)


def test_issue_title_labeling_is_still_enabled():
    """Keep issue triage while isolating PR-only actions."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "issues:" in text
    assert "pull_request_target:" in text
    assert "github.rest.issues.addLabels" in text


def test_pr_only_actions_skip_issue_and_closed_pr_events():
    """Never call PR size/file APIs on an issue or a closed PR."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for name in ("Apply labels", "Size label"):
        marker = f"      - name: {name}\n"
        assert text.count(marker) == 1
        section = text.split(marker, 1)[1].split("\n      - ", 1)[0]
        assert ACTIVE_PR_ONLY in section, name
