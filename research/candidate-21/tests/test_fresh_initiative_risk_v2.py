import math
import unittest

from candidate21_fresh_initiative_strategy_v2 import modeled_adverse_fill_raw


class ModeledAdverseFillTests(unittest.TestCase):
    def test_long_and_short_are_symmetric(self) -> None:
        self.assertAlmostEqual(modeled_adverse_fill_raw(100.0, 1, 0.001), 100.1)
        self.assertAlmostEqual(modeled_adverse_fill_raw(100.0, -1, 0.001), 99.9)

    def test_zero_slippage_uses_observed_price(self) -> None:
        self.assertEqual(modeled_adverse_fill_raw(123.45, 1, 0.0), 123.45)
        self.assertEqual(modeled_adverse_fill_raw(123.45, -1, 0.0), 123.45)

    def test_invalid_inputs_fail_closed(self) -> None:
        for value in (
            modeled_adverse_fill_raw(math.nan, 1, 0.001),
            modeled_adverse_fill_raw(0.0, 1, 0.001),
            modeled_adverse_fill_raw(100.0, 0, 0.001),
            modeled_adverse_fill_raw(100.0, 1, -0.001),
        ):
            self.assertTrue(math.isnan(value))

    def test_v2_does_not_change_signal_router(self) -> None:
        from candidate21_fresh_initiative_strategy_v2 import (
            RiskEfficientFreshInitiativeMixin,
        )
        from candidate21_fresh_initiative_strategy import (
            FreshInitiativeAcceptanceMixin,
        )

        self.assertTrue(issubclass(RiskEfficientFreshInitiativeMixin, FreshInitiativeAcceptanceMixin))
        self.assertNotIn("_detect_sweep", RiskEfficientFreshInitiativeMixin.__dict__)
        self.assertNotIn("_advance_fresh", RiskEfficientFreshInitiativeMixin.__dict__)


if __name__ == "__main__":
    unittest.main()
