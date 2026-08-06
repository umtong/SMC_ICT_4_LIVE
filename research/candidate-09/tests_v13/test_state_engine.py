from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine_v13_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config_v13.json").read_text())


def bar(ts: int, o: float, h: float, l: float, c: float, *, buy_fraction: float = 0.5) -> FlowBar:
    return FlowBar(ts, o, h, l, c, 100.0, 100.0 * buy_fraction, 100)


def level(kind: str = "HIGH", midpoint: float = 90.0) -> AuctionLevel:
    return AuctionLevel("x", kind, 100.0, 60, 0, 60, 120.0, 80.0, midpoint, 40.0, 0)


def pending_up(*, extreme: float = 105.0) -> PendingResolution:
    return PendingResolution(
        "up",
        level("HIGH", 90.0),
        "UP",
        "ACCEPTED",
        1,
        0.3,
        0.2,
        1,
        extreme,
        outside_closes=2,
        acceptance_index=2,
    )


def pending_down(*, extreme: float = 90.0) -> PendingResolution:
    return PendingResolution(
        "down",
        level("LOW", 90.0),
        "DOWN",
        "ACCEPTED",
        1,
        0.3,
        -0.2,
        1,
        extreme,
        outside_closes=2,
        acceptance_index=2,
    )


class ConfigContractTest(unittest.TestCase):
    def test_single_variable_ablations(self):
        base = EngineConfig.from_mapping(CONFIG, ablation="baseline")
        disabled = EngineConfig.from_mapping(CONFIG, ablation="no-boundary-stop-salvage")
        floor = EngineConfig.from_mapping(CONFIG, ablation="with-price-risk-floor")
        all_boundary = EngineConfig.from_mapping(CONFIG, ablation="boundary-stop-all")

        self.assertTrue(base.enable_boundary_stop_salvage)
        self.assertFalse(base.boundary_stop_all_reversals)
        self.assertFalse(base.enforce_price_risk_floor)
        self.assertFalse(disabled.enable_boundary_stop_salvage)
        self.assertTrue(floor.enforce_price_risk_floor)
        self.assertTrue(all_boundary.boundary_stop_all_reversals)
        self.assertEqual(base.auction_horizons_minutes, (15, 60, 1440))
        self.assertFalse(base.enable_continuation_entries)


class BoundaryInvalidationTest(unittest.TestCase):
    @staticmethod
    def engine(config: EngineConfig) -> LiquidityStateEngine:
        engine = LiquidityStateEngine(config)
        engine._atr = 2.0
        return engine

    def test_valid_v10_signal_is_preserved_in_baseline(self):
        engine = self.engine(
            EngineConfig(
                minimum_net_reward_to_risk=0.5,
                composite_cost_per_fill=0.0,
                enable_boundary_stop_salvage=True,
            ),
        )
        signal = engine._build_signal(
            pending_up(extreme=101.0),
            bar(1, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25),
            branch="REVERSAL",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.reason_code, "ACCEPTED_BREAKOUT_FAILED_AND_TRAPPED_PARTICIPANTS")
        self.assertFalse(engine._boundary_attempted)

    def test_rejected_accepted_extreme_stop_can_use_boundary_invalidation(self):
        engine = self.engine(
            EngineConfig(
                minimum_net_reward_to_risk=1.2,
                composite_cost_per_fill=0.00075,
                enable_boundary_stop_salvage=True,
            ),
        )
        # The accepted-extreme stop below 80 is too wide. The failed-boundary
        # invalidation is just below the 99.5 failure-bar low and remains causal.
        signal = engine._build_signal(
            pending_down(extreme=80.0),
            bar(1, 100.0, 101.0, 99.5, 100.8, buy_fraction=0.75),
            branch="REVERSAL",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.side, "BUY")
        self.assertGreater(signal.stop_price, 80.0)
        self.assertLess(signal.stop_price, signal.entry_reference)
        self.assertEqual(signal.target_price, 120.0)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)
        self.assertEqual(signal.reason_code, "ACCEPTED_BREAKOUT_FAILURE_WITH_BOUNDARY_REACCEPTANCE_INVALIDATION")

    def test_no_salvage_is_exact_v10_rejection(self):
        engine = self.engine(
            EngineConfig(
                minimum_net_reward_to_risk=1.2,
                composite_cost_per_fill=0.00075,
                enable_boundary_stop_salvage=False,
            ),
        )
        signal = engine._build_signal(
            pending_down(extreme=80.0),
            bar(1, 100.0, 101.0, 99.5, 100.8, buy_fraction=0.75),
            branch="REVERSAL",
        )
        self.assertIsNone(signal)
        self.assertFalse(engine._boundary_attempted)

    def test_price_risk_floor_ablation_only_rejects_small_price_move(self):
        base = LiquidityStateEngine(
            EngineConfig(
                minimum_net_reward_to_risk=0.5,
                composite_cost_per_fill=0.00075,
                enforce_price_risk_floor=False,
            ),
        )
        floor = LiquidityStateEngine(
            EngineConfig(
                minimum_net_reward_to_risk=0.5,
                composite_cost_per_fill=0.00075,
                enforce_price_risk_floor=True,
            ),
        )
        base._atr = floor._atr = 0.1
        current = bar(1, 100.0, 100.01, 99.90, 99.95, buy_fraction=0.25)
        signal_base, _ = base._build_boundary_stop_signal(pending_up(extreme=105.0), current)
        signal_floor, diagnostic = floor._build_boundary_stop_signal(pending_up(extreme=105.0), current)

        self.assertIsNotNone(signal_base)
        self.assertIsNone(signal_floor)
        self.assertEqual(
            diagnostic["rejection_reason"],
            "BOUNDARY_STOP_PRICE_RISK_BELOW_REDUNDANT_COST_FLOOR",
        )

    def test_boundary_stop_all_changes_even_tradeable_v10_signal(self):
        engine = self.engine(
            EngineConfig(
                minimum_net_reward_to_risk=0.5,
                composite_cost_per_fill=0.0,
                boundary_stop_all_reversals=True,
            ),
        )
        signal = engine._build_signal(
            pending_up(extreme=101.0),
            bar(1, 99.0, 99.5, 97.5, 98.0, buy_fraction=0.25),
            branch="REVERSAL",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.reason_code, "ACCEPTED_BREAKOUT_FAILURE_WITH_BOUNDARY_REACCEPTANCE_INVALIDATION")
        self.assertTrue(engine._boundary_attempted)
        self.assertTrue(engine._boundary_diagnostic["boundary_stop_all_controlled_ablation"])


class RiskSizingTest(unittest.TestCase):
    def test_full_cost_floor_respects_three_percent(self):
        result = risk_based_quantity(
            nav=Decimal("100000"),
            risk_fraction=Decimal("0.03"),
            entry_price=Decimal("50000"),
            stop_price=Decimal("49950"),
            cost_rate_per_fill=Decimal("0.00075"),
            quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(result.planned_loss, Decimal("3000"))
        self.assertGreater(result.per_unit_expected_loss, Decimal("50"))

    def test_above_three_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal("100000"),
                risk_fraction=Decimal("0.030001"),
                entry_price=Decimal("50000"),
                stop_price=Decimal("49950"),
                cost_rate_per_fill=Decimal("0.00075"),
                quantity_increment=Decimal("0.001"),
            )


if __name__ == "__main__":
    unittest.main()
