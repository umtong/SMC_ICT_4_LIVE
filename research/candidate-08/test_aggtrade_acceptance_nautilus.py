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

from aggtrade_acceptance_signals import AcceptanceSignal, build_acceptance_signals
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource
from run_aggtrade_acceptance_nautilus import (
    _build_instrument,
    _funding_updates_from_frame,
    _mark_price_updates_from_frame,
    _ten_second_bar_type,
)


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


def frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(rows), freq="10s")
    result = pd.DataFrame(rows, index=index)
    result.index.name = "observed_time"
    return result


def base_row(**overrides: float) -> dict[str, float]:
    row = {
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
    row.update(overrides)
    return row


class AcceptanceSignalContractTests(unittest.TestCase):
    def _build(self, rows: list[dict[str, float]], levels: tuple[ExternalLevel, ...]):
        data = frame(rows)
        context_time = int((data.index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
        future_context_time = int((data.index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
        bar = context_bar(context_time)
        future_bar = context_bar(future_context_time)
        return build_acceptance_signals(
            data=data,
            context_times=np.asarray([context_time, future_context_time], dtype=np.int64),
            context_bars=(bar, future_bar),
            snapshots=(levels, levels),
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            tick=0.1,
            fee_rate=0.0006,
            minimum_net_reward_risk=1.2,
        )

    def test_acceptance_uses_observed_retest_extreme_for_structural_stop(self) -> None:
        rows = [
            base_row(),
            base_row(
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
            base_row(
                open=100.4,
                high=100.45,
                low=100.02,
                close=100.2,
                volume=100.0,
                trade_count=100.0,
                imbalance=0.1,
                volume_ratio=0.8,
                trade_ratio=0.8,
                close_location=0.42,
            ),
            base_row(
                open=100.2,
                high=100.8,
                low=100.15,
                close=100.7,
                volume=120.0,
                trade_count=120.0,
                imbalance=0.3,
                volume_ratio=1.4,
                trade_ratio=1.4,
                close_location=0.85,
            ),
        ]
        bundle = self._build(
            rows,
            (
                level("completed-high-boundary", LevelKind.HIGH, 100.0),
                level("completed-high-target", LevelKind.HIGH, 105.0),
                level("completed-low", LevelKind.LOW, 95.0),
            ),
        )
        signals = [signal for items in bundle.signals_by_time_ns.values() for signal in items]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertAlmostEqual(signal.structural_stop, 99.99)
        self.assertEqual(signal.details["stop_reference_source"], "ACCEPTANCE_RETEST_LOW")
        self.assertAlmostEqual(signal.details["stop_reference"], 100.02)
        self.assertLess(signal.structural_stop, signal.entry_reference)
        self.assertLess(signal.entry_reference, signal.external_target)
        self.assertGreaterEqual(signal.net_reward_risk, 1.2)
        self.assertEqual(
            [(event.previous_state, event.next_state) for event in signal.events],
            [("IDLE", "ACCEPTED"), ("ACCEPTED", "RETEST_HELD"), ("RETEST_HELD", "CONFIRMED")],
        )

    def test_non_acceptance_first_cross_consumes_completed_level(self) -> None:
        rows = [
            base_row(),
            base_row(
                open=99.9,
                high=100.4,
                low=99.8,
                close=100.1,
                volume=100.0,
                trade_count=100.0,
                imbalance=0.05,
                volume_ratio=1.0,
                trade_ratio=1.0,
                close_location=0.5,
            ),
            base_row(
                open=100.1,
                high=100.8,
                low=100.0,
                close=100.7,
                volume=300.0,
                trade_count=300.0,
                imbalance=0.7,
                volume_ratio=3.0,
                trade_ratio=3.0,
                close_location=0.88,
            ),
        ]
        bundle = self._build(
            rows,
            (
                level("completed-high-boundary", LevelKind.HIGH, 100.0),
                level("completed-high-target", LevelKind.HIGH, 105.0),
            ),
        )
        self.assertEqual(sum(len(items) for items in bundle.signals_by_time_ns.values()), 0)
        self.assertEqual(bundle.diagnostics.get("NON_ACCEPTANCE_INTERACTION_CONSUMED"), 1)
        self.assertEqual(bundle.diagnostics.get("ACCEPTANCE_ARMED", 0), 0)

    def test_signal_contains_no_future_outcome_fields(self) -> None:
        forbidden = {"outcome", "outcome_time", "net_r_proxy", "mfe", "mae"}
        field_names = {item.name for item in fields(AcceptanceSignal)}
        self.assertFalse(any(any(token in name for token in forbidden) for name in field_names))


class NautilusWiringContractTests(unittest.TestCase):
    def test_instrument_has_no_arbitrary_maximum_quantity_or_notional(self) -> None:
        spec = {
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "base_currency": "BTC",
            "price_precision": 1,
            "size_precision": 3,
            "tick_size": "0.1",
            "size_increment": "0.001",
            "min_quantity": "0.001",
        }
        instrument = _build_instrument("BTCUSDT", spec, 0.0006)
        self.assertIsNone(instrument.max_quantity)
        self.assertIsNone(instrument.max_notional)
        self.assertEqual(str(instrument.maker_fee), "0.0006")
        self.assertEqual(str(instrument.taker_fee), "0.0006")
        self.assertEqual(str(_ten_second_bar_type(instrument)), "BTCUSDT-PERP.BINANCE-10-SECOND-LAST-EXTERNAL")

    def test_completed_mark_price_becomes_native_update(self) -> None:
        spec = {
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "base_currency": "BTC",
            "price_precision": 1,
            "size_precision": 3,
            "tick_size": "0.1",
            "size_increment": "0.001",
            "min_quantity": "0.001",
        }
        instrument = _build_instrument("BTCUSDT", spec, 0.0006)
        timestamp = pd.Timestamp("2024-04-08T07:59:59.999Z")
        normalized = pd.DataFrame(
            {"mark_price": [70123.46]},
            index=pd.DatetimeIndex([timestamp]),
        )
        updates = _mark_price_updates_from_frame(normalized, instrument=instrument)
        self.assertEqual(len(updates), 1)
        update = updates[0]
        self.assertEqual(str(update.instrument_id), "BTCUSDT-PERP.BINANCE")
        self.assertEqual(str(update.value), "70123.5")
        self.assertEqual(int(update.ts_event), int(timestamp.as_unit("ns").value))

    def test_normalized_funding_row_becomes_boundary_settling_update(self) -> None:
        spec = {
            "instrument_id": "BTCUSDT-PERP.BINANCE",
            "base_currency": "BTC",
            "price_precision": 1,
            "size_precision": 3,
            "tick_size": "0.1",
            "size_increment": "0.001",
            "min_quantity": "0.001",
        }
        instrument = _build_instrument("BTCUSDT", spec, 0.0006)
        timestamp = pd.Timestamp("2024-04-08T08:00:00Z")
        normalized = pd.DataFrame(
            {"funding_rate": [0.0001], "funding_interval_minutes": [480]},
            index=pd.DatetimeIndex([timestamp]),
        )
        updates = _funding_updates_from_frame(normalized, instrument=instrument)
        self.assertEqual(len(updates), 1)
        update = updates[0]
        self.assertEqual(str(update.instrument_id), "BTCUSDT-PERP.BINANCE")
        self.assertEqual(str(update.rate), "0.0001")
        self.assertEqual(update.interval, 480)
        self.assertIsNone(update.next_funding_ns)
        self.assertEqual(int(update.ts_event), int(timestamp.as_unit("ns").value))


if __name__ == "__main__":
    unittest.main()
