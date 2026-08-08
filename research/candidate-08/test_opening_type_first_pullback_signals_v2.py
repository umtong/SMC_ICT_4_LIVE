"""Causal contracts for opening-type first-pullback state router V2."""
from __future__ import annotations

import unittest
import numpy as np
import pandas as pd

from opening_type_first_pullback_signals_v2 import (
    INITIATIVE_FAMILY,
    SIGNAL_REVISION,
    build_opening_type_first_pullback_signals,
    reprice_bundle_for_bar_market_preserving_events,
)
from range_fvg_logic import FiveMinuteBar


def bar(index: int, start: pd.Timestamp, o: float, h: float, l: float, c: float,
        volume: float = 100.0, atr: float = 1.0) -> FiveMinuteBar:
    return FiveMinuteBar(
        index,
        int((start + pd.Timedelta(minutes=5) - pd.Timedelta(milliseconds=1)).value),
        o, h, l, c, volume, 100.0, 50.0, 0.0, atr, 1.0, 1.0, 0.0, 0,
        str(start.floor("4h")), str(start.floor("1D")),
        str((start - pd.Timedelta(days=start.weekday())).floor("1D")),
    )


def context(*, invalidate: bool = False) -> tuple[FiveMinuteBar, ...]:
    starts = pd.date_range("2026-01-04T00:00:00Z", "2026-01-05T00:35:00Z", freq="5min")
    rows: list[FiveMinuteBar] = []
    session = pd.Timestamp("2026-01-05T00:00:00Z")
    for index, start in enumerate(starts):
        if start < session:
            center = 99.5 if index % 2 == 0 else 100.5
            rows.append(bar(index, start, center - 0.05, center + 0.2, center - 0.2, center + 0.05))
        elif start < session + pd.Timedelta(minutes=30):
            # The first IB bar tests prior value; later bars establish accepted value above it.
            if start == session:
                rows.append(bar(index, start, 102.0, 104.0, 99.8, 103.0))
            else:
                rows.append(bar(index, start, 102.7, 103.5, 102.5, 103.2))
        elif start == session + pd.Timedelta(minutes=30):
            if invalidate:
                rows.append(bar(index, start, 103.2, 103.4, 99.7, 100.0))
            else:
                # First pullback rotates through the IB high but still closes above prior value.
                rows.append(bar(index, start, 103.3, 104.2, 103.95, 104.1))
        else:
            # A distinct later M5 starts a new auction leg beyond pullback and IB structure.
            rows.append(bar(index, start, 104.1, 106.2, 104.0, 106.0))
    return tuple(rows)


def execution(extra_rows: int = 0) -> pd.DataFrame:
    end = pd.Timestamp("2026-01-05T00:45:00Z") + pd.Timedelta(seconds=10 * extra_rows)
    index = pd.date_range("2026-01-04T23:00:10Z", end, freq="10s")
    # Synthetic execution stays inside the new leg and preserves ample cost-after geometry.
    close = np.full(len(index), 105.2)
    return pd.DataFrame(
        {"open": close, "high": close + 0.05, "low": close - 0.05,
         "close": close, "volume": 10.0},
        index=index,
    )


def build(*, invalidate: bool = False, extra_rows: int = 0):
    bars = context(invalidate=invalidate)
    raw = build_opening_type_first_pullback_signals(
        data=execution(extra_rows),
        context_times=np.asarray([row.ts_event_ns for row in bars], dtype=np.int64),
        context_bars=bars,
        snapshots=tuple(() for _ in bars),
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        tick=0.1,
        fee_rate=0.0006,
        minimum_net_reward_risk=1.2,
        router_config={},
    )
    return reprice_bundle_for_bar_market_preserving_events(
        raw, tick=0.1, minimum_net_reward_risk=1.2
    )


class Contracts(unittest.TestCase):
    def test_rotation_inside_ib_does_not_invalidate_previous_value_acceptance(self):
        bundle = build()
        self.assertEqual(bundle.diagnostics["FIRST_PULLBACK_HELD_STATE_EDGE"], 1)
        self.assertEqual(bundle.diagnostics["TRADEABLE_OPENING_TYPE_FIRST_PULLBACK_SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        self.assertEqual(signal.scenario_family, INITIATIVE_FAMILY)
        self.assertEqual(signal.details["opening_type"], "OPEN_TEST_DRIVE")
        self.assertEqual(signal.details["signal_revision"], SIGNAL_REVISION)
        self.assertEqual(len(signal.events), 4)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "OPENING_TYPE_CLASSIFIED",
                "FIRST_PULLBACK_HELD",
                "NEW_AUCTION_LEG_CONFIRMED",
                "NEXT_EXECUTION_BUCKET_OBSERVED",
            ],
        )
        self.assertTrue(
            signal.events[0].event_time_ns
            < signal.events[1].event_time_ns
            < signal.events[2].event_time_ns
            < signal.events[3].event_time_ns
        )
        self.assertGreaterEqual(signal.net_reward_risk, 1.2)
        self.assertFalse(signal.details["ten_second_alpha_inputs"])

    def test_previous_value_reentry_invalidates_state_before_trigger(self):
        bundle = build(invalidate=True)
        self.assertEqual(bundle.diagnostics["OPENING_VALUE_STATE_INVALIDATED"], 1)
        self.assertEqual(bundle.diagnostics["SIGNAL"], 0)

    def test_future_suffix_does_not_change_existing_signal(self):
        left = build(extra_rows=0)
        right = build(extra_rows=60)
        a = next(iter(next(iter(left.signals_by_time_ns.values()))))
        b = next(iter(next(iter(right.signals_by_time_ns.values()))))
        self.assertEqual(
            (a.signal_time_ns, a.entry_reference, a.structural_stop, a.external_target, a.net_reward_risk),
            (b.signal_time_ns, b.entry_reference, b.structural_stop, b.external_target, b.net_reward_risk),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
