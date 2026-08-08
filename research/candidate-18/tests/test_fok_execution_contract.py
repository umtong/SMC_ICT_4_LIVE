from __future__ import annotations

from pathlib import Path
import unittest


class RetiredFokExecutionContractTests(unittest.TestCase):
    def test_fok_is_retained_but_not_effective(self) -> None:
        root = Path(__file__).resolve().parents[1]
        retired = (root / "fok_capped_strategy.py").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("time_in_force=TimeInForce.FOK", retired)
        self.assertNotIn("fok_capped_strategy", adapter)
        self.assertIn("partial_oto_ioc_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
