"""Set the release manifest version and fail when the update cannot complete."""

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

MANIFEST_PATH = Path("custom_components") / "frank_energie" / "manifest.json"


def _reject_json_constant(constant: str) -> None:
    """Reject nonstandard JSON constants before replacing the manifest."""
    raise ValueError(f"Manifest contains an invalid JSON constant: {constant}.")


def update_manifest(version: str) -> str:
    """Write the requested version, propagating invalid data and file errors."""
    normalized_version = version.strip().removeprefix("v")
    if not normalized_version:
        raise ValueError("The release version must not be empty after removing 'v'.")

    try:
        manifest = json.loads(
            MANIFEST_PATH.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Manifest '{MANIFEST_PATH}' contains invalid JSON: {error.msg}."
        ) from error

    if not isinstance(manifest, dict):
        raise TypeError(f"Manifest '{MANIFEST_PATH}' must contain a JSON object.")

    manifest["version"] = normalized_version
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=MANIFEST_PATH.parent,
            prefix=".manifest-",
            suffix=".json",
            delete=False,
        ) as temporary_manifest:
            temporary_path = Path(temporary_manifest.name)
            json.dump(
                manifest, temporary_manifest, indent=4, sort_keys=True, allow_nan=False
            )
        temporary_path.chmod(MANIFEST_PATH.stat().st_mode)
        temporary_path.replace(MANIFEST_PATH)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return normalized_version


def main(arguments: Sequence[str] | None = None) -> int:
    """Update the manifest and return a failing exit status for expected errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        "-V",
        default=datetime.now(UTC).strftime("%Y.%m.%d"),
        help="Release version, optionally prefixed with v; defaults to the UTC date.",
    )
    parsed_arguments = parser.parse_args(arguments)

    try:
        updated_version = update_manifest(parsed_arguments.version)
    except (OSError, TypeError, ValueError) as error:
        print(f"Unable to update integration manifest: {error}", file=sys.stderr)
        return 1

    print(f"Manifest updated with version: {updated_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
