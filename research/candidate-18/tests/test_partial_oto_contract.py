from __future__ import annotations

from pathlib import Path
import unittest


class RetiredPartialOtoContractTests(unittest.TestCase):
    def test_v3_is_retained_but_v4_is_effective(self) -> None:
        root = Path(__file__).resolve().parents[1]
        retained = (root / "partial_oto_ioc_strategy.py").read_text(encoding="utf-8")
        failure = (root / "V3_PARTIAL_OTO_FAILURE.md").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("partial-fill protection", retained)
        self.assertIn("oto_trigger_mode=PARTIAL", failure)
        self.assertNotIn("partial_oto_ioc_strategy", adapter)
        self.assertIn("managed_protection_ioc_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
