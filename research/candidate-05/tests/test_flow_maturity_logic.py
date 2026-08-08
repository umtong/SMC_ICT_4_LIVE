from __future__ import annotations

import math
import unittest

from flow_maturity_logic import TWO_TO_ONE_IMBALANCE
from flow_maturity_logic import early_reversal_transfer


class FlowMaturityLogicTests(unittest.TestCase):
    def test_early_transfer_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            early_reversal_transfer(
                side=1,
                flow_15s=0.30,
                flow_60s=0.12,
                flow_3m=0.08,
            ),
        )
        self.assertTrue(
            early_reversal_transfer(
                side=-1,
                flow_15s=-0.30,
                flow_60s=-0.12,
                flow_3m=-0.08,
            ),
        )

    def test_mature_broad_flow_is_rejected(self) -> None:
        self.assertFalse(
            early_reversal_transfer(
                side=1,
                flow_15s=0.60,
                flow_60s=TWO_TO_ONE_IMBALANCE,
                flow_3m=TWO_TO_ONE_IMBALANCE,
            ),
        )

    def test_tail_must_lead_broad_flow(self) -> None:
        self.assertFalse(
            early_reversal_transfer(
                side=1,
                flow_15s=0.10,
                flow_60s=0.20,
                flow_3m=0.20,
            ),
        )
        self.assertFalse(
            early_reversal_transfer(
                side=-1,
                flow_15s=-0.10,
                flow_60s=-0.20,
                flow_3m=-0.20,
            ),
        )

    def test_non_finite_and_invalid_side_do_not_pass(self) -> None:
        self.assertFalse(
            early_reversal_transfer(
                side=1,
                flow_15s=math.nan,
                flow_60s=0.1,
                flow_3m=0.0,
            ),
        )
        with self.assertRaises(ValueError):
            early_reversal_transfer(
                side=0,
                flow_15s=0.2,
                flow_60s=0.1,
                flow_3m=0.0,
            )


if __name__ == "__main__":
    unittest.main()
