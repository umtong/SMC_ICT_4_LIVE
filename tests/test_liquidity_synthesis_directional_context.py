from __future__ import annotations

import math
import unittest

from smc_ict_4.episode_policy_live.directional_context import (
    boundary_role,
    build_active_liquidity_context,
    build_directional_context,
    build_directional_update,
)
from smc_ict_4.episode_policy_live.domain import Bar, LiquidityBoundary


NS_PER_MINUTE = 60_000_000_000


def bars(symbol: str, count: int, step: float, *, start: float = 100.0) -> list[Bar]:
    output: list[Bar] = []
    for index in range(count):
        open_price = start + step * index
        close = open_price + step
        output.append(
            Bar(
                symbol=symbol,
                interval_minutes=5,
                open_time_ns=index * 5 * NS_PER_MINUTE,
                close_time_ns=(index + 1) * 5 * NS_PER_MINUTE,
                open=open_price,
                high=max(open_price, close) + 0.25,
                low=min(open_price, close) - 0.25,
                close=close,
                volume=10.0,
                quote_volume=1_000.0,
                taker_buy_quote_volume=650.0 if step > 0 else 350.0,
                trade_count=20,
            )
        )
    return output


def boundary(
    boundary_id: str,
    side: str,
    price: float,
    timeframe: int,
    strength: float,
    *,
    kind: str | None = None,
    observed: int = 0,
    consumed: int | None = None,
) -> LiquidityBoundary:
    return LiquidityBoundary(
        boundary_id=boundary_id,
        symbol="BTCUSDT",
        side=side,
        kind=kind or f"SWING_{timeframe}M",
        timeframe_minutes=timeframe,
        observed_time_ns=observed,
        lower=price - 0.1,
        upper=price + 0.1,
        price=price,
        strength=strength,
        consumed_time_ns=consumed,
    )


class DirectionalContextTest(unittest.TestCase):
    def test_missing_history_and_peer_state_remain_unknown(self) -> None:
        only = bars("BTCUSDT", 1, 0.0)
        context = build_directional_context(
            symbol="BTCUSDT",
            side="LONG",
            decision_time_ns=only[-1].close_time_ns,
            bars_by_symbol={"BTCUSDT": only},
        )
        self.assertIsNone(context.atr_price)
        self.assertIsNone(context.trend_alignment)
        self.assertIsNone(context.path_efficiency)
        self.assertIsNone(context.common_component)
        self.assertIsNone(context.symbol_residual)
        self.assertTrue(all(item.move_atr is None for item in context.horizons))

    def test_path_common_and_symbol_residual_are_separate(self) -> None:
        target = bars("BTCUSDT", 170, 2.0)
        peer = bars("ETHUSDT", 170, 1.0, start=50.0)
        decision = target[-1].close_time_ns
        context = build_directional_context(
            symbol="BTCUSDT",
            side="LONG",
            decision_time_ns=decision,
            bars_by_symbol={"BTCUSDT": target, "ETHUSDT": peer},
        )
        self.assertAlmostEqual(context.path_efficiency or -1.0, 1.0)
        self.assertIsNotNone(context.common_component)
        self.assertIsNotNone(context.symbol_residual)
        self.assertGreater(context.common_component or 0.0, 0.0)
        self.assertGreater(context.symbol_residual or 0.0, 0.0)
        for item in context.horizons:
            self.assertAlmostEqual(item.path_efficiency or -1.0, 1.0)
            self.assertAlmostEqual(
                item.residual_move_atr or 0.0,
                (item.move_atr or 0.0) - (item.common_move_atr or 0.0),
            )

        short = build_directional_context(
            symbol="BTCUSDT",
            side="SHORT",
            decision_time_ns=decision,
            bars_by_symbol={"BTCUSDT": target, "ETHUSDT": peer},
        )
        self.assertAlmostEqual(short.common_component or 0.0, -(context.common_component or 0.0))
        self.assertAlmostEqual(short.symbol_residual or 0.0, -(context.symbol_residual or 0.0))

    def test_future_mutation_cannot_change_decision_context(self) -> None:
        history = bars("BTCUSDT", 170, 1.0)
        decision = history[-1].close_time_ns
        baseline = build_directional_context(
            symbol="BTCUSDT",
            side="LONG",
            decision_time_ns=decision,
            bars_by_symbol={"BTCUSDT": history},
        )
        future = bars("BTCUSDT", 1, -100.0, start=10_000.0)[0]
        future = Bar(
            **{
                **future.to_dict(),
                "open_time_ns": decision,
                "close_time_ns": decision + 5 * NS_PER_MINUTE,
            }
        )
        changed = build_directional_context(
            symbol="BTCUSDT",
            side="LONG",
            decision_time_ns=decision,
            bars_by_symbol={"BTCUSDT": [*history, future]},
        )
        self.assertEqual(baseline, changed)

    def test_pre_event_direction_is_not_overwritten_by_event_update(self) -> None:
        history = bars("BTCUSDT", 170, 1.0)
        update = build_directional_update(
            symbol="BTCUSDT",
            side="LONG",
            prior_time_ns=history[-3].close_time_ns,
            decision_time_ns=history[-1].close_time_ns,
            bars_by_symbol={"BTCUSDT": history},
        )
        self.assertEqual(update.prior.decision_time_ns, history[-3].close_time_ns)
        self.assertEqual(update.posterior.decision_time_ns, history[-1].close_time_ns)
        self.assertIsNot(update.prior, update.posterior)
        self.assertIsNone(update.common_component_update)
        self.assertIsNone(update.symbol_residual_update)

    def test_gap_is_rejected_instead_of_measuring_a_false_path(self) -> None:
        history = bars("BTCUSDT", 20, 1.0)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            build_directional_context(
                symbol="BTCUSDT",
                side="LONG",
                decision_time_ns=history[-1].close_time_ns,
                bars_by_symbol={"BTCUSDT": [*history[:8], *history[9:]]},
            )


