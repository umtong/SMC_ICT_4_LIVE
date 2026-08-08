from __future__ import annotations

import unittest

from l1_pressure_router import PressureObservation
from l1_pressure_router import failure_pressure_transition
from l1_pressure_router import pressure_persistence
from l1_pressure_router import pressure_state


def observation(
    *,
    twap: float,
    close: float,
    premium: float,
    spread_twap: float = 1.0,
    spread_close: float = 0.8,
    update_rate: float = 10.0,
) -> PressureObservation:
    return PressureObservation(
        imbalance_twap=twap,
        imbalance_close=close,
        microprice_premium_close=premium,
        spread_bps_twap=spread_twap,
        spread_bps_close=spread_close,
        update_rate=update_rate,
    )


class L1PressureRouterTests(unittest.TestCase):
    def test_upward_attack_can_flip_to_downward_pressure(self) -> None:
        value = observation(twap=0.2, close=-0.1, premium=-0.01)
        self.assertTrue(failure_pressure_transition(1, value))
        self.assertEqual(pressure_state(1, value), "PRESSURE_FLIPPED")

    def test_downward_attack_is_mirror_symmetric(self) -> None:
        value = observation(twap=-0.2, close=0.1, premium=0.01)
        self.assertTrue(failure_pressure_transition(-1, value))
        self.assertEqual(pressure_state(-1, value), "PRESSURE_FLIPPED")

    def test_directional_pressure_must_persist_in_all_three_channels(self) -> None:
        self.assertTrue(
            pressure_persistence(
                1,
                observation(twap=0.2, close=0.1, premium=0.01),
            ),
        )
        self.assertFalse(
            pressure_persistence(
                1,
                observation(twap=0.2, close=0.1, premium=-0.01),
            ),
        )
        self.assertTrue(
            pressure_persistence(
                -1,
                observation(twap=-0.2, close=-0.1, premium=-0.01),
            ),
        )

    def test_wider_closing_spread_is_unresolved(self) -> None:
        value = observation(
            twap=0.2,
            close=-0.1,
            premium=-0.01,
            spread_twap=1.0,
            spread_close=1.1,
        )
        self.assertFalse(failure_pressure_transition(1, value))
        self.assertFalse(pressure_persistence(-1, value))
        self.assertEqual(pressure_state(1, value), "PRESSURE_UNRESOLVED")

    def test_no_sign_agreement_is_unresolved(self) -> None:
        value = observation(twap=0.2, close=0.1, premium=-0.01)
        self.assertEqual(pressure_state(1, value), "PRESSURE_UNRESOLVED")

    def test_zero_pressure_is_not_directional_evidence(self) -> None:
        value = observation(twap=0.0, close=0.1, premium=0.01)
        self.assertFalse(pressure_persistence(1, value))
        self.assertFalse(failure_pressure_transition(-1, value))

    def test_invalid_direction_or_observation_fails_closed(self) -> None:
        value = observation(twap=0.2, close=0.1, premium=0.01)
        with self.assertRaises(ValueError):
            pressure_persistence(0, value)
        with self.assertRaises(ValueError):
            pressure_persistence(
                1,
                observation(
                    twap=0.2,
                    close=0.1,
                    premium=0.01,
                    update_rate=0.0,
                ),
            )
        with self.assertRaises(ValueError):
            pressure_persistence(
                1,
                observation(
                    twap=0.2,
                    close=0.1,
                    premium=0.01,
                    spread_close=0.0,
                ),
            )


if __name__ == "__main__":
    unittest.main()
