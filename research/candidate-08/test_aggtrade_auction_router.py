from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys
import unittest

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from aggtrade_acceptance_signals import AcceptanceSignal
from aggtrade_auction_router_signals import (
    FAILED_AUCTION_FAMILY,
    INITIATIVE_FAMILY,
    build_auction_router_signals,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


def level(level_id: str, kind: LevelKind, value: float) -> ExternalLevel:
    return ExternalLevel(
        level_id=level_id,
        kind=kind,
        source=LevelSource.FOUR_HOUR,
        level=value,
        formed_index=0,
        formed_time_ns=1,
        period_key="p0",
    )


def context_bar(ts_event_ns: int) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=0,
        ts_event_ns=ts_event_ns,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
        trade_count=1000.0,
        taker_buy_volume=500.0,
        imbalance=0.0,
        atr=1.0,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.1,
        direction_60m=1.0,
        session_key="s0",
        day_key="d0",
        week_key="w0",
    )


def row(**overrides: float) -> dict[str, float]:
    result = {
        "open": 99.8,
        "high": 99.9,
        "low": 99.7,
        "close": 99.8,
        "volume": 100.0,
        "trade_count": 100.0,
        "imbalance": 0.0,
        "volume_ratio": 1.0,
        "trade_ratio": 1.0,
        "close_location": 0.5,
    }
    result.update(overrides)
    return result


def build(rows: list[dict[str, float]]):
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(rows), freq="10s")
    data = pd.DataFrame(rows, index=index)
    before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
    after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
    levels = (
        level("completed-high-boundary", LevelKind.HIGH, 100.0),
        level("completed-high-target", LevelKind.HIGH, 105.0),
        level("completed-low-target", LevelKind.LOW, 95.0),
    )
    return build_auction_router_signals(
        data=data,
        context_times=np.asarray([before, after], dtype=np.int64),
        context_bars=(context_bar(before), context_bar(after)),
        snapshots=(levels, levels),
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        tick=0.1,
        fee_rate=0.0006,
        minimum_net_reward_risk=1.2,
    )


