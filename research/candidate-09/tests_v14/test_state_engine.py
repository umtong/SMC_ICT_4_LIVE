from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from state_engine_v14_direct import (
    AuctionLevel,
    EngineConfig,
    FlowBar,
    LiquidityStateEngine,
    PendingResolution,
    risk_based_quantity,
)

CONFIG = json.loads((Path(__file__).resolve().parents[1] / "config_v14.json").read_text())


def bar(ts: int, o: float, h: float, l: float, c: float, buy: float) -> FlowBar:
    return FlowBar(ts, o, h, l, c, 100.0, 100.0 * buy, 100)


def pending(direction: str, *, extreme: float) -> PendingResolution:
    kind = "HIGH" if direction == "UP" else "LOW"
    level = AuctionLevel("x", kind, 100.0, 60, 0, 60, 120.0, 80.0, 90.0, 40.0, 0)
    return PendingResolution(
        direction.lower(), level, direction, "ACCEPTED", 1, 0.3,
        0.2 if direction == "UP" else -0.2, 1, extreme,
        outside_closes=2, acceptance_index=2,
    )


class V14ContractTest(unittest.TestCase):
    def test_ablation_mapping_changes_only_the_declared_layer(self):
        base = EngineConfig.from_mapping(CONFIG, ablation="baseline")
        accepted = EngineConfig.from_mapping(CONFIG, ablation="accepted-extreme-stop")
        salvage = EngineConfig.from_mapping(CONFIG, ablation="salvage-only")
        no_flow = EngineConfig.from_mapping(CONFIG, ablation="no-flow")
        self.assertTrue(base.boundary_stop_all_reversals)
        self.assertFalse(accepted.boundary_stop_all_reversals)
        self.assertFalse(accepted.enable_boundary_stop_salvage)
        self.assertFalse(salvage.boundary_stop_all_reversals)
        self.assertTrue(salvage.enable_boundary_stop_salvage)
        self.assertTrue(no_flow.boundary_stop_all_reversals)
        self.assertFalse(no_flow.use_flow_confirmation)
        self.assertTrue(base.use_flow_confirmation)
        self.assertEqual(base.auction_horizons_minutes, (15, 60, 1440))
        self.assertFalse(base.enable_continuation_entries)

    def test_baseline_uses_failed_boundary_invalidation_for_tradeable_reversal(self):
        engine = LiquidityStateEngine(EngineConfig(
            minimum_net_reward_to_risk=0.5,
            composite_cost_per_fill=0.0,
            boundary_stop_all_reversals=True,
        ))
        engine._atr = 2.0
        signal = engine._build_signal(
            pending("UP", extreme=101.0),
            bar(1, 99.0, 99.5, 97.5, 98.0, 0.25),
            branch="REVERSAL",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.reason_code, "ACCEPTED_BREAKOUT_FAILURE_WITH_BOUNDARY_REACCEPTANCE_INVALIDATION")
        self.assertTrue(engine._boundary_diagnostic["boundary_stop_all_controlled_ablation"])

    def test_accepted_extreme_control_restores_v10_signal(self):
        engine = LiquidityStateEngine(EngineConfig(
            minimum_net_reward_to_risk=0.5,
            composite_cost_per_fill=0.0,
            enable_boundary_stop_salvage=False,
            boundary_stop_all_reversals=False,
        ))
        engine._atr = 2.0
        signal = engine._build_signal(
            pending("UP", extreme=101.0),
            bar(1, 99.0, 99.5, 97.5, 98.0, 0.25),
            branch="REVERSAL",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.reason_code, "ACCEPTED_BREAKOUT_FAILED_AND_TRAPPED_PARTICIPANTS")
        self.assertFalse(engine._boundary_attempted)

    def test_salvage_only_recovers_wide_accepted_excursion_stop(self):
        engine = LiquidityStateEngine(EngineConfig(
            minimum_net_reward_to_risk=1.2,
            composite_cost_per_fill=0.00075,
            enable_boundary_stop_salvage=True,
            boundary_stop_all_reversals=False,
        ))
        engine._atr = 2.0
        signal = engine._build_signal(
            pending("DOWN", extreme=80.0),
            bar(1, 100.0, 101.0, 99.5, 100.8, 0.75),
            branch="REVERSAL",
        )
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertGreater(signal.stop_price, 80.0)
        self.assertEqual(signal.target_price, 120.0)
        self.assertGreaterEqual(signal.net_reward_to_risk, 1.2)

    def test_full_cost_three_percent_sizing(self):
        result = risk_based_quantity(
            nav=Decimal("100000"), risk_fraction=Decimal("0.03"),
            entry_price=Decimal("50000"), stop_price=Decimal("49950"),
            cost_rate_per_fill=Decimal("0.00075"), quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(result.planned_loss, Decimal("3000"))
        self.assertGreater(result.per_unit_expected_loss, Decimal("50"))
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal("100000"), risk_fraction=Decimal("0.030001"),
                entry_price=Decimal("50000"), stop_price=Decimal("49950"),
                cost_rate_per_fill=Decimal("0.00075"), quantity_increment=Decimal("0.001"),
            )


if __name__ == "__main__":
    unittest.main()
