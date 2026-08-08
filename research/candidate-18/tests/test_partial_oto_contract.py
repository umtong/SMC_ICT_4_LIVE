from __future__ import annotations

from pathlib import Path
import unittest


class RetiredPartialOtoContractTests(unittest.TestCase):
    def test_v3_is_retained_but_not_effective(self) -> None:
        root = Path(__file__).resolve().parents[1]
        retained = (root / "partial_oto_ioc_strategy.py").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("PARTIAL", retained)
        self.assertNotIn("partial_oto_ioc_strategy", adapter)
        self.assertIn("managed_protection_ioc_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
