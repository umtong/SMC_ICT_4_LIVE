from __future__ import annotations

from pathlib import Path
import unittest


class RetiredIocExecutionContractTests(unittest.TestCase):
    def test_v1_ioc_is_retained_but_not_effective(self) -> None:
        root = Path(__file__).resolve().parents[1]
        retired = (root / "latency_capped_ioc_strategy.py").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("time_in_force=TimeInForce.IOC", retired)
        self.assertNotIn("latency_capped_ioc_strategy", adapter)
        self.assertIn("fok_capped_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
