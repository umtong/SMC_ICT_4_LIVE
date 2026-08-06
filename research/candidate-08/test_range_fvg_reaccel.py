from __future__ import annotations

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from range_fvg_logic import (
    Direction,
    FiveMinuteBar,
    LevelSource,
    LogicEvent,
    RangeFVGSignal,
    ScenarioFamily,
)
from range_fvg_reaccel_logic import (
    RetestReaccelConfig,
    _confirmed_signal,
    _reaccelerates,
    _retest_holds,
)


def bar(
    index: int,
    *,
    open: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
    trades: float = 100.0,
    imbalance: float = 0.0,
    atr: float = 1.0,
) -> FiveMinuteBar:
    return FiveMinuteBar(
        index=index,
        ts_event_ns=(index + 1) * 300_000_000_000,
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trade_count=trades,
        taker_buy_volume=volume * (1.0 + imbalance) / 2.0,
        imbalance=imbalance,
        atr=atr,
        volume_ratio=1.2,
        trade_ratio=1.1,
        efficiency_60m=0.2,
        direction_60m=1.0,
        session_key="s0",
        day_key="d0",
        week_key="w0",
    )


def base_long() -> RangeFVGSignal:
    events = (
        LogicEvent(
            scenario_id="base-a",
            event_type="EXTERNAL_LEVEL_ACCEPTED",
            event_time_ns=1,
            observed_time_ns=1,
            previous_state="IDLE",
            next_state="ACCEPTED",
            reason_code="FOUR_HOUR_HIGH_DISPLACEMENT_CLOSE",
        ),
        LogicEvent(
            scenario_id="base-a",
            event_type="FVG_CONFIRMED",
            event_time_ns=2,
            observed_time_ns=2,
            previous_state="ACCEPTED",
            next_state="CONFIRMED",
            reason_code="FIVE_MINUTE_FVG_LONG",
        ),
    )
    return RangeFVGSignal(
        scenario_id="base-a",
        family=ScenarioFamily.ACCEPTANCE,
        direction=Direction.LONG,
        signal_index=12,
        signal_time_ns=3_900_000_000_000,
        boundary_id="boundary",
        boundary_source=LevelSource.FOUR_HOUR,
        boundary_level=100.0,
        fvg_low=100.2,
        fvg_high=100.8,
        limit_entry=100.5,
        structural_stop=99.0,
        external_target_id="target",
        external_target_source=LevelSource.DAY,
        external_target=105.0,
        atr=1.0,
        invalidation_before_fill=99.4,
        events=events,
        details={"displacement_index": 11, "confirmation_index": 12},
    )


class RetestReaccelerationTests(unittest.TestCase):
    def test_retest_requires_lower_activity_than_displacement(self) -> None:
        signal = base_long()
        displacement = bar(
            11,
            open=99.5,
            high=101.2,
            low=99.4,
            close=101.0,
            volume=300.0,
            trades=250.0,
            imbalance=0.30,
        )
        contracted = bar(
            13,
            open=101.0,
            high=101.1,
            low=100.4,
            close=100.7,
            volume=150.0,
            trades=120.0,
            imbalance=-0.08,
        )
        hot = bar(
            13,
            open=101.0,
            high=101.1,
            low=100.4,
            close=100.7,
            volume=350.0,
            trades=300.0,
            imbalance=-0.35,
        )
        self.assertTrue(_retest_holds(signal, displacement, contracted))
        self.assertFalse(_retest_holds(signal, displacement, hot))

    def test_retest_touch_does_not_itself_confirm_entry(self) -> None:
        signal = base_long()
        retest = bar(
            13,
            open=101.0,
            high=101.1,
            low=100.4,
            close=100.7,
            volume=150.0,
            trades=120.0,
            imbalance=-0.08,
        )
        self.assertFalse(
            _reaccelerates(signal, retest, retest, RetestReaccelConfig())
        )

    def test_separate_directional_displacement_confirms_reacceleration(self) -> None:
        signal = base_long()
        retest = bar(
            13,
            open=101.0,
            high=101.1,
            low=100.4,
            close=100.7,
            volume=150.0,
            trades=120.0,
            imbalance=-0.08,
        )
        follow = bar(
            14,
            open=100.7,
            high=101.8,
            low=100.6,
            close=101.7,
            volume=180.0,
            trades=150.0,
            imbalance=0.25,
        )
        self.assertTrue(
            _reaccelerates(signal, retest, follow, RetestReaccelConfig())
        )

    def test_confirmed_signal_uses_retest_extreme_as_structural_invalidation(self) -> None:
        signal = base_long()
        displacement = bar(
            11,
            open=99.5,
            high=101.2,
            low=99.4,
            close=101.0,
            volume=300.0,
            trades=250.0,
            imbalance=0.30,
        )
        retest = bar(
            13,
            open=101.0,
            high=101.1,
            low=100.4,
            close=100.7,
            volume=150.0,
            trades=120.0,
            imbalance=-0.08,
        )
        follow = bar(
            14,
            open=100.7,
            high=101.8,
            low=100.6,
            close=101.7,
            volume=180.0,
            trades=150.0,
            imbalance=0.25,
        )
        confirmed = _confirmed_signal(
            signal,
            displacement,
            retest,
            follow,
            RetestReaccelConfig(),
        )
        self.assertIsNotNone(confirmed)
        assert confirmed is not None
        self.assertEqual(confirmed.signal_time_ns, follow.ts_event_ns)
        self.assertLess(confirmed.structural_stop, retest.low)
        self.assertGreater(confirmed.external_target, confirmed.limit_entry)
        self.assertEqual(confirmed.events[-2].next_state, "RETEST_HELD")
        self.assertEqual(confirmed.events[-1].previous_state, "RETEST_HELD")


if __name__ == "__main__":
    unittest.main()
