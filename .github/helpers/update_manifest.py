"""Update the manifest file.

Sets the version number in the manifest file to the version number
"""

import sys
import json
from pathlib import Path
from datetime import datetime


MANIFEST_PATH = Path.cwd() / "custom_components" / "frank_energie" / "manifest.json"
VERSION_FLAG = ["--version", "-V"]


def update_manifest():
    """Update the manifest file with the current date as the version number."""
    # Get the current date in YEAR.MONTH.DAY format
    release_date = datetime.now().strftime("%Y.%m.%d")
    
    version = release_date  # Set version to the release date
    
    # Check if version argument is passed (optional, can still override)
    for index, value in enumerate(sys.argv):
        if value in VERSION_FLAG:
            # Ensure the version number is present
            if index + 1 < len(sys.argv):
                version = sys.argv[index + 1]
            else:
                print("Error: No version number provided after the '--version' flag.")
                return

    # Remove the 'v' from the version if it exists
    version = version.lstrip("v")

    try:
        with open(MANIFEST_PATH, "r") as manifestfile:
            manifest = json.load(manifestfile)
        
        # Update the version in the manifest
        manifest["version"] = version

        with open(MANIFEST_PATH, "w") as manifestfile:
            json.dump(manifest, manifestfile, indent=4, sort_keys=True)

        print(f"Manifest updated with version: {version}")
        
    except FileNotFoundError:
        print(f"Error: The manifest file '{MANIFEST_PATH}' does not exist.")
    except json.JSONDecodeError:
        print(f"Error: The manifest file '{MANIFEST_PATH}' is not valid JSON.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


update_manifest()
