from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
import unittest
from unittest.mock import patch

from c10_v41_overlay import reframe_first_displacement_entry
from c10_v41_overlay import source_entry_mode


class Scenario(StrEnum):
    FAR = "FAR"


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Plan:
    scenario_id: str
    scenario: Scenario
    direction: Direction
    observed_ts_ns: int
    expected_entry: float
    stop_price: float
    target_price: float
    atr: float
    loss_per_unit: float
    gain_per_unit: float
    net_r: float
    reason_code: str
    expire_ts_ns: int
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class Config:
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    min_net_r: float = 1.25


@dataclass
class Logic:
    config: Config = field(default_factory=Config)


def long_plan() -> Plan:
    return Plan(
        scenario_id="SOURCE-LOW",
        scenario=Scenario.FAR,
        direction=Direction.LONG,
        observed_ts_ns=100,
        expected_entry=101.0,
        stop_price=98.0,
        target_price=108.0,
        atr=2.0,
        loss_per_unit=3.1184,
        gain_per_unit=6.9164,
        net_r=2.21,
        reason_code="FIRST_NEAR",
        expire_ts_ns=1000,
        details={
            "zone_low": 100.0,
            "zone_high": 101.0,
            "confirmation_close": 103.0,
            "ce_rejection_primary": {
                "entry_process": "IMMEDIATE_FIRST_DISPLACEMENT_RETRACE",
            },
        },
    )


class V41EntryTimingTest(unittest.TestCase):
    def test_entry_mode_environment_is_exact(self) -> None:
        for value in (
            "FIRST_DISPLACEMENT_NEAR_EDGE",
            "FIRST_DISPLACEMENT_CE",
            "SECOND_REJECTION_DISPLACEMENT",
        ):
            with patch.dict(os.environ, {"C10_V41_SOURCE_ENTRY_MODE": value}):
                self.assertEqual(source_entry_mode(), value)

    def test_first_displacement_ce_changes_only_entry_and_costs(self) -> None:
        plan = long_plan()
        with patch.dict(
            os.environ,
            {"C10_V41_SOURCE_ENTRY_MODE": "FIRST_DISPLACEMENT_CE"},
        ):
            result = reframe_first_displacement_entry(plan, Logic())
        self.assertTrue(result.approved)
        self.assertEqual(result.plan.expected_entry, 100.5)
        self.assertEqual(result.plan.stop_price, plan.stop_price)
        self.assertEqual(result.plan.target_price, plan.target_price)
        self.assertGreater(result.plan.net_r, plan.net_r)
        self.assertEqual(
            result.plan.details["ce_rejection_primary"]["entry_process"],
            "FIRST_DISPLACEMENT_CE_RETRACE",
        )

    def test_near_and_second_modes_leave_plan_identity_unchanged(self) -> None:
        plan = long_plan()
        for value in (
            "FIRST_DISPLACEMENT_NEAR_EDGE",
            "SECOND_REJECTION_DISPLACEMENT",
        ):
            with patch.dict(os.environ, {"C10_V41_SOURCE_ENTRY_MODE": value}):
                result = reframe_first_displacement_entry(plan, Logic())
            self.assertTrue(result.approved)
            self.assertIs(result.plan, plan)

    def test_ce_must_remain_passive_and_cost_feasible(self) -> None:
        plan = long_plan()
        non_passive = Plan(
            **{
                **plan.__dict__,
                "details": {
                    **plan.details,
                    "confirmation_close": 100.4,
                },
            },
        )
        with patch.dict(
            os.environ,
            {"C10_V41_SOURCE_ENTRY_MODE": "FIRST_DISPLACEMENT_CE"},
        ):
            result = reframe_first_displacement_entry(non_passive, Logic())
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "FIRST_DISPLACEMENT_CE_NON_CAUSAL_PRICE_ORDER",
        )

    def test_short_ce_uses_same_void_midpoint(self) -> None:
        plan = long_plan()
        short = Plan(
            **{
                **plan.__dict__,
                "direction": Direction.SHORT,
                "expected_entry": 101.0,
                "stop_price": 104.0,
                "target_price": 94.0,
                "details": {
                    **plan.details,
                    "confirmation_close": 99.0,
                },
            },
        )
        with patch.dict(
            os.environ,
            {"C10_V41_SOURCE_ENTRY_MODE": "FIRST_DISPLACEMENT_CE"},
        ):
            result = reframe_first_displacement_entry(short, Logic())
        self.assertTrue(result.approved)
        self.assertEqual(result.plan.expected_entry, 100.5)
        self.assertEqual(result.plan.stop_price, 104.0)
        self.assertEqual(result.plan.target_price, 94.0)


if __name__ == "__main__":
    unittest.main()
