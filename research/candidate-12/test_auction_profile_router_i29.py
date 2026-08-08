from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import unittest

import pandas as pd

from auction_profile_router_i29 import ExcessTailAuctionRouter


class Scenario(str, Enum):
    LONDON_HIGH_REJECTION = "LONDON_HIGH_REJECTION"
    LONDON_LOW_REJECTION = "LONDON_LOW_REJECTION"


@dataclass
class Plan:
    scenario: Scenario
    observed_ts_ns: int
    details: dict


def auction_frame(
    *,
    previous_price: float,
    current_price: float,
    current_high: float,
    current_low: float,
) -> pd.DataFrame:
    index = pd.date_range(
        "2023-06-25 00:01:00+00:00",
        "2023-06-25 12:00:00+00:00",
        freq="1min",
    )
    values = []
    for ts in index:
        price = (
            previous_price
            if ts <= pd.Timestamp("2023-06-25 06:00:00+00:00")
            else current_price
        )
        values.append(
            {
                "high": price + 0.1,
                "low": price - 0.1,
                "close": price,
                "volume": 1.0,
            }
        )
    frame = pd.DataFrame(values, index=index)
    frame.loc[pd.Timestamp("2023-06-25 06:30:00+00:00"), "high"] = current_high
    frame.loc[pd.Timestamp("2023-06-25 08:00:00+00:00"), "low"] = current_low
    return frame


class ExcessTailAuctionRouterTests(unittest.TestCase):
    @staticmethod
    def router(frame: pd.DataFrame) -> ExcessTailAuctionRouter:
        return ExcessTailAuctionRouter({"BTCUSDT": frame}, {"BTCUSDT": 0.1})

    @staticmethod
    def plan(scenario: Scenario, raid_extreme: float) -> Plan:
        observed = pd.Timestamp("2023-06-25 12:05:00+00:00")
        return Plan(
            scenario=scenario,
            observed_ts_ns=int(observed.value),
            details={"source": "LONDON", "raid_extreme": raid_extreme},
        )

    def test_shallow_high_probe_is_not_faded_against_higher_value(self) -> None:
        router = self.router(
            auction_frame(
                previous_price=95.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_HIGH_REJECTION, 101.5),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "MIGRATED_HIGH_RAID_DID_NOT_CLEAR_EXCESS_TAIL",
        )
        self.assertFalse(
            decision.context["excess_tail_test"]["cleared_excess_tail"]
        )

    def test_deep_high_probe_can_fail_after_clearing_existing_tail(self) -> None:
        router = self.router(
            auction_frame(
                previous_price=95.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_HIGH_REJECTION, 102.5),
        )
        self.assertTrue(decision.approved)
        self.assertEqual(decision.reason, "EXCESS_TAIL_AUCTION_APPROVED")
        self.assertTrue(
            decision.context["excess_tail_test"]["cleared_excess_tail"]
        )

    def test_shallow_low_probe_is_not_bought_against_lower_value(self) -> None:
        router = self.router(
            auction_frame(
                previous_price=105.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_LOW_REJECTION, 98.5),
        )
        self.assertFalse(decision.approved)
        self.assertEqual(
            decision.reason,
            "MIGRATED_LOW_RAID_DID_NOT_CLEAR_EXCESS_TAIL",
        )

    def test_overlapping_value_does_not_require_tail_clearance(self) -> None:
        router = self.router(
            auction_frame(
                previous_price=100.0,
                current_price=100.0,
                current_high=101.0,
                current_low=99.0,
            )
        )
        decision = router.evaluate(
            "BTCUSDT",
            self.plan(Scenario.LONDON_HIGH_REJECTION, 101.2),
        )
        self.assertTrue(decision.approved)
        self.assertFalse(
            decision.context["excess_tail_test"]["against_migration"]
        )


if __name__ == "__main__":
    unittest.main()
