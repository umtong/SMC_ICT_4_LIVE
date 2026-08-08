from __future__ import annotations

from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from model import Direction, LogicConfig, SignalBar  # noqa: E402
from model_structural_mss import MinuteSwing, _StructuralEpisode  # noqa: E402
from model_structural_mss_close import (  # noqa: E402
    TargetSafeStructuralMSSCloseRouter,
)


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


def warm(router: TargetSafeStructuralMSSCloseRouter, *, through: int = 100) -> None:
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


def episode(direction: Direction = Direction.LONG) -> _StructuralEpisode:
    if direction is Direction.LONG:
        boundary = MinuteSwing(
            "1MH-boundary",
            "UPPER",
            101.0,
            90 * NS_PER_MINUTE,
            92 * NS_PER_MINUTE,
        )
        source_level, extreme = 99.0, 98.0
        # The ablation enters at the displacement close rather than a later
        # retest. Keep the synthetic objective far enough away that the same
        # unchanged 1.25R economic geometry can legitimately pass.
        internal, external = 107.0, 110.0
    else:
        boundary = MinuteSwing(
            "1ML-boundary",
            "LOWER",
            99.0,
            90 * NS_PER_MINUTE,
            92 * NS_PER_MINUTE,
        )
        source_level, extreme = 101.0, 102.0
        internal, external = 93.0, 90.0
    return _StructuralEpisode(
        scenario_id="structural-close-episode",
        direction=direction,
        source_time_ns=100 * NS_PER_MINUTE,
        source_signal_index=1,
        source_level=source_level,
        event_extreme=extreme,
        event_atr=2.0,
        opposing_internal=internal,
        opposing_external=external,
        boundary=boundary,
    )


class StructuralMSSCloseTests(unittest.TestCase):
    def test_true_ranked_mss_routes_without_retest(self) -> None:
        router = TargetSafeStructuralMSSCloseRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode()
        result = router.observe_minute(
            bar(101, open_=100.0, high=101.7, low=99.9, close=101.6)
        )
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.direction, Direction.LONG)
        self.assertFalse(result.plan.details["same_boundary_retest"])
        self.assertEqual(
            result.plan.details["structural_route"],
            "5M_SWEEP_1M_TRUE_MSS_CLOSE",
        )
        self.assertEqual(
            [item.reason_code for item in result.transitions],
            [
                "INDEPENDENT_1M_DISPLACEMENT_MSS",
                "INDEPENDENT_1M_MSS_CLOSE_ENTRY_READY",
            ],
        )
        self.assertIsNone(router._structural_episode)

    def test_source_objective_still_preconsumes_late_mss(self) -> None:
        router = TargetSafeStructuralMSSCloseRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode()
        result = router.observe_minute(
            bar(101, open_=101.0, high=107.2, low=100.9, close=106.8)
        )
        self.assertIsNone(result.plan)
        self.assertEqual(
            [item.reason_code for item in result.transitions],
            ["SOURCE_OBJECTIVE_DELIVERED_BEFORE_ENTRY"],
        )

    def test_short_true_mss_is_symmetric(self) -> None:
        router = TargetSafeStructuralMSSCloseRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode(Direction.SHORT)
        result = router.observe_minute(
            bar(101, open_=100.0, high=100.1, low=98.3, close=98.4)
        )
        self.assertIsNotNone(result.plan)
        assert result.plan is not None
        self.assertEqual(result.plan.direction, Direction.SHORT)
        self.assertFalse(result.plan.details["same_boundary_retest"])

    def test_source_extreme_still_invalidates_before_mss(self) -> None:
        router = TargetSafeStructuralMSSCloseRouter(LogicConfig())
        warm(router)
        router._structural_episode = episode()
        result = router.observe_minute(
            bar(101, open_=99.0, high=99.2, low=97.8, close=98.1)
        )
        self.assertIsNone(result.plan)
        self.assertEqual(
            [item.reason_code for item in result.transitions],
            ["SOURCE_INVALIDATED_BEFORE_MSS"],
        )


if __name__ == "__main__":
    unittest.main()
