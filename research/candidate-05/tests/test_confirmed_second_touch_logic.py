from __future__ import annotations

import math
import unittest

from confirmed_second_touch_logic import confirmed_second_touch_geometry


class ConfirmedSecondTouchLogicTest(unittest.TestCase):
    def test_known_geometry_rejections_become_valid_only_at_choch_reference(self) -> None:
        cases = (
            (30264.9, 30271.1, 30237.4, 30348.4),
            (42644.2, 42648.1, 42598.7, 42778.0),
            (40964.8, 41006.7, 40835.3, 41132.0),
        )
        for reference, response, stop, target in cases:
            with self.subTest(reference=reference):
                geometry = confirmed_second_touch_geometry(
                    side=1,
                    choch_reference=reference,
                    confirmed_response_price=response,
                    stop=stop,
                    target=target,
                    cost_rate=0.00075,
                    adverse_slippage_rate=0.00025,
                    minimum_net_r=0.40,
                )
                self.assertIsNotNone(geometry)
                assert geometry is not None
                self.assertGreaterEqual(geometry.target_net_r, 0.40)

    def test_confirmation_must_make_reference_passive(self) -> None:
        self.assertIsNone(
            confirmed_second_touch_geometry(
                side=1,
                choch_reference=100.0,
                confirmed_response_price=99.9,
                stop=95.0,
                target=110.0,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
                minimum_net_r=0.40,
            ),
        )
        self.assertIsNone(
            confirmed_second_touch_geometry(
                side=-1,
                choch_reference=100.0,
                confirmed_response_price=100.1,
                stop=105.0,
                target=90.0,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
                minimum_net_r=0.40,
            ),
        )

    def test_long_and_short_are_mirror_symmetric(self) -> None:
        long_geometry = confirmed_second_touch_geometry(
            side=1,
            choch_reference=100.0,
            confirmed_response_price=102.0,
            stop=95.0,
            target=110.0,
            cost_rate=0.001,
            adverse_slippage_rate=0.0005,
            minimum_net_r=0.40,
        )
        short_geometry = confirmed_second_touch_geometry(
            side=-1,
            choch_reference=100.0,
            confirmed_response_price=98.0,
            stop=105.0,
            target=90.0,
            cost_rate=0.001,
            adverse_slippage_rate=0.0005,
            minimum_net_r=0.40,
        )
        self.assertIsNotNone(long_geometry)
        self.assertIsNotNone(short_geometry)
        assert long_geometry is not None and short_geometry is not None
        self.assertAlmostEqual(
            long_geometry.planned_loss_per_unit,
            short_geometry.planned_loss_per_unit,
        )
        self.assertAlmostEqual(long_geometry.target_net_r, short_geometry.target_net_r)

    def test_reference_must_still_preserve_existing_post_cost_r(self) -> None:
        self.assertIsNone(
            confirmed_second_touch_geometry(
                side=1,
                choch_reference=108.0,
                confirmed_response_price=109.0,
                stop=95.0,
                target=110.0,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
                minimum_net_r=0.40,
            ),
        )

    def test_nonfinite_observation_is_invalid(self) -> None:
        self.assertIsNone(
            confirmed_second_touch_geometry(
                side=1,
                choch_reference=100.0,
                confirmed_response_price=math.nan,
                stop=95.0,
                target=110.0,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
                minimum_net_r=0.40,
            ),
        )

    def test_side_must_be_directional(self) -> None:
        with self.assertRaises(ValueError):
            confirmed_second_touch_geometry(
                side=0,
                choch_reference=100.0,
                confirmed_response_price=102.0,
                stop=95.0,
                target=110.0,
                cost_rate=0.001,
                adverse_slippage_rate=0.0005,
                minimum_net_r=0.40,
            )


if __name__ == "__main__":
    unittest.main()
