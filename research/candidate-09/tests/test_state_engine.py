from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_loader import ONE_MINUTE_NS, parse_archive
from state_engine import (
    EngineConfig,
    FlowBar,
    LiquidityPool,
    LiquidityStateEngine,
    risk_based_quantity,
)


def bar(
    index: int,
    *,
    open_: float = 100.0,
    high: float = 100.4,
    low: float = 99.6,
    close: float = 100.0,
    volume: float = 100.0,
    buy: float = 50.0,
) -> FlowBar:
    return FlowBar(
        ts_ns=(index + 1) * ONE_MINUTE_NS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        taker_buy_volume=buy,
        trade_count=100,
    )


def compact_config(**overrides: object) -> EngineConfig:
    values: dict[str, object] = {
        "pivot_left_bars": 2,
        "pivot_right_bars": 2,
        "atr_period": 3,
        "volume_period": 3,
        "minimum_pivot_prominence_atr": 0.10,
        "maximum_active_pools_per_side": 12,
        "minimum_breach_atr": 0.02,
        "reclaim_buffer_atr": 0.01,
        "acceptance_buffer_atr": 0.05,
        "acceptance_closes": 2,
        "timeout_bars": 6,
        "retest_timeout_bars": 4,
        "retest_tolerance_atr": 0.25,
        "stop_buffer_atr": 0.10,
        "directional_imbalance": 0.10,
        "opposite_confirmation": -0.01,
        "minimum_volume_ratio": 0.50,
        "minimum_displacement_atr": 0.20,
        "absorption_max_progress_atr": 0.20,
        "absorption_min_wick_atr": 0.20,
        "minimum_net_reward_to_risk": 1.0,
        "composite_cost_per_fill": 0.00075,
        "cooldown_bars": 1,
    }
    values.update(overrides)
    return EngineConfig(**values)


class RiskSizingTest(unittest.TestCase):
    def test_full_cost_and_floor_keep_planned_loss_below_three_percent(self) -> None:
        result = risk_based_quantity(
            nav=Decimal("100000"),
            risk_fraction=Decimal("0.03"),
            entry_price=Decimal("50000"),
            stop_price=Decimal("49500"),
            cost_rate_per_fill=Decimal("0.00075"),
            quantity_increment=Decimal("0.001"),
        )
        self.assertLessEqual(result.planned_loss, Decimal("3000"))
        self.assertGreater(result.quantity, Decimal("0"))
        next_quantity = result.quantity + Decimal("0.001")
        self.assertGreater(next_quantity * result.per_unit_expected_loss, Decimal("3000"))

    def test_risk_above_three_percent_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            risk_based_quantity(
                nav=Decimal("100000"),
                risk_fraction=Decimal("0.03001"),
                entry_price=Decimal("50000"),
                stop_price=Decimal("49500"),
                cost_rate_per_fill=Decimal("0.00075"),
                quantity_increment=Decimal("0.001"),
            )


class CausalStructureTest(unittest.TestCase):
    def test_pivot_is_observed_only_after_right_confirmation_bars(self) -> None:
        engine = LiquidityStateEngine(compact_config())
        sequence = [
            bar(0, high=100.5, low=99.5),
            bar(1, high=101.0, low=99.6),
            bar(2, high=103.0, low=100.0, close=101.0),
            bar(3, high=101.5, low=99.8),
            bar(4, high=101.0, low=99.7),
        ]
        results = [engine.on_bar(item) for item in sequence]
        high_events = [
            event
            for result in results
            for event in result.events
            if event.reason_code == "CONFIRMED_HIGH_PIVOT"
        ]
        self.assertEqual(len(high_events), 1)
        event = high_events[0]
        self.assertEqual(event.event_time_ns, sequence[2].ts_ns)
        self.assertEqual(event.observed_time_ns, sequence[4].ts_ns)
        self.assertGreater(event.observed_time_ns, event.event_time_ns)

    def test_same_bar_two_sided_breach_is_no_trade(self) -> None:
        engine = LiquidityStateEngine(compact_config())
        for index in range(8):
            engine.on_bar(bar(index))
        engine._pools["HIGH"] = [LiquidityPool("h", "HIGH", 101.0, 1, 1, 0)]
        engine._pools["LOW"] = [LiquidityPool("l", "LOW", 99.0, 1, 1, 0)]
        result = engine.on_bar(bar(8, open_=100.0, high=102.0, low=98.0, close=100.0, volume=250.0, buy=125.0))
        self.assertIsNone(result.signal)
        self.assertTrue(any(event.event_type == "AMBIGUOUS_BREACH" for event in result.events))
        self.assertIsNone(engine._pending)


