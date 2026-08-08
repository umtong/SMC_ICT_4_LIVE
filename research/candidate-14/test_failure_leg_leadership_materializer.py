from __future__ import annotations

import unittest

from failure_leg_leadership_materializer import (
    NEW_BLOCK,
    OLD_BLOCK,
    materialize_failure_leg_leadership_source,
)


class FailureLegLeadershipMaterializerTests(unittest.TestCase):
    def test_failure_timestamp_owns_only_matching_plan(self) -> None:
        source = f"before\n{OLD_BLOCK}\nafter\n"
        result = materialize_failure_leg_leadership_source(source)
        self.assertIn(NEW_BLOCK, result)
        self.assertNotIn(OLD_BLOCK, result)
        self.assertIn('plan.details.get("acceptance_failure_ts_ns")', result)
        self.assertIn('"ACCEPTANCE_FAILURE_OBSERVATION"', result)
        self.assertIn('else "ORIGINAL_SOURCE_SWEEP"', result)
        self.assertEqual(
            result.count("Candidate 14 v10: the failure observation owns"),
            1,
        )

    def test_contract_drift_fails_closed(self) -> None:
        with self.assertRaises(RuntimeError):
            materialize_failure_leg_leadership_source("missing inherited block")
        with self.assertRaises(RuntimeError):
            materialize_failure_leg_leadership_source(OLD_BLOCK + OLD_BLOCK)


if __name__ == "__main__":
    unittest.main()
