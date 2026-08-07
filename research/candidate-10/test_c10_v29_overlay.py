from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import unittest

from c10_v29_overlay import certify_plan


class Scenario(StrEnum):
    FAR = "FAR"
    AAC = "AAC"


@dataclass(frozen=True)
class Plan:
    scenario: Scenario
    details: dict[str, str]


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str


class IndependentDrawCertificateTest(unittest.TestCase):
    def test_far_requires_independent_external_draw(self):
        plan = Plan(Scenario.FAR, {"draw_method": "CONTEXT_FLOW_MOMENTUM"})
        result = certify_plan(plan, Decision(True, "RESOLVED_FAR"))
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FAR_REQUIRES_INDEPENDENT_EXTERNAL_DRAW")

    def test_external_hazard_far_is_preserved(self):
        plan = Plan(Scenario.FAR, {"draw_method": "EXTERNAL_HAZARD_DOMINANCE"})
        result = certify_plan(plan, Decision(True, "RESOLVED_FAR"))
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "INDEPENDENT_DRAW_RESOLVED_FAR")

    def test_aac_is_not_changed(self):
        plan = Plan(Scenario.AAC, {"draw_method": "SOURCE_RANGE_ACCEPTANCE"})
        result = certify_plan(plan, Decision(True, "AAC"))
        self.assertEqual(result, Decision(True, "AAC"))


if __name__ == "__main__":
    unittest.main()
