from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unittest

import pandas as pd

from market_role_router_i30 import MarketRoleAuctionRouter


class Scenario(str, Enum):
    LONDON_HIGH_REJECTION = "LONDON_HIGH_REJECTION"
    LONDON_HIGH_ACCEPTANCE = "LONDON_HIGH_ACCEPTANCE"
    LONDON_LOW_ACCEPTANCE = "LONDON_LOW_ACCEPTANCE"
    LONDON_LOW_ACCEPTANCE_REACCELERATION = "LONDON_LOW_ACCEPTANCE_REACCELERATION"
    LONDON_LOW_DELAYED_REJECTION = "LONDON_LOW_DELAYED_REJECTION"
    LONDON_HIGH_ACCEPTANCE_FAILURE = "LONDON_HIGH_ACCEPTANCE_FAILURE"


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
    current_price: float = 100.0,
    source_high: float = 101.0,
    source_low: float = 99.0,
    post_closes: tuple[float, float] = (100.0, 100.0),
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
            else current_price
        )
        rows.append(
            {"open": price, "high": price + 0.1, "low": price - 0.1, "close": price, "volume": 1.0}
        )
    result = pd.DataFrame(rows, index=index)
    result.loc[pd.Timestamp("2023-06-25 06:30:00+00:00"), "high"] = source_high
    result.loc[pd.Timestamp("2023-06-25 08:00:00+00:00"), "low"] = source_low
    for timestamp, close in zip(
        (
            pd.Timestamp("2023-06-25 12:05:00+00:00"),
            pd.Timestamp("2023-06-25 12:10:00+00:00"),
        ),
        post_closes,
        strict=True,
    ):
        result.loc[timestamp, ["open", "high", "low", "close"]] = [
            close,
            close + 0.05,
            close - 0.05,
            close,
        ]
    return result


class MarketRoleAuctionRouterTests(unittest.TestCase):
    def router(
        self,
        *,
        local: pd.DataFrame | None = None,
        benchmark: pd.DataFrame | None = None,
    ) -> MarketRoleAuctionRouter:
        frames = {
            "BTCUSDT": benchmark if benchmark is not None else frame(),
            "ETHUSDT": local if local is not None else frame(),
        }
        return MarketRoleAuctionRouter(
            frames,
            {"BTCUSDT": 0.1, "ETHUSDT": 0.1},
            benchmark_symbol="BTCUSDT",
        )

    @staticmethod
    def plan(
        scenario: Scenario,
        *,
        entry: float = 100.0,
        target: float = 99.0,
        **details: float | str,
    ) -> Plan:
        observed = pd.Timestamp("2023-06-25 12:10:00+00:00")
        payload = {"source": "LONDON", **details}
        return Plan(
            scenario=scenario,
            observed_ts_ns=int(observed.value),
            expected_entry=entry,
            target_price=target,
            details=payload,
        )

    def test_follower_high_acceptance_is_not_independent_discovery(self) -> None:
        decision = self.router().evaluate(
            "ETHUSDT",
            self.plan(Scenario.LONDON_HIGH_ACCEPTANCE),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "FOLLOWER_HIGH_ACCEPTANCE_LACKED_BENCHMARK_OWNERSHIP",
        )

    def test_benchmark_true_acceptance_vetoes_follower_high_fade(self) -> None:
        benchmark = frame(post_closes=(101.2, 101.3))
        decision = self.router(benchmark=benchmark).evaluate(
            "ETHUSDT",
            self.plan(
                Scenario.LONDON_HIGH_REJECTION,
                raid_extreme=101.2,
            ),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "BENCHMARK_TRUE_HIGH_ACCEPTANCE_VETOED_FOLLOWER_FADE",
        )

    def test_benchmark_failed_acceptance_can_confirm_shallow_follower_fade(self) -> None:
        benchmark = frame(post_closes=(101.2, 100.5))
        decision = self.router(benchmark=benchmark).evaluate(
            "ETHUSDT",
            self.plan(
                Scenario.LONDON_HIGH_REJECTION,
                raid_extreme=101.2,
            ),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.context["follower_high_rejection_transition"],
            "BENCHMARK_FAILED_ACCEPTANCE",
        )

    def test_follower_new_price_probe_can_confirm_high_fade(self) -> None:
        decision = self.router().evaluate(
            "ETHUSDT",
            self.plan(
                Scenario.LONDON_HIGH_REJECTION,
                raid_extreme=102.5,
            ),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(
            decision.context["follower_high_rejection_transition"],
            "FOLLOWER_CLEARED_PRE_EXISTING_EXCESS_TAIL",
        )

    def test_delayed_low_reversal_requires_close_above_reclaim_high(self) -> None:
        decision = self.router().evaluate(
            "BTCUSDT",
            self.plan(
                Scenario.LONDON_LOW_DELAYED_REJECTION,
                entry=100.0,
                reclaim_high=101.0,
            ),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "DELAYED_LOW_REVERSAL_LACKED_ACTUAL_BULLISH_MSS",
        )

    def test_high_acceptance_failure_cannot_fade_migrated_higher_value(self) -> None:
        local = frame(previous_price=95.0, current_price=100.0)
        decision = self.router(local=local).evaluate(
            "ETHUSDT",
            self.plan(Scenario.LONDON_HIGH_ACCEPTANCE_FAILURE),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "HIGH_ACCEPTANCE_FAILURE_AGAINST_MIGRATED_HIGHER_VALUE",
        )

    def test_low_reacceleration_after_full_range_traversal_is_mature(self) -> None:
        decision = self.router().evaluate(
            "ETHUSDT",
            self.plan(
                Scenario.LONDON_LOW_ACCEPTANCE_REACCELERATION,
                entry=96.5,
                session_low=99.0,
                session_width=2.0,
            ),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "LOW_REACCELERATION_AFTER_FULL_SOURCE_RANGE_TRAVERSAL",
        )

    def test_idiosyncratic_follower_low_acceptance_keeps_full_range_objective(self) -> None:
        decision = self.router().evaluate(
            "ETHUSDT",
            self.plan(
                Scenario.LONDON_LOW_ACCEPTANCE,
                entry=98.9,
                target=97.0,
                session_low=99.0,
                session_width=2.0,
            ),
        )
        self.assertTrue(decision.approved)
        ownership = decision.context["follower_low_acceptance_ownership"]
        self.assertTrue(ownership["owns_full_range"])
        self.assertFalse(ownership["benchmark_true_low_acceptance"])

    def test_benchmark_downside_acceptance_vetoes_follower_low_acceptance(self) -> None:
        benchmark = frame(post_closes=(98.8, 98.7))
        decision = self.router(benchmark=benchmark).evaluate(
            "ETHUSDT",
            self.plan(
                Scenario.LONDON_LOW_ACCEPTANCE,
                entry=98.9,
                target=97.0,
                session_low=99.0,
                session_width=2.0,
            ),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "FOLLOWER_LOW_ACCEPTANCE_AFTER_BENCHMARK_DISCOVERY",
        )


if __name__ == "__main__":
    unittest.main()
