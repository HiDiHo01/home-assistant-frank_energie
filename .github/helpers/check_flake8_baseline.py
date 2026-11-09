"""Reject new full-Flake8 findings while existing technical debt is reduced."""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".github" / "flake8-baseline.json"
DIAGNOSTIC_PATTERN = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): "
    r"(?P<code>[A-Z]\d+) (?P<message>.+)$"
)

type Finding = tuple[str, str, str]


def _parse_diagnostics(output: str) -> Counter[Finding]:
    """Parse every Flake8 finding, failing rather than ignoring unknown output."""
    findings: Counter[Finding] = Counter()
    for line in output.splitlines():
        if not line.strip():
            continue
        match = DIAGNOSTIC_PATTERN.fullmatch(line.strip())
        if match is None:
            raise ValueError(f"Unexpected Flake8 output: {line}")
        path = match.group("path").removeprefix("./")
        findings[(path, match.group("code"), match.group("message"))] += 1
    return findings


def _load_baseline(baseline_path: Path) -> Counter[Finding]:
    """Read a reviewable baseline of previously reported Flake8 findings."""
    content = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(content, dict) or not isinstance(content.get("allowed"), list):
        raise ValueError("The Flake8 baseline must define an 'allowed' list.")
    allowed: Counter[Finding] = Counter()
    for record in content["allowed"]:
        if not isinstance(record, dict):
            raise ValueError("Flake8 baseline records must be JSON objects.")
        fields = (record.get("path"), record.get("code"), record.get("message"))
        if any(not isinstance(value, str) or not value for value in fields):
            raise ValueError(f"Invalid Flake8 baseline finding: {record!r}")
        allowed[(fields[0], fields[1], fields[2])] += 1
    return allowed


def main() -> int:
    """Run full Flake8 and fail for new findings or scanner failures."""
    command = [
        sys.executable,
        "-m",
        "flake8",
        ".",
        "--jobs=1",
        "--extend-exclude=.venv",
        "--max-complexity=10",
        "--max-line-length=120",
        # Black/Ruff-compatible slices intentionally permit E203.
        "--extend-ignore=E203",
        "--format=%(path)s:%(row)d:%(col)d: %(code)s %(text)s",
    ]
    try:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True, check=False
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(f"Flake8 exited unexpectedly ({result.returncode}).")
        findings = _parse_diagnostics(result.stdout)
        if result.returncode == 1 and not findings:
            raise RuntimeError("Flake8 failed without recognizable diagnostics.")
        unexpected = findings - _load_baseline(BASELINE)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Unable to validate Flake8 baseline: {error}", file=sys.stderr)
        return 1

    if unexpected:
        for (path, code, message), count in sorted(unexpected.items()):
            print(
                f"New Flake8 finding ({count}): {path}: {code} {message}",
                file=sys.stderr,
            )
        return 1

    remaining = sum(findings.values())
    print(f"Full Flake8 checked: {remaining} known finding(s), no new violations.")
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
