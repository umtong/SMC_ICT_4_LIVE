from __future__ import annotations

from pathlib import Path
import unittest


class EffectiveIocExecutionContractTests(unittest.TestCase):
    def test_v1_ioc_engine_is_reused_through_protected_wrapper(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ioc = (root / "latency_capped_ioc_strategy.py").read_text(encoding="utf-8")
        wrapper = (root / "partial_oto_ioc_strategy.py").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("time_in_force=TimeInForce.IOC", ioc)
        self.assertIn("latency_capped_ioc_strategy", wrapper)
        self.assertIn("partial_oto_ioc_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
