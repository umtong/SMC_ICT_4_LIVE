from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from downloader import selected_weeks_from_seed, validate_frozen_selection
from lrae import BarSnapshot, LiquidityReactionEngine, risk_quantity, planned_loss_per_unit


CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))


def make_bar(
    i: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    taker_buy: float = 50.0,
) -> BarSnapshot:
    return BarSnapshot(
        ts_ns=(i + 1) * 60_000_000_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=taker_buy,
        trades=100,
    )


class SelectionTests(unittest.TestCase):
    def test_frozen_weeks_reproduce_seed(self) -> None:
        expected = ["2024-10-14", "2024-05-13", "2025-01-13"]
        self.assertEqual(selected_weeks_from_seed(CONFIG["selection"]), expected)
        validate_frozen_selection(CONFIG)


class RiskTests(unittest.TestCase):
    def test_cost_aware_quantity_never_exceeds_three_percent_nav(self) -> None:
        nav = 100_000.0
        quantity = risk_quantity(
            nav=nav,
            risk_fraction=0.03,
            entry=60_000.0,
            stop=59_700.0,
            fee_rate_per_side=0.00065,
            size_increment=0.001,
        )
        loss = quantity * planned_loss_per_unit(60_000.0, 59_700.0, 0.00065)
        self.assertLessEqual(loss, nav * 0.03 + 1e-9)
        self.assertGreater(quantity, 0.0)


class StateMachineTests(unittest.TestCase):
    def _warmed_engine(self, *, variant: str = "base") -> tuple[LiquidityReactionEngine, int]:
        engine = LiquidityReactionEngine(CONFIG, variant=variant)
        for i in range(140):
            phase = i % 20
            close = 100.0 + phase * (5.0 / 19.0)
            engine.observe(
                make_bar(
                    i,
                    open_=close - 0.1,
                    high=close + 0.4,
                    low=close - 0.4,
                    close=close,
                )
            )
        return engine, 140

    def test_lower_sweep_absorption_reclaim_emits_long_plan(self) -> None:
        engine, i = self._warmed_engine()
        transitions, plan = engine.observe(
            make_bar(
                i,
                open_=100.2,
                high=100.5,
                low=99.2,
                close=99.9,
                volume=180.0,
                taker_buy=15.0,
            )
        )
        self.assertIsNone(plan)
        self.assertTrue(any(item.next_state == "BREACHED" for item in transitions))

        transitions, plan = engine.observe(
            make_bar(
                i + 1,
                open_=100.0,
                high=100.8,
                low=99.9,
                close=100.4,
                volume=150.0,
                taker_buy=120.0,
            )
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, "long")
        self.assertEqual(plan.scenario_type, "absorption_reclaim_reversal")
        self.assertLess(plan.stop, plan.entry)
        self.assertGreater(plan.target, plan.entry)
        self.assertGreaterEqual(plan.expected_net_rr, CONFIG["min_expected_net_rr"])
        self.assertTrue(any(item.next_state == "RECLAIMED" for item in transitions))

    def test_two_sided_breach_is_not_traded(self) -> None:
        engine, i = self._warmed_engine()
        transitions, plan = engine.observe(
            make_bar(
                i,
                open_=102.0,
                high=107.0,
                low=98.0,
                close=102.0,
                volume=250.0,
                taker_buy=125.0,
            )
        )
        self.assertEqual(transitions, [])
        self.assertIsNone(plan)

    def test_observed_times_never_precede_pivot_event_times(self) -> None:
        local = dict(CONFIG)
        local["min_history"] = 5
        engine = LiquidityReactionEngine(local)
        bars = [
            make_bar(0, open_=100, high=101, low=99, close=100),
            make_bar(1, open_=100, high=102, low=99.5, close=101),
            make_bar(2, open_=101, high=105, low=100, close=104),
            make_bar(3, open_=104, high=104.5, low=101, close=102),
            make_bar(4, open_=102, high=103, low=100, close=101),
        ]
        for bar in bars:
            engine.observe(bar)
        self.assertTrue(engine.pivot_highs)
        _, event_ts, _ = engine.pivot_highs[-1]
        self.assertEqual(event_ts, bars[2].ts_ns)
        self.assertGreaterEqual(bars[4].ts_ns, event_ts)


if __name__ == "__main__":
    unittest.main()