class ActiveLiquidityContextTest(unittest.TestCase):
    def test_direction_sources_and_route_obstacles_have_distinct_roles(self) -> None:
        local = boundary("local", "HIGH", 101.0, 15, 1.10)
        external = boundary("external", "HIGH", 102.0, 60, 1.0)
        prior_day = boundary(
            "pdl",
            "LOW",
            98.0,
            1440,
            4.0,
            kind="PRIOR_DAY_LOW",
        )
        self.assertFalse(boundary_role(local).direction_source)
        self.assertTrue(boundary_role(local).route_obstacle)
        self.assertTrue(boundary_role(external).direction_source)
        self.assertTrue(boundary_role(prior_day).direction_source)

    def test_two_sided_map_uses_only_fresh_levels_and_local_can_block_route(self) -> None:
        decision = 100
        levels = [
            boundary("near-local", "HIGH", 101.0, 15, 1.10),
            boundary("high-source", "HIGH", 103.0, 60, 2.0),
            boundary("low-source", "LOW", 97.0, 60, 2.0),
            boundary("consumed", "LOW", 99.0, 60, 3.0, consumed=90),
            boundary("future", "HIGH", 100.5, 60, 3.0, observed=101),
        ]
        context = build_active_liquidity_context(
            boundaries=levels,
            price=100.0,
            decision_time_ns=decision,
            serial=0,
            atr_price=2.0,
        )
        self.assertEqual(context.nearest_long_obstacle.boundary_id, "near-local")
        self.assertEqual(context.nearest_short_obstacle.boundary_id, "low-source")
        self.assertFalse(context.nearest_long_obstacle.direction_source)
        self.assertEqual({item.boundary_id for item in context.above}, {"near-local", "high-source"})
        self.assertEqual({item.boundary_id for item in context.below}, {"low-source"})
        self.assertIsNotNone(context.direction_source_balance)
        self.assertTrue(math.isfinite(context.two_sided_source_pull or math.nan))

    def test_one_sided_source_map_is_unknown_not_zero(self) -> None:
        context = build_active_liquidity_context(
            boundaries=[boundary("only-high", "HIGH", 102.0, 60, 2.0)],
            price=100.0,
            decision_time_ns=10,
            serial=0,
            atr_price=1.0,
        )
        self.assertIsNone(context.direction_source_balance)
        self.assertIsNone(context.two_sided_source_pull)


if __name__ == "__main__":
    unittest.main()
