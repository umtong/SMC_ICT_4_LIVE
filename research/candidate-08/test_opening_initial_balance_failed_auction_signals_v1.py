"""Causal contracts for the session-opening initial-balance failed-auction detector."""

from __future__ import annotations

from datetime import date
import unittest

import numpy as np
import pandas as pd

from opening_initial_balance_failed_auction_signals_v1 import (
    OpeningAuctionConfig,
    SESSION_SPECS,
    build_initial_balances,
    build_opening_failed_auction_signals,
    session_open_utc,
)
from range_fvg_logic import FiveMinuteBar


FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000


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
        "2024-04-08T00:35:00Z",
        freq="5min",
    )
    bars: list[FiveMinuteBar] = []
    for index, start in enumerate(starts):
        bars.append(
            _bar(
                index,
                start,
                open_=97.0,
                high=97.2,
                low=96.8,
                close=97.1,
            )
        )

    # Complete Asia 00:00-00:30 UTC initial balance: high 101, low 94. Individual bars remain
    # narrow so the shifted displacement baseline is not accidentally inflated by the test fixture.
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

    # 00:30 upper-edge sweep and close back inside.
    bars[18] = _bar(
        18,
        starts[18],
        open_=100.1,
        high=101.2,
        low=100.0,
        close=100.3,
    )
    # Separate 00:35 bearish displacement through the sweep midpoint.
    bars[19] = _bar(
        19,
        starts[19],
        open_=100.2,
        high=100.3,
        low=99.3,
        close=99.4,
    )
    return tuple(bars)


def _execution_data(extra_rows: int = 0) -> pd.DataFrame:
    index = pd.date_range(
        "2024-04-07T23:00:10Z",
        pd.Timestamp("2024-04-08T00:40:00Z") + pd.Timedelta(seconds=10 * extra_rows),
        freq="10s",
    )
    close = np.full(len(index), 99.4, dtype=float)
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


class OpeningInitialBalanceFailedAuctionTests(unittest.TestCase):
    def test_london_and_new_york_opens_follow_dst(self) -> None:
        london = next(spec for spec in SESSION_SPECS if spec.name == "LONDON")
        new_york = next(spec for spec in SESSION_SPECS if spec.name == "NEW_YORK")
        self.assertEqual(
            session_open_utc(date(2025, 1, 15), london).isoformat(),
            "2025-01-15T08:00:00+00:00",
        )
        self.assertEqual(
            session_open_utc(date(2025, 6, 15), london).isoformat(),
            "2025-06-15T07:00:00+00:00",
        )
        self.assertEqual(
            session_open_utc(date(2025, 1, 15), new_york).isoformat(),
            "2025-01-15T14:30:00+00:00",
        )
        self.assertEqual(
            session_open_utc(date(2025, 6, 15), new_york).isoformat(),
            "2025-06-15T13:30:00+00:00",
        )

    def test_initial_balance_requires_exactly_six_contiguous_bars(self) -> None:
        bars = _synthetic_bars()
        balances = build_initial_balances(bars, OpeningAuctionConfig())
        asia = [item for item in balances if item.session_name == "ASIA" and item.local_date == "2024-04-08"]
        self.assertEqual(len(asia), 1)
        self.assertEqual(asia[0].first_five_position, 12)
        self.assertEqual(asia[0].last_five_position, 17)
        self.assertEqual(asia[0].high, 101.0)
        self.assertEqual(asia[0].low, 94.0)

        missing = tuple(bar for position, bar in enumerate(bars) if position != 15)
        balances_missing = build_initial_balances(missing, OpeningAuctionConfig())
        self.assertFalse(
            any(
                item.session_name == "ASIA" and item.local_date == "2024-04-08"
                for item in balances_missing
            )
        )

    def _build(self, *, extra_rows: int = 0):
        bars = _synthetic_bars()
        return build_opening_failed_auction_signals(
            data=_execution_data(extra_rows),
            context_times=np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64),
            context_bars=bars,
            snapshots=tuple(() for _ in bars),
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            tick=0.1,
            fee_rate=0.0006,
            minimum_net_reward_risk=1.2,
            config=OpeningAuctionConfig(),
        )

    def test_builder_requires_separate_m5_displacement_then_next_execution_bucket(self) -> None:
        bundle = self._build()
        self.assertEqual(bundle.diagnostics["TRADEABLE_OPENING_FAILED_AUCTION_SIGNAL"], 1)
        self.assertEqual(bundle.diagnostics["SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        confirmation_ns = _synthetic_bars()[19].ts_event_ns
        self.assertGreater(signal.signal_time_ns, confirmation_ns)
        self.assertLessEqual(signal.signal_time_ns - confirmation_ns, 11_000_000_000)
        self.assertEqual(signal.direction, -1)
        self.assertEqual(signal.external_target, 94.0)
        self.assertGreater(signal.structural_stop, signal.entry_reference)
        self.assertEqual(len(signal.events), 3)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "INITIAL_BALANCE_EDGE_SWEPT_AND_RECLAIMED",
                "FAILED_AUCTION_DISPLACEMENT_CONFIRMED",
                "NEXT_EXECUTION_BUCKET_OBSERVED",
            ],
        )
        self.assertFalse(signal.details["ten_second_alpha_inputs"])

    def test_future_suffix_does_not_change_existing_signal(self) -> None:
        original = self._build(extra_rows=0)
        extended = self._build(extra_rows=30)
        left = next(iter(next(iter(original.signals_by_time_ns.values()))))
        right = next(iter(next(iter(extended.signals_by_time_ns.values()))))
        self.assertEqual(left.signal_time_ns, right.signal_time_ns)
        self.assertEqual(left.entry_reference, right.entry_reference)
        self.assertEqual(left.structural_stop, right.structural_stop)
        self.assertEqual(left.external_target, right.external_target)
        self.assertEqual(left.net_reward_risk, right.net_reward_risk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
