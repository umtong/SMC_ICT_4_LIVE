from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import os
import unittest
from unittest.mock import patch

from logic import LogicConfig

from c10_v43_overlay import first_favorable_pivot_observation
from c10_v43_overlay import funded_micro_reduction_enabled
from c10_v43_overlay import solve_funded_reduction
from c10_v43_state import FundedMicroRiskTransferEngine


@dataclass(frozen=True)
class Bar:
    ts_ns: int
    high: float
    low: float


class V43FundedMicroTest(unittest.TestCase):
    def test_environment_ablation_is_exact(self) -> None:
        with patch.dict(os.environ, {"C10_V43_FUNDED_MICRO_REDUCTION": "0"}):
            self.assertFalse(funded_micro_reduction_enabled())
        with patch.dict(os.environ, {"C10_V43_FUNDED_MICRO_REDUCTION": "1"}):
            self.assertTrue(funded_micro_reduction_enabled())

    def test_favorable_pivot_must_remain_defended(self) -> None:
        valid = first_favorable_pivot_observation(
            direction="LONG",
            micro_highs=[],
            micro_lows=[(110, 120, 101.0)],
            bars=[Bar(120, 103.0, 101.0), Bar(130, 104.0, 101.2)],
            entry_fill_ts_ns=100,
            observed_ts_ns=130,
            entry_reference=100.0,
            current_price=103.0,
            target_price=108.0,
        )
        broken = first_favorable_pivot_observation(
            direction="LONG",
            micro_highs=[],
            micro_lows=[(110, 120, 101.0)],
            bars=[Bar(120, 103.0, 101.0), Bar(130, 104.0, 100.9)],
            entry_fill_ts_ns=100,
            observed_ts_ns=130,
            entry_reference=100.0,
            current_price=103.0,
            target_price=108.0,
        )
        self.assertIsNotNone(valid)
        self.assertIsNone(broken)

    def test_pivot_can_wait_until_net_gain_funds_residual(self) -> None:
        pivot = first_favorable_pivot_observation(
            direction="SHORT",
            micro_highs=[(110, 120, 99.0)],
            micro_lows=[],
            bars=[Bar(120, 99.0, 98.0), Bar(180, 98.8, 96.0)],
            entry_fill_ts_ns=100,
            observed_ts_ns=180,
            entry_reference=100.0,
            current_price=96.5,
            target_price=90.0,
        )
        self.assertIsNotNone(pivot)
        reduction = solve_funded_reduction(
            direction="SHORT",
            total_quantity=Decimal("10"),
            entry_price=Decimal("100"),
            current_price=Decimal("96.5"),
            original_loss_per_unit=Decimal("2"),
            maker_fee=Decimal("0.0004"),
            taker_fee=Decimal("0.0008"),
            impact_per_side=Decimal("0.10"),
            tick_size=Decimal("0.1"),
            quantity_increment=Decimal("0.1"),
            min_quantity=Decimal("0.1"),
        )
        self.assertIsNotNone(reduction)
        assert reduction is not None
        self.assertGreater(reduction.residual_quantity, Decimal("0"))
        self.assertGreaterEqual(
            reduction.locked_profit,
            reduction.residual_max_loss,
        )
        self.assertNotEqual(reduction.fraction, Decimal("0.5"))

    def test_nonpositive_all_cost_gain_does_nothing(self) -> None:
        reduction = solve_funded_reduction(
            direction="LONG",
            total_quantity=Decimal("10"),
            entry_price=Decimal("100"),
            current_price=Decimal("100.2"),
            original_loss_per_unit=Decimal("2"),
            maker_fee=Decimal("0.0004"),
            taker_fee=Decimal("0.0008"),
            impact_per_side=Decimal("0.10"),
            tick_size=Decimal("0.1"),
            quantity_increment=Decimal("0.1"),
            min_quantity=Decimal("0.1"),
        )
        self.assertIsNone(reduction)


class V43StateEvidenceTest(unittest.TestCase):
    def test_state_records_solved_partial_and_residual(self) -> None:
        engine = FundedMicroRiskTransferEngine(
            LogicConfig(),
            "TEST-PERP.BINANCE",
        )
        engine.active_trade_id = "SCENARIO-1"
        engine.active_trade_state = "POSITION"
        engine.mark_funded_micro_reduction(
            observed_ts_ns=300,
            pivot_event_ts_ns=200,
            direction="SHORT",
            pivot_level=99.0,
            entry_reference=100.0,
            partial_quantity=7.2,
            residual_quantity=2.8,
            locked_profit=5.6,
            residual_max_loss=5.6,
        )
        event = engine.events[-1]
        self.assertEqual(
            event.event_type,
            "FUNDED_MICRO_RISK_TRANSFER_CONFIRMED",
        )
        self.assertEqual(event.event_time_ns, 200)
        self.assertEqual(event.observed_time_ns, 300)
        self.assertEqual(event.next_state, "FUNDED_RESIDUAL_RUNNER")
        self.assertEqual(
            engine.active_trade_state,
            "FUNDED_RESIDUAL_RUNNER",
        )
        self.assertEqual(event.details["partial_quantity"], 7.2)
        self.assertEqual(event.details["residual_quantity"], 2.8)


if __name__ == "__main__":
    unittest.main()
