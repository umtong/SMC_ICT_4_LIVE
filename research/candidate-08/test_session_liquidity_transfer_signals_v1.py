"""Causal contract tests for Session Liquidity Transfer V1."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import session_liquidity_transfer_signals_v1 as signals
from day_liquidity_delivery_context_v1 import (
    RAID_FAMILY,
    DayLiquidityDeliveryConfig,
    DrawContext,
    RouteCandidate,
    SessionRange,
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


def _bars(*, directional: bool = True) -> tuple[FiveMinuteBar, ...]:
    rows = [_bar(i, open_=100.2, high=100.5, low=100.0, close=100.2) for i in range(16)]
    rows[13] = _bar(
        13,
        open_=99.4 if directional else 100.0,
        high=100.2,
        low=99.0,
        close=99.8,
    )
    return tuple(rows)


def _source() -> SessionRange:
    return SessionRange(
        day_start_ns=0,
        name="ASIA_SOURCE",
        high=105.0,
        low=99.0,
        first_five_index=0,
        last_five_index=11,
    )


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
    return DrawContext(
        direction=1,
        break_swing_id="h4-high",
        break_level=101.0,
        origin_swing_id="h4-low",
        origin_level=95.0,
        observed_time_ns=10 * 300 * 1_000_000_000,
        h4_index=2,
        h4_atr=4.0,
    )


def _candidate() -> RouteCandidate:
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
        interaction_details={"raid_high": 100.0, "raid_low": 98.5},
    )


def _ten_second_data(extra_rows: int = 0) -> pd.DataFrame:
    end_ns = 16 * 300 * 1_000_000_000 + extra_rows * 10 * 1_000_000_000
    index = pd.date_range(
        pd.Timestamp(0, tz="UTC") + pd.Timedelta(seconds=10),
        pd.Timestamp(end_ns, tz="UTC"),
        freq="10s",
    )
    values = np.full(len(index), 99.8)
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


class SessionLiquidityTransferTests(unittest.TestCase):
    def test_first_boundary_retest_contract(self) -> None:
        valid = _bar(0, open_=99.4, high=100.2, low=99.0, close=99.8)
        self.assertEqual(
            signals._first_boundary_retest(
                valid,
                direction=1,
                boundary=99.0,
                tolerance_atr=0.05,
                require_directional_close=True,
            ),
            "VALID_RETEST",
        )
        non_directional = _bar(0, open_=100.0, high=100.2, low=99.0, close=99.8)
        self.assertEqual(
            signals._first_boundary_retest(
                non_directional,
                direction=1,
                boundary=99.0,
                tolerance_atr=0.05,
                require_directional_close=True,
            ),
            "NON_DIRECTIONAL_FIRST_TOUCH",
        )

    def test_target_is_nearest_source_or_frozen_htf_liquidity(self) -> None:
        selected = signals._select_target(
            candidate=_candidate(),
            source=_source(),
            entry=99.8,
            tick=0.1,
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected.target_level, 105.0)
        self.assertEqual(selected.target_source, "ASIA_SOURCE_HIGH")

    def _build(self, *, directional: bool = True, extra_rows: int = 0):
        bars = _bars(directional=directional)
        contexts = tuple(_draw() for _ in bars)
        snapshots = tuple((_target(),) for _ in bars)
        candidate = _candidate()
        with (
            patch.object(signals, "build_draw_contexts", return_value=contexts),
            patch.object(signals, "build_route_candidates", return_value=(candidate,)),
            patch.object(signals, "build_session_ranges", return_value={(0, "ASIA_SOURCE"): _source()}),
            patch.object(signals, "same_draw", return_value=True),
            patch.object(signals, "target_still_active", return_value=True),
        ):
            return signals.build_session_liquidity_transfer_signals(
                data=_ten_second_data(extra_rows),
                context_times=np.asarray([bar.ts_event_ns for bar in bars], dtype=np.int64),
                context_bars=bars,
                snapshots=snapshots,
                symbol="BTCUSDT",
                instrument_id="BTCUSDT-PERP.BINANCE",
                tick=0.1,
                fee_rate=0.0006,
                minimum_net_reward_risk=1.2,
                day_config=DayLiquidityDeliveryConfig(),
                transfer_config=signals.SessionLiquidityTransferConfig(),
            )

    def test_full_builder_emits_only_after_completed_five_minute_retest(self) -> None:
        bundle = self._build()
        self.assertEqual(bundle.diagnostics["SIGNAL"], 1)
        signal = next(iter(next(iter(bundle.signals_by_time_ns.values()))))
        self.assertGreater(signal.signal_time_ns, _bars()[13].ts_event_ns)
        self.assertEqual(signal.scenario_family, signals.SCENARIO_FAMILY)
        self.assertEqual(signal.external_target, 105.0)
        self.assertLess(signal.structural_stop, signal.entry_reference)
        self.assertEqual(len(signal.events), 2)

    def test_non_directional_first_touch_is_terminal(self) -> None:
        bundle = self._build(directional=False)
        self.assertEqual(bundle.diagnostics.get("SIGNAL", 0), 0)
        self.assertEqual(
            bundle.diagnostics["FIRST_SOURCE_BOUNDARY_RETEST_NON_DIRECTIONAL_FIRST_TOUCH"],
            1,
        )

    def test_future_suffix_does_not_change_existing_signal(self) -> None:
        original = self._build(extra_rows=0)
        extended = self._build(extra_rows=12)
        left = next(iter(next(iter(original.signals_by_time_ns.values()))))
        right = next(iter(next(iter(extended.signals_by_time_ns.values()))))
        self.assertEqual(left.signal_time_ns, right.signal_time_ns)
        self.assertEqual(left.external_target, right.external_target)
        self.assertEqual(left.structural_stop, right.structural_stop)
        self.assertEqual(left.net_reward_risk, right.net_reward_risk)


if __name__ == "__main__":
    unittest.main()
