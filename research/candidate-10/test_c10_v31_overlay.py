from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import unittest

from c10_v31_overlay import certify_sweep_efficiency


class Scenario(StrEnum):
    FAR = "FAR"


@dataclass
class Plan:
    scenario_id: str
    scenario: Scenario = Scenario.FAR
    details: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str


@dataclass(frozen=True)
class Event:
    scenario_id: str
    event_type: str
    details: dict[str, float]


@dataclass(frozen=True)
class Config:
    displacement_body_atr: float = 0.20


@dataclass
class Logic:
    events: list[Event]
    config: Config = Config()


class EfficientLiquidityRaidCertificateTest(unittest.TestCase):
    def test_inefficient_high_turnover_raid_is_rejected(self):
        plan = Plan("S1")
        logic = Logic(
            [Event("S1", "LIQUIDITY_SWEEP", {
                "penetration_atr": 0.50,
                "relative_volume": 5.0,
            })],
        )
        result = certify_sweep_efficiency(plan, Decision(True, "FAR"), logic)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "INEFFICIENT_LIQUIDITY_RAID_FOR_FAR")
        self.assertAlmostEqual(plan.details["sweep_excursion_efficiency"], 0.10)

    def test_efficiency_uses_frozen_displacement_threshold(self):
        plan = Plan("S2")
        logic = Logic(
            [Event("S2", "LIQUIDITY_SWEEP", {
                "penetration_atr": 1.0,
                "relative_volume": 4.0,
            })],
        )
        result = certify_sweep_efficiency(plan, Decision(True, "FAR"), logic)
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "EFFICIENT_RAID_FAR")
        self.assertEqual(
            plan.details["sweep_excursion_efficiency_threshold"],
            logic.config.displacement_body_atr,
        )

    def test_missing_sweep_evidence_fails_closed(self):
        result = certify_sweep_efficiency(
            Plan("MISSING"),
            Decision(True, "FAR"),
            Logic([]),
        )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "SWEEP_EFFICIENCY_EVIDENCE_MISSING")


if __name__ == "__main__":
    unittest.main()
