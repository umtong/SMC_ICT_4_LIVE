from __future__ import annotations

from pathlib import Path
import unittest


class RetiredIocExecutionContractTests(unittest.TestCase):
    def test_v1_ioc_is_retained_but_v4_is_effective(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ioc = (root / "latency_capped_ioc_strategy.py").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("time_in_force=TimeInForce.IOC", ioc)
        self.assertIn("managed_protection_ioc_strategy", adapter)
        self.assertNotIn("latency_capped_ioc_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
