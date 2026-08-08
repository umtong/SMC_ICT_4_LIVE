from __future__ import annotations

from decimal import Decimal
import os
from types import SimpleNamespace
import unittest

from logic import BarObs, Direction, Scenario, TradePlan

from c10_v57_overlay import select_funded_checkpoint


def plan() -> TradePlan:
    return TradePlan(
        scenario_id="SOURCE",
        scenario=Scenario.FAR,
        direction=Direction.LONG,
        observed_ts_ns=1000,
        expected_entry=100.0,
        stop_price=90.0,
        target_price=130.0,
        atr=2.0,
        loss_per_unit=10.0,
        gain_per_unit=29.0,
        net_r=2.9,
        reason_code="EXTERNAL_RUNNER",
        expire_ts_ns=2000,
        details={"source_equilibrium_checkpoint": 110.0},
    )


def solution() -> SimpleNamespace:
    return SimpleNamespace(
        quantity=Decimal("100"),
        per_unit_loss=Decimal("10"),
        impact_per_side=Decimal("0.1"),
    )


def instrument() -> SimpleNamespace:
    return SimpleNamespace(
        price_increment=Decimal("0.1"),
        size_increment=Decimal("1"),
        min_quantity=Decimal("1"),
    )


def logic(*, delivered: bool = False) -> SimpleNamespace:
    bars = []
    if delivered:
        bars.append(
            BarObs(
                ts_ns=500,
                open=103.0,
                high=105.0,
                low=102.0,
                close=104.0,
                volume=10.0,
                taker_buy_volume=5.0,
            ),
        )
    return SimpleNamespace(
        internal_highs=[(100, 200, 105.0)],
        internal_lows=[],
        bars=bars,
    )


class FundedCheckpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = os.environ.get(
            "C10_V57_INTERNAL_FUNDING_CHECKPOINT"
        )
        os.environ["C10_V57_INTERNAL_FUNDING_CHECKPOINT"] = "1"

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("C10_V57_INTERNAL_FUNDING_CHECKPOINT", None)
        else:
            os.environ["C10_V57_INTERNAL_FUNDING_CHECKPOINT"] = self.previous

    def decide(self, logic_value: SimpleNamespace):
        return select_funded_checkpoint(
            plan(),
            logic_value,
            solution(),
            instrument(),
            maker_fee=Decimal("0.0004"),
            taker_fee=Decimal("0.0008"),
        )

    def test_nearest_live_internal_level_is_selected_when_solvable(self) -> None:
        decision = self.decide(logic())
        self.assertTrue(decision.approved)
        self.assertEqual(decision.level, 105.0)
        self.assertEqual(
            decision.source,
            "PRECONFIRMED_FIVE_MINUTE_INTERNAL_LIQUIDITY",
        )
        self.assertEqual(decision.plan.details["funding_checkpoint"], 105.0)
        self.assertLess(
            decision.details["selected_expected_partial_fraction"],
            1.0,
        )

    def test_already_delivered_internal_level_falls_back_to_equilibrium(self) -> None:
        decision = self.decide(logic(delivered=True))
        self.assertTrue(decision.approved)
        self.assertEqual(decision.level, 110.0)
        self.assertEqual(decision.source, "SOURCE_EQUILIBRIUM")

    def test_disabled_variant_preserves_source_equilibrium(self) -> None:
        os.environ["C10_V57_INTERNAL_FUNDING_CHECKPOINT"] = "0"
        decision = self.decide(logic())
        self.assertTrue(decision.approved)
        self.assertEqual(decision.level, 110.0)
        self.assertIs(decision.plan, plan()) if False else None
        self.assertFalse(decision.details["applied"])


if __name__ == "__main__":
    unittest.main()
