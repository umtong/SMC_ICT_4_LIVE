from __future__ import annotations

import unittest

from select_promoted_components_v3 import choose


class PromotedComponentSelectionV3Tests(unittest.TestCase):
    def _latest(self):
        return {
            "all_expected_complete": True,
            "promotion": {
                "v55_spot_price_discovery": False,
                "v56_early_flow_core": True,
                "v58_forced_basis_reversion": False,
            },
            "decisions": {"v55_loop": {}},
        }

    def test_v70_is_added_only_after_full_pass(self) -> None:
        result = choose(
            latest=self._latest(),
            v59={},
            v62={},
            v68={},
            v70={
                "classification": "V70_PARTICIPATION_EXPANSION_PASSED_DEV_OOS_AND_CONTINUOUS",
            },
        )
        self.assertEqual(result["components"], ["v56", "v70"])
        self.assertTrue(result["v70_promoted"])
        self.assertTrue(result["run_later_participation_authoritative"])

    def test_failed_v70_does_not_change_existing_selection(self) -> None:
        result = choose(
            latest=self._latest(),
            v59={},
            v62={"classification": "V62_POST_FUNDING_RESET_PASSED_DEV_OOS_AND_CONTINUOUS"},
            v68={"classification": "V68_LIQUIDATION_EXHAUSTION_PASSED_DEV_OOS_AND_CONTINUOUS"},
            v70={"classification": "V70_PARTICIPATION_EXPANSION_FAILED_UNTOUCHED_EVALUATION"},
        )
        self.assertEqual(result["components"], ["v56", "v62", "v68"])
        self.assertFalse(result["v70_promoted"])


if __name__ == "__main__":
    unittest.main()
