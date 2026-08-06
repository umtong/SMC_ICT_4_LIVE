from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

import validated_boundary_negotiation_compiler as candidate


class ValidatedBoundaryNegotiationCompilerTests(unittest.TestCase):
    def test_failed_stress_parent_side_is_rejected_before_intent(self) -> None:
        parent = SimpleNamespace(
            scenario=candidate.v30.STRESS_PARENT,
            side=1,
        )
        passed, details = candidate.validated_settled_cause(
            pd.DataFrame(),
            parent,
            10,
            1,
        )
        self.assertFalse(passed)
        self.assertTrue(details["route_removed_by_controlled_ablation"])
        self.assertEqual(
            details["removed_scenario"],
            candidate.FAILED_STRESS_ROUTE,
        )

    def test_opposite_stress_side_delegates_to_original_cause(self) -> None:
        parent = SimpleNamespace(
            scenario=candidate.v30.STRESS_PARENT,
            side=1,
        )
        expected = (True, {"route": "validated_deleveraging"})
        with patch.object(
            candidate,
            "_ORIGINAL_SETTLED_CAUSE",
            return_value=expected,
        ) as original:
            observed = candidate.validated_settled_cause(
                pd.DataFrame(),
                parent,
                11,
                -1,
            )
        self.assertEqual(observed, expected)
        original.assert_called_once()

    def test_collect_uses_cause_stage_override_and_restores_module(self) -> None:
        original = candidate.v31.settled_cause
        observed = {}

        def fake_collect(*args, **kwargs):
            del args, kwargs
            observed["during"] = candidate.v31.settled_cause
            return [], {"router_contract": {"base": "v31"}}

        with patch.object(candidate.v31, "collect_signals", side_effect=fake_collect):
            intents, summary = candidate.collect_signals(
                pd.DataFrame(),
                pd.Timestamp("2025-01-01", tz="UTC"),
                pd.Timestamp("2025-01-02", tz="UTC"),
                object(),
                object(),
                object(),
            )

        self.assertEqual(intents, [])
        self.assertIs(observed["during"], candidate.validated_settled_cause)
        self.assertIs(candidate.v31.settled_cause, original)
        self.assertEqual(
            summary["changes_from_v31"]["removal_stage"],
            "settled cause classification before intent creation",
        )
        self.assertEqual(
            summary["router_contract"]["validated_v31_core"]
            ["excluded_at_cause_stage"],
            candidate.FAILED_STRESS_ROUTE,
        )

    def test_failed_scenario_cannot_escape_compiler(self) -> None:
        failed = SimpleNamespace(scenario=candidate.FAILED_STRESS_ROUTE)
        with patch.object(
            candidate.v31,
            "collect_signals",
            return_value=([failed], {"router_contract": {}}),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "escaped cause-stage exclusion",
            ):
                candidate.collect_signals(
                    pd.DataFrame(),
                    pd.Timestamp("2025-01-01", tz="UTC"),
                    pd.Timestamp("2025-01-02", tz="UTC"),
                    object(),
                    object(),
                    object(),
                )


if __name__ == "__main__":
    unittest.main()
