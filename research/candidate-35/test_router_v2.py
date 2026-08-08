from __future__ import annotations

import math
import unittest

from router import BarObservation, FeatureObservation
from router_v2 import RouteConfig, RouteDecision, economic_net_reward_r, route_universe


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


class RouterV2Test(unittest.TestCase):
    def test_sponsored_context_aligned_continuation_is_actionable(self) -> None:
        bars = {
            symbol: make_bars(start=10.0 + offset)
            for offset, symbol in enumerate(SYMBOLS)
        }
        features = {
            symbol: FeatureObservation(
                observed_time_ns=items[-3].ts_event,
                ready=True,
                flow_open_10s=0.25,
                notional_open_10s_burst=1.50,
                flow_60s=0.20,
                efficiency_60s=0.20,
                oi_change_15m=0.002,
                premium_z=0.0,
            )
            for symbol, items in bars.items()
        }
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=features,
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

    def test_price_break_without_same_side_flow_fails_closed(self) -> None:
        bars = {symbol: make_bars() for symbol in SYMBOLS}
        features = {
            symbol: FeatureObservation(
                observed_time_ns=items[-3].ts_event,
                ready=True,
                flow_open_10s=0.25,
                notional_open_10s_burst=1.50,
                flow_60s=-0.20,
                efficiency_60s=0.20,
            )
            for symbol, items in bars.items()
        }
        winner, decisions = route_universe(
            bars_by_symbol=bars,
            features_by_symbol=features,
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
