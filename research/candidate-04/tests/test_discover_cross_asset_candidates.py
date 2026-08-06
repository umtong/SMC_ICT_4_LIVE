from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import discover_cross_asset_candidates as candidate


class CandidateDiscoveryTests(unittest.TestCase):
    def test_v36_multiple_successful_routes_are_expanded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = {
                "candidate": "candidate-04-v36-micro-auction-balance-transition",
                "source_commit": "a" * 40,
                "final_validation_completed": True,
                "project_target_reached": True,
                "successful_routes": ["continuation", "reversal"],
            }
            (root / "final_decision.json").write_text(json.dumps(evidence))
            rows = candidate.discover(
                root,
                "b" * 40,
                "test-workflow",
                123,
            )
            self.assertEqual(
                [(row["family"], row["route"]) for row in rows],
                [("v36", "continuation"), ("v36", "reversal")],
            )
            self.assertTrue(
                all(row["source_commit"] == "a" * 40 for row in rows)
            )

    def test_failed_long_candidate_is_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = {
                "candidate": "candidate-04-v33-exact-causal-target",
                "source_commit": "c" * 40,
                "final_validation_completed": True,
                "project_target_reached": False,
            }
            (root / "final_decision.json").write_text(json.dumps(evidence))
            self.assertEqual(
                candidate.discover(root, "d" * 40, "test", 1),
                [],
            )

    def test_explicit_v34_compiler_and_route_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evidence = {
                "candidate": "candidate-04-v34-reversal-only",
                "family": "v34",
                "route": "reversal",
                "compiler": "post_event_inventory_resolution_compiler_v2.py",
                "source_commit": "e" * 40,
                "final_validation_completed": True,
                "project_target_reached": True,
            }
            (root / "final_decision.json").write_text(json.dumps(evidence))
            rows = candidate.discover(root, "f" * 40, "test", 2)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["route"], "reversal")
            self.assertEqual(
                rows[0]["compiler"],
                "post_event_inventory_resolution_compiler_v2.py",
            )


if __name__ == "__main__":
    unittest.main()
