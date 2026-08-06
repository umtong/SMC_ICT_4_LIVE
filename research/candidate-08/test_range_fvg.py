from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from range_fvg_logic import (
    Direction,
    ExternalLevel,
    FiveMinuteBar,
    LevelKind,
    LevelSource,
    RangeFVGConfig,
    ScenarioFamily,
    _acceptance_signal,
    _build_level_snapshots,
    _rejection_signal,
    aggregate_five_minute_bars,
)


def bar(
    index: int,
    *,
    open: float,
    high: float,
    low: float,
    close: float,
    imbalance: float = 0.0,
    atr: float = 1.0,
    volume_ratio: float = 1.5,
    trade_ratio: float = 1.3,
    efficiency: float = 0.1,
    direction: float = 0.0,
    session_key: str = "s0",
    day_key: str = "d0",
    week_key: str = "w0",
) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=index,
        ts_event_ns=(index + 1) * 300_000_000_000,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        trade_count=100.0,
        taker_buy_volume=50.0 * (1.0 + imbalance),
        imbalance=imbalance,
        atr=atr,
        volume_ratio=volume_ratio,
        trade_ratio=trade_ratio,
        efficiency_60m=efficiency,
        direction_60m=direction,
        session_key=session_key,
        day_key=day_key,
        week_key=week_key,
    )


def level(
    level_id: str,
    kind: LevelKind,
    value: float,
    source: LevelSource = LevelSource.FOUR_HOUR,
) -> ExternalLevel:
    return ExternalLevel(
        level_id=level_id,
        kind=kind,
        source=source,
        level=value,
        formed_index=0,
        formed_time_ns=1,
        period_key="p0",
    )


class RangeFVGContractTests(unittest.TestCase):
    def test_five_minute_aggregation_uses_only_complete_source_rows(self) -> None:
        index = pd.date_range("2024-01-01T00:00:59.999Z", periods=11, freq="1min")
        frame = pd.DataFrame(
            {
                "open": [100.0 + i for i in range(11)],
                "high": [101.0 + i for i in range(11)],
                "low": [99.0 + i for i in range(11)],
                "close": [100.5 + i for i in range(11)],
                "volume": [10.0] * 11,
                "trade_count": [5.0] * 11,
                "taker_buy_volume": [5.0] * 11,
            },
            index=index,
        )
        result = aggregate_five_minute_bars(
            frame,
            RangeFVGConfig(five_minute_atr_period=1, activity_lookback=2),
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result.index[0], index[4])
        self.assertEqual(result.index[1], index[9])
        self.assertEqual(float(result.iloc[0]["open"]), 100.0)
        self.assertEqual(float(result.iloc[0]["close"]), 104.5)

    def test_four_hour_levels_appear_only_after_period_completion(self) -> None:
        bars = tuple(
            bar(
                i,
                open=100.0,
                high=101.0 + i * 0.01,
                low=99.0 - i * 0.01,
                close=100.0,
                session_key="s0" if i < 48 else "s1",
            )
            for i in range(49)
        )
        snapshots = _build_level_snapshots(bars, RangeFVGConfig())
        self.assertFalse(any(item.source is LevelSource.FOUR_HOUR for item in snapshots[47]))
        completed = [item for item in snapshots[48] if item.source is LevelSource.FOUR_HOUR]
        self.assertEqual(len(completed), 2)
        self.assertEqual({item.kind for item in completed}, {LevelKind.HIGH, LevelKind.LOW})

    def test_acceptance_requires_completed_boundary_fvg_and_external_target(self) -> None:
        a = bar(10, open=99.2, high=99.9, low=99.0, close=99.5)
        b = bar(11, open=99.5, high=101.2, low=99.4, close=101.0, imbalance=0.30)
        c = bar(12, open=101.0, high=101.3, low=100.2, close=101.1, imbalance=0.08)
        signal, reason = _acceptance_signal(
            "acceptance-test",
            a,
            b,
            c,
            (level("boundary-high", LevelKind.HIGH, 100.0),),
            (level("target-high", LevelKind.HIGH, 105.0, LevelSource.DAY),),
            RangeFVGConfig(),
        )
        self.assertEqual(reason, "ACCEPTANCE_SIGNAL")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.family, ScenarioFamily.ACCEPTANCE)
        self.assertEqual(signal.direction, Direction.LONG)
        self.assertLess(signal.structural_stop, signal.limit_entry)
        self.assertLess(signal.limit_entry, signal.external_target)
        self.assertEqual(signal.external_target, 105.0)
        self.assertEqual(signal.events[0].next_state, "ACCEPTED")
        self.assertEqual(signal.events[1].previous_state, "ACCEPTED")

    def test_acceptance_has_no_reward_multiple_fallback_without_target(self) -> None:
        a = bar(10, open=99.2, high=99.9, low=99.0, close=99.5)
        b = bar(11, open=99.5, high=101.2, low=99.4, close=101.0, imbalance=0.30)
        c = bar(12, open=101.0, high=101.3, low=100.2, close=101.1)
        signal, reason = _acceptance_signal(
            "no-target-test",
            a,
            b,
            c,
            (level("boundary-high", LevelKind.HIGH, 100.0),),
            (),
            RangeFVGConfig(),
        )
        self.assertIsNone(signal)
        self.assertEqual(reason, "NO_UNCONSUMED_EXTERNAL_TARGET")

    def test_efficient_up_auction_blocks_bearish_sweep_reversal(self) -> None:
        sweep = bar(
            20,
            open=99.9,
            high=100.8,
            low=99.5,
            close=99.7,
            efficiency=0.5,
            direction=1.0,
        )
        displacement = bar(
            21,
            open=99.6,
            high=99.7,
            low=98.7,
            close=98.9,
            imbalance=-0.30,
        )
        confirmation = bar(22, open=98.9, high=99.2, low=98.5, close=98.7)
        signal, reason = _rejection_signal(
            "blocked-rejection",
            sweep,
            displacement,
            confirmation,
            (level("boundary-high", LevelKind.HIGH, 100.0),),
            (level("target-low", LevelKind.LOW, 95.0),),
            RangeFVGConfig(),
        )
        self.assertIsNone(signal)
        self.assertEqual(reason, "BEARISH_REJECTION_BLOCKED_BY_UP_AUCTION")

    def test_sweep_reclaim_displacement_and_fvg_form_rejection(self) -> None:
        sweep = bar(
            20,
            open=99.9,
            high=100.8,
            low=99.5,
            close=99.7,
            efficiency=0.2,
            direction=-1.0,
        )
        displacement = bar(
            21,
            open=99.6,
            high=99.7,
            low=98.7,
            close=98.9,
            imbalance=-0.30,
        )
        confirmation = bar(22, open=98.9, high=99.2, low=98.5, close=98.7)
        signal, reason = _rejection_signal(
            "valid-rejection",
            sweep,
            displacement,
            confirmation,
            (level("boundary-high", LevelKind.HIGH, 100.0, LevelSource.DAY),),
            (level("target-low", LevelKind.LOW, 95.0, LevelSource.DAY),),
            RangeFVGConfig(),
        )
        self.assertEqual(reason, "REJECTION_SIGNAL")
        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertEqual(signal.family, ScenarioFamily.REJECTION)
        self.assertEqual(signal.direction, Direction.SHORT)
        self.assertLess(signal.external_target, signal.limit_entry)
        self.assertLess(signal.limit_entry, signal.structural_stop)
        self.assertEqual(
            [event.next_state for event in signal.events],
            ["SWEEP_RECLAIMED", "DISPLACED", "CONFIRMED"],
        )


if __name__ == "__main__":
    unittest.main()
