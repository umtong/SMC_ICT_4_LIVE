from __future__ import annotations

import unittest

from surprise_impact_core import assess_surprise_impact


class SurpriseImpactCoreTests(unittest.TestCase):
    def base(self, **overrides):
        kwargs = {
            "prior_flow_intensity": [-0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, -0.3],
            "prior_signed_displacement_atr": [-0.3, -0.2, -0.1, 0.0, 0.1, 0.25, 0.35, -0.25],
            "current_flow_intensity": 0.8,
            "current_directional_displacement_atr": 1.2,
            "direction": "LONG",
            "use_surprise": True,
            "use_impact_efficiency": True,
            "lookback": 8,
            "minimum_history": 8,
            "flow_quantile": 0.75,
            "minimum_efficiency_history": 4,
        }
        kwargs.update(overrides)
        return assess_surprise_impact(**kwargs)

    def test_unexpected_flow_with_effective_displacement_is_accepted(self) -> None:
        result = self.base()
        self.assertTrue(result.ready)
        self.assertTrue(result.passed)
        self.assertEqual(result.classification, "FLOW_SURPRISE_CONVERTED_TO_EFFECTIVE_DISPLACEMENT")

    def test_positive_raw_flow_below_positive_expectation_is_not_surprise(self) -> None:
        result = self.base(
            prior_flow_intensity=[0.4, 0.5, 0.6, 0.5, 0.4, 0.6, 0.5, 0.55],
            current_flow_intensity=0.2,
        )
        self.assertFalse(result.passed)
        self.assertLess(result.directional_surprise or 0.0, 0.0)

    def test_large_surprise_with_weak_price_response_is_absorption(self) -> None:
        result = self.base(current_directional_displacement_atr=0.02)
        self.assertFalse(result.passed)
        self.assertEqual(result.classification, "FLOW_SURPRISE_ABSORBED_WITH_WEAK_PRICE_RESPONSE")

    def test_impact_ablation_retains_surprise_only_contract(self) -> None:
        result = self.base(
            current_directional_displacement_atr=0.0,
            use_impact_efficiency=False,
        )
        self.assertTrue(result.passed)

    def test_current_event_is_not_inserted_into_its_own_threshold(self) -> None:
        result = self.base(current_flow_intensity=100.0)
        self.assertLess(result.surprise_threshold or 0.0, 1.0)
        self.assertGreater(result.directional_surprise or 0.0, 99.0)


if __name__ == "__main__":
    unittest.main()