class StatePathTest(unittest.TestCase):
    def _warm(self, engine: LiquidityStateEngine, count: int = 8) -> None:
        for index in range(count):
            engine.on_bar(bar(index, high=100.3 + (index % 2) * 0.05, low=99.7, close=100.0))
        engine._pools["HIGH"] = [
            LiquidityPool("breach-high", "HIGH", 101.0, 1, 1, 0),
            LiquidityPool("target-high", "HIGH", 104.0, 1, 1, 0),
        ]
        engine._pools["LOW"] = [LiquidityPool("target-low", "LOW", 98.0, 1, 1, 0)]

    def test_absorption_reclaim_failed_retest_emits_short(self) -> None:
        engine = LiquidityStateEngine(compact_config())
        self._warm(engine)
        breach = engine.on_bar(
            bar(8, open_=100.0, high=101.55, low=99.9, close=100.85, volume=220.0, buy=170.0),
        )
        self.assertIsNone(breach.signal)
        self.assertTrue(any(event.event_type == "RANGE_RECLAIM" for event in breach.events))
        confirmation = engine.on_bar(
            bar(9, open_=100.85, high=100.95, low=100.25, close=100.35, volume=130.0, buy=50.0),
        )
        self.assertIsNotNone(confirmation.signal)
        assert confirmation.signal is not None
        self.assertEqual(confirmation.signal.branch, "REVERSAL")
        self.assertEqual(confirmation.signal.side, "SELL")
        self.assertLess(confirmation.signal.target_price, confirmation.signal.entry_reference)
        self.assertGreater(confirmation.signal.stop_price, confirmation.signal.entry_reference)

    def test_depletion_acceptance_defended_retest_emits_long(self) -> None:
        engine = LiquidityStateEngine(compact_config())
        self._warm(engine)
        first = engine.on_bar(
            bar(8, open_=100.0, high=101.45, low=99.95, close=101.30, volume=190.0, buy=150.0),
        )
        self.assertIsNone(first.signal)
        second = engine.on_bar(
            bar(9, open_=101.30, high=101.75, low=101.20, close=101.65, volume=180.0, buy=145.0),
        )
        self.assertTrue(any(event.event_type == "OUTSIDE_ACCEPTANCE" for event in second.events))
        third = engine.on_bar(
            bar(10, open_=101.65, high=101.70, low=101.03, close=101.25, volume=120.0, buy=60.0),
        )
        self.assertIsNotNone(third.signal)
        assert third.signal is not None
        self.assertEqual(third.signal.branch, "CONTINUATION")
        self.assertEqual(third.signal.side, "BUY")
        self.assertLess(third.signal.stop_price, third.signal.entry_reference)
        self.assertGreater(third.signal.target_price, third.signal.entry_reference)


class BinanceArchiveTest(unittest.TestCase):
    def test_header_and_millisecond_timestamp_are_parsed_causally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.zip"
            csv_text = (
                "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote,ignore\n"
                "1640995200000,47000,47100,46900,47050,10,1640995259999,0,100,6,0,0\n"
            )
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("sample.csv", csv_text)
            bars = parse_archive(path)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].ts_ns, 1640995200000 * 1_000_000 + ONE_MINUTE_NS)
        self.assertAlmostEqual(bars[0].flow_imbalance, 0.2)


class FrozenConfigTest(unittest.TestCase):
    def test_baseline_and_ablation_are_explicit(self) -> None:
        payload = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        baseline = EngineConfig.from_mapping(payload, ablation="baseline")
        no_flow = EngineConfig.from_mapping(payload, ablation="no-flow")
        self.assertTrue(baseline.use_flow_confirmation)
        self.assertFalse(no_flow.use_flow_confirmation)
        self.assertEqual(baseline.pivot_left_bars, no_flow.pivot_left_bars)
        with self.assertRaises(ValueError):
            EngineConfig.from_mapping(payload, ablation="optimizer-created-variant")


if __name__ == "__main__":
    unittest.main()
