from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import os
import unittest
from unittest.mock import patch

from c10_v34_overlay import reframe_source_retest


class Scenario(StrEnum):
    FAR = "FAR"
    AAC = "AAC"


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
class Pool:
    scenario_id: str
    level: float
    opposite_level: float | None


@dataclass
class Bar:
    ts_ns: int
    high: float
    low: float


@dataclass
class Config:
    effective_maker_rate: float = 0.0004
    effective_taker_rate: float = 0.0008
    min_net_r: float = 1.25


@dataclass
class Logic:
    pools: list[Pool]
    bars: list[Bar]
    config: Config = field(default_factory=Config)


def plan() -> Plan:
    return Plan(
        scenario_id="HIGH-RAID",
        scenario=Scenario.FAR,
        direction=Direction.SHORT,
        observed_ts_ns=300,
        expected_entry=98.0,
        stop_price=106.0,
        target_price=80.0,
        atr=4.0,
        loss_per_unit=8.124,
        gain_per_unit=17.9288,
        net_r=2.20,
        reason_code="BASE",
        expire_ts_ns=400,
        details={
            "confirmation_close": 96.0,
            "sweep_extreme": 105.0,
            "sweep_ts_ns": 100,
            "zone_low": 98.0,
            "zone_high": 99.0,
        },
    )


def logic(*, source: float = 100.0, delivered: bool = False) -> Logic:
    return Logic(
        pools=[Pool("HIGH-RAID", source, 80.0)],
        bars=[
            Bar(100, 105.0, 99.0),
            Bar(200, 100.0, 94.0 if delivered else 95.0),
            Bar(300, 98.0, 89.0 if delivered else 95.0),
        ],
    )


class SourceBoundaryRetestTest(unittest.TestCase):
    def test_exact_baseline_is_identity(self) -> None:
        original = plan()
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "0",
                "C10_V34_EQUILIBRIUM_TARGET": "0",
            },
        ):
            result = reframe_source_retest(original, logic())
        self.assertTrue(result.approved)
        self.assertIs(result.plan, original)
        self.assertEqual(result.reason, "EXACT_BASELINE_UNCHANGED")

    def test_source_boundary_is_passive_structural_entry(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "1",
                "C10_V34_EQUILIBRIUM_TARGET": "0",
            },
        ):
            result = reframe_source_retest(plan(), logic())
        self.assertTrue(result.approved)
        self.assertEqual(result.plan.expected_entry, 100.0)
        self.assertEqual(result.plan.target_price, 80.0)
        self.assertEqual(
            result.plan.details["source_retest_primary"]["entry_state_sequence"],
            [
                "FAILED_AUCTION_CONFIRMED",
                "SOURCE_BOUNDARY_RETEST_PENDING",
                "PRIMARY_TRADE_OPEN_OR_EXPIRED",
            ],
        )

    def test_source_retest_can_make_equilibrium_payoff_viable(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "0",
                "C10_V34_EQUILIBRIUM_TARGET": "1",
            },
        ):
            near = reframe_source_retest(plan(), logic())
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "1",
                "C10_V34_EQUILIBRIUM_TARGET": "1",
            },
        ):
            source = reframe_source_retest(plan(), logic())
        self.assertFalse(near.approved)
        self.assertEqual(near.reason, "SOURCE_RETEST_INSUFFICIENT_COSTED_STRUCTURAL_R")
        self.assertTrue(source.approved)
        self.assertEqual(source.plan.expected_entry, 100.0)
        self.assertEqual(source.plan.target_price, 90.0)
        self.assertGreater(source.plan.net_r, 1.25)

    def test_nonpassive_source_boundary_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "1",
                "C10_V34_EQUILIBRIUM_TARGET": "1",
            },
        ):
            result = reframe_source_retest(plan(), logic(source=95.0))
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SOURCE_RETEST_NOT_PASSIVE_BETWEEN_CONFIRMATION_AND_RAID",
        )

    def test_equilibrium_already_delivered_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "1",
                "C10_V34_EQUILIBRIUM_TARGET": "1",
            },
        ):
            result = reframe_source_retest(plan(), logic(delivered=True))
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SOURCE_EQUILIBRIUM_DELIVERED_BEFORE_ENTRY_PLAN",
        )

    def test_entry_and_target_are_independent_ablation_axes(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "1",
                "C10_V34_EQUILIBRIUM_TARGET": "0",
            },
        ):
            source_external = reframe_source_retest(plan(), logic())
        with patch.dict(
            os.environ,
            {
                "C10_V34_SOURCE_RETEST_ENTRY": "0",
                "C10_V34_EQUILIBRIUM_TARGET": "1",
            },
        ):
            near_equilibrium = reframe_source_retest(plan(), logic())
        self.assertTrue(source_external.approved)
        self.assertEqual(source_external.plan.expected_entry, 100.0)
        self.assertEqual(source_external.plan.target_price, 80.0)
        self.assertFalse(near_equilibrium.approved)


if __name__ == "__main__":
    unittest.main()
