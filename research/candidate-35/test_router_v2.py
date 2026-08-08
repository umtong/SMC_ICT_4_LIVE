from __future__ import annotations

from dataclasses import replace
import math
import unittest

from router import BarObservation, FeatureObservation
from router_v2 import (
    RouteConfig,
    RouteDecision,
    economic_net_reward_r,
    preserve_structural_invalidation,
    route_universe,
)


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


def make_bars(*, side: int = 1, start: float = 10.0) -> list[BarObservation]:
    rows: list[BarObservation] = []
    price = start
    for index in range(140):
        if index < 122:
            drift = 0.006 * side
        elif index < 137:
            drift = 0.055 * side
        else:
            drift = 0.090 * side
        open_price = price
        close = open_price + drift
        rows.append(
            BarObservation(
                ts_event=index * 60_000_000_000,
                open=open_price,
                high=max(open_price, close) + 0.030,
                low=min(open_price, close) - 0.030,
                close=close,
                volume=100.0 if index < 137 else 180.0,
            ),
        )
        price = close
    return rows


def sponsored_features(
    bars: dict[str, list[BarObservation]],
    *,
    flow_60s: float = 0.20,
) -> dict[str, FeatureObservation]:
    return {
        symbol: FeatureObservation(
            observed_time_ns=items[-3].ts_event,
            ready=True,
            flow_open_10s=0.25,
            notional_open_10s_burst=1.50,
            flow_60s=flow_60s,
            efficiency_60s=0.20,
            oi_change_15m=0.002,
            premium_z=0.0,
        )
        for symbol, items in bars.items()
    }


class RouterV2Test(unittest.TestCase):
    def test_sponsored_context_aligned_continuation_is_actionable(self) -> None:
        bars = {
            symbol: make_bars(start=10.0 + offset)
            for offset, symbol in enumerate(SYMBOLS)
        }
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=sponsored_features(bars),
            config=RouteConfig(
                min_route_score=2.0,
                min_net_reward_r=1.0,
                all_in_cost_bps_each_side=0.0,
                adverse_slippage_bps_each_side=0.0,
                funding_reserve_bps=0.0,
                ambiguity_score_gap=0.0,
            ),
        )
        self.assertIsNotNone(winner)
        assert winner is not None
        self.assertEqual(winner.state, "PHASE_ACCEPTED_CONTINUATION")
        self.assertEqual(winner.side, 1)
        self.assertEqual(len(decisions), 4)
        self.assertGreaterEqual(
            float(winner.diagnostics["context_breadth"]),
            3,
        )
        anchor = min(
            float(winner.diagnostics["response_low"]),
            float(winner.diagnostics["prior_high"]),
        )
        self.assertLess(winner.stop_reference, anchor)

    def test_price_break_without_same_side_flow_fails_closed(self) -> None:
        bars = {symbol: make_bars() for symbol in SYMBOLS}
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=sponsored_features(bars, flow_60s=-0.20),
            config=RouteConfig(
                min_route_score=2.0,
                min_net_reward_r=0.0,
                all_in_cost_bps_each_side=0.0,
                adverse_slippage_bps_each_side=0.0,
                funding_reserve_bps=0.0,
            ),
        )
        self.assertIsNone(winner)
        self.assertTrue(
            all(
                decision.state == "UNRESOLVED"
                for decision in decisions.values()
            ),
        )
        reasons = {decision.reasons[0] for decision in decisions.values()}
        self.assertIn(
            "OPENING_RESPONSE_NOT_SPONSORED_BY_FLOW_AND_PRICE",
            reasons,
        )

    def test_final_counter_directional_response_fails_closed(self) -> None:
        bars = {symbol: make_bars() for symbol in SYMBOLS}
        for symbol, items in bars.items():
            final = items[-1]
            close = final.open - 0.010
            items[-1] = replace(
                final,
                high=final.open + 0.020,
                low=close - 0.020,
                close=close,
            )
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=sponsored_features(bars),
            config=RouteConfig(
                min_route_score=2.0,
                min_net_reward_r=0.0,
                all_in_cost_bps_each_side=0.0,
                adverse_slippage_bps_each_side=0.0,
                funding_reserve_bps=0.0,
                ambiguity_score_gap=0.0,
            ),
        )
        self.assertIsNone(winner)
        reasons = {decision.reasons[0] for decision in decisions.values()}
        self.assertIn(
            "FINAL_RESPONSE_BAR_NEGATED_ACCEPTANCE_BEFORE_ENTRY",
            reasons,
        )

    def test_structural_stop_is_never_shrunk_inside_invalidation(self) -> None:
        decision = RouteDecision(
            symbol="BTCUSDT",
            state="PHASE_ACCEPTED_CONTINUATION",
            side=1,
            score=5.0,
            expected_target_r=2.2,
            atr=1.0,
            entry_reference=100.0,
            stop_reference=99.5,
            objective_reference=111.0,
            episode_ts=0,
            diagnostics={
                "response_low": 95.0,
                "response_high": 101.0,
                "prior_high": 97.0,
                "prior_low": 90.0,
            },
        )
        repaired = preserve_structural_invalidation(
            decision,
            RouteConfig(
                min_stop_atr=0.42,
                stop_buffer_atr=0.10,
            ),
        )
        self.assertIsNotNone(repaired)
        assert repaired is not None
        self.assertLess(repaired.stop_reference, 95.0)
        self.assertEqual(
            repaired.diagnostics["stop_policy"],
            "NEVER_SHRINK_INSIDE_FROZEN_AUCTION_STRUCTURE",
        )
        self.assertTrue(
            bool(repaired.diagnostics["legacy_stop_inside_invalidation"]),
        )

    def test_costs_are_part_of_reward_rather_than_only_position_size(self) -> None:
        decision = RouteDecision(
            symbol="BTCUSDT",
            state="PHASE_ACCEPTED_CONTINUATION",
            side=1,
            score=5.0,
            expected_target_r=2.0,
            atr=1.0,
            entry_reference=100.0,
            stop_reference=99.9,
            objective_reference=100.2,
            episode_ts=0,
        )
        net_r, planned_loss, expected_profit = economic_net_reward_r(
            decision,
            RouteConfig(),
        )
        self.assertTrue(math.isfinite(net_r))
        self.assertGreater(planned_loss, 0.0)
        self.assertLess(expected_profit, 0.0)
        self.assertLess(net_r, 0.0)


if __name__ == "__main__":
    unittest.main()
