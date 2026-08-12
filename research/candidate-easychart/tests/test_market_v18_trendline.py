from __future__ import annotations

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v15 import FootprintRef
from market_v16_structure import ReactionInterval
from market_v18_trendline import (
    FeasibleTrendlineVersion,
    TrendlineRoleFlipConfig,
    TrendlineRoleFlipEngine,
    build_feasible_trendlines,
    feasible_slope_interval,
)


def candle(index: int, o: float, h: float, l: float, c: float) -> Candle:
    start = index * 10
    return Candle(start, start + 9, o, h, l, c, 1.0)


def pivot(
    *,
    center: int,
    observed: int,
    side: str,
    level: float,
    event: int | None = None,
    observed_time: int | None = None,
) -> StructuralPivot:
    return StructuralPivot(
        center_index=center,
        observed_index=observed,
        side=side,
        level=level,
        event_time_ns=center * 10 + 9 if event is None else event,
        observed_time_ns=observed * 10 + 9 if observed_time is None else observed_time,
    )


def interval(event: int, observed: int, side: str, low: float, high: float) -> ReactionInterval:
    level = high if side == "HIGH" else low
    return ReactionInterval(
        pivot=pivot(
            center=0,
            observed=0,
            side=side,
            level=level,
            event=event,
            observed_time=observed,
        ),
        low=low,
        high=high,
    )


class FeasibleTrendlineGeometryTests(unittest.TestCase):
    def test_slope_interval_and_exact_forward_price_band(self) -> None:
        anchors = (
            interval(0, 1, "HIGH", 10.0, 11.0),
            interval(10, 11, "HIGH", 8.0, 9.0),
        )
        self.assertEqual(feasible_slope_interval(anchors), (-0.3, -0.1))
        line = FeasibleTrendlineVersion(
            line_id="line",
            version_id="line-v1",
            version=1,
            supersedes_version_ids=(),
            symbol="BTCUSDT",
            anchor_side="HIGH",
            trade_side=Side.LONG,
            observed_time_ns=11,
            timeframe_minutes=5,
            anchors=anchors,
            slope_low_per_ns=-0.3,
            slope_high_per_ns=-0.1,
        )
        low, high = line.price_band(20)
        self.assertAlmostEqual(low, 5.0)
        self.assertAlmostEqual(high, 8.0)

    def test_ambiguous_flat_or_sloped_family_is_not_directional(self) -> None:
        bars = [
            candle(0, 10.0, 11.0, 9.0, 10.0),
            candle(1, 10.0, 10.2, 9.0, 9.5),
            candle(2, 10.0, 11.0, 9.0, 10.0),
            candle(3, 10.0, 10.2, 9.0, 9.5),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=11.0),
            pivot(center=2, observed=3, side="HIGH", level=11.0),
        ]
        self.assertEqual(
            build_feasible_trendlines(
                symbol="BTCUSDT",
                candles=bars,
                pivots=pivots,
                timeframe_minutes=5,
            ),
            [],
        )


class FeasibleTrendlineBuilderTests(unittest.TestCase):
    def test_third_anchor_refines_one_causal_line_version(self) -> None:
        bars = [
            candle(0, 10.0, 11.0, 9.5, 10.2),    # HIGH [10.2, 11]
            candle(1, 10.0, 10.1, 8.0, 8.5),
            candle(2, 8.8, 9.6, 8.0, 9.0),       # HIGH [9.0, 9.6]
            candle(3, 8.7, 8.9, 7.0, 7.5),
            candle(4, 7.8, 8.5, 7.0, 8.0),       # HIGH [8.0, 8.5]
            candle(5, 7.8, 7.9, 6.0, 6.5),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=11.0),
            pivot(center=2, observed=3, side="HIGH", level=9.6),
            pivot(center=4, observed=5, side="HIGH", level=8.5),
        ]
        versions = build_feasible_trendlines(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=5,
        )
        self.assertEqual(len(versions), 2)
        first, refined = versions
        self.assertEqual(first.version, 1)
        self.assertEqual(refined.version, 2)
        self.assertEqual(refined.line_id, first.line_id)
        self.assertEqual(refined.supersedes_version_ids, (first.version_id,))
        self.assertEqual(refined.anchor_count, 3)
        self.assertLess(refined.slope_high_per_ns, 0.0)

    def test_pair_already_broken_before_second_anchor_confirmation_is_rejected(self) -> None:
        bars = [
            candle(0, 10.0, 11.0, 9.5, 10.2),
            candle(1, 10.0, 10.1, 8.0, 8.5),
            candle(2, 8.8, 9.6, 8.0, 9.0),
            # Second anchor exists at bar 2, but the confirming history has
            # already accepted above every feasible descending line.
            candle(3, 10.5, 12.0, 10.0, 11.5),
            candle(4, 10.0, 10.2, 8.0, 8.5),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=11.0),
            pivot(center=2, observed=4, side="HIGH", level=9.6),
        ]
        versions = build_feasible_trendlines(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=5,
        )
        self.assertEqual(versions, [])

    def test_accepted_break_resets_old_anchor_for_future_pair(self) -> None:
        bars = [
            candle(0, 10.0, 11.0, 9.5, 10.2),
            candle(1, 10.0, 10.1, 8.0, 8.5),
            candle(2, 8.8, 9.6, 8.0, 9.0),
            candle(3, 8.7, 8.9, 7.0, 7.5),
            candle(4, 10.0, 12.0, 9.5, 11.5),  # accepted above old line
            candle(5, 8.0, 8.6, 7.0, 8.1),
            candle(6, 8.0, 8.1, 6.0, 6.5),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=11.0),
            pivot(center=2, observed=3, side="HIGH", level=9.6),
            pivot(center=5, observed=6, side="HIGH", level=8.6),
        ]
        versions = build_feasible_trendlines(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=5,
        )
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].version, 1)


