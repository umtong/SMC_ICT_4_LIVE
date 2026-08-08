from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from logic import BarObs, Direction, Scenario, TradePlan

from c10_v44_overlay import reframe_primary_target


def bar(ts: int, *, high: float, low: float, close: float) -> BarObs:
    open_ = close
    return BarObs(
        ts_ns=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        taker_buy_volume=50.0,
    )


def plan(
    *,
    direction: Direction = Direction.LONG,
    entry: float = 100.0,
    stop: float = 99.0,
    target: float = 110.0,
    observed: int = 500,
) -> TradePlan:
    reward = target - entry if direction == Direction.LONG else entry - target
    return TradePlan(
        scenario_id="TEST-SCENARIO",
        scenario=Scenario.FAR,
        direction=direction,
        observed_ts_ns=observed,
        expected_entry=entry,
        stop_price=stop,
        target_price=target,
        atr=1.0,
        loss_per_unit=1.0,
        gain_per_unit=reward,
        net_r=reward,
        reason_code="BASELINE",
        expire_ts_ns=observed + 12,
        details={"confirmation_close": 100.2},
    )


def logic(
    *,
    highs: list[tuple[int, int, float]] | None = None,
    lows: list[tuple[int, int, float]] | None = None,
    bars: list[BarObs] | None = None,
    minimum_r: float = 1.25,
) -> SimpleNamespace:
    return SimpleNamespace(
        internal_highs=highs or [],
        internal_lows=lows or [],
        bars=bars or [],
        config=SimpleNamespace(
            effective_maker_rate=0.0,
            min_net_r=minimum_r,
        ),
    )


class V44TargetHierarchyTest(unittest.TestCase):
    def test_source_equilibrium_cell_is_exactly_unchanged(self) -> None:
        baseline = plan()
        with patch.dict(
            "os.environ",
            {"C10_V44_PRIMARY_TARGET_MODE": "SOURCE_EQUILIBRIUM"},
        ):
            decision = reframe_primary_target(baseline, logic())
        self.assertTrue(decision.approved)
        self.assertIs(decision.plan, baseline)
        self.assertFalse(decision.details["applied"])

    def test_long_skips_consumed_and_subminimum_levels(self) -> None:
        baseline = plan()
        market = logic(
            highs=[
                (50, 100, 100.5),
                (60, 110, 101.0),
                (70, 120, 103.0),
            ],
            bars=[
                bar(100, high=100.4, low=99.8, close=100.0),
                bar(150, high=100.6, low=99.9, close=100.3),
                bar(200, high=100.9, low=100.0, close=100.5),
            ],
        )
        with patch.dict(
            "os.environ",
            {
                "C10_V44_PRIMARY_TARGET_MODE": (
                    "PRECONFIRMED_INTERNAL_LIQUIDITY"
                ),
            },
        ):
            decision = reframe_primary_target(baseline, market)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.plan.target_price, 103.0)
        self.assertEqual(decision.plan.net_r, 3.0)
        details = decision.plan.details["source_target_hierarchy"]
        self.assertEqual(details["selected_internal_liquidity"], 103.0)
        evaluated = details["evaluated_candidates"]
        self.assertTrue(evaluated[0]["delivered_after_confirmation"])
        self.assertFalse(evaluated[1]["cost_qualified"])
        self.assertTrue(evaluated[2]["cost_qualified"])

    def test_pivot_must_be_known_strictly_before_plan_observation(self) -> None:
        baseline = plan(observed=500)
        market = logic(highs=[(400, 500, 104.0)])
        with patch.dict(
            "os.environ",
            {
                "C10_V44_PRIMARY_TARGET_MODE": (
                    "PRECONFIRMED_INTERNAL_LIQUIDITY"
                ),
            },
        ):
            decision = reframe_primary_target(baseline, market)
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "NO_PRECONFIRMED_INTERNAL_LIQUIDITY_BETWEEN_ENTRY_AND_EQUILIBRIUM",
        )

    def test_short_uses_nearest_live_internal_low(self) -> None:
        baseline = plan(
            direction=Direction.SHORT,
            entry=100.0,
            stop=101.0,
            target=90.0,
        )
        market = logic(
            lows=[(50, 100, 97.5), (60, 110, 95.0)],
            bars=[bar(150, high=100.2, low=98.0, close=99.0)],
        )
        with patch.dict(
            "os.environ",
            {
                "C10_V44_PRIMARY_TARGET_MODE": (
                    "PRECONFIRMED_INTERNAL_LIQUIDITY"
                ),
            },
        ):
            decision = reframe_primary_target(baseline, market)
        self.assertTrue(decision.approved)
        self.assertEqual(decision.plan.target_price, 97.5)
        self.assertEqual(decision.plan.net_r, 2.5)

    def test_internal_cell_rejects_when_every_level_is_consumed(self) -> None:
        baseline = plan()
        market = logic(
            highs=[(50, 100, 103.0)],
            bars=[bar(150, high=103.1, low=100.0, close=102.0)],
        )
        with patch.dict(
            "os.environ",
            {
                "C10_V44_PRIMARY_TARGET_MODE": (
                    "PRECONFIRMED_INTERNAL_LIQUIDITY"
                ),
            },
        ):
            decision = reframe_primary_target(baseline, market)
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "NO_LIVE_COST_QUALIFIED_PRECONFIRMED_INTERNAL_LIQUIDITY",
        )


if __name__ == "__main__":
    unittest.main()
