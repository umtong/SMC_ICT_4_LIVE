from __future__ import annotations

import unittest

from domain_v3 import Candle, Side
from market_v4 import StructuralPivot
from market_v15 import FootprintRef
from market_v16_structure import ReactionInterval
from market_v18_trendline import FeasibleTrendlineVersion, TrendlineRoleFlipConfig
from market_v19_trendline import ActiveStructureTrendlineRoleFlipEngine


def candle(index: int, o: float, h: float, l: float, c: float) -> Candle:
    start = index * 10
    return Candle(start, start + 9, o, h, l, c, 1.0)


def pivot(*, side: str, level: float, event: int, observed: int) -> StructuralPivot:
    return StructuralPivot(0, 0, side, level, event, observed)


def interval(event: int, observed: int, low: float, high: float) -> ReactionInterval:
    return ReactionInterval(
        pivot=pivot(side="HIGH", level=high, event=event, observed=observed),
        low=low,
        high=high,
    )


def line() -> FeasibleTrendlineVersion:
    anchors = (
        interval(9, 19, 100.0, 101.0),
        interval(29, 39, 98.0, 99.0),
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


def pivots() -> list[StructuralPivot]:
    return [
        pivot(side="HIGH", level=105.0, event=15, observed=25),
        pivot(side="LOW", level=94.0, event=35, observed=45),
    ]


def ob(*, observed: int, name: str = "bullish-ob") -> FootprintRef:
    return FootprintRef(
        footprint_id=name,
        kind="ORDER_BLOCK",
        side=Side.LONG,
        observed_time_ns=observed,
        zone_low=95.5,
        zone_high=96.0,
        invalidation=95.0,
        source_two_x_quality=True,
        timeframe_minutes=5,
    )


class ActiveOrderBlockRoleTests(unittest.TestCase):
    def engine(self) -> ActiveStructureTrendlineRoleFlipEngine:
        return ActiveStructureTrendlineRoleFlipEngine(
            "BTCUSDT",
            [line()],
            pivots(),
            TrendlineRoleFlipConfig(
                tick_size=0.1,
                signal_timeframe_minutes=5,
                valid_until_ns=1000,
            ),
        )

    @staticmethod
    def break_and_accept(engine) -> None:
        engine.on_close(candle(5, 98.0, 100.0, 97.0, 99.0), 5)
        engine.on_close(candle(6, 98.0, 101.0, 97.5, 100.0), 6)

    def test_fresh_preexisting_ob_to_the_left_can_sponsor_first_retest(self) -> None:
        engine = self.engine()
        engine.ingest_footprints([ob(observed=49, name="left-ob")])
        self.break_and_accept(engine)
        update = engine.on_close(candle(7, 98.0, 101.0, 95.0, 98.0), 7)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertEqual(setup.entry, 96.0)
        self.assertIn("ACTIVE_OB", setup.family)
        self.assertIn("OB_TEMPORAL_ROLE=PREEXISTING_ACTIVE_OB", setup.context_bias)
        self.assertEqual(engine.diagnostics["setups_preexisting_active_ob"], 1)
        self.assertEqual(
            engine.audit_rows[-1]["order_block_relation"],
            "PREEXISTING_ACTIVE_OB",
        )

    def test_preexisting_ob_mitigated_before_break_is_not_reused(self) -> None:
        engine = self.engine()
        engine.ingest_footprints([ob(observed=39, name="stale-left-ob")])
        engine.on_close(candle(4, 98.0, 99.0, 95.8, 98.0), 4)
        self.break_and_accept(engine)
        update = engine.on_close(candle(7, 98.0, 101.0, 95.0, 98.0), 7)
        self.assertEqual(update.setups, ())
        self.assertEqual(engine.diagnostics["first_retest_without_overlapping_ob"], 1)

    def test_break_response_ob_remains_an_alternative_role_witness(self) -> None:
        engine = self.engine()
        self.break_and_accept(engine)
        engine.ingest_footprints([ob(observed=69, name="response-ob")])
        update = engine.on_close(candle(7, 98.0, 101.0, 95.0, 98.0), 7)
        self.assertEqual(len(update.setups), 1)
        setup = update.setups[0]
        self.assertIn("OB_TEMPORAL_ROLE=BREAK_RESPONSE_OB", setup.context_bias)
        self.assertEqual(engine.diagnostics["setups_break_response_ob"], 1)


if __name__ == "__main__":
    unittest.main()