class TrendlineRoleFlipEngineTests(unittest.TestCase):
    @staticmethod
    def line() -> FeasibleTrendlineVersion:
        anchors = (
            interval(9, 19, "HIGH", 100.0, 101.0),
            interval(29, 39, "HIGH", 98.0, 99.0),
        )
        return FeasibleTrendlineVersion(
            line_id="line-root",
            version_id="line-root-v1",
            version=1,
            supersedes_version_ids=(),
            symbol="BTCUSDT",
            anchor_side="HIGH",
            trade_side=Side.LONG,
            observed_time_ns=39,
            timeframe_minutes=5,
            anchors=anchors,
            slope_low_per_ns=-0.15,
            slope_high_per_ns=-0.05,
        )

    @staticmethod
    def pivots() -> list[StructuralPivot]:
        return [
            pivot(
                center=1,
                observed=2,
                side="HIGH",
                level=105.0,
                event=15,
                observed_time=25,
            ),
            pivot(
                center=3,
                observed=4,
                side="LOW",
                level=94.0,
                event=35,
                observed_time=45,
            ),
        ]

    def engine(self) -> TrendlineRoleFlipEngine:
        return TrendlineRoleFlipEngine(
            "BTCUSDT",
            [self.line()],
            self.pivots(),
            TrendlineRoleFlipConfig(
                tick_size=0.1,
                signal_timeframe_minutes=5,
                valid_until_ns=1000,
            ),
        )

    @staticmethod
    def order_block() -> FootprintRef:
        return FootprintRef(
            footprint_id="bullish-ob",
            kind="ORDER_BLOCK",
            side=Side.LONG,
            observed_time_ns=69,
            zone_low=96.0,
            zone_high=97.0,
            invalidation=95.0,
            source_two_x_quality=True,
            timeframe_minutes=5,
        )

    def break_and_accept(self, engine: TrendlineRoleFlipEngine) -> None:
        first = engine.on_close(candle(5, 98.0, 100.0, 97.0, 99.0), 5)
        self.assertEqual(first.setups, ())
        second = engine.on_close(candle(6, 98.0, 101.0, 97.5, 100.0), 6)
        self.assertEqual(second.setups, ())

    def test_first_retest_with_overlapping_ob_arms_one_setup(self) -> None:
        engine = self.engine()
        self.break_and_accept(engine)
        engine.ingest_footprints([self.order_block()])
        update = engine.on_close(candle(7, 98.0, 101.0, 95.0, 98.0), 7)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.entry, 97.0)
        self.assertAlmostEqual(setup.stop, 93.9)
        self.assertEqual(setup.initial_target, 105.0)
        self.assertIn("TRENDLINE_ACCEPTED_BREAK_FIRST_RETEST", setup.family)
        self.assertIn("OVERLAPPING_OB", setup.family)

    def test_first_retest_without_ob_is_consumed_not_delayed_to_later_touch(self) -> None:
        engine = self.engine()
        self.break_and_accept(engine)
        first = engine.on_close(candle(7, 98.0, 101.0, 95.0, 98.0), 7)
        self.assertEqual(first.setups, ())
        engine.ingest_footprints([self.order_block()])
        later = engine.on_close(candle(8, 98.0, 100.0, 94.0, 98.0), 8)
        self.assertEqual(later.setups, ())
        self.assertEqual(engine.diagnostics["first_retest_without_overlapping_ob"], 1)

    def test_moving_line_and_fixed_ob_separation_cancels_pending_intent(self) -> None:
        engine = self.engine()
        self.break_and_accept(engine)
        engine.ingest_footprints([self.order_block()])
        armed = engine.on_close(candle(7, 98.0, 101.0, 95.0, 98.0), 7)
        setup_id = armed.setups[0].setup_id
        # At t=90 the entire descending feasible line band is below the fixed
        # OB.  A still-pending order no longer represents their overlap.
        update = engine.on_close(candle(9, 98.0, 99.0, 97.5, 98.5), 9)
        self.assertEqual(update.cancel_setup_ids, (setup_id,))
        self.assertEqual(
            engine.diagnostics["pending_cancelled_line_ob_overlap_ended"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
