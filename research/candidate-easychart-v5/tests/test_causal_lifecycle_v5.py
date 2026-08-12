from __future__ import annotations

import unittest

from causal_lifecycle_v5 import LifecycleAwareStructureBook
from contracts_v5 import (
    Channel,
    ObjectKind,
    ScenarioPath,
    ScenarioSetup,
    SetupState,
    TrendLine,
)
from domain import Candle, Side
from easychart_zones import ZoneSide
from scenario_engine_v5 import StructureScenarioEngine

NS = 60_000_000_000


def candle(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(index * NS, open_, high, low, close, 1.0)


def make_engine() -> StructureScenarioEngine:
    return StructureScenarioEngine(
        "TEST",
        0.1,
        scale_name="MACRO",
        higher_minutes=60,
        decision_minutes=15,
        trigger_minutes=5,
        minimum_gross_rr=1.0,
    )


class CausalDiagonalLifecycleTests(unittest.TestCase):
    def _uptrend_line(self) -> TrendLine:
        return TrendLine(
            structure_id="UP_LINE",
            kind=ObjectKind.UPTREND_LINE,
            side=ZoneSide.SUPPORT,
            timeframe_minutes=60,
            first_pivot_id="L1",
            second_pivot_id="L2",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=100.0,
            second_price=110.0,
            observed_time_ns=10 * NS,
            pivot_span=1,
            strength_ratio=2.0,
        )

    def test_wick_touch_and_valid_close_do_not_delete_trend_line(self) -> None:
        book = LifecycleAwareStructureBook("TEST", 60, 0.1, pivot_spans=(1,))
        line = self._uptrend_line()
        book.trend_lines.append(line)
        book._line_ids.add(line.structure_id)

        # The projected line is 111.0. Price trades through the one-tick band
        # intrabar but closes back above it, which is a possible bounce/fakeout.
        book.observe_price(candle(11, 111.2, 111.4, 110.7, 111.2))
        later = book.boundaries_at(12 * NS)
        self.assertTrue(any(zone.source_structure_id == line.structure_id for zone in later))
        self.assertIsNone(book.boundary_retired_time_ns(line.structure_id))

    def test_body_close_through_support_retires_trend_line(self) -> None:
        book = LifecycleAwareStructureBook("TEST", 60, 0.1, pivot_spans=(1,))
        line = self._uptrend_line()
        book.trend_lines.append(line)
        book._line_ids.add(line.structure_id)

        book.observe_price(candle(11, 111.1, 111.2, 110.4, 110.6))
        after = book.boundaries_at(12 * NS)
        self.assertFalse(any(zone.source_structure_id == line.structure_id for zone in after))
        self.assertEqual(book.boundary_retired_time_ns(line.structure_id), 11 * NS)
        self.assertEqual(book.diagnostics.get("trend_line_body_close_invalidated"), 1)

    def test_channel_edges_invalidate_independently(self) -> None:
        book = LifecycleAwareStructureBook("TEST", 60, 0.1, pivot_spans=(1,))
        channel = Channel(
            channel_id="ASC_CH",
            timeframe_minutes=60,
            direction="ASCENDING",
            main_first_pivot_id="L1",
            main_second_pivot_id="L2",
            opposite_pivot_id="H1",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=100.0,
            second_price=110.0,
            offset=10.0,
            observed_time_ns=10 * NS,
            pivot_span=1,
            strength_ratio=2.0,
        )
        book.channels.append(channel)
        book._channel_ids.add(channel.channel_id)

        # At t=11 the lower edge is 111 and the upper edge is 121. Close below
        # the lower edge invalidates that support only.
        book.observe_price(candle(11, 111.0, 111.05, 110.4, 110.6))
        later = book.boundaries_at(12 * NS)
        sources = {zone.source_structure_id for zone in later}
        self.assertNotIn("ASC_CH:LOWER", sources)
        self.assertIn("ASC_CH:UPPER", sources)

    def test_channel_acceptance_stop_is_beyond_observed_retest_wick(self) -> None:
        engine = make_engine()
        channel = Channel(
            channel_id="ASC_CH",
            timeframe_minutes=60,
            direction="ASCENDING",
            main_first_pivot_id="L1",
            main_second_pivot_id="L2",
            opposite_pivot_id="H1",
            first_time_ns=0,
            second_time_ns=10 * NS,
            first_price=100.0,
            second_price=110.0,
            offset=10.0,
            observed_time_ns=10 * NS,
            pivot_span=2,
            strength_ratio=2.0,
        )
        engine.structure.channels.append(channel)
        context = engine.structure.channel_edge_snapshot(channel, "UPPER", 11 * NS)
        setup = ScenarioSetup(
            setup_id="CHANNEL_ACCEPTANCE",
            scale_name="MACRO",
            path=ScenarioPath.ACCEPTANCE,
            side=Side.LONG,
            state=SetupState.WAITING_ACCEPTANCE_RETEST,
            context=context,
            context_members=(context,),
            observed_time_ns=10 * NS,
            interaction_time_ns=11 * NS,
            interaction_index=0,
            interaction_extreme=122.0,
            target_zone=context,
            target_price=130.0,
            confirmation_time_ns=12 * NS,
            channel_id=channel.channel_id,
        )
        retest = candle(13, 123.3, 124.0, 122.8, 123.5)
        engine._current_trigger_bar = retest
        try:
            stop = engine._acceptance_stop(setup, retest.ts_close_ns)
        finally:
            engine._current_trigger_bar = None
        self.assertIsNotNone(stop)
        assert stop is not None
        self.assertLess(stop, retest.low)
        self.assertAlmostEqual(stop, 122.7)
        self.assertEqual(
            engine.diagnostics.get("acceptance_stop_extended_beyond_entry_bar"),
            1,
        )


if __name__ == "__main__":
    unittest.main()
