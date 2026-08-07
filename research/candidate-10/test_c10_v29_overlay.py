from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import unittest

import pandas as pd

from c10_v29_overlay import certify_plan, repair_kline_flow_frame


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

    def test_impossible_vendor_volume_is_repaired_from_quote_volume(self):
        frame = pd.DataFrame(
            [
                {
                    "open_time": 1701347700000,
                    "open": 0.6038,
                    "high": 0.6038,
                    "low": 0.6034,
                    "close": 0.6036,
                    "volume": 91695.7,
                    "quote_volume": 200298.79368,
                    "taker_buy_volume": 132462.5,
                    "taker_buy_quote_volume": 79957.28937,
                },
            ],
        )
        repaired, records = repair_kline_flow_frame(frame, "XRPUSDT-1m-2023-11-30.zip")
        self.assertEqual(len(records), 1)
        self.assertGreater(float(repaired.loc[0, "volume"]), 132462.5)
        self.assertAlmostEqual(
            float(repaired.loc[0, "volume"]),
            200298.79368 / ((0.6038 + 0.6038 + 0.6034 + 0.6036) / 4.0),
        )

    def test_valid_vendor_volume_is_unchanged(self):
        frame = pd.DataFrame(
            [
                {
                    "open_time": 1,
                    "open": 10.0,
                    "high": 10.1,
                    "low": 9.9,
                    "close": 10.0,
                    "volume": 100.0,
                    "quote_volume": 1000.0,
                    "taker_buy_volume": 40.0,
                    "taker_buy_quote_volume": 400.0,
                },
            ],
        )
        repaired, records = repair_kline_flow_frame(frame, "valid.zip")
        self.assertFalse(records)
        self.assertEqual(float(repaired.loc[0, "volume"]), 100.0)


if __name__ == "__main__":
    unittest.main()
