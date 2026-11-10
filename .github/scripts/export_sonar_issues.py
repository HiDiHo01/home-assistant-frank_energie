"""Export all matching SonarQube Cloud issues to a single validated JSON file."""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_API_URL = "https://sonarcloud.io/api/issues/search"
_PAGE_SIZE = 100
_MAX_RESULTS = 10_000
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_CONFIG = _REPOSITORY_ROOT / "sonar-project.properties"
_OUTPUT_PATH = _REPOSITORY_ROOT / "sonar-open-issues.json"


def _project_key() -> str:
    """Read the project key only from this repository's fixed configuration."""
    try:
        properties = _PROJECT_CONFIG.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(
            "Cannot read repository SonarQube project configuration"
        ) from error

    for line in properties.splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and name == "sonar.projectKey" and value.strip():
            return value.strip()

    raise ValueError("sonar.projectKey is missing from sonar-project.properties")


def _pull_request_number(value: str) -> str | None:
    """Validate an optional positive pull-request number without URL injection."""
    stripped = value.strip()
    if not stripped:
        return None
    if not stripped.isascii() or not stripped.isdecimal() or int(stripped) <= 0:
        raise ValueError("Pull request must be a positive integer or left empty")
    return str(int(stripped))


def _fetch_page(
    project_key: str,
    pull_request: str | None,
    page: int,
    token: str,
) -> dict[str, object]:
    """Fetch one page of open and confirmed SonarQube Cloud issues."""
    query = {
        "componentKeys": project_key,
        "issueStatuses": "OPEN,CONFIRMED",
        "p": str(page),
        "ps": str(_PAGE_SIZE),
    }
    if pull_request is not None:
        query["pullRequest"] = pull_request
        query["sinceLeakPeriod"] = "true"

    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{_API_URL}?{urlencode(query)}", headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            data = json.load(response)
    except HTTPError as error:
        raise RuntimeError(
            f"SonarQube API returned HTTP {error.code} on page {page}; "
            "check project Browse access and SONAR_TOKEN, or retry later"
        ) from None
    except URLError:
        raise RuntimeError(
            f"Cannot connect to SonarQube Cloud for page {page}; check network access"
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError(
            f"SonarQube API returned invalid JSON on page {page}"
        ) from None

    if not isinstance(data, dict):
        raise RuntimeError(f"SonarQube API returned an invalid response on page {page}")
    return data


def _validate_issue_page(
    response: dict[str, object], page: int
) -> tuple[int, list[dict[str, object]]]:
    """Validate the total and issues list before accepting an API page."""
    total = response.get("total")
    current_issues = response.get("issues")
    if (
        not isinstance(total, int)
        or isinstance(total, bool)
        or total < 0
        or not isinstance(current_issues, list)
        or not all(isinstance(issue, dict) for issue in current_issues)
    ):
        raise RuntimeError(
            f"SonarQube API returned invalid issue pagination on page {page}"
        )
    return total, current_issues


def _append_unique_issues(
    current_issues: list[dict[str, object]],
    issues: list[dict[str, object]],
    issue_keys: set[str],
    page: int,
) -> None:
    """Reject malformed/duplicate issue keys before adding a response page."""
    for issue in current_issues:
        issue_key = issue.get("key")
        if not isinstance(issue_key, str) or not issue_key:
            raise RuntimeError(
                f"SonarQube returned an invalid issue on page {page}"
            )
        if issue_key in issue_keys:
            raise RuntimeError(
                "SonarQube returned duplicate issues between pages; retry"
            )
        issue_keys.add(issue_key)
        issues.append(issue)


def _export_issues(
    project_key: str,
    pull_request: str | None,
    token: str,
) -> dict[str, object]:
    """Collect every page without truncating or silently accepting stale totals."""
    issues: list[dict[str, object]] = []
    issue_keys: set[str] = set()
    expected_total: int | None = None
    page = 1

    while True:
        response = _fetch_page(project_key, pull_request, page, token)
        total, current_issues = _validate_issue_page(response, page)

        if expected_total is None:
            expected_total = total
            if expected_total > _MAX_RESULTS:
                raise RuntimeError(
                    "SonarQube returned over 10,000 findings; "
                    "narrow the export to a pull request"
                )
        elif total != expected_total:
            raise RuntimeError(
                "SonarQube findings changed between pages; retry the export"
            )

        _append_unique_issues(current_issues, issues, issue_keys, page)

        if len(issues) == expected_total:
            break
        if len(issues) > expected_total or len(current_issues) != _PAGE_SIZE:
            raise RuntimeError(
                "SonarQube returned an incomplete or inconsistent issue page"
            )
        page += 1

    return {
        "projectKey": project_key,
        "pullRequest": pull_request,
        "issueStatuses": ["OPEN", "CONFIRMED"],
        "sinceLeakPeriod": pull_request is not None,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "total": expected_total,
        "issues": issues,
    }


def main() -> int:
    """Validate inputs, fetch the full issue set and write one JSON artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pull-request",
        default="",
        help="Optional pull request number; omit for all open project findings",
    )
    args = parser.parse_args()

    try:
        project_key = _project_key()
        pull_request = _pull_request_number(args.pull_request)
        result = _export_issues(
            project_key, pull_request, os.environ.get("SONAR_TOKEN", "").strip()
        )
        _OUTPUT_PATH.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"SonarQube issue export failed: {error}", file=sys.stderr)
        return 1

    print(f"Exported {result['total']} SonarQube issues to {_OUTPUT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
