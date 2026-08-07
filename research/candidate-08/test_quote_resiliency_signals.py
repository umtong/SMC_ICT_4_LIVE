"""Pure state-machine contracts for external-liquidity quote resiliency."""

from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from quote_resiliency_features_v3 import QuoteResiliencyConfig
from quote_resiliency_signals import (
    CONTINUATION_FAMILY,
    REVERSAL_FAMILY,
    SIGNAL_REVISION,
    build_quote_resiliency_signals,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


class QuoteResiliencySignalContracts(unittest.TestCase):
    @staticmethod
    def _bar(index: int, timestamp: str) -> FiveMinuteBar:
        ts = pd.Timestamp(timestamp)
        return FiveMinuteBar(
            index=index,
            ts_event_ns=int(ts.as_unit("ns").value),
            open=99.5,
            high=100.5,
            low=99.0,
            close=100.0,
            volume=100.0,
            trade_count=100.0,
            taker_buy_volume=50.0,
            imbalance=0.0,
            atr=1.0,
            volume_ratio=1.0,
            trade_ratio=1.0,
            efficiency_60m=0.0,
            direction_60m=0.0,
            session_key="s",
            day_key="d",
            week_key="w",
        )

    @classmethod
    def _context(cls):
        bars = (
            cls._bar(0, "2023-10-15T00:00:00Z"),
            cls._bar(1, "2023-10-15T00:05:00Z"),
            cls._bar(2, "2023-10-15T00:10:00Z"),
        )
        times = np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64)
        levels = (
            ExternalLevel(
                level_id="day-high-100",
                kind=LevelKind.HIGH,
                source=LevelSource.DAY,
                level=100.0,
                formed_index=-1,
                formed_time_ns=0,
                period_key="prior-day",
            ),
            ExternalLevel(
                level_id="day-high-102",
                kind=LevelKind.HIGH,
                source=LevelSource.DAY,
                level=102.0,
                formed_index=-1,
                formed_time_ns=0,
                period_key="older-day",
            ),
            ExternalLevel(
                level_id="day-low-98",
                kind=LevelKind.LOW,
                source=LevelSource.DAY,
                level=98.0,
                formed_index=-1,
                formed_time_ns=0,
                period_key="older-day",
            ),
        )
        snapshots = (levels, levels, levels)
        return times, bars, snapshots

    @staticmethod
    def _frame(rows: list[dict[str, float]]) -> pd.DataFrame:
        index = pd.date_range("2023-10-15T00:05:10Z", periods=len(rows), freq="10s")
        defaults = {
            "open": 99.8,
            "high": 99.9,
            "low": 99.7,
            "close": 99.8,
            "aggressive_pressure_ratio": 0.0,
            "quote_ofi_ratio": 0.0,
            "quote_ofi_qty": 0.0,
            "bid_add_qty": 0.0,
            "bid_remove_qty": 0.0,
            "ask_add_qty": 0.0,
            "ask_remove_qty": 0.0,
            "spread_median_ratio": 1.0,
            "quote_resiliency_observable": True,
        }
        materialized: list[dict[str, float]] = []
        for row in rows:
            item = {**defaults, **row}
            item.setdefault("bid_close", float(item["close"]) - 0.1)
            item.setdefault("ask_close", float(item["close"]) + 0.1)
            materialized.append(item)
        return pd.DataFrame(materialized, index=index)

    @classmethod
    def _run(
        cls,
        data: pd.DataFrame,
        *,
        quote_gate: bool = True,
        minimum_rr: float = 1.0,
    ):
        times, bars, snapshots = cls._context()
        return build_quote_resiliency_signals(
            data=data,
            context_times=times,
            context_bars=bars,
            snapshots=snapshots,
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            tick=0.1,
            fee_rate=0.0,
            minimum_net_reward_risk=minimum_rr,
            config=QuoteResiliencyConfig(),
            quote_ofi_confirmation_required=quote_gate,
        )

    @staticmethod
    def _signals(bundle):
        return [signal for items in bundle.signals_by_time_ns.values() for signal in items]

    def test_quote_replenished_high_sweep_emits_short_reversal(self) -> None:
        data = self._frame(
            [
                {},
                {
                    "open": 99.8,
                    "high": 100.3,
                    "low": 99.8,
                    "close": 100.1,
                    "aggressive_pressure_ratio": 1.2,
                },
                {
                    "open": 100.1,
                    "high": 100.2,
                    "low": 99.7,
                    "close": 99.9,
                    "ask_add_qty": 10.0,
                    "ask_remove_qty": 2.0,
                    "quote_ofi_qty": -8.0,
                    "quote_ofi_ratio": -0.2,
                },
                {
                    "open": 99.9,
                    "high": 99.95,
                    "low": 99.5,
                    "close": 99.6,
                    "aggressive_pressure_ratio": -0.7,
                    "quote_ofi_ratio": -0.5,
                    "quote_ofi_qty": -5.0,
                },
            ]
        )
        bundle = self._run(data)
        signals = self._signals(bundle)
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.scenario_family, REVERSAL_FAMILY)
        self.assertEqual(signal.direction_name, "SHORT")
        self.assertEqual(signal.boundary_id, "day-high-100")
        self.assertEqual(signal.target_id, "day-low-98")
        self.assertAlmostEqual(signal.entry_reference, 99.5)
        self.assertAlmostEqual(signal.details["trade_confirmation_close"], 99.6)
        self.assertEqual(signal.stop_reference_source, "FULL_SWEEP_RESPONSE_EXTREME")
        self.assertGreater(signal.structural_stop, 100.3)
        self.assertEqual(signal.details["signal_revision"], SIGNAL_REVISION)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_INTERACTED",
                "QUOTE_REPLENISHED_RECLAIM",
                "SCENARIO_CONFIRMED",
            ],
        )

    def test_quote_replenished_low_sweep_emits_long_reversal(self) -> None:
        data = self._frame(
            [
                {"close": 98.2, "open": 98.2, "high": 98.3, "low": 98.1},
                {
                    "open": 98.2,
                    "high": 98.2,
                    "low": 97.7,
                    "close": 97.9,
                    "aggressive_pressure_ratio": -1.3,
                },
                {
                    "open": 97.9,
                    "high": 98.3,
                    "low": 97.8,
                    "close": 98.1,
                    "bid_add_qty": 12.0,
                    "bid_remove_qty": 2.0,
                    "quote_ofi_qty": 10.0,
                    "quote_ofi_ratio": 0.3,
                },
                {
                    "open": 98.1,
                    "high": 98.5,
                    "low": 98.05,
                    "close": 98.4,
                    "aggressive_pressure_ratio": 0.8,
                    "quote_ofi_ratio": 0.5,
                    "quote_ofi_qty": 5.0,
                },
            ]
        )
        bundle = self._run(data)
        signals = self._signals(bundle)
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.scenario_family, REVERSAL_FAMILY)
        self.assertEqual(signal.direction_name, "LONG")
        self.assertEqual(signal.boundary_id, "day-low-98")
        self.assertEqual(signal.target_id, "day-high-100")
        self.assertAlmostEqual(signal.entry_reference, 98.5)
        self.assertAlmostEqual(signal.details["trade_confirmation_close"], 98.4)
        self.assertLess(signal.structural_stop, 97.7)

    def test_quote_withdrawal_acceptance_emits_long_continuation_after_separate_retest(self) -> None:
        data = self._frame(
            [
                {},
                {
                    "open": 99.8,
                    "high": 100.3,
                    "low": 99.8,
                    "close": 100.1,
                    "aggressive_pressure_ratio": 1.2,
                },
                {
                    "open": 100.1,
                    "high": 100.25,
                    "low": 100.05,
                    "close": 100.2,
                    "ask_add_qty": 2.0,
                    "ask_remove_qty": 10.0,
                    "bid_add_qty": 8.0,
                    "bid_remove_qty": 2.0,
                    "quote_ofi_qty": 14.0,
                    "quote_ofi_ratio": 0.4,
                    "spread_median_ratio": 1.1,
                },
                {
                    "open": 100.2,
                    "high": 100.15,
                    "low": 99.98,
                    "close": 100.05,
                    "aggressive_pressure_ratio": 0.4,
                    "quote_ofi_ratio": 0.0,
                },
                {
                    "open": 100.05,
                    "high": 100.4,
                    "low": 100.03,
                    "close": 100.3,
                    "aggressive_pressure_ratio": 0.7,
                    "quote_ofi_ratio": 0.5,
                    "quote_ofi_qty": 5.0,
                },
            ]
        )
        bundle = self._run(data)
        signals = self._signals(bundle)
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.scenario_family, CONTINUATION_FAMILY)
        self.assertEqual(signal.direction_name, "LONG")
        self.assertEqual(signal.target_id, "day-high-102")
        self.assertEqual(signal.stop_reference_source, "FROZEN_RETEST_EXTREME")
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_INTERACTED",
                "QUOTE_WITHDRAWAL_ACCEPTED",
                "LOWER_PRESSURE_RETEST_HELD",
                "SCENARIO_CONFIRMED",
            ],
        )

    def test_confirmation_quote_ofi_ablation_changes_only_the_predeclared_gate(self) -> None:
        data = self._frame(
            [
                {},
                {
                    "high": 100.3,
                    "low": 99.8,
                    "close": 100.1,
                    "aggressive_pressure_ratio": 1.2,
                },
                {
                    "high": 100.2,
                    "low": 99.7,
                    "close": 99.9,
                    "ask_add_qty": 10.0,
                    "ask_remove_qty": 2.0,
                    "quote_ofi_qty": -8.0,
                    "quote_ofi_ratio": -0.2,
                },
                {
                    "high": 99.95,
                    "low": 99.5,
                    "close": 99.6,
                    "aggressive_pressure_ratio": -0.7,
                    "quote_ofi_ratio": 0.5,
                    "quote_ofi_qty": 5.0,
                },
            ]
        )
        base_bundle = self._run(data, quote_gate=True)
        ablation_bundle = self._run(data, quote_gate=False)
        self.assertEqual(len(self._signals(base_bundle)), 0)
        self.assertEqual(len(self._signals(ablation_bundle)), 1)
        self.assertEqual(
            self._signals(ablation_bundle)[0].scenario_family,
            REVERSAL_FAMILY,
        )

    def test_target_crossed_on_confirmation_bucket_is_not_reused(self) -> None:
        data = self._frame(
            [
                {},
                {
                    "high": 100.3,
                    "low": 99.8,
                    "close": 100.1,
                    "aggressive_pressure_ratio": 1.2,
                },
                {
                    "high": 100.2,
                    "low": 99.7,
                    "close": 99.9,
                    "ask_add_qty": 10.0,
                    "ask_remove_qty": 2.0,
                    "quote_ofi_qty": -8.0,
                    "quote_ofi_ratio": -0.2,
                },
                {
                    "high": 99.95,
                    "low": 97.9,
                    "close": 99.6,
                    "aggressive_pressure_ratio": -0.7,
                    "quote_ofi_ratio": -0.5,
                    "quote_ofi_qty": -5.0,
                },
            ]
        )
        bundle = self._run(data)
        self.assertEqual(len(self._signals(bundle)), 0)
        reasons = {item["reason"] for item in bundle.rejected_scenarios}
        self.assertIn("NO_ACTIVE_COMPLETED_EXTERNAL_TARGET", reasons)

    def test_future_rows_do_not_change_existing_signal(self) -> None:
        base = self._frame(
            [
                {},
                {"high": 100.3, "close": 100.1, "aggressive_pressure_ratio": 1.2},
                {
                    "high": 100.2,
                    "low": 99.7,
                    "close": 99.9,
                    "ask_add_qty": 10.0,
                    "ask_remove_qty": 2.0,
                    "quote_ofi_qty": -8.0,
                    "quote_ofi_ratio": -0.2,
                },
                {
                    "high": 99.95,
                    "low": 99.5,
                    "close": 99.6,
                    "aggressive_pressure_ratio": -0.7,
                    "quote_ofi_ratio": -0.5,
                    "quote_ofi_qty": -5.0,
                },
            ]
        )
        first = self._run(base)
        future_index = pd.date_range(base.index[-1] + pd.Timedelta(seconds=10), periods=4, freq="10s")
        future = pd.DataFrame(
            [
                {
                    "open": 1000.0,
                    "high": 2000.0,
                    "low": 1.0,
                    "close": 1500.0,
                    "aggressive_pressure_ratio": 100.0,
                    "quote_ofi_ratio": -100.0,
                    "quote_ofi_qty": -1e9,
                    "bid_add_qty": 1e9,
                    "bid_remove_qty": 1.0,
                    "ask_add_qty": 1.0,
                    "ask_remove_qty": 1e9,
                    "spread_median_ratio": 1.0,
                    "quote_resiliency_observable": True,
                }
                for _ in range(4)
            ],
            index=future_index,
        )
        second = self._run(pd.concat([base, future]))
        first_signal = self._signals(first)[0]
        matching = [
            signal
            for signal in self._signals(second)
            if signal.scenario_id == first_signal.scenario_id
        ]
        self.assertEqual(matching, [first_signal])

    def test_detector_source_contains_no_execution_or_outcome_logic(self) -> None:
        source = Path(__file__).with_name("quote_resiliency_signals.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "realized_pnl",
            "future_high",
            "future_low",
            "win_rate",
            "profit_factor",
            "model_score",
            "risk_multiplier",
            "BacktestEngine(",
            "submit_order",
            "order_factory",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
