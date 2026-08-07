"""Causal contract tests for day-liquidity-delivery V1."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import day_liquidity_delivery_signals_v1 as signals
from day_liquidity_delivery_context_v1 import (
    DayLiquidityDeliveryConfig,
    DrawContext,
    RouteCandidate,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


def _bar(index: int, *, open_: float, high: float, low: float, close: float) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=index,
        ts_event_ns=(index + 1) * 300 * 1_000_000_000,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        trade_count=100.0,
        taker_buy_volume=50.0,
        imbalance=0.0,
        atr=1.0,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.0,
        direction_60m=0,
        session_key="SYNTHETIC",
        day_key="1970-01-01",
        week_key="1970-W01",
    )


def _bars() -> tuple[FiveMinuteBar, ...]:
    result = [
        _bar(i, open_=100.0, high=100.4, low=99.6, close=100.1)
        for i in range(16)
    ]
    result[3] = _bar(3, open_=100.0, high=100.3, low=99.7, close=100.0)
    result[4] = _bar(4, open_=100.0, high=100.5, low=99.7, close=100.1)
    result[5] = _bar(5, open_=100.0, high=101.0, low=99.8, close=100.2)
    result[6] = _bar(6, open_=100.1, high=100.6, low=99.8, close=100.0)
    result[7] = _bar(7, open_=100.0, high=100.4, low=99.7, close=100.1)
    result[13] = _bar(13, open_=100.0, high=102.2, low=101.2, close=102.0)
    result[14] = _bar(14, open_=101.3, high=101.9, low=100.8, close=101.7)
    return tuple(result)


def _target() -> ExternalLevel:
    return ExternalLevel(
        level_id="day-previous-high",
        kind=LevelKind.HIGH,
        source=LevelSource.DAY,
        level=110.0,
        formed_index=0,
        formed_time_ns=0,
        period_key="previous-day",
    )


def _draw() -> DrawContext:
    return DrawContext(
        direction=1,
        break_swing_id="h4-break-high",
        break_level=101.0,
        origin_swing_id="h4-origin-low",
        origin_level=95.0,
        observed_time_ns=10 * 300 * 1_000_000_000,
        h4_index=2,
        h4_atr=4.0,
    )


def _candidate(target: ExternalLevel) -> RouteCandidate:
    return RouteCandidate(
        scenario_id="day-delivery-test-000001",
        family=signals.RAID_FAMILY,
        route_name="EUROPE",
        source_name="ASIA",
        route_start_ns=12 * 300 * 1_000_000_000,
        route_end_ns=18 * 300 * 1_000_000_000,
        direction=1,
        draw=_draw(),
        target=target,
        boundary_id="asia-low",
        boundary_source="SESSION_ASIA_LOW",
        boundary_level=99.5,
        interaction_time_ns=12 * 300 * 1_000_000_000,
        trigger_time_ns=13 * 300 * 1_000_000_000,
        trigger_five_index=12,
        structural_reference=99.0,
        interaction_details={"contract": "synthetic-causal-test"},
    )


def _ten_second_data(extra_rows: int = 0) -> pd.DataFrame:
    end_ns = 16 * 300 * 1_000_000_000 + extra_rows * 10 * 1_000_000_000
    index = pd.date_range(
        pd.Timestamp(0, tz="UTC") + pd.Timedelta(seconds=10),
        pd.Timestamp(end_ns, tz="UTC"),
        freq="10s",
    )
    close = np.full(len(index), 101.7, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": np.full(len(index), 10.0),
        },
        index=index,
    )


class DayLiquidityDeliverySignalTests(unittest.TestCase):
    def test_swing_is_not_observable_before_right_span(self) -> None:
        states = signals._confirmed_five_swings(_bars(), span=2)
        self.assertIsNone(states[6].latest_high)
        self.assertIsNotNone(states[7].latest_high)
        self.assertEqual(states[7].latest_high.formed_index, 5)
        self.assertEqual(states[7].latest_high.confirmed_index, 7)

    def test_displacement_and_retrace_must_be_separate_completed_bars(self) -> None:
        bars = _bars()
        states = signals._confirmed_five_swings(bars, span=2)
        prior_body, prior_range = signals._shifted_prior_medians(bars, lookback=12)
        displacement = signals._five_displacement_fvg(
            bars=bars,
            position=13,
            direction=1,
            frozen_swing=states[12].latest_high,
            prior_body_median=prior_body,
            prior_range_median=prior_range,
            close_location=2.0 / 3.0,
            tick=0.1,
        )
        self.assertIsNotNone(displacement)
        self.assertLess(displacement.position, 14)
        self.assertEqual(
            signals._first_fvg_touch_result(
                bar=bars[14],
                direction=1,
                fvg_low=displacement.fvg_low,
                fvg_high=displacement.fvg_high,
            ),
            "VALID_RETRACE",
        )

    def test_touched_but_nonconfirming_first_touch_is_terminal(self) -> None:
        bad = _bar(14, open_=101.7, high=101.9, low=100.8, close=101.1)
        self.assertEqual(
            signals._first_fvg_touch_result(
                bar=bad,
                direction=1,
                fvg_low=100.4,
                fvg_high=101.2,
            ),
            "INVALID_FIRST_TOUCH",
        )

    def _build(self, *, extra_rows: int = 0):
        bars = _bars()
        target = _target()
        snapshots = tuple((target,) for _ in bars)
        contexts = tuple(_draw() for _ in bars)
        candidate = _candidate(target)
        with (
            patch.object(signals, "build_draw_contexts", return_value=contexts),
            patch.object(signals, "build_route_candidates", return_value=(candidate,)),
            patch.object(signals, "same_draw", return_value=True),
            patch.object(signals, "target_still_active", return_value=True),
        ):
            return signals.build_day_liquidity_delivery_signals(
                data=_ten_second_data(extra_rows),
                context_times=np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64),
                context_bars=bars,
                snapshots=snapshots,
                symbol="BTCUSDT",
                instrument_id="BTCUSDT-PERP.BINANCE",
                tick=0.1,
                fee_rate=0.0006,
                minimum_net_reward_risk=1.2,
                config=DayLiquidityDeliveryConfig(),
            )

    def test_full_builder_emits_after_completed_retrace(self) -> None:
        bundle = self._build()
        self.assertEqual(bundle.diagnostics["SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        retrace_ns = _bars()[14].ts_event_ns
        self.assertGreater(signal.signal_time_ns, retrace_ns)
        self.assertEqual(signal.scenario_family, signals.RAID_FAMILY)
        self.assertLess(signal.structural_stop, signal.entry_reference)
        self.assertGreater(signal.external_target, signal.entry_reference)
        self.assertEqual(len(signal.events), 3)

    def test_future_suffix_does_not_change_existing_signal(self) -> None:
        original = self._build(extra_rows=0)
        extended = self._build(extra_rows=12)
        left = next(iter(next(iter(original.signals_by_time_ns.values()))))
        right = next(iter(next(iter(extended.signals_by_time_ns.values()))))
        self.assertEqual(left.signal_time_ns, right.signal_time_ns)
        self.assertEqual(left.structural_stop, right.structural_stop)
        self.assertEqual(left.external_target, right.external_target)
        self.assertEqual(left.net_reward_risk, right.net_reward_risk)


if __name__ == "__main__":
    unittest.main()