class AuctionRouterContracts(unittest.TestCase):
    def test_initiative_continuation_requires_causal_noise_clearance(self) -> None:
        bundle = build(
            [
                row(),
                row(
                    open=99.9,
                    high=100.5,
                    low=99.9,
                    close=100.4,
                    volume=200.0,
                    trade_count=200.0,
                    imbalance=0.5,
                    volume_ratio=2.0,
                    trade_ratio=2.0,
                    close_location=0.83,
                ),
                row(
                    open=100.4,
                    high=100.45,
                    low=100.02,
                    close=100.2,
                    volume=190.0,
                    trade_count=195.0,
                    imbalance=0.45,
                    volume_ratio=1.2,
                    trade_ratio=1.2,
                    close_location=0.42,
                ),
                row(
                    open=100.2,
                    high=101.6,
                    low=100.15,
                    close=101.5,
                    volume=220.0,
                    trade_count=220.0,
                    imbalance=0.4,
                    volume_ratio=1.6,
                    trade_ratio=1.5,
                    close_location=0.93,
                ),
            ]
        )
        signals = [signal for values in bundle.signals_by_time_ns.values() for signal in values]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.direction, 1)
        self.assertEqual(signal.details["scenario_family"], INITIATIVE_FAMILY)
        self.assertGreaterEqual(
            signal.details["boundary_displacement_depth"],
            signal.details["causal_noise_reserve"],
        )
        self.assertGreaterEqual(signal.details["displacement_to_noise_ratio"], 1.0)
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LEVEL_ACCEPTED",
                "ACCEPTANCE_RETEST_HELD",
                "INITIATIVE_REACCELERATION_CONFIRMED",
            ],
        )

    def test_shallow_reacceleration_inside_causal_noise_is_rejected(self) -> None:
        bundle = build(
            [
                row(),
                row(
                    open=99.9,
                    high=100.2,
                    low=99.9,
                    close=100.1,
                    volume=200.0,
                    trade_count=200.0,
                    imbalance=0.5,
                    volume_ratio=2.0,
                    trade_ratio=2.0,
                    close_location=0.8,
                ),
                row(
                    open=100.1,
                    high=100.02,
                    low=99.99,
                    close=100.0,
                    volume=190.0,
                    trade_count=195.0,
                    imbalance=0.4,
                    volume_ratio=1.1,
                    trade_ratio=1.1,
                    close_location=0.5,
                ),
                row(
                    open=100.0,
                    high=100.08,
                    low=99.99,
                    close=100.07,
                    volume=200.0,
                    trade_count=200.0,
                    imbalance=0.3,
                    volume_ratio=1.4,
                    trade_ratio=1.4,
                    close_location=0.88,
                ),
            ]
        )
        self.assertEqual(sum(len(values) for values in bundle.signals_by_time_ns.values()), 0)
        self.assertEqual(
            bundle.diagnostics.get("SHALLOW_DISPLACEMENT_WITHIN_CAUSAL_NOISE"),
            1,
        )

    def test_reclaimed_acceptance_routes_to_failed_auction_reversal(self) -> None:
        bundle = build(
            [
                row(),
                row(
                    open=99.9,
                    high=100.5,
                    low=99.9,
                    close=100.4,
                    volume=200.0,
                    trade_count=200.0,
                    imbalance=0.5,
                    volume_ratio=2.0,
                    trade_ratio=2.0,
                    close_location=0.83,
                ),
                row(
                    open=100.4,
                    high=100.45,
                    low=99.7,
                    close=99.8,
                    volume=180.0,
                    trade_count=180.0,
                    imbalance=-0.2,
                    volume_ratio=1.2,
                    trade_ratio=1.2,
                    close_location=0.2,
                ),
                row(
                    open=99.8,
                    high=99.85,
                    low=99.3,
                    close=99.4,
                    volume=210.0,
                    trade_count=210.0,
                    imbalance=-0.4,
                    volume_ratio=1.5,
                    trade_ratio=1.4,
                    close_location=0.18,
                ),
            ]
        )
        signals = [signal for values in bundle.signals_by_time_ns.values() for signal in values]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal.direction, -1)
        self.assertEqual(signal.details["scenario_family"], FAILED_AUCTION_FAMILY)
        self.assertGreater(signal.structural_stop, signal.entry_reference)
        self.assertGreater(signal.entry_reference, signal.external_target)
        self.assertEqual(signal.details["stop_reference_source"], "FAILED_AUCTION_SWEEP_HIGH")
        self.assertEqual(
            [event.event_type for event in signal.events],
            [
                "EXTERNAL_LIQUIDITY_SWEPT",
                "FAILED_AUCTION_RECLAIMED",
                "INWARD_DISPLACEMENT_CONFIRMED",
            ],
        )

    def test_future_rows_do_not_change_already_observed_signal(self) -> None:
        initial_rows = [
            row(),
            row(
                open=99.9,
                high=100.5,
                low=99.9,
                close=100.4,
                volume=200.0,
                trade_count=200.0,
                imbalance=0.5,
                volume_ratio=2.0,
                trade_ratio=2.0,
                close_location=0.83,
            ),
            row(
                open=100.4,
                high=100.45,
                low=100.02,
                close=100.2,
                volume=190.0,
                trade_count=195.0,
                imbalance=0.45,
                volume_ratio=1.2,
                trade_ratio=1.2,
                close_location=0.42,
            ),
            row(
                open=100.2,
                high=101.6,
                low=100.15,
                close=101.5,
                volume=220.0,
                trade_count=220.0,
                imbalance=0.4,
                volume_ratio=1.6,
                trade_ratio=1.5,
                close_location=0.93,
            ),
        ]
        first = build(initial_rows)
        second = build(initial_rows + [row(close=102.0), row(close=98.0)])
        first_signal = next(iter(next(iter(first.signals_by_time_ns.values()))))
        second_signal = second.signals_by_time_ns[first_signal.signal_time_ns][0]
        self.assertEqual(first_signal, second_signal)

    def test_signal_schema_contains_no_outcome_proxy(self) -> None:
        forbidden = ("outcome", "mfe", "mae", "net_r_proxy")
        names = {field.name for field in fields(AcceptanceSignal)}
        self.assertFalse(any(any(token in name for token in forbidden) for name in names))


if __name__ == "__main__":
    unittest.main()
