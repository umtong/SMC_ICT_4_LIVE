from __future__ import annotations

import unittest

from validated_btc_research import candidate_qualified
from validated_btc_research import implementation_error
from validated_btc_research import metric_score


class ValidatedBtcResearchTest(unittest.TestCase):
    def test_implementation_error_is_distinct_from_logic_failure(self) -> None:
        self.assertTrue(implementation_error("IMPLEMENTATION_ERROR_CANDIDATE"))
        self.assertTrue(implementation_error("IMPLEMENTATION_OR_EVIDENCE_ERROR_30D"))
        self.assertFalse(implementation_error("LOGIC_FAILURE_NONPOSITIVE_INCREMENTAL_EXPECTANCY"))
        self.assertFalse(implementation_error("BTC_91D_ALPHA_GATE_PASSED"))

    def test_candidate_is_qualified_only_with_exact_control_gate_and_integrity(self) -> None:
        run = {
            "available": True,
            "integrity_checks": {"a": True, "b": True},
            "geometric_daily_growth": 0.012,
            "max_drawdown": 0.20,
            "trades": 60,
            "wins": 30,
        }
        result = {
            "classification": "BTC_91D_ALPHA_GATE_PASSED",
            "strategy": "module:Strategy",
            "branch": "BRANCH",
            "long_gate": {"passed": True},
            "runs": {"continuous-91d": run},
        }
        qualified = candidate_qualified(result)
        self.assertIsNotNone(qualified)
        assert qualified is not None
        self.assertEqual(qualified["strategy"], "module:Strategy")

        result["classification"] = "LOGIC_FAILURE_DID_NOT_IMPROVE_CONTROL_91D"
        self.assertIsNone(candidate_qualified(result))

    def test_selection_score_prefers_growth_then_lower_drawdown(self) -> None:
        high_growth = {
            "geometric_daily_growth": 0.013,
            "max_drawdown": 0.30,
            "trades": 50,
            "wins": 20,
        }
        low_growth = {
            "geometric_daily_growth": 0.012,
            "max_drawdown": 0.10,
            "trades": 80,
            "wins": 40,
        }
        self.assertGreater(metric_score(high_growth), metric_score(low_growth))

        same_growth_low_drawdown = {
            "geometric_daily_growth": 0.013,
            "max_drawdown": 0.20,
            "trades": 40,
            "wins": 15,
        }
        self.assertGreater(
            metric_score(same_growth_low_drawdown),
            metric_score(high_growth),
        )


if __name__ == "__main__":
    unittest.main()
