from __future__ import annotations

from pathlib import Path
import unittest


class RetiredLatencyEmulationContractTests(unittest.TestCase):
    def test_failed_emulation_is_retained_but_v4_is_effective(self) -> None:
        root = Path(__file__).resolve().parents[1]
        retained = (root / "latency_emulated_strategy.py").read_text(encoding="utf-8")
        adapter = (root / "candidate18_strategy.py").read_text(encoding="utf-8")
        self.assertIn("emulation_trigger=TriggerType.DEFAULT", retained)
        self.assertNotIn("latency_emulated_strategy", adapter)
        self.assertIn("managed_protection_ioc_strategy", adapter)


if __name__ == "__main__":
    unittest.main()
