from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unittest

import pandas as pd

from auction_profile_router import AuctionProfileRouter


class Scenario(str, Enum):
    LONDON_HIGH_REJECTION = "LONDON_HIGH_REJECTION"
    ASIA_HIGH_REACCEPTANCE = "ASIA_HIGH_REACCEPTANCE"
    LONDON_LOW_DELAYED_REJECTION = "LONDON_LOW_DELAYED_REJECTION"


@dataclass
class Plan:
    scenario: Scenario
    observed_ts_ns: int
    details: dict


def session_frame(
    *,
    previous_price: float,
    current_price: float,
    current_high: float,
    current_low: float,
    duplicate_high: bool = False,
) -> pd.DataFrame:
    index = pd.date_range(
        "2023-06-25 00:01:00+00:00",
        "2023-06-25 12:00:00+00:00",
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
            {"high": price + 0.1, "low": price - 0.1, "close": price, "volume": 1.0}
        )
    frame = pd.DataFrame(rows, index=index)
    frame.loc[pd.Timestamp("2023-06-25 06:30:00+00:00"), "high"] = current_high
    if duplicate_high:
        frame.loc[pd.Timestamp("2023-06-25 07:30:00+00:00"), "high"] = current_high
    frame.loc[pd.Timestamp("2023-06-25 08:00:00+00:00"), "low"] = current_low
    return frame


class AuctionProfileRouterTests(unittest.TestCase):
    def make_router(self, frame: pd.DataFrame) -> AuctionProfileRouter:
        return AuctionProfileRouter({"BTCUSDT": frame}, {"BTCUSDT": 0.1})

    @staticmethod
    def plan(scenario: Scenario) -> Plan:
        observed = pd.Timestamp("2023-06-25 12:05:00+00:00")
        return Plan(
            scenario=scenario,
            observed_ts_ns=int(observed.value),
            details={"source": "LONDON"},
        )

    def test_poor_high_is_not_faded(self) -> None:
        router = self.make_router(
            session_frame(
                previous_price=100.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
                duplicate_high=True,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_HIGH_REJECTION),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "POOR_HIGH_UNFINISHED_AUCTION_NOT_FADEABLE",
        )
        self.assertTrue(decision.context["current"]["poor_high"])

    def test_unique_excess_high_is_faded_but_not_reaccepted(self) -> None:
        router = self.make_router(
            session_frame(
                previous_price=100.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
                duplicate_high=False,
            )
        )
        reversal = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_HIGH_REJECTION),
        )
        continuation = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.ASIA_HIGH_REACCEPTANCE),
        )
        self.assertTrue(reversal.approved)
        self.assertFalse(continuation.approved)
        self.assertEqual(
            continuation.reason,
            "HIGH_REACCEPTANCE_LACKED_UNFINISHED_AUCTION",
        )

    def test_delayed_low_reversal_is_blocked_when_value_migrated_lower(self) -> None:
        router = self.make_router(
            session_frame(
                previous_price=105.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
                duplicate_high=False,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_LOW_DELAYED_REJECTION),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "DELAYED_LOW_REVERSAL_AGAINST_LOWER_VALUE",
        )

    def test_delayed_low_reversal_is_allowed_when_value_migrated_higher(self) -> None:
        router = self.make_router(
            session_frame(
                previous_price=95.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
                duplicate_high=False,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_LOW_DELAYED_REJECTION),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.reason, "AUCTION_PROFILE_APPROVED")


if __name__ == "__main__":
    unittest.main()
