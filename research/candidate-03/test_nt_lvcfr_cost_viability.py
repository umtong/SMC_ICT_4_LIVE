from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from apply_nt_lvcfr_cost_viability_patch import MARKER, apply_patch
from nt_lvcfr_cost_viability import expected_structural_target_net_per_unit


class CostViabilityTests(unittest.TestCase):
    def test_long_target_must_clear_round_trip_fees(self) -> None:
        self.assertLess(
            expected_structural_target_net_per_unit(
                entry_price=100.0,
                target_price=100.05,
                direction=1,
                fee_fraction=0.0005,
            ),
            0.0,
        )
        self.assertGreater(
            expected_structural_target_net_per_unit(
                entry_price=100.0,
                target_price=100.20,
                direction=1,
                fee_fraction=0.0005,
            ),
            0.0,
        )

    def test_short_target_and_adverse_funding_are_symmetric(self) -> None:
        net = expected_structural_target_net_per_unit(
            entry_price=100.0,
            target_price=99.8,
            direction=-1,
            fee_fraction=0.0005,
            adverse_funding_per_unit=0.02,
        )
        self.assertGreater(net, 0.0)

    def test_patch_is_idempotent_and_inserts_native_entry_gate(self) -> None:
        source = '''import math
from nautilus_trader.trading.strategy import Strategy
class S:
    def __init__(self):
        self.counters = {
            "invalid_structural_target": 0,
        }
    def f(self):
        self._submit_entry(pending, tick)
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy.py"
            path.write_text(source, encoding="utf-8")
            self.assertTrue(apply_patch(path))
            patched = path.read_text(encoding="utf-8")
            self.assertIn(MARKER, patched)
            self.assertIn("expected_structural_target_net_per_unit", patched)
            self.assertFalse(apply_patch(path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
