from __future__ import annotations

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v16_structure import (
    HorizontalAcceptedBreakEngine,
    HorizontalReactionShelf,
    StructuralAcceptedBreakConfig,
    build_horizontal_reaction_shelves,
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


class HorizontalShelfTests(unittest.TestCase):
    def test_same_side_wick_body_overlap_creates_one_shelf(self) -> None:
        bars = [
            candle(0, 99.0, 101.0, 98.0, 100.0),
            candle(1, 100.0, 100.2, 95.0, 96.0),
            candle(2, 100.5, 101.5, 99.0, 100.2),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=101.0),
            pivot(center=2, observed=3, side="HIGH", level=101.5),
        ]
        shelves = build_horizontal_reaction_shelves(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=15,
        )
        self.assertEqual(len(shelves), 1)
        self.assertIs(shelves[0].side, Side.LONG)
        self.assertEqual(shelves[0].zone_low, 100.5)
        self.assertEqual(shelves[0].zone_high, 101.0)

    def test_nonoverlapping_reactions_do_not_invent_tolerance(self) -> None:
        bars = [
            candle(0, 99.0, 100.0, 98.0, 99.5),
            candle(1, 99.5, 100.0, 95.0, 96.0),
            candle(2, 101.0, 102.0, 100.5, 101.5),
        ]
        pivots = [
            pivot(center=0, observed=1, side="HIGH", level=100.0),
            pivot(center=2, observed=3, side="HIGH", level=102.0),
        ]
        shelves = build_horizontal_reaction_shelves(
            symbol="BTCUSDT",
            candles=bars,
            pivots=pivots,
            timeframe_minutes=15,
        )
        self.assertEqual(shelves, [])


class AcceptedBreakTests(unittest.TestCase):
    @staticmethod
    def shelf() -> HorizontalReactionShelf:
        first = pivot(
            center=0,
            observed=1,
            side="HIGH",
            level=101.0,
            event=9,
            observed_time=19,
        )
        second = pivot(
            center=2,
            observed=3,
            side="HIGH",
            level=101.2,
            event=29,
            observed_time=39,
        )
        return HorizontalReactionShelf(
            shelf_id="shelf",
            symbol="BTCUSDT",
            side=Side.LONG,
            observed_time_ns=39,
            timeframe_minutes=15,
            zone_low=100.0,
            zone_high=101.0,
            first=first,
            second=second,
        )

    def engine(self, pivots):
        return HorizontalAcceptedBreakEngine(
            "BTCUSDT",
            [self.shelf()],
            pivots,
            StructuralAcceptedBreakConfig(
                tick_size=0.1,
                signal_timeframe_minutes=5,
                valid_until_ns=1000,
            ),
        )

    def test_distinct_outside_open_close_arms_first_retest(self) -> None:
        origin = pivot(
            center=3,
            observed=4,
            side="LOW",
            level=95.0,
            event=30,
            observed_time=45,
        )
        objective = pivot(
            center=1,
            observed=2,
            side="HIGH",
            level=110.0,
            event=15,
            observed_time=25,
        )
        engine = self.engine([objective, origin])
        first = candle(5, 100.5, 103.0, 100.0, 102.0)
        second = candle(6, 101.5, 104.0, 101.2, 103.0)
        self.assertEqual(engine.on_close(first, 0).setups, ())
        update = engine.on_close(second, 1)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.entry, 101.0)
        self.assertEqual(setup.stop, 94.9)
        self.assertEqual(setup.initial_target, 110.0)
        self.assertLess(origin.event_time_ns, first.ts_close_ns)

    def test_same_break_bar_cannot_also_establish_acceptance(self) -> None:
        origin = pivot(
            center=3,
            observed=4,
            side="LOW",
            level=95.0,
            event=30,
            observed_time=45,
        )
        objective = pivot(
            center=1,
            observed=2,
            side="HIGH",
            level=110.0,
            event=15,
            observed_time=25,
        )
        engine = self.engine([objective, origin])
        update = engine.on_close(candle(5, 102.0, 104.0, 101.2, 103.0), 0)
        self.assertEqual(update.setups, ())
        self.assertEqual(engine.diagnostics.get("accepted_breaks", 0), 0)

    def test_origin_printed_after_break_is_not_used(self) -> None:
        late_origin = pivot(
            center=6,
            observed=6,
            side="LOW",
            level=99.0,
            event=69,
            observed_time=69,
        )
        objective = pivot(
            center=1,
            observed=2,
            side="HIGH",
            level=110.0,
            event=15,
            observed_time=25,
        )
        engine = self.engine([objective, late_origin])
        engine.on_close(candle(5, 100.5, 103.0, 100.0, 102.0), 0)
        update = engine.on_close(candle(6, 101.5, 104.0, 101.2, 103.0), 1)
        self.assertEqual(update.setups, ())
        self.assertEqual(engine.diagnostics["missing_prebreak_wave_origin"], 1)

    def test_sub_one_r_first_objective_is_not_skipped(self) -> None:
        origin = pivot(
            center=3,
            observed=4,
            side="LOW",
            level=95.0,
            event=30,
            observed_time=45,
        )
        near = pivot(
            center=1,
            observed=2,
            side="HIGH",
            level=103.0,
            event=15,
            observed_time=25,
        )
        far = pivot(
            center=2,
            observed=3,
            side="HIGH",
            level=120.0,
            event=28,
            observed_time=38,
        )
        engine = self.engine([near, far, origin])
        engine.on_close(candle(5, 100.5, 102.4, 100.0, 102.0), 0)
        update = engine.on_close(candle(6, 101.5, 102.5, 101.2, 102.0), 1)
        self.assertEqual(update.setups, ())
        self.assertEqual(engine.diagnostics["first_objective_rr_lt_1"], 1)
        self.assertEqual(
            engine.audit_rows[-1]["objective"],
            103.0,
        )


if __name__ == "__main__":
    unittest.main()
