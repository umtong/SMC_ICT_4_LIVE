"""Causal contract tests for direct Session Raid Reversal V1."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import session_raid_reversal_signals_v1 as signals
from day_liquidity_delivery_context_v1 import (
    RAID_FAMILY,
    DayLiquidityDeliveryConfig,
    DrawContext,
    RouteCandidate,
    SessionRange,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource


def _bar(index: int, close: float = 99.8) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=index,
        ts_event_ns=(index + 1) * 300 * 1_000_000_000,
        open=close - 0.1,
        high=close + 0.2,
        low=close - 0.2,
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
    return tuple(_bar(i) for i in range(16))


def _source() -> SessionRange:
    return SessionRange(0, "ASIA_SOURCE", 105.0, 99.0, 0, 11)


def _target() -> ExternalLevel:
    return ExternalLevel(
        level_id="previous-day-high",
        kind=LevelKind.HIGH,
        source=LevelSource.DAY,
        level=110.0,
        formed_index=0,
        formed_time_ns=0,
        period_key="previous-day",
    )


def _draw() -> DrawContext:
    return DrawContext(1, "h4-high", 101.0, "h4-low", 95.0, 3600, 2, 4.0)


def _candidate(*, raid_high: float = 100.0) -> RouteCandidate:
    return RouteCandidate(
        scenario_id="BTCUSDT-day-delivery-00001-test",
        family=RAID_FAMILY,
        route_name="EUROPE_ROUTE",
        source_name="ASIA_SOURCE",
        route_start_ns=12 * 300 * 1_000_000_000,
        route_end_ns=16 * 300 * 1_000_000_000,
        direction=1,
        draw=_draw(),
        target=_target(),
        boundary_id="ASIA_SOURCE-0-LOW",
        boundary_source="ASIA_SOURCE_LOW",
        boundary_level=99.0,
        interaction_time_ns=13 * 300 * 1_000_000_000,
        trigger_time_ns=13 * 300 * 1_000_000_000,
        trigger_five_index=12,
        structural_reference=98.5,
        interaction_details={"raid_high": raid_high, "raid_low": 98.5},
    )


def _data(entry: float = 99.8, extra_rows: int = 0) -> pd.DataFrame:
    end_ns = 16 * 300 * 1_000_000_000 + extra_rows * 10 * 1_000_000_000
    index = pd.date_range(
        pd.Timestamp(0, tz="UTC") + pd.Timedelta(seconds=10),
        pd.Timestamp(end_ns, tz="UTC"),
        freq="10s",
    )
    values = np.full(len(index), entry)
    return pd.DataFrame(
        {
            "open": values,
            "high": values + 0.1,
            "low": values - 0.1,
            "close": values,
            "volume": np.full(len(index), 10.0),
        },
        index=index,
    )


class SessionRaidReversalTests(unittest.TestCase):
    def _build(self, *, entry: float = 99.8, raid_high: float = 100.0, extra_rows: int = 0):
        bars = _bars()
        candidate = _candidate(raid_high=raid_high)
        contexts = tuple(_draw() for _ in bars)
        snapshots = tuple((_target(),) for _ in bars)
        with (
            patch.object(signals, "build_draw_contexts", return_value=contexts),
            patch.object(signals, "build_route_candidates", return_value=(candidate,)),
            patch.object(signals, "build_session_ranges", return_value={(0, "ASIA_SOURCE"): _source()}),
            patch.object(signals, "target_still_active", return_value=True),
        ):
            return signals.build_session_raid_reversal_signals(
                data=_data(entry, extra_rows),
                context_times=np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64),
                context_bars=bars,
                snapshots=snapshots,
                symbol="BTCUSDT",
                instrument_id="BTCUSDT-PERP.BINANCE",
                tick=0.1,
                fee_rate=0.0006,
                minimum_net_reward_risk=1.2,
                day_config=DayLiquidityDeliveryConfig(),
            )

    def test_direct_signal_uses_first_bucket_after_completed_raid(self) -> None:
        bundle = self._build()
        self.assertEqual(bundle.diagnostics["SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        self.assertGreater(signal.signal_time_ns, _candidate().trigger_time_ns)
        self.assertEqual(signal.scenario_family, signals.SCENARIO_FAMILY)
        self.assertEqual(signal.external_target, 105.0)
        self.assertLess(signal.structural_stop, signal.entry_reference)
        self.assertEqual(signal.scenario_id, signal.events[0].scenario_id)
        self.assertFalse(signal.details["scalping_alpha_inputs"])

    def test_entry_outside_source_half_is_rejected(self) -> None:
        bundle = self._build(entry=103.0)
        self.assertEqual(bundle.diagnostics.get("SIGNAL", 0), 0)
        self.assertEqual(bundle.diagnostics["ENTRY_OUTSIDE_REQUIRED_SOURCE_SESSION_HALF"], 1)

    def test_raid_bar_cannot_preconsume_source_opposite_target(self) -> None:
        bundle = self._build(raid_high=105.0)
        self.assertEqual(bundle.diagnostics.get("SIGNAL", 0), 0)
        self.assertEqual(
            bundle.diagnostics["SOURCE_OPPOSITE_LIQUIDITY_ALREADY_CONSUMED_BY_RAID_BAR"],
            1,
        )

    def test_future_suffix_does_not_change_existing_signal(self) -> None:
        left_bundle = self._build(extra_rows=0)
        right_bundle = self._build(extra_rows=12)
        left = next(iter(next(iter(left_bundle.signals_by_time_ns.values()))))
        right = next(iter(next(iter(right_bundle.signals_by_time_ns.values()))))
        self.assertEqual(left.signal_time_ns, right.signal_time_ns)
        self.assertEqual(left.external_target, right.external_target)
        self.assertEqual(left.structural_stop, right.structural_stop)
        self.assertEqual(left.net_reward_risk, right.net_reward_risk)


if __name__ == "__main__":
    unittest.main()
