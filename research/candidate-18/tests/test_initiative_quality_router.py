from __future__ import annotations

import math
import unittest

from initiative_quality_router import InitiativeRoute
from initiative_quality_router import classify_initiative_quality


class InitiativeQualityRouterTests(unittest.TestCase):
    def test_full_window_is_sustained(self) -> None:
        decision = classify_initiative_quality(
            observations=3,
            max_wait_bars=3,
            notional_burst=0.2,
        )
        self.assertEqual(decision.route, InitiativeRoute.SUSTAINED)

    def test_first_bar_above_baseline_is_shock(self) -> None:
        decision = classify_initiative_quality(
            observations=1,
            max_wait_bars=3,
            notional_burst=1.01,
        )
        self.assertEqual(decision.route, InitiativeRoute.SHOCK)

    def test_middle_window_and_ordinary_first_bar_are_unresolved(self) -> None:
        for observations, burst in ((1, 1.0), (2, 10.0), (1, math.nan)):
            with self.subTest(observations=observations, burst=burst):
                decision = classify_initiative_quality(
                    observations=observations,
                    max_wait_bars=3,
                    notional_burst=burst,
                )
                self.assertEqual(decision.route, InitiativeRoute.UNRESOLVED)

    def test_invalid_contract_inputs_fail_closed(self) -> None:
        for kwargs in (
            {"observations": 0, "max_wait_bars": 3, "notional_burst": 1.0},
            {"observations": 4, "max_wait_bars": 3, "notional_burst": 1.0},
            {"observations": 1, "max_wait_bars": 0, "notional_burst": 1.0},
            {
                "observations": 1,
                "max_wait_bars": 3,
                "notional_burst": 1.0,
                "shock_burst_min": 0.9,
            },
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    classify_initiative_quality(**kwargs)


if __name__ == "__main__":
    unittest.main()
