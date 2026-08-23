from __future__ import annotations

import unittest

from smc_ict_4.episode_policy_live.domain import Bar, LiquidityBoundary, Pivot
from smc_ict_4.episode_policy_live.market_state import (
    NS_PER_MINUTE,
    BarAggregator,
    BoundaryBook,
    ObjectiveBook,
    SymbolMarketState,
)


def bar(minute: int, *, interval: int = 1, high: float = 101.0, low: float = 99.0) -> Bar:
    return Bar(
        symbol="BTCUSDT",
        interval_minutes=interval,
        open_time_ns=minute * NS_PER_MINUTE,
        close_time_ns=(minute + interval) * NS_PER_MINUTE - 1,
        open=100.0,
        high=high,
        low=low,
        close=100.0,
        volume=1.0,
        quote_volume=100.0,
        taker_buy_quote_volume=50.0,
        trade_count=1,
    )


class AggregationContinuityTests(unittest.TestCase):
    def test_nautilus_exact_right_edge_is_valid(self) -> None:
        aggregator = BarAggregator("BTCUSDT", 1, 5)
        completed = None
        for minute in range(5):
            source = bar(minute)
            completed = aggregator.push(
                Bar.from_dict(
                    {
                        **source.to_dict(),
                        "close_time_ns": (minute + 1) * NS_PER_MINUTE,
                    },
                ),
            )
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.close_time_ns, 5 * NS_PER_MINUTE)

    def test_gap_never_emits_a_partial_target_bar(self) -> None:
        aggregate = BarAggregator("BTCUSDT", 1, 5)
        aggregate.push(bar(0))
        aggregate.push(bar(1))
        with self.assertRaisesRegex(RuntimeError, "gapped"):
            aggregate.push(bar(3))

    def test_initial_fragment_is_ignored_until_target_alignment(self) -> None:
        aggregate = BarAggregator("BTCUSDT", 1, 5)
        for minute in range(2, 5):
            self.assertIsNone(aggregate.push(bar(minute)))
        output = None
        for minute in range(5, 10):
            output = aggregate.push(bar(minute))
        self.assertIsNotNone(output)
        self.assertEqual(output.open_time_ns, 5 * NS_PER_MINUTE)

    def test_exchange_final_millisecond_close_is_valid(self) -> None:
        aggregate = BarAggregator("BTCUSDT", 1, 5)
        output = None
        for minute in range(5):
            source = bar(minute)
            source = Bar(
                **{
                    **source.to_dict(),
                    "close_time_ns": (minute + 1) * NS_PER_MINUTE - 1_000_000,
                }
            )
            output = aggregate.push(source)
        self.assertIsNotNone(output)


class BoundaryFreshnessTests(unittest.TestCase):
    def test_consumed_boundary_cannot_be_reused_as_source_or_target(self) -> None:
        book = BoundaryBook("BTCUSDT", 0.1)
        boundary = LiquidityBoundary(
            boundary_id="used",
            symbol="BTCUSDT",
            side="HIGH",
            kind="SWING_15M",
            timeframe_minutes=15,
            observed_time_ns=NS_PER_MINUTE,
            lower=99.9,
            upper=100.1,
            price=100.0,
            strength=2.0,
            anchor_serial=0,
        )
        book.boundaries[boundary.boundary_id] = boundary
        decision = bar(5, interval=5, high=101.0, low=99.0)
        book.mark_consumed(decision, 1)
        self.assertEqual(book.source_candidates(100.0, decision.close_time_ns, 1, 2.0), [])
        self.assertEqual(
            book.destination_candidates(
                side="LONG", entry=95.0, decision_time_ns=decision.close_time_ns, serial=1
            ),
            [],
        )

    def test_dynamic_slope_is_measured_per_global_five_minute_serial(self) -> None:
        book = BoundaryBook("BTCUSDT", 0.1)
        first = Pivot("p1", "BTCUSDT", 60, "LOW", 100.0, 1, 2, 3, 1.0)
        second = Pivot("p2", "BTCUSDT", 60, "LOW", 112.0, 3, 4, 4, 1.0)
        book.add_pivots([first], current_serial=12, atr=10.0)
        created = book.add_pivots([second], current_serial=24, atr=10.0)
        dynamic = next(item for item in created if item.kind == "UPTREND_LINE_60M")
        self.assertAlmostEqual(dynamic.dynamic_slope_per_bar, 1.0)
        self.assertAlmostEqual(dynamic.price_at(30), 118.0)


