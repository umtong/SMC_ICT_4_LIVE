from __future__ import annotations

import unittest

from select_promoted_components_v2 import choose


class PromotedComponentSelectionV2Tests(unittest.TestCase):
    def _latest(self):
        return {
            "all_expected_complete": True,
            "promotion": {
                "v55_spot_price_discovery": False,
                "v56_early_flow_core": True,
                "v58_forced_basis_reversion": True,
            },
            "decisions": {"v55_loop": {}},
        }

    def test_v68_is_added_only_after_full_pass(self) -> None:
        result = choose(
            latest=self._latest(),
            v59={},
            v62={},
            v68={
                "classification": "V68_LIQUIDATION_EXHAUSTION_PASSED_DEV_OOS_AND_CONTINUOUS",
            },
        )
        self.assertEqual(result["components"], ["v56", "v58", "v68"])
        self.assertTrue(result["v68_promoted"])
        self.assertTrue(result["run_later_authoritative"])

    def test_failed_v68_does_not_change_core_selection(self) -> None:
        result = choose(
            latest=self._latest(),
            v59={},
            v62={},
            v68={
                "classification": "V68_LIQUIDATION_EXHAUSTION_FAILED_UNTOUCHED_EVALUATION",
            },
        )
        self.assertEqual(result["components"], ["v56", "v58"])
        self.assertFalse(result["v68_promoted"])
        self.assertFalse(result["run_later_authoritative"])


if __name__ == "__main__":
    unittest.main()
