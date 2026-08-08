from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine import EngineConfig, FlowBar, LiquidityStateEngine, MINUTE_NS, risk_based_quantity

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def bar(source_minute: int, o: float, h: float, l: float, c: float, *, imbalance: float = 0.5) -> FlowBar:
    volume = 100.0
    taker_buy = volume * (imbalance + 1.0) / 2.0
    return FlowBar(
        (source_minute + 1) * MINUTE_NS,
        o,
        h,
        l,
        c,
        volume,
        taker_buy,
        100,
    )


class QuarterHourContracts(unittest.TestCase):
    def test_phase_ablation_is_a_seven_minute_placebo_only(self):
        base = EngineConfig.from_mapping(CONFIG, ablation="baseline")
        shifted = EngineConfig.from_mapping(CONFIG, ablation="shifted-phase")
        self.assertEqual(base.phase_offset_minutes, 0)
        self.assertEqual(shifted.phase_offset_minutes, 7)
        self.assertEqual(base.lag_openings, shifted.lag_openings)
        self.assertEqual(base.minimum_lag_agreement, shifted.minimum_lag_agreement)

    def test_no_imbalance_and_no_lag_remove_only_declared_confirmation(self):
        base = EngineConfig.from_mapping(CONFIG, ablation="baseline")
        no_flow = EngineConfig.from_mapping(CONFIG, ablation="no-imbalance")
        no_lag = EngineConfig.from_mapping(CONFIG, ablation="no-boundary-lag")
        self.assertTrue(base.use_imbalance)
        self.assertFalse(no_flow.use_imbalance)
        self.assertTrue(base.use_boundary_lag)
        self.assertFalse(no_lag.use_boundary_lag)

    def test_phase_is_computed_from_source_minute_not_close_timestamp(self):
        engine = LiquidityStateEngine(EngineConfig.from_mapping(CONFIG))
        self.assertTrue(engine._phase_open(bar(15, 100, 101, 99, 100.5)))
        self.assertFalse(engine._phase_open(bar(16, 100, 101, 99, 100.5)))

    def test_risk_sizing_includes_both_entry_and_stop_cost(self):
        sizing = risk_based_quantity(
            nav=Decimal("100000"),
            risk_fraction=Decimal("0.03"),
            entry_price=Decimal("100"),
            stop_price=Decimal("99"),
            cost_rate_per_fill=Decimal("0.00075"),
            quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(sizing.planned_loss, Decimal("3000"))
        self.assertGreater(sizing.loss_per_unit, Decimal("1"))

    def test_current_opening_is_not_in_its_own_lag_snapshot(self):
        engine = LiquidityStateEngine(EngineConfig.from_mapping(CONFIG))
        for minute in range(0, 241):
            source = minute
            opening = source % 15 == 0
            close = 100.2 if opening else 100.0
            result = engine.on_bar(bar(source, 100.0, 100.4, 99.8, close))
            if source == 240:
                events = result.events
                self.assertTrue(events)
                lag_signs = tuple(events[-1].details.get("lag_signs", ()))
                self.assertEqual(len(lag_signs), 4)
                self.assertEqual(lag_signs, (1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
