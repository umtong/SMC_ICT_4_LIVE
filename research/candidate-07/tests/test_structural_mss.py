from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from model import Direction, LogicConfig, ScenarioState, SignalBar  # noqa: E402
from model_structural_mss import (  # noqa: E402
    MinuteSwing,
    StructuralMSSRouter,
    StructuralStage,
    _StructuralEpisode,
)
from strategy_structural_mss import _TargetSafeStructuralMSSRouter  # noqa: E402


NS_PER_MINUTE = 60_000_000_000


def bar(
    minute: int,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float = 100.0,
) -> SignalBar:
    return SignalBar(
        ts_event_ns=minute * NS_PER_MINUTE,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def warm(router: StructuralMSSRouter, *, through: int = 100) -> None:
    # The structural rank requires 24 bars for the causal ATR and then 60
    # completed body/ATR observations. One hundred bars supplies both histories
    # without touching private rank state directly.
    for minute in range(1, through + 1):
        open_ = 100.0 + (0.01 if minute % 2 else -0.01)
        close = 100.0 - (0.01 if minute % 2 else -0.01)
        router.observe_minute(
            bar(
                minute,
                open_=open_,
                high=max(open_, close) + 0.10,
                low=min(open_, close) - 0.10,
                close=close,
            )
        )


def episode(
    *,
    direction: Direction = Direction.LONG,
    source_time_ns: int = 100 * NS_PER_MINUTE,
) -> _StructuralEpisode:
    if direction is Direction.LONG:
        boundary = MinuteSwing(
            "1MH-boundary",
            "UPPER",
            101.0,
            90 * NS_PER_MINUTE,
            92 * NS_PER_MINUTE,
        )
        source_level, extreme = 99.0, 98.0
        # Keep the synthetic target far enough beyond the retest close that the
        # unchanged 1.25R economic geometry contract can legitimately pass.
        internal, external = 106.0, 110.0
    else:
        boundary = MinuteSwing(
            "1ML-boundary",
            "LOWER",
            99.0,
            90 * NS_PER_MINUTE,
            92 * NS_PER_MINUTE,
        )
        source_level, extreme = 101.0, 102.0
        internal, external = 94.0, 90.0
    return _StructuralEpisode(
        scenario_id="structural-episode",
        direction=direction,
        source_time_ns=source_time_ns,
        source_signal_index=1,
        source_level=source_level,
        event_extreme=extreme,
        event_atr=2.0,
        opposing_internal=internal,
        opposing_external=external,
        boundary=boundary,
    )


class StructuralMSSRouterTests(unittest.TestCase):
    def test_boundary_must_be_confirmed_before_sweep_bar_begins(self) -> None:
        router = StructuralMSSRouter(LogicConfig())
        old = MinuteSwing(
            "old",
            "UPPER",
            102.0,
            90 * NS_PER_MINUTE,
            92 * NS_PER_MINUTE,
        )
        inside_source_bar = MinuteSwing(
            "inside",
            "UPPER",
            101.0,
            98 * NS_PER_MINUTE,
            100 * NS_PER_MINUTE,
        )
        router._minute_swings.extend((old, inside_source_bar))
        selected = router._latest_independent_boundary(
            direction=Direction.LONG,
            source_time_ns=104 * NS_PER_MINUTE,
            source_close=100.0,
        )
        self.assertEqual(selected, old)

    def test_ranked_mss_then_first_same_boundary_retest_routes(self) -> None:
        router = StructuralMSSRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode()

        mss = router.observe_minute(
            bar(101, open_=100.0, high=101.7, low=99.9, close=101.6)
        )
        self.assertIsNone(mss.plan)
        self.assertEqual(
            [item.reason_code for item in mss.transitions],
            ["INDEPENDENT_1M_DISPLACEMENT_MSS"],
        )
        assert router._structural_episode is not None
        self.assertEqual(
            router._structural_episode.scenario_state,
            ScenarioState.CONFIRMED,
        )

        retest = router.observe_minute(
            bar(102, open_=101.10, high=101.45, low=100.95, close=101.35)
        )
        self.assertIsNotNone(retest.plan)
        assert retest.plan is not None
        self.assertEqual(retest.plan.direction, Direction.LONG)
        self.assertEqual(retest.plan.details["boundary_id"], "1MH-boundary")
        self.assertTrue(retest.plan.details["same_boundary_retest"])
        self.assertEqual(
            [item.reason_code for item in retest.transitions],
            ["FIRST_SAME_BOUNDARY_1M_RETEST_REJECTED"],
        )
        self.assertIsNone(router._structural_episode)

    def test_first_touch_which_is_not_defended_consumes_episode(self) -> None:
        router = StructuralMSSRouter(LogicConfig())
        warm(router)
        active = episode()
        active.stage = StructuralStage.AWAIT_RETEST
        active.scenario_state = ScenarioState.CONFIRMED
        active.mss_ns = 100 * NS_PER_MINUTE
        router._structural_episode = active

        result = router.observe_minute(
            bar(101, open_=101.2, high=101.3, low=100.8, close=100.9)
        )
        self.assertIsNone(result.plan)
        self.assertEqual(
            [item.reason_code for item in result.transitions],
            ["FIRST_1M_BOUNDARY_RETEST_NOT_DEFENDED"],
        )
        self.assertIsNone(router._structural_episode)

    def test_source_extreme_invalidates_before_mss(self) -> None:
        router = StructuralMSSRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode()
        result = router.observe_minute(
            bar(101, open_=99.0, high=99.2, low=97.8, close=98.1)
        )
        self.assertEqual(
            [item.reason_code for item in result.transitions],
            ["SOURCE_INVALIDATED_BEFORE_MSS"],
        )
        self.assertIsNone(router._structural_episode)

    def test_source_objective_delivery_precedes_any_late_entry(self) -> None:
        router = _TargetSafeStructuralMSSRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode()
        result = router.observe_minute(
            bar(101, open_=101.0, high=106.2, low=100.9, close=105.8)
        )
        self.assertIsNone(result.plan)
        self.assertEqual(
            [item.reason_code for item in result.transitions],
            ["SOURCE_OBJECTIVE_DELIVERED_BEFORE_ENTRY"],
        )
        self.assertIsNone(router._structural_episode)

    def test_short_path_is_symmetric(self) -> None:
        router = StructuralMSSRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode(direction=Direction.SHORT)
        mss = router.observe_minute(
            bar(101, open_=100.0, high=100.1, low=98.3, close=98.4)
        )
        self.assertEqual(
            [item.reason_code for item in mss.transitions],
            ["INDEPENDENT_1M_DISPLACEMENT_MSS"],
        )
        retest = router.observe_minute(
            bar(102, open_=98.9, high=99.05, low=98.55, close=98.65)
        )
        self.assertIsNotNone(retest.plan)
        assert retest.plan is not None
        self.assertEqual(retest.plan.direction, Direction.SHORT)


if __name__ == "__main__":
    unittest.main()
