from __future__ import annotations

import unittest

from contracts_v5 import Pivot
from domain import Side
from scenario_target_ablation_v5 import (
    NEAREST_ANY_PIVOT_RULE,
    NearestAnyPivotStructureBook,
    NearestAnyTargetResearchScenarioBundleV5,
)

NS = 60_000_000_000


def pivot(pivot_id: str, side: str, price: float, span: int, observed_index: int) -> Pivot:
    return Pivot(
        pivot_id=pivot_id,
        side=side,
        price=price,
        index=max(0, observed_index - span),
        event_time_ns=max(0, observed_index - span) * NS,
        observed_index=observed_index,
        observed_time_ns=observed_index * NS,
        span=span,
        strength_ratio=2.0,
    )


class TargetPolicyAblationTests(unittest.TestCase):
    def test_nearest_any_uses_closer_confirmed_small_pivot(self) -> None:
        book = NearestAnyPivotStructureBook("TEST", 60, 0.1, pivot_spans=(2, 6))
        close = pivot("CLOSE_HIGH", "HIGH", 105.0, 2, 10)
        far = pivot("FAR_HIGH", "HIGH", 112.0, 6, 9)
        book.pivots.extend([far, close])
        result = book.target_for(
            Side.LONG,
            interaction_time_ns=20 * NS,
            source_span=6,
            current_high=101.0,
            current_low=99.0,
        )
        self.assertIsNotNone(result)
        assert result is not None
        zone, price = result
        self.assertEqual(price, 105.0)
        self.assertEqual(zone.source_structure_id, close.pivot_id)
        self.assertEqual(book.diagnostics.get("nearest_any_pivot_target_selected"), 1)

    def test_spent_or_future_observed_pivots_are_never_targets(self) -> None:
        book = NearestAnyPivotStructureBook("TEST", 60, 0.1)
        spent = pivot("SPENT", "LOW", 95.0, 2, 5)
        spent.consumed = True
        spent.consumed_time_ns = 10 * NS
        future = pivot("FUTURE", "LOW", 94.0, 2, 25)
        eligible = pivot("ELIGIBLE", "LOW", 90.0, 6, 8)
        book.pivots.extend([spent, future, eligible])
        result = book.target_for(
            Side.SHORT,
            interaction_time_ns=20 * NS,
            source_span=6,
            current_high=101.0,
            current_low=99.0,
        )
        self.assertIsNotNone(result)
        assert result is not None
        zone, price = result
        self.assertEqual(price, 90.0)
        self.assertEqual(zone.source_structure_id, eligible.pivot_id)

    def test_bundle_exposes_target_policy_without_changing_risk_contract(self) -> None:
        bundle = NearestAnyTargetResearchScenarioBundleV5("TEST", 0.1)
        policy = bundle.diagnostics["target_policy"]
        self.assertEqual(
            policy["name"],
            "NEAREST_ANY_CONFIRMED_PREEXISTING_OPPOSITE_PIVOT",
        )
        self.assertEqual(policy["rule_provenance"], NEAREST_ANY_PIVOT_RULE)
        self.assertEqual(bundle.macro.minimum_gross_rr, 1.0)
        self.assertEqual(bundle.micro.minimum_gross_rr, 1.0)


if __name__ == "__main__":
    unittest.main()
