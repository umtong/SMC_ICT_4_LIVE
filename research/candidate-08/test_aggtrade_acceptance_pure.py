from __future__ import annotations

import ast
from dataclasses import dataclass, fields
from enum import Enum
from pathlib import Path
import tempfile
import sys
import types
import unittest
import zipfile

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The production repository supplies range_fvg_logic. The fallback makes the market-state and
# evaluation contracts executable in a minimal review environment without installing Nautilus.
try:
    import range_fvg_logic as _range_logic  # noqa: F401
except ModuleNotFoundError:
    class LevelKind(Enum):
        HIGH = "HIGH"
        LOW = "LOW"

    class LevelSource(Enum):
        FOUR_HOUR = "FOUR_HOUR"
        DAY = "DAY"
        WEEK = "WEEK"

    SOURCE_RANK = {
        LevelSource.FOUR_HOUR: 1,
        LevelSource.DAY: 2,
        LevelSource.WEEK: 3,
    }

    @dataclass(frozen=True, slots=True)
    class ExternalLevel:
        level_id: str
        kind: LevelKind
        source: LevelSource
        level: float
        formed_index: int
        formed_time_ns: int
        period_key: str

    @dataclass(frozen=True, slots=True)
    class FiveMinuteBar:
        index: int
        ts_event_ns: int
        open: float
        high: float
        low: float
        close: float
        volume: float
        trade_count: float
        taker_buy_volume: float
        imbalance: float
        atr: float
        volume_ratio: float
        trade_ratio: float
        efficiency_60m: float
        direction_60m: float
        session_key: str
        day_key: str
        week_key: str

    module = types.ModuleType("range_fvg_logic")
    for name, value in {
        "LevelKind": LevelKind,
        "LevelSource": LevelSource,
        "SOURCE_RANK": SOURCE_RANK,
        "ExternalLevel": ExternalLevel,
        "FiveMinuteBar": FiveMinuteBar,
    }.items():
        setattr(module, name, value)
    sys.modules["range_fvg_logic"] = module

from range_fvg_logic import ExternalLevel, FiveMinuteBar, LevelKind, LevelSource
from aggtrade_acceptance_funding import (
    FundingDataError,
    _normalize_funding_frame,
    _read_month,
    causal_funding_cost_state,
    funding_observations_from_frame,
)
from aggtrade_acceptance_mark_price import (
    _normalize_mark_price_frame,
    _read_month as _read_mark_price_month,
)
from aggtrade_acceptance_evaluation import (
    fill_and_risk_contract_checks,
    suite_summary,
)
from aggtrade_acceptance_signals import (
    AcceptanceSignal,
    _context_for_ten_second_close,
    build_acceptance_signals,
    causal_stop_slippage_reserve_series,
)


def level(level_id: str, kind: LevelKind, value: float, source: LevelSource = LevelSource.FOUR_HOUR) -> ExternalLevel:
    return ExternalLevel(
        level_id=level_id,
        kind=kind,
        source=source,
        level=value,
        formed_index=0,
        formed_time_ns=1,
        period_key="p0",
    )


def five_bar(index: int, timestamp_ns: int, atr: float = 1.0) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=index,
        ts_event_ns=timestamp_ns,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        volume=1000.0,
        trade_count=1000.0,
        taker_buy_volume=500.0,
        imbalance=0.0,
        atr=atr,
        volume_ratio=1.0,
        trade_ratio=1.0,
        efficiency_60m=0.1,
        direction_60m=1.0,
        session_key="s0",
        day_key="d0",
        week_key="w0",
    )


