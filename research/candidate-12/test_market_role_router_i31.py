from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unittest

import pandas as pd

from market_role_router_i31 import BenchmarkMaturityAuctionRouter


class Scenario(str, Enum):
    LONDON_LOW_ACCEPTANCE_REACCELERATION = "LONDON_LOW_ACCEPTANCE_REACCELERATION"


@dataclass
class Plan:
    scenario: Scenario
    observed_ts_ns: int
    expected_entry: float
    target_price: float
    details: dict


def frame(
    *,
    previous_price: float = 100.0,
    source_price: float = 100.0,
    source_high: float = 101.0,
    source_low: float = 99.0,
    post_close: float = 98.5,
) -> pd.DataFrame:
    index = pd.date_range(
        "2023-06-25 00:01:00+00:00",
        "2023-06-25 12:10:00+00:00",
        freq="1min",
    )
    rows = []
    for ts in index:
        price = (
            previous_price
            if ts <= pd.Timestamp("2023-06-25 06:00:00+00:00")
            else source_price
        )
        if ts > pd.Timestamp("2023-06-25 12:00:00+00:00"):
            price = post_close
        rows.append(
            {
                "open": price,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 1.0,
            }
        )
    result = pd.DataFrame(rows, index=index)
    result.loc[pd.Timestamp("2023-06-25 06:30:00+00:00"), "high"] = source_high
    result.loc[pd.Timestamp("2023-06-25 08:00:00+00:00"), "low"] = source_low
    return result


class BenchmarkMaturityAuctionRouterTests(unittest.TestCase):
    @staticmethod
    def plan() -> Plan:
        observed = pd.Timestamp("2023-06-25 12:10:00+00:00")
        return Plan(
            scenario=Scenario.LONDON_LOW_ACCEPTANCE_REACCELERATION,
            observed_ts_ns=int(observed.value),
            expected_entry=98.5,
            target_price=98.0,
            details={
                "source": "LONDON",
                "session_low": 99.0,
                "session_width": 2.0,
            },
        )

    def test_follower_is_rejected_after_benchmark_full_range_projection(self) -> None:
        router = BenchmarkMaturityAuctionRouter(
            {
                "BTCUSDT": frame(post_close=96.9),
                "ETHUSDT": frame(post_close=98.5),
            },
            {"BTCUSDT": 0.1, "ETHUSDT": 0.1},
        )
        decision = router.evaluate("ETHUSDT", self.plan())
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "FOLLOWER_REACCELERATION_AFTER_BENCHMARK_FULL_RANGE_DISCOVERY",
        )
        state = decision.context["benchmark_downside_maturity"]
        self.assertTrue(state["full_range_projection_consumed"])

    def test_follower_is_allowed_before_benchmark_full_range_projection(self) -> None:
        router = BenchmarkMaturityAuctionRouter(
            {
                "BTCUSDT": frame(post_close=97.1),
                "ETHUSDT": frame(post_close=98.5),
            },
            {"BTCUSDT": 0.1, "ETHUSDT": 0.1},
        )
        decision = router.evaluate("ETHUSDT", self.plan())
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.reason,
            "BENCHMARK_MATURITY_AUCTION_APPROVED",
        )
        state = decision.context["benchmark_downside_maturity"]
        self.assertFalse(state["full_range_projection_consumed"])

    def test_benchmark_can_trade_its_own_reacceleration_at_projection(self) -> None:
        router = BenchmarkMaturityAuctionRouter(
            {"BTCUSDT": frame(post_close=96.9)},
            {"BTCUSDT": 0.1},
        )
        decision = router.evaluate("BTCUSDT", self.plan())
        self.assertTrue(decision.approved)
        self.assertTrue(
            decision.context["benchmark_downside_maturity"][
                "full_range_projection_consumed"
            ]
        )


if __name__ == "__main__":
    unittest.main()