class PriorDayBoundaryTests(unittest.TestCase):
    def test_only_a_complete_prior_utc_day_is_published_causally(self) -> None:
        state = SymbolMarketState("BTCUSDT", 0.1)
        for index in range(288):
            state.push_five_minute(
                bar(
                    index * 5,
                    interval=5,
                    high=110.0 if index == 100 else 101.0,
                    low=90.0 if index == 200 else 99.0,
                )
            )
        created = state.push_five_minute(bar(1440, interval=5))
        prior = {item.kind: item for item in created if item.kind.startswith("PRIOR_DAY_")}
        self.assertEqual(set(prior), {"PRIOR_DAY_HIGH", "PRIOR_DAY_LOW"})
        self.assertEqual(prior["PRIOR_DAY_HIGH"].price, 110.0)
        self.assertEqual(prior["PRIOR_DAY_LOW"].price, 90.0)
        self.assertEqual(prior["PRIOR_DAY_HIGH"].observed_time_ns, 1440 * NS_PER_MINUTE)

    def test_partial_day_does_not_create_prior_day_levels(self) -> None:
        state = SymbolMarketState("BTCUSDT", 0.1)
        for minute in range(60, 1440, 5):
            state.push_five_minute(bar(minute, interval=5))
        created = state.push_five_minute(bar(1440, interval=5))
        self.assertFalse(any(item.kind.startswith("PRIOR_DAY_") for item in created))


class HorizontalObjectiveBookTests(unittest.TestCase):
    def test_confirmation_close_is_not_retroactive_and_later_first_touch_consumes(self) -> None:
        book = ObjectiveBook("BTCUSDT", 0.1)
        pivot = Pivot(
            "P:1M:HIGH",
            "BTCUSDT",
            1,
            "HIGH",
            110.0,
            6 * NS_PER_MINUTE,
            13 * NS_PER_MINUTE - 1,
            12,
            2.0,
        )
        objective = book.add_pivots([pivot])[0]
        confirmation = bar(12, high=111.0, low=99.0)

        book.observe_price(confirmation)

        self.assertIsNone(book.objectives[objective.boundary_id].consumed_time_ns)
        self.assertEqual(book.active(confirmation.close_time_ns), [])
        # The executable LONG target would be one tick inside at 109.9, but
        # lifecycle identity is the actual 110.0 pivot and is not consumed yet.
        later = bar(13, high=109.9, low=99.0)
        book.observe_price(later)
        self.assertEqual(
            [item.boundary_id for item in book.active(later.close_time_ns)],
            [objective.boundary_id],
        )
        touch = bar(14, high=110.0, low=99.0)
        book.observe_price(touch)
        self.assertEqual(book.active(touch.close_time_ns), [])

    def test_market_state_builds_only_declared_objective_pivot_scales(self) -> None:
        state = SymbolMarketState("BTCUSDT", 0.1)
        self.assertEqual(state._pivot_1.span, 6)
        self.assertEqual(state._pivot_5.span, 2)
        self.assertEqual(state._pivot_15.span, 2)

        for index in range(15):
            high = 120.0 if index == 6 else 105.0 if index == 2 else 101.0
            state.push_five_minute(
                bar(index * 5, interval=5, high=high, low=99.0),
            )

        kinds = {item.kind for item in state.objective_book.objectives.values()}
        self.assertIn("HORIZONTAL_OBJECTIVE_5M", kinds)
        self.assertIn("HORIZONTAL_OBJECTIVE_15M", kinds)
        self.assertNotIn("HORIZONTAL_OBJECTIVE_60M", kinds)
        self.assertFalse(any("PRIOR_DAY" in kind for kind in kinds))


if __name__ == "__main__":
    unittest.main()
