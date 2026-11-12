"""Independent Python 3.15 smoke tests without unsupported Home Assistant imports."""

import json
import sys
import unittest
from importlib.metadata import version
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from python_frank_energie import (
    ContractPriceResolutionState,
    FrankEnergie,
    PriceData,
)

_ROOT = Path(__file__).resolve().parents[2]
_INTEGRATION_DIR = _ROOT / "custom_components" / "frank_energie"


class Python315SmokeTests(unittest.TestCase):
    """Verify the upstream client and integration syntax on Python 3.15."""

    def test_interpreter_and_client_release(self) -> None:
        """Use the exact experimental interpreter and declared client release."""
        self.assertEqual(sys.version_info[:3], (3, 15, 2))
        manifest = json.loads(
            (_INTEGRATION_DIR / "manifest.json").read_text(encoding="utf-8")
        )
        requirements = [
            requirement
            for requirement in manifest["requirements"]
            if requirement.startswith("python-frank-energie==")
        ]
        self.assertEqual(len(requirements), 1)
        expected_version = requirements[0].partition("==")[2]
        self.assertEqual(version("python-frank-energie"), expected_version)
        self.assertTrue(callable(FrankEnergie))

    def test_upstream_models_parse_offline(self) -> None:
        """Exercise actual client models without contacting Frank Energie."""
        resolution = ContractPriceResolutionState.from_dict(
            {
                "activeOption": "PT15M",
                "availableOptions": ["PT15M", "PT60M"],
                "isChangeRequestPossible": True,
            }
        )
        self.assertEqual(resolution.active_option, "PT15M")
        self.assertEqual(resolution.available_options, ["PT15M", "PT60M"])
        self.assertTrue(resolution.is_change_request_possible)

        electricity = PriceData(prices=[], energy_type="electricity")
        self.assertEqual(electricity.price_data, [])

    def test_pydantic_runtime(self) -> None:
        """Confirm the client's Pydantic dependency can validate data."""

        class PriceContract(BaseModel):
            """Represent an isolated strict parser sanity check."""

            model_config = ConfigDict(strict=True, extra="forbid")
            total: float

        parsed = PriceContract.model_validate({"total": 0.25})
        self.assertEqual(parsed.model_dump(mode="json"), {"total": 0.25})

    def test_integration_sources_compile(self) -> None:
        """Compile every integration module without importing Home Assistant."""
        source_files = sorted(_INTEGRATION_DIR.rglob("*.py"))
        self.assertTrue(source_files)
        for source_file in source_files:
            with self.subTest(source_file=source_file.relative_to(_ROOT)):
                compile(source_file.read_bytes(), str(source_file), "exec")


if __name__ == "__main__":
    unittest.main()
