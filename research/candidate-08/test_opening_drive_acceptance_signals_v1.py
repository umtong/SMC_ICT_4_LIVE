"""Causal contracts for session-opening drive acceptance and boundary retest V1."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from opening_drive_acceptance_signals_v1 import (
    OpeningDriveConfig,
    build_opening_drive_acceptance_signals,
)
from range_fvg_logic import FiveMinuteBar


def _bar(
    index: int,
    start: pd.Timestamp,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
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
        volume=100.0,
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
    starts = pd.date_range(
        "2024-04-07T23:00:00Z",
        "2024-04-08T00:40:00Z",
        freq="5min",
    )
    bars = [
        _bar(
            index,
            start,
            open_=97.0,
            high=97.2,
            low=96.8,
            close=97.1,
        )
        for index, start in enumerate(starts)
    ]

    # Complete Asia initial balance 00:00-00:30 UTC: high 101, low 94. Individual bars remain
    # narrow so the shifted displacement baseline represents prior activity, not fixture width.
    ib_values = {
        12: (97.0, 97.3, 96.9, 97.1),
        13: (100.2, 101.0, 100.0, 100.4),
        14: (94.8, 95.0, 94.0, 94.6),
        15: (97.0, 97.3, 96.9, 97.1),
        16: (97.1, 97.4, 97.0, 97.2),
        17: (97.2, 97.5, 97.1, 97.3),
    }
    for position, values in ib_values.items():
        open_, high, low, close = values
        bars[position] = _bar(
            position,
            starts[position],
            open_=open_,
            high=high,
            low=low,
            close=close,
        )

    # 00:30: first displaced close above completed IB high.
    bars[18] = _bar(
        18,
        starts[18],
        open_=100.6,
        high=101.6,
        low=100.5,
        close=101.5,
    )
    # 00:35: immediate second completed close remains outside.
    bars[19] = _bar(
        19,
        starts[19],
        open_=101.4,
        high=101.9,
        low=101.2,
        close=101.7,
    )
    # 00:40: separate retest touches the accepted boundary and closes outside.
    bars[20] = _bar(
        20,
        starts[20],
        open_=101.6,
        high=101.7,
        low=100.98,
        close=101.3,
    )
    return tuple(bars)


def _execution_data(extra_rows: int = 0) -> pd.DataFrame:
    index = pd.date_range(
        "2024-04-07T23:00:10Z",
        pd.Timestamp("2024-04-08T00:45:00Z") + pd.Timedelta(seconds=10 * extra_rows),
        freq="10s",
    )
    close = np.full(len(index), 101.3, dtype=float)
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
    return build_opening_drive_acceptance_signals(
        data=_execution_data(extra_rows),
        context_times=np.asarray([bar.ts_event_ns for bar in immutable], dtype=np.int64),
        context_bars=immutable,
        snapshots=tuple(() for _ in immutable),
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        tick=0.1,
        fee_rate=0.0006,
        minimum_net_reward_risk=1.2,
        config=OpeningDriveConfig(),
    )


class OpeningDriveAcceptanceSignalTests(unittest.TestCase):
    def test_requires_immediate_second_outside_close_then_separate_retest(self) -> None:
        bundle = _build()
        self.assertEqual(bundle.diagnostics["TRADEABLE_OPENING_DRIVE_ACCEPTANCE_SIGNAL"], 1)
        self.assertEqual(bundle.diagnostics["SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        retest_ns = _synthetic_bars()[20].ts_event_ns
        self.assertGreater(signal.signal_time_ns, retest_ns)
        self.assertLessEqual(signal.signal_time_ns - retest_ns, 11_000_000_000)
        self.assertEqual(signal.direction, 1)
        self.assertEqual(signal.boundary_level, 101.0)
        self.assertEqual(signal.external_target, 108.0)
        self.assertLess(signal.structural_stop, signal.entry_reference)
        self.assertGreater(signal.net_reward_risk, 1.2)
        self.assertEqual(len(signal.events), 3)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "OPENING_DRIVE_DISPLACEMENT_CLOSE_OUTSIDE",
                "SECOND_OUTSIDE_CLOSE_ACCEPTED",
                "ACCEPTED_BOUNDARY_RETEST_HELD",
            ],
        )
        self.assertEqual(
            [event.observed_time_ns for event in signal.events],
            [
                _synthetic_bars()[18].ts_event_ns,
                _synthetic_bars()[19].ts_event_ns,
                _synthetic_bars()[20].ts_event_ns,
            ],
        )
        self.assertFalse(signal.details["ten_second_alpha_inputs"])

    def test_second_close_inside_initial_balance_is_terminal(self) -> None:
        bars = list(_synthetic_bars())
        start = pd.Timestamp("2024-04-08T00:35:00Z")
        bars[19] = _bar(
            19,
            start,
            open_=101.4,
            high=101.6,
            low=100.7,
            close=100.9,
        )
        bundle = _build(bars=tuple(bars))
        self.assertEqual(bundle.diagnostics["OPENING_DRIVE_NOT_ACCEPTED_ON_SECOND_CLOSE"], 1)
        self.assertEqual(bundle.diagnostics["SIGNAL"], 0)
        self.assertFalse(bundle.signals_by_time_ns)

    def test_second_outside_close_cannot_double_as_retest(self) -> None:
        bars = list(_synthetic_bars())
        # The immediate second outside close touches the edge, but the later bar does not. The
        # scenario must not reuse the acceptance bar as its separate retest.
        bars[19] = _bar(
            19,
            pd.Timestamp("2024-04-08T00:35:00Z"),
            open_=101.4,
            high=101.8,
            low=100.99,
            close=101.5,
        )
        bars[20] = _bar(
            20,
            pd.Timestamp("2024-04-08T00:40:00Z"),
            open_=101.5,
            high=101.9,
            low=101.4,
            close=101.8,
        )
        bundle = _build(bars=tuple(bars))
        self.assertEqual(bundle.diagnostics["NO_ACCEPTED_BOUNDARY_RETEST_BEFORE_ROUTE_END"], 1)
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
