from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
import os
import unittest
from unittest.mock import patch

from c10_v35_overlay import reframe_consequent_encroachment


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


def short_plan() -> Plan:
    return Plan(
        scenario_id="HIGH-RAID",
        scenario=Scenario.FAR,
        direction=Direction.SHORT,
        observed_ts_ns=300,
        expected_entry=98.0,
        stop_price=105.0,
        target_price=80.0,
        atr=4.0,
        loss_per_unit=7.1232,
        gain_per_unit=17.9288,
        net_r=2.51,
        reason_code="BASE",
        expire_ts_ns=400,
        details={
            "confirmation_close": 96.0,
            "sweep_extreme": 104.0,
            "sweep_ts_ns": 100,
            "zone_low": 98.0,
            "zone_high": 100.0,
        },
    )


def logic(*, delivered: bool = False, opposite: float | None = 80.0) -> Logic:
    return Logic(
        pools=[Pool("HIGH-RAID", 100.0, opposite)],
        bars=[
            Bar(100, 104.0, 99.0),
            Bar(200, 100.0, 89.0 if delivered else 95.0),
            Bar(300, 98.0, 89.0 if delivered else 95.0),
        ],
    )


class ConsequentEncroachmentContractTest(unittest.TestCase):
    def test_exact_baseline_is_identity(self) -> None:
        original = short_plan()
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "0",
                "C10_V35_EQUILIBRIUM_TARGET": "0",
            },
        ):
            result = reframe_consequent_encroachment(original, logic())
        self.assertTrue(result.approved)
        self.assertIs(result.plan, original)
        self.assertEqual(result.reason, "EXACT_BASELINE_UNCHANGED")

    def test_ce_is_exact_displacement_zone_midpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "1",
                "C10_V35_EQUILIBRIUM_TARGET": "0",
            },
        ):
            result = reframe_consequent_encroachment(short_plan(), logic())
        self.assertTrue(result.approved)
        self.assertEqual(result.plan.expected_entry, 99.0)
        self.assertEqual(result.plan.target_price, 80.0)
        details = result.plan.details["displacement_ce_primary"]
        self.assertEqual(details["confirmation_zone_low"], 98.0)
        self.assertEqual(details["confirmation_zone_high"], 100.0)
        self.assertEqual(details["consequent_encroachment"], 99.0)
        self.assertEqual(
            details["entry_state_sequence"],
            [
                "FAILED_AUCTION_CONFIRMED",
                "DISPLACEMENT_CE_RETRACE_PENDING",
                "PRIMARY_TRADE_OPEN_OR_EXPIRED",
            ],
        )

    def test_ce_can_make_equilibrium_payoff_viable(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "0",
                "C10_V35_EQUILIBRIUM_TARGET": "1",
            },
        ):
            near = reframe_consequent_encroachment(short_plan(), logic())
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "1",
                "C10_V35_EQUILIBRIUM_TARGET": "1",
            },
        ):
            ce = reframe_consequent_encroachment(short_plan(), logic())
        self.assertFalse(near.approved)
        self.assertEqual(
            near.reason,
            "DISPLACEMENT_CE_INSUFFICIENT_COSTED_STRUCTURAL_R",
        )
        self.assertTrue(ce.approved)
        self.assertEqual(ce.plan.expected_entry, 99.0)
        self.assertEqual(ce.plan.target_price, 90.0)
        self.assertGreater(ce.plan.net_r, 1.25)

    def test_nonpassive_ce_is_rejected(self) -> None:
        original = short_plan()
        invalid = replace(
            original,
            details={**original.details, "confirmation_close": 99.5},
        )
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "1",
                "C10_V35_EQUILIBRIUM_TARGET": "1",
            },
        ):
            result = reframe_consequent_encroachment(invalid, logic())
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "DISPLACEMENT_CE_NOT_PASSIVE_BETWEEN_CONFIRMATION_AND_RAID",
        )

    def test_equilibrium_already_delivered_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "1",
                "C10_V35_EQUILIBRIUM_TARGET": "1",
            },
        ):
            result = reframe_consequent_encroachment(
                short_plan(),
                logic(delivered=True),
            )
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "SOURCE_EQUILIBRIUM_DELIVERED_BEFORE_ENTRY_PLAN",
        )

    def test_entry_and_target_are_independent_ablation_axes(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "1",
                "C10_V35_EQUILIBRIUM_TARGET": "0",
            },
        ):
            ce_external = reframe_consequent_encroachment(short_plan(), logic())
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "0",
                "C10_V35_EQUILIBRIUM_TARGET": "1",
            },
        ):
            near_equilibrium = reframe_consequent_encroachment(
                short_plan(),
                logic(),
            )
        self.assertTrue(ce_external.approved)
        self.assertEqual(ce_external.plan.expected_entry, 99.0)
        self.assertEqual(ce_external.plan.target_price, 80.0)
        self.assertFalse(near_equilibrium.approved)

    def test_missing_source_pair_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "C10_V35_CE_ENTRY": "1",
                "C10_V35_EQUILIBRIUM_TARGET": "1",
            },
        ):
            result = reframe_consequent_encroachment(
                short_plan(),
                logic(opposite=None),
            )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SOURCE_RANGE_ENDPOINTS_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
