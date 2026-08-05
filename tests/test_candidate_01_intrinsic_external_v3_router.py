from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
for item in (CANDIDATE, ROOT / "src"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import VolumeBar
from directional_change_failed_sweep_week import DirectionalChangeEvent
from impact_regime_probe import EventFeature
from intrinsic_external_liquidity_v3_router import (
    build_open_liquidity_snapshots,
    liquidity_event_key,
)


def feature(index: int, *, high: float, low: float) -> EventFeature:
    close = 0.5 * (high + low)
    bar = VolumeBar(
        index=index,
        start_time_ns=index * 10 + 1,
        end_time_ns=index * 10 + 9,
        open=close,
        high=high,
        low=low,
        close=close,
        base_quantity=1.0,
        quote_notional=1000.0,
        signed_quote_notional=0.0,
        aggressive_buy_quote=500.0,
        aggressive_sell_quote=500.0,
        aggregate_trades=1,
        first_agg_trade_id=index,
        last_agg_trade_id=index,
        target_quote_notional=1000.0,
    )
    return EventFeature(bar=bar, true_range=high - low, atr=1.0, imbalance_z=0.0)


def event(
    event_type: str,
    *,
    confirmation_index: int,
    pivot_index: int,
    pivot_price: float,
) -> DirectionalChangeEvent:
    return DirectionalChangeEvent(
        event_type=event_type,
        confirmation_index=confirmation_index,
        confirmation_time_ns=confirmation_index * 10 + 9,
        confirmation_price=pivot_price,
        pivot_index=pivot_index,
        pivot_time_ns=pivot_index * 10 + 9,
        pivot_price=pivot_price,
        trend_start_index=0,
        trend_flow_imbalance=0.0,
        reversal_flow_imbalance=0.0,
        path_high=pivot_price,
        path_low=pivot_price,
    )


class CausalLiquidityLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.features = [
            feature(0, high=100.0, low=99.0),
            feature(1, high=105.0, low=98.0),
            feature(2, high=104.0, low=95.0),
            feature(3, high=106.0, low=96.0),
            feature(4, high=109.0, low=94.0),
            feature(5, high=111.0, low=93.0),
        ]
        self.events = [
            # Confirmation bars deliberately touch their own pivot. They must
            # remain open until a later completed event consumes them.
            event("DOWN", confirmation_index=1, pivot_index=0, pivot_price=105.0),
            event("UP", confirmation_index=2, pivot_index=1, pivot_price=95.0),
            event("DOWN", confirmation_index=3, pivot_index=2, pivot_price=110.0),
            event("UP", confirmation_index=4, pivot_index=3, pivot_price=93.5),
        ]

    def legacy_open(self, event: DirectionalChangeEvent, signal_index: int) -> bool:
        if event.confirmation_index > signal_index:
            return False
        subsequent = self.features[event.confirmation_index + 1 : signal_index + 1]
        if event.event_type == "DOWN":
            return not any(row.bar.high >= event.pivot_price for row in subsequent)
        return not any(row.bar.low <= event.pivot_price for row in subsequent)

    def test_snapshots_match_prefix_scan_at_every_index(self) -> None:
        snapshots = build_open_liquidity_snapshots(
            features=self.features,
            events=self.events,
            signal_indices=range(len(self.features)),
        )
        for index in range(len(self.features)):
            expected_high = {
                liquidity_event_key(item)
                for item in self.events
                if item.event_type == "DOWN" and self.legacy_open(item, index)
            }
            expected_low = {
                liquidity_event_key(item)
                for item in self.events
                if item.event_type == "UP" and self.legacy_open(item, index)
            }
            self.assertEqual(snapshots[index].high_keys, expected_high)
            self.assertEqual(snapshots[index].low_keys, expected_low)

    def test_confirmation_event_does_not_consume_new_pool(self) -> None:
        snapshots = build_open_liquidity_snapshots(
            features=self.features,
            events=self.events,
            signal_indices=(1, 2),
        )
        high_key = liquidity_event_key(self.events[0])
        low_key = liquidity_event_key(self.events[1])
        self.assertIn(high_key, snapshots[1].high_keys)
        self.assertIn(low_key, snapshots[2].low_keys)

    def test_invalid_signal_index_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_open_liquidity_snapshots(
                features=self.features,
                events=self.events,
                signal_indices=(len(self.features),),
            )


if __name__ == "__main__":
    unittest.main()
