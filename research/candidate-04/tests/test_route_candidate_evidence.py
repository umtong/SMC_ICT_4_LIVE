from __future__ import annotations

import unittest

import route_candidate_evidence as candidate


class EvidenceRoutingTests(unittest.TestCase):
    def test_implementation_failure_precedes_economic_discard(self) -> None:
        result = candidate.classify(
            {
                "failure_classification": "implementation_or_workflow",
                "controlled_ablations": {},
            }
        )
        self.assertEqual(
            result["action"],
            "same_week_implementation_recovery",
        )
        self.assertTrue(result["implementation_failure"])

    def test_single_reversal_ablation_survivor_is_cross_developed(self) -> None:
        result = candidate.classify(
            {
                "controlled_ablations": {
                    "continuation_only": {"candidate_pass": False},
                    "reversal_only": {"candidate_pass": True},
                },
                "source_commit": "a" * 40,
            }
        )
        self.assertEqual(result["action"], "cross_develop_single_survivor")
        self.assertEqual(result["survivors"], ["reversal"])
        self.assertEqual(result["source_commit"], "a" * 40)

    def test_both_survivors_are_not_arbitrarily_reduced_to_one(self) -> None:
        result = candidate.classify(
            {
                "ablations": {
                    "continuation_only": {"candidate_pass": True},
                    "reversal_only": {"candidate_pass": True},
                }
            }
        )
        self.assertEqual(result["action"], "cross_develop_all_survivors")
        self.assertEqual(
            result["survivors"],
            ["continuation", "reversal"],
        )

    def test_failed_final_long_evaluation_ends_current_economic_path(self) -> None:
        result = candidate.classify(
            {
                "final_validation_completed": True,
                "project_target_reached": False,
            }
        )
        self.assertEqual(
            result["action"],
            "economic_path_exhausted_after_long_evaluation",
        )

    def test_project_target_reached_routes_full_candidate(self) -> None:
        result = candidate.classify(
            {
                "final_validation_completed": True,
                "project_target_reached": True,
            }
        )
        self.assertEqual(result["action"], "project_target_reached")
        self.assertEqual(result["survivors"], ["full"])


if __name__ == "__main__":
    unittest.main()
