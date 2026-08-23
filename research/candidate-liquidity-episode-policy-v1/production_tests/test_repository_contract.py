from __future__ import annotations

from pathlib import Path
import unittest


class RepositoryContractTests(unittest.TestCase):
    def test_windows_scripts_are_native_and_foreground(self) -> None:
        root = Path(__file__).resolve().parents[1]
        expected = {
            "Bootstrap.ps1", "Verify.ps1", "Run-Shadow.ps1", "Run-Paper.ps1",
            "Run-Testnet.ps1", "Build-Model.ps1", "Run-Historical-Continuous.ps1", "Status.ps1",
        }
        present = {path.name for path in (root / "windows").glob("*.ps1")}
        self.assertTrue(expected.issubset(present))

    def test_shadow_configuration_has_no_order_capability(self) -> None:
        import json
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "configs" / "shadow.windows.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["mode"], "shadow")
        self.assertFalse(payload["allow_testnet_orders"])


if __name__ == "__main__":
    unittest.main()