def row(**overrides: float) -> dict[str, float]:
    value = {
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
    value.update(overrides)
    return value


def acceptance_rows() -> list[dict[str, float]]:
    return [
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
            volume=100.0,
            trade_count=100.0,
            imbalance=0.1,
            volume_ratio=0.8,
            trade_ratio=0.8,
            close_location=0.42,
        ),
        row(
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


def build(rows: list[dict[str, float]]):
    index = pd.date_range("2024-01-01T00:00:10Z", periods=len(rows), freq="10s")
    data = pd.DataFrame(rows, index=index)
    before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
    after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
    levels = (
        level("boundary", LevelKind.HIGH, 100.0),
        level("target", LevelKind.HIGH, 105.0),
        level("opposite", LevelKind.LOW, 95.0),
    )
    return build_acceptance_signals(
        data=data,
        context_times=np.asarray([before, after], dtype=np.int64),
        context_bars=(five_bar(0, before), five_bar(1, after)),
        snapshots=(levels, levels),
        symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        tick=0.1,
        fee_rate=0.0006,
        minimum_net_reward_risk=1.2,
    )


class DetectorContracts(unittest.TestCase):
    def test_retest_extreme_is_structural_invalidation(self) -> None:
        bundle = build(acceptance_rows())
        signals = [signal for items in bundle.signals_by_time_ns.values() for signal in items]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertAlmostEqual(signal.structural_stop, 99.99)
        self.assertAlmostEqual(signal.details["stop_reference"], 100.02)
        self.assertEqual(signal.details["stop_reference_source"], "ACCEPTANCE_RETEST_LOW")
        self.assertEqual(
            [(item.previous_state, item.next_state) for item in signal.events],
            [("IDLE", "ACCEPTED"), ("ACCEPTED", "RETEST_HELD"), ("RETEST_HELD", "CONFIRMED")],
        )

    def test_stop_slippage_reserve_is_shifted_and_causal(self) -> None:
        index = pd.date_range("2024-01-01T00:00:10Z", periods=50, freq="10s")
        frame = pd.DataFrame(
            {
                "high": [101.0] * 49 + [130.0],
                "low": [99.0] * 50,
                "close": [100.0] * 50,
            },
            index=index,
        )
        reserve = causal_stop_slippage_reserve_series(
            frame,
            tick=0.1,
            lookback_bars=36,
            quantile=0.99,
        )
        # The current 31-point shock is not available to its own reserve. It becomes observable
        # only for the next completed bucket.
        self.assertAlmostEqual(float(reserve.iloc[-1]), 2.0)
        extended = pd.concat(
            [
                frame,
                pd.DataFrame(
                    {"high": [100.5], "low": [99.5], "close": [100.0]},
                    index=[index[-1] + pd.Timedelta(seconds=10)],
                ),
            ]
        )
        next_reserve = causal_stop_slippage_reserve_series(
            extended,
            tick=0.1,
            lookback_bars=36,
            quantile=0.99,
        )
        self.assertGreater(float(next_reserve.iloc[-1]), 2.0)

    def test_future_rows_do_not_change_already_observed_signal(self) -> None:
        first = build(acceptance_rows())
        second = build(acceptance_rows() + [row(close=101.0), row(close=98.0)])
        first_signal = next(iter(next(iter(first.signals_by_time_ns.values()))))
        second_signal = second.signals_by_time_ns[first_signal.signal_time_ns][0]
        self.assertEqual(first_signal, second_signal)

    def test_exact_five_minute_close_preserves_prebar_boundary_and_postbar_target(self) -> None:
        # Production Binance klines close at xx:xx:59.999 while the corresponding aggTrade
        # bucket is stamped at the exact next second.
        kline_t0 = pd.Timestamp("2024-01-01T00:04:59.999Z").as_unit("ns").value
        kline_t1 = pd.Timestamp("2024-01-01T00:09:59.999Z").as_unit("ns").value
        agg_t0 = pd.Timestamp("2024-01-01T00:05:00Z").as_unit("ns").value
        old_boundary = level("old-boundary", LevelKind.HIGH, 100.0)
        new_target = level("new-target", LevelKind.HIGH, 105.0, LevelSource.DAY)
        snapshots = ((old_boundary,), (new_target,))
        context = _context_for_ten_second_close(
            timestamp_ns=int(agg_t0),
            context_times=np.asarray([kline_t0, kline_t1], dtype=np.int64),
            context_bars=(five_bar(0, int(kline_t0)), five_bar(1, int(kline_t1))),
            snapshots=snapshots,
        )
        self.assertIsNotNone(context)
        assert context is not None
        _, before, after = context
        self.assertEqual(before, (old_boundary,))
        self.assertEqual(after, (new_target,))

    def test_ten_seconds_after_boundary_uses_postbar_state_for_both_views(self) -> None:
        kline_t0 = pd.Timestamp("2024-01-01T00:04:59.999Z").as_unit("ns").value
        kline_t1 = pd.Timestamp("2024-01-01T00:09:59.999Z").as_unit("ns").value
        agg_time = pd.Timestamp("2024-01-01T00:05:10Z").as_unit("ns").value
        old_boundary = level("old-boundary", LevelKind.HIGH, 100.0)
        new_target = level("new-target", LevelKind.HIGH, 105.0, LevelSource.DAY)
        context = _context_for_ten_second_close(
            timestamp_ns=int(agg_time),
            context_times=np.asarray([kline_t0, kline_t1], dtype=np.int64),
            context_bars=(five_bar(0, int(kline_t0)), five_bar(1, int(kline_t1))),
            snapshots=((old_boundary,), (new_target,)),
        )
        self.assertIsNotNone(context)
        assert context is not None
        _, before, after = context
        self.assertEqual(before, (new_target,))
        self.assertEqual(after, (new_target,))

    def test_sequence_expiry_uses_elapsed_time_not_observed_row_count(self) -> None:
        index = pd.DatetimeIndex(
            [
                pd.Timestamp("2024-01-01T00:00:10Z"),
                pd.Timestamp("2024-01-01T00:00:20Z"),
                pd.Timestamp("2024-01-01T00:02:00Z"),
            ]
        )
        rows = acceptance_rows()[:2] + [acceptance_rows()[2]]
        data = pd.DataFrame(rows, index=index)
        before = int((index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
        after = int((index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
        levels = (
            level("boundary", LevelKind.HIGH, 100.0),
            level("target", LevelKind.HIGH, 105.0),
        )
        bundle = build_acceptance_signals(
            data=data,
            context_times=np.asarray([before, after], dtype=np.int64),
            context_bars=(five_bar(0, before), five_bar(1, after)),
            snapshots=(levels, levels),
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            tick=0.1,
            fee_rate=0.0006,
            minimum_net_reward_risk=1.2,
        )
        self.assertEqual(bundle.diagnostics.get("ACCEPTANCE_SEQUENCE_TIMEOUT"), 1)
        self.assertEqual(sum(len(items) for items in bundle.signals_by_time_ns.values()), 0)

    def test_signal_type_has_no_outcome_or_path_proxy(self) -> None:
        names = {item.name for item in fields(AcceptanceSignal)}
        forbidden = ("outcome", "mfe", "mae", "net_r_proxy")
        self.assertFalse(any(any(token in name for token in forbidden) for name in names))


    def test_retest_contraction_ablation_removes_only_energy_filter(self) -> None:
        rows = [
            row(),
            row(
                open=99.9, high=100.5, low=99.9, close=100.4, volume=200.0,
                trade_count=200.0, imbalance=0.5, volume_ratio=2.0, trade_ratio=2.0,
                close_location=0.83,
            ),
            # Touches and holds the accepted boundary but is deliberately not contracted.
            row(
                open=100.4, high=100.45, low=100.02, close=100.2, volume=190.0,
                trade_count=195.0, imbalance=0.45, volume_ratio=1.2, trade_ratio=1.2,
                close_location=0.42,
            ),
            row(
                open=100.2, high=100.8, low=100.15, close=100.7, volume=210.0,
                trade_count=210.0, imbalance=0.3, volume_ratio=1.4, trade_ratio=1.4,
                close_location=0.85,
            ),
        ]
        index = pd.date_range("2024-01-01T00:00:10Z", periods=len(rows), freq="10s")
        data = pd.DataFrame(rows, index=index)
        context_time = int((data.index[0] - pd.Timedelta(seconds=10)).as_unit("ns").value)
        future_context_time = int((data.index[-1] + pd.Timedelta(minutes=5)).as_unit("ns").value)
        bars = (five_bar(0, context_time), five_bar(1, future_context_time))
        levels = (
            level("boundary", LevelKind.HIGH, 100.0),
            level("target", LevelKind.HIGH, 105.0),
            level("low", LevelKind.LOW, 95.0),
        )
        kwargs = dict(
            data=data,
            context_times=np.asarray([context_time, future_context_time], dtype=np.int64),
            context_bars=bars,
            snapshots=(levels, levels),
            symbol="BTCUSDT",
            instrument_id="BTCUSDT-PERP.BINANCE",
            tick=0.1,
            fee_rate=0.0006,
            minimum_net_reward_risk=1.2,
        )
        base = build_acceptance_signals(**kwargs, require_retest_contraction=True)
        ablated = build_acceptance_signals(**kwargs, require_retest_contraction=False)
        self.assertEqual(sum(len(items) for items in base.signals_by_time_ns.values()), 0)
        self.assertEqual(sum(len(items) for items in ablated.signals_by_time_ns.values()), 1)
        signal = next(iter(next(iter(ablated.signals_by_time_ns.values()))))
        self.assertEqual(signal.details["stop_reference_source"], "ACCEPTANCE_RETEST_LOW")


class FundingDataContracts(unittest.TestCase):
    @staticmethod
    def _calc_ms(value: str) -> int:
        return int(pd.Timestamp(value).timestamp() * 1000)

    def test_official_archive_schema_normalizes_hours_to_nautilus_minutes(self) -> None:
        csv_text = "\n".join(
            [
                "calc_time,funding_interval_hours,last_funding_rate",
                f"{self._calc_ms('2024-04-08T00:00:00Z')},8,0.0001",
                f"{self._calc_ms('2024-04-08T08:00:00Z')},8,-0.00005",
                f"{self._calc_ms('2024-04-08T16:00:00Z')},8,0.0002",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "BTCUSDT-fundingRate-2024-04.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("BTCUSDT-fundingRate-2024-04.csv", csv_text)
            raw = _read_month(archive_path)
        normalized, quality = _normalize_funding_frame(
            raw,
            start=pd.Timestamp("2024-04-08T00:00:00Z").to_pydatetime(),
            end=pd.Timestamp("2024-04-09T00:00:00Z").to_pydatetime(),
        )
        self.assertEqual(list(normalized["funding_interval_minutes"]), [480, 480, 480])
        self.assertEqual(list(normalized["funding_rate"]), [0.0001, -0.00005, 0.0002])
        self.assertEqual(quality["rows"], 3)
        self.assertEqual(quality["internal_gap_count"], 0)
        self.assertEqual(quality["timestamp_unit_detected"], "ms")

    def test_millisecond_funding_boundary_jitter_is_canonicalized(self) -> None:
        start = pd.Timestamp("2024-04-08T00:00:00Z").to_pydatetime()
        end = pd.Timestamp("2024-04-09T00:00:00Z").to_pydatetime()
        frame = pd.DataFrame(
            {
                "calc_time": [
                    self._calc_ms("2024-04-08T00:00:00Z") + 1,
                    self._calc_ms("2024-04-08T08:00:00Z") + 6,
                ],
                "funding_interval_hours": [8, 8],
                "last_funding_rate": [0.0001, -0.00005],
            }
        )
        normalized, quality = _normalize_funding_frame(frame, start=start, end=end)
        self.assertEqual(
            list(normalized.index),
            [pd.Timestamp("2024-04-08T00:00:00Z"), pd.Timestamp("2024-04-08T08:00:00Z")],
        )
        self.assertEqual(quality["internal_gap_count"], 0)
        self.assertAlmostEqual(quality["max_boundary_jitter_milliseconds"], 6.0)

    def test_off_boundary_or_conflicting_duplicate_funding_is_rejected(self) -> None:
        start = pd.Timestamp("2024-04-08T00:00:00Z").to_pydatetime()
        end = pd.Timestamp("2024-04-09T00:00:00Z").to_pydatetime()
        off_boundary = pd.DataFrame(
            {
                "calc_time": [self._calc_ms("2024-04-08T01:00:00Z")],
                "funding_interval_hours": [8],
                "last_funding_rate": [0.0001],
            }
        )
        with self.assertRaises(FundingDataError):
            _normalize_funding_frame(off_boundary, start=start, end=end)

        duplicate = pd.DataFrame(
            {
                "calc_time": [
                    self._calc_ms("2024-04-08T00:00:00Z"),
                    self._calc_ms("2024-04-08T00:00:00Z"),
                ],
                "funding_interval_hours": [8, 8],
                "last_funding_rate": [0.0001, 0.0002],
            }
        )
        with self.assertRaises(FundingDataError):
            _normalize_funding_frame(duplicate, start=start, end=end)

    def test_expected_funding_reserve_uses_only_last_observation_at_signal(self) -> None:
        index = pd.DatetimeIndex(
            [
                pd.Timestamp("2024-04-08T00:00:00Z"),
                pd.Timestamp("2024-04-08T08:00:00Z"),
            ]
        )
        normalized = pd.DataFrame(
            {
                "funding_rate": [0.0001, 0.005],
                "funding_interval_minutes": [480, 480],
            },
            index=index,
        )
        observations = funding_observations_from_frame(normalized)
        signal_time = int(pd.Timestamp("2024-04-08T06:00:00Z").as_unit("ns").value)
        state = causal_funding_cost_state(
            observations,
            signal_time_ns=signal_time,
            entry_price=100.0,
            maximum_hold_minutes=240,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(
            state["funding_observed_time_ns"],
            int(pd.Timestamp("2024-04-08T00:00:00Z").as_unit("ns").value),
        )
        self.assertEqual(state["funding_rate_observed"], 0.0001)
        self.assertEqual(state["expected_funding_crossings"], 1)
        self.assertAlmostEqual(state["minutes_to_next_funding"], 120.0)
        self.assertAlmostEqual(state["expected_funding_reserve_per_unit"], 0.01)

    def test_expected_funding_reserve_counts_all_boundaries_in_hold(self) -> None:
        normalized = pd.DataFrame(
            {"funding_rate": [-0.0002], "funding_interval_minutes": [240]},
            index=pd.DatetimeIndex([pd.Timestamp("2024-04-08T00:00:00Z")]),
        )
        observations = funding_observations_from_frame(normalized)
        signal_time = int(pd.Timestamp("2024-04-08T01:00:00Z").as_unit("ns").value)
        state = causal_funding_cost_state(
            observations,
            signal_time_ns=signal_time,
            entry_price=200.0,
            maximum_hold_minutes=480,
        )
        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["expected_funding_crossings"], 2)
        self.assertAlmostEqual(state["expected_funding_reserve_per_unit"], 0.08)

    def test_expected_funding_reserve_refuses_future_only_state(self) -> None:
        normalized = pd.DataFrame(
            {"funding_rate": [0.0001], "funding_interval_minutes": [480]},
            index=pd.DatetimeIndex([pd.Timestamp("2024-04-08T08:00:00Z")]),
        )
        observations = funding_observations_from_frame(normalized)
        signal_time = int(pd.Timestamp("2024-04-08T07:00:00Z").as_unit("ns").value)
        self.assertIsNone(
            causal_funding_cost_state(
                observations,
                signal_time_ns=signal_time,
                entry_price=100.0,
                maximum_hold_minutes=240,
            )
        )


class MarkPriceDataContracts(unittest.TestCase):
    @staticmethod
    def _close_ms(value: str) -> int:
        return int(pd.Timestamp(value).timestamp() * 1000)

    def test_mark_price_archive_normalizes_completed_one_minute_closes(self) -> None:
        rows = [
            [
                self._close_ms("2024-04-08T00:00:00Z"),
                "100.0",
                "101.0",
                "99.0",
                "100.5",
                "0",
                self._close_ms("2024-04-08T00:00:59.999Z"),
                "0",
                "0",
                "0",
                "0",
                "0",
            ],
            [
                self._close_ms("2024-04-08T00:01:00Z"),
                "100.5",
                "101.0",
                "100.0",
                "100.7",
                "0",
                self._close_ms("2024-04-08T00:01:59.999Z"),
                "0",
                "0",
                "0",
                "0",
                "0",
            ],
        ]
        csv_text = "\n".join(",".join(map(str, row)) for row in rows)
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "BTCUSDT-1m-2024-04.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("BTCUSDT-1m-2024-04.csv", csv_text)
            raw = _read_mark_price_month(archive_path)
        normalized, quality = _normalize_mark_price_frame(
            raw,
            start=pd.Timestamp("2024-04-08T00:00:00Z").to_pydatetime(),
            end=pd.Timestamp("2024-04-08T00:02:00Z").to_pydatetime(),
        )
        self.assertEqual(list(normalized["mark_price"]), [100.5, 100.7])
        self.assertEqual(quality["rows"], 2)
        self.assertEqual(quality["missing_ratio"], 0.0)
        self.assertEqual(quality["timestamp_unit_detected"], "ms")




class EvaluationContracts(unittest.TestCase):
    def test_fill_adjusted_and_realized_budget_excess_are_separate(self) -> None:
        intents = [
            {
                "scenario_id": "s1",
                "signal_time_ns": 100,
                "entry_fill_time_ns": 110,
                "entry_fill_price": 101.0,
                "risk_budget": 3000.0,
                "planned_stop_loss": 2999.0,
                "fill_adjusted_expected_stop_loss": 3010.0,
                "entry_reference": 100.0,
                "funding_observed_time_ns": 90,
                "funding_rate_observed": 0.0001,
                "funding_interval_minutes": 480,
                "expected_funding_crossings": 1,
                "expected_funding_reserve_per_unit": 0.01,
            }
        ]
        trades = [
            {
                "scenario_id": "s1",
                "symbol": "BTCUSDT",
                "entry_fill_time_ns": 110,
                "position_close_time_ns": 120,
                "realized_pnl": -3050.0,
                "close_reason": "STRUCTURAL_STOP",
            }
        ]
        checks = fill_and_risk_contract_checks(intents, trades)
        self.assertEqual(checks["planned_loss_over_budget_count"], 0)
        self.assertEqual(checks["fill_adjusted_loss_over_budget_count"], 1)
        self.assertEqual(checks["realized_loss_over_budget_count"], 1)
        self.assertEqual(checks["entry_fill_before_signal_count"], 0)
        self.assertEqual(checks["nonpositive_position_holding_time_count"], 0)

    def test_same_timestamp_entry_and_exit_is_rejected_as_bar_lookahead(self) -> None:
        intent = {
            "scenario_id": "same-bar",
            "signal_time_ns": 100,
            "entry_fill_time_ns": 110,
            "entry_fill_price": 101.0,
            "risk_budget": 3000.0,
            "planned_stop_loss": 2990.0,
            "fill_adjusted_expected_stop_loss": 2995.0,
            "funding_observed_time_ns": 90,
            "funding_rate_observed": 0.0001,
            "funding_interval_minutes": 480,
            "expected_funding_crossings": 0,
            "expected_funding_reserve_per_unit": 0.0,
        }
        trade = {
            "scenario_id": "same-bar",
            "entry_fill_time_ns": 110,
            "position_close_time_ns": 110,
            "realized_pnl": -100.0,
            "close_reason": "STRUCTURAL_STOP",
        }
        checks = fill_and_risk_contract_checks([intent], [trade])
        self.assertEqual(checks["nonpositive_position_holding_time_count"], 1)
        self.assertEqual(checks["missing_position_close_time_count"], 0)

    def test_screen_gate_implements_all_predeclared_dimensions(self) -> None:
        zero_contracts = {
            "entry_fill_before_signal_count": 0,
            "planned_loss_over_budget_count": 0,
            "fill_adjusted_loss_over_budget_count": 0,
            "realized_loss_over_budget_count": 0,
            "missing_entry_fill_time_count": 0,
            "missing_entry_fill_price_count": 0,
            "missing_funding_cost_state_count": 0,
            "funding_observation_after_signal_count": 0,
            "invalid_funding_reserve_count": 0,
            "unmatched_closed_trade_count": 0,
            "missing_position_close_time_count": 0,
            "nonpositive_position_holding_time_count": 0,
        }
        results = []
        for index in range(3):
            trades = [
                {"scenario_id": f"w{index}-a", "symbol": "BTCUSDT", "realized_pnl": 1200.0, "close_reason": "EXTERNAL_TARGET"},
                {"scenario_id": f"w{index}-b", "symbol": "ETHUSDT", "realized_pnl": 900.0, "close_reason": "EXTERNAL_TARGET"},
                {"scenario_id": f"w{index}-c", "symbol": "SOLUSDT", "realized_pnl": -500.0, "close_reason": "STRUCTURAL_STOP"},
            ]
            results.append(
                {
                    "window": {"name": f"screen-0{index + 1}"},
                    "calendar_days": 7.0,
                    "nav_multiple": 1.08,
                    "final_nav_usdt": 108000.0,
                    "total_return": 0.08,
                    "daily_geometric_growth": 1.08 ** (1 / 7) - 1,
                    "maximum_realized_equity_drawdown": 0.03,
                    "position_metrics": {"closed_trades": 3, "wins": 2, "win_rate": 2 / 3},
                    "closed_trade_records": trades,
                    "first_window_gate_passed": True,
                    "execution_failures": 0,
                    "open_positions_after_run": 0,
                    "open_orders_after_run": 0,
                    "unexpected_or_liquidation_closes": 0,
                    "contract_checks": zero_contracts,
                    "unprocessed_signal_times": 0,
                }
            )
        config = {
            "candidate": "test",
            "suites": {
                "screen": [dict(item["window"]) for item in results],
            },
            "screen_gate": {
                "minimum_closed_trades_per_week": 3,
                "all_three_cost_after_positive": True,
                "minimum_positive_trade_share": 0.45,
                "maximum_single_positive_pnl_share": 0.5,
                "combined_daily_geometric_growth": 0.01,
                "no_execution_failures": True,
                "no_residual_exposure": True,
            },
        }
        summary = suite_summary(config, "screen", results)
        self.assertTrue(summary["suite_gate_passed"])
        self.assertTrue(all(summary["suite_gate_checks"].values()))
        self.assertGreaterEqual(summary["combined_daily_geometric_growth"], 0.01)

        wrong = [dict(item) for item in results]
        wrong[2] = {**wrong[2], "window": {"name": "not-predeclared"}}
        rejected = suite_summary(config, "screen", wrong)
        self.assertFalse(rejected["suite_gate_checks"]["exactly_three_predeclared_windows"])
        self.assertFalse(rejected["suite_gate_passed"])

        ablated = [{**item, "ablation": "remove_retest_contraction"} for item in results]
        ablation_summary = suite_summary(config, "screen", ablated)
        self.assertFalse(ablation_summary["promotable"])
        self.assertFalse(ablation_summary["suite_gate_checks"]["base_contract_not_ablated"])
        self.assertFalse(ablation_summary["suite_gate_passed"])


class StaticWiringContracts(unittest.TestCase):
    def test_strategy_uses_one_market_bracket_and_global_availability_scan(self) -> None:
        source = (HERE / "aggtrade_acceptance_strategy.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("entry_order_type=OrderType.MARKET", source)
        self.assertEqual(source.count("self.submit_order_list(orders)"), 1)
        self.assertIn("return super().__new__(cls, config)", source)
        self.assertIn("super().__init__(config)", source)
        self.assertIn("entry_order, stop_order, target_order = orders", source)
        self.assertNotIn("orders.orders", source)
        self.assertIn("for instrument_id in self.instrument_ids", source)
        self.assertIn("self.portfolio.is_flat(instrument_id)", source)
        self.assertIn("self.cache.orders_open(instrument_id=instrument_id)", source)
        self.assertIn("risk_sized_quantity(", source)
        self.assertIn("causal_funding_cost_state(", source)
        self.assertIn('"expected_funding_reserve_per_unit"', source)
        self.assertNotIn("from logic import minutes_to_next_funding", source)
        self.assertNotIn("risk_multiplier", source)
        self.assertTrue(any(isinstance(node, ast.ClassDef) and node.name == "AggTradeAcceptanceStrategy" for node in ast.walk(tree)))

    def test_runner_keeps_single_shared_account_and_no_arbitrary_max_notional(self) -> None:
        source = (HERE / "run_aggtrade_acceptance_nautilus.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("engine.add_venue("), 1)
        self.assertIn("max_quantity=None", source)
        self.assertIn("max_notional=None", source)
        self.assertEqual(source.count("engine.add_venue("), 1)
        self.assertEqual(source.count("nautilus_pyo3.BacktestEngine("), 1)
        self.assertIn("nautilus_pyo3.Money.from_str(", source)
        self.assertIn("config['starting_nav_usdt']", source)
        self.assertIn("base_currency=NATIVE_USDT", source)
        self.assertIn("aggtrade_start = start - timedelta(minutes=35)", source)
        self.assertIn("funding_context_start = start - timedelta(hours=8, minutes=5)", source)
        self.assertIn("load_start=start - timedelta(days=15)", source)
        self.assertIn("_evaluate_suite_summary", source)
        self.assertIn("from nautilus_trader.model import FundingRateUpdate, MarkPriceUpdate", source)
        self.assertIn("load_official_funding_rates(", source)
        self.assertIn("load_official_mark_prices(", source)
        self.assertIn('interval=int(row["funding_interval_minutes"])', source)
        self.assertIn("engine.add_data(native_mark_price_updates, sort=False)", source)
        self.assertIn("engine.add_data(native_funding_updates, sort=False)", source)
        self.assertIn("funding_observations_from_frame(funding.frame)", source)
        self.assertIn("funding_observations_by_instrument=funding_observations_by_instrument", source)
        self.assertIn('liquidation_enabled=bool(config["venue"]["liquidation_enabled"])', source)
        self.assertIn('liquidation_trigger_ratio=float(config["venue"]["liquidation_trigger_ratio"])', source)
        self.assertIn("engine.sort_data()", source)
        self.assertIn("--reuse-first-dir", source)

    def test_ablation_is_single_variable_and_never_reused_for_promotion(self) -> None:
        detector = (HERE / "aggtrade_acceptance_signals.py").read_text(encoding="utf-8")
        runner = (HERE / "run_aggtrade_acceptance_nautilus.py").read_text(encoding="utf-8")
        workflow = (HERE.parents[1] / ".github" / "workflows" / "candidate-08-aggtrade-acceptance-nautilus.yml").read_text(encoding="utf-8")
        self.assertIn("require_retest_contraction: bool = True", detector)
        self.assertIn('choices=("none", "remove_retest_contraction")', runner)
        self.assertIn('and ablation == "none"', runner)
        self.assertIn("The ablation is diagnostic only and never opens a promotion path", workflow)
        self.assertIn('"promotable": False', workflow)

    def test_execution_failure_cancels_contingent_orders_before_global_reset(self) -> None:
        source = (HERE / "aggtrade_acceptance_strategy.py").read_text(encoding="utf-8")
        failure_block = source[source.index("def _handle_execution_failure"):source.index("def _record_skip")]
        self.assertGreaterEqual(failure_block.count("self.cancel_all_orders(self.active_instrument_id)"), 2)

    def test_fill_adjusted_risk_breach_cancels_children_and_forces_exit(self) -> None:
        source = (HERE / "aggtrade_acceptance_strategy.py").read_text(encoding="utf-8")
        fill_block = source[source.index("def on_order_filled"):source.index("def on_order_canceled")]
        opened_block = source[source.index("def on_position_opened"):source.index("def on_position_closed")]
        self.assertIn('"FILL_ADJUSTED_RISK_BUDGET_EXCEEDED"', fill_block)
        self.assertIn("self.cancel_all_orders(self.active_instrument_id)", fill_block)
        self.assertIn("self._request_exit(", fill_block)
        self.assertIn("self.fill_adjusted_risk_violation", opened_block)
        self.assertIn('"FILL_ADJUSTED_RISK_BUDGET_EXCEEDED"', opened_block)


if __name__ == "__main__":
    unittest.main()
