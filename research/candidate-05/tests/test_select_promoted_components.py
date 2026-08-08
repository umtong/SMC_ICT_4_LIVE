from __future__ import annotations

import unittest

from select_promoted_components import choose


class PromotedComponentSelectionTests(unittest.TestCase):
    def _latest(self, *, v55: bool, v56: bool = False, v58: bool = False, environment=None, v55_growth=0.001):
        return {
            "all_expected_complete": True,
            "promotion": {
                "v55_spot_price_discovery": v55,
                "v56_early_flow_core": v56,
                "v58_forced_basis_reversion": v58,
            },
            "decisions": {
                "v55_loop": {
                    "selection": {"environment": environment or {}},
                    "continuous_metrics": {
                        "geometric_daily_growth": v55_growth,
                        "profit_factor": 1.5,
                        "total_return": 0.1,
                    },
                },
            },
        }

    def _v59(self, *, passed: bool, growth=0.002):
        return {
            "classification": (
                "V59_BOUNDARY_RETEST_PASSED_DEV_OOS_AND_CONTINUOUS"
                if passed else "V59_BOUNDARY_RETEST_NO_DEVELOPMENT_BREAKTHROUGH"
            ),
            "continuous_metrics": {
                "geometric_daily_growth": growth,
                "profit_factor": 1.4,
                "total_return": 0.2,
            },
        }

    def test_strict_v55_and_v59_can_coexist(self) -> None:
        result = choose(
            latest=self._latest(v55=True),
            v59=self._v59(passed=True),
            v62={},
        )
        self.assertEqual(result["components"], ["v55", "v59"])
        self.assertFalse(result["spot_contract_conflict_resolved"])

    def test_relaxed_v55_and_strict_v59_select_stronger_v59(self) -> None:
        result = choose(
            latest=self._latest(
                v55=True,
                environment={"V55_SPOT_LEAD_BPS_MIN": "0.0"},
                v55_growth=0.001,
            ),
            v59=self._v59(passed=True, growth=0.002),
            v62={},
        )
        self.assertEqual(result["components"], ["v59"])
        self.assertEqual(result["environment"], {})
        self.assertTrue(result["spot_contract_conflict_resolved"])

    def test_relaxed_v55_is_kept_when_it_outperforms(self) -> None:
        result = choose(
            latest=self._latest(
                v55=True,
                environment={"V55_CONTEXT_MIN_AGE_BARS": "5"},
                v55_growth=0.003,
            ),
            v59=self._v59(passed=True, growth=0.002),
            v62={},
        )
        self.assertEqual(result["components"], ["v55"])
        self.assertEqual(result["environment"], {"V55_CONTEXT_MIN_AGE_BARS": "5"})

    def test_non_spot_components_accumulate_independently(self) -> None:
        result = choose(
            latest=self._latest(v55=False, v56=True, v58=True),
            v59=self._v59(passed=False),
            v62={"classification": "V62_POST_FUNDING_RESET_PASSED_DEV_OOS_AND_CONTINUOUS"},
        )
        self.assertEqual(result["components"], ["v56", "v58", "v62"])


if __name__ == "__main__":
    unittest.main()
