"""Causal contracts for previous-day value migration continuation V1."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from range_fvg_logic import FiveMinuteBar
from session_value_migration_signals_v1 import (
    SessionValueMigrationConfig,
    build_completed_daily_value_profiles,
    build_session_value_migration_signals,
)


def _bar(
    index: int,
    start: pd.Timestamp,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    atr: float = 1.0,
) -> FiveMinuteBar:
    end_ns = int((start + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1)).value)
    return FiveMinuteBar(
        index=index,
        ts_event_ns=end_ns,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trade_count=100.0,
        taker_buy_volume=50.0,
        imbalance=0.0,
        atr=atr,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.0,
        direction_60m=0,
        session_key=str(start.floor("4h")),
        day_key=str(start.floor("1D")),
        week_key=str((start - pd.Timedelta(days=start.weekday())).floor("1D")),
    )


def _synthetic_bars() -> tuple[FiveMinuteBar, ...]:
    bars: list[FiveMinuteBar] = []
    prior_starts = pd.date_range(
        "2024-04-07T00:00:00Z",
        "2024-04-07T23:55:00Z",
        freq="5min",
    )
    for index, start in enumerate(prior_starts):
        value = 99.0 if index % 2 == 0 else 101.0
        bars.append(
            _bar(
                index,
                start,
                open_=value,
                high=value + 0.1,
                low=value - 0.1,
                close=value,
            )
        )

    session_starts = pd.date_range(
        "2024-04-08T00:00:00Z",
        "2024-04-08T00:30:00Z",
        freq="5min",
    )
    base = len(bars)
    values = [
        (101.10, 101.35, 101.05, 101.25),
        (101.20, 101.40, 101.10, 101.30),
        (101.25, 101.50, 101.20, 101.35),
        (101.30, 101.50, 101.20, 101.35),
        (101.35, 101.55, 101.25, 101.40),
        (101.40, 101.60, 101.30, 101.45),
        (101.20, 101.35, 100.99, 101.08),
    ]
    for offset, (open_, high, low, close) in enumerate(values):
        bars.append(
            _bar(
                base + offset,
                session_starts[offset],
                open_=open_,
                high=high,
                low=low,
                close=close,
            )
        )
    return tuple(bars)


def _execution_data(extra_rows: int = 0) -> pd.DataFrame:
    index = pd.date_range(
        "2024-04-07T23:00:10Z",
        pd.Timestamp("2024-04-08T00:35:00Z") + pd.Timedelta(seconds=10 * extra_rows),
        freq="10s",
    )
    close = np.full(len(index), 101.08, dtype=float)
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


def _build(
    *,
    bars: tuple[FiveMinuteBar, ...] | None = None,
    extra_rows: int = 0,
):
    immutable = _synthetic_bars() if bars is None else bars
    return build_session_value_migration_signals(
        data=_execution_data(extra_rows),
        context_times=np.asarray([bar.ts_event_ns for bar in immutable], dtype=np.int64),
        context_bars=immutable,
        snapshots=tuple(() for _ in immutable),
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        tick=0.1,
        fee_rate=0.0006,
        minimum_net_reward_risk=1.2,
        config=SessionValueMigrationConfig(),
    )


class SessionValueMigrationSignalTests(unittest.TestCase):
    def test_complete_previous_day_profile_is_volume_weighted_and_causal(self) -> None:
        bars = _synthetic_bars()
        profiles = build_completed_daily_value_profiles(bars)
        day_start = int(pd.Timestamp("2024-04-07T00:00:00Z").value)
        profile = profiles[day_start]
        self.assertAlmostEqual(profile.vwap, 100.0)
        self.assertAlmostEqual(profile.sigma, 1.0)
        self.assertAlmostEqual(profile.value_low, 99.0)
        self.assertAlmostEqual(profile.value_high, 101.0)
        self.assertAlmostEqual(profile.lower_extension, 97.0)
        self.assertAlmostEqual(profile.upper_extension, 103.0)
        self.assertLess(profile.observed_time_ns, int(pd.Timestamp("2024-04-08T00:00:00Z").value))

        incomplete = tuple(bar for position, bar in enumerate(bars) if position != 100)
        self.assertNotIn(day_start, build_completed_daily_value_profiles(incomplete))

    def test_two_m15_closes_session_vwap_migration_and_separate_retest_emit(self) -> None:
        bundle = _build()
        self.assertEqual(bundle.diagnostics["TRADEABLE_SESSION_VALUE_MIGRATION_SIGNAL"], 1)
        self.assertEqual(bundle.diagnostics["SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        retest_ns = _synthetic_bars()[-1].ts_event_ns
        self.assertGreater(signal.signal_time_ns, retest_ns)
        self.assertLessEqual(signal.signal_time_ns - retest_ns, 11_000_000_000)
        self.assertEqual(signal.direction, 1)
        self.assertAlmostEqual(signal.boundary_level, 101.0)
        self.assertAlmostEqual(signal.external_target, 103.0)
        self.assertLess(signal.structural_stop, signal.entry_reference)
        self.assertGreater(signal.net_reward_risk, 1.2)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "FIRST_M15_CLOSE_OUTSIDE_PREVIOUS_VALUE",
                "SECOND_M15_CLOSE_AND_SESSION_VWAP_MIGRATION_ACCEPTED",
                "PREVIOUS_VALUE_EDGE_RETEST_HELD",
            ],
        )
        self.assertGreater(signal.details["accepted_session_vwap"], 101.0)
        self.assertFalse(signal.details["ten_second_alpha_inputs"])

    def test_second_close_without_session_vwap_migration_is_rejected(self) -> None:
        bars = list(_synthetic_bars())
        starts = pd.date_range("2024-04-08T00:00:00Z", "2024-04-08T00:25:00Z", freq="5min")
        base = 288
        replacement = [
            (99.4, 99.6, 99.3, 99.5),
            (99.4, 99.6, 99.3, 99.5),
            (99.5, 101.2, 99.4, 101.10),
            (99.4, 99.6, 99.3, 99.5),
            (99.4, 99.6, 99.3, 99.5),
            (99.5, 101.2, 99.4, 101.10),
        ]
        for offset, values in enumerate(replacement):
            open_, high, low, close = values
            bars[base + offset] = _bar(
                base + offset,
                starts[offset],
                open_=open_,
                high=high,
                low=low,
                close=close,
            )
        bundle = _build(bars=tuple(bars))
        self.assertEqual(bundle.diagnostics["SESSION_VWAP_DID_NOT_MIGRATE_OUTSIDE_VALUE"], 1)
        self.assertEqual(bundle.diagnostics["SIGNAL"], 0)

    def test_future_suffix_does_not_change_existing_signal(self) -> None:
        original = _build(extra_rows=0)
        extended = _build(extra_rows=30)
        left = next(iter(next(iter(original.signals_by_time_ns.values()))))
        right = next(iter(next(iter(extended.signals_by_time_ns.values()))))
        self.assertEqual(left.signal_time_ns, right.signal_time_ns)
        self.assertEqual(left.entry_reference, right.entry_reference)
        self.assertEqual(left.structural_stop, right.structural_stop)
        self.assertEqual(left.external_target, right.external_target)
        self.assertEqual(left.net_reward_risk, right.net_reward_risk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
