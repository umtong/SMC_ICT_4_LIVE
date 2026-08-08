from __future__ import annotations

import unittest

from crowded_initiative_router import CrowdedDecision
from crowded_initiative_router import CrowdedShockObservation
from crowded_initiative_router import CrowdedShockState
from crowded_initiative_router import LaterFailureObservation
from crowded_initiative_router import advance_crowded_shock
from crowded_initiative_router import qualify_crowded_shock


class CrowdedInitiativeRouterTests(unittest.TestCase):
    def shock(self, **changes) -> CrowdedShockObservation:
        values = {
            "bar_index": 10,
            "ts_event": 1_700_000_000_000_000_000,
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 102.0,
            "atr": 2.0,
            "ret_60s_bps": 25.0,
            "flow_60s": 0.20,
            "notional_burst": 1.20,
            "oi_change_5m": 0.002,
            "metrics_age_seconds": 60.0,
            "l1_imbalance_close": -0.20,
        }
        values.update(changes)
        return CrowdedShockObservation(**values)

    def qualify(self, observation: CrowdedShockObservation):
        return qualify_crowded_shock(
            observation,
            economic_floor_bps=20.0,
            minimum_notional_burst=1.0,
            maximum_metrics_age_seconds=300.0,
        )

    def state(self, direction: int = 1) -> CrowdedShockState:
        if direction > 0:
            values = dict(
                shock_open=100.0,
                shock_high=103.0,
                shock_low=99.0,
                shock_close=102.0,
            )
        else:
            values = dict(
                shock_open=100.0,
                shock_high=101.0,
                shock_low=97.0,
                shock_close=98.0,
            )
        return CrowdedShockState(
            scenario_id="test",
            shock_direction=direction,
            fade_side=-direction,
            shock_index=10,
            last_index=10,
            expires_index=13,
            atr=2.0,
            **values,
        )

    def test_cost_exceeding_new_oi_with_opposing_l1_qualifies(self) -> None:
        decision = self.qualify(self.shock())
        self.assertTrue(decision.qualified)
        self.assertEqual(decision.shock_direction, 1)
        self.assertEqual(decision.fade_side, -1)

    def test_liquidation_like_oi_contraction_does_not_qualify(self) -> None:
        decision = self.qualify(self.shock(oi_change_5m=-0.002))
        self.assertFalse(decision.qualified)
        self.assertEqual(
            decision.reason,
            "INITIATIVE_DID_NOT_ADD_OPEN_INTEREST",
        )

    def test_l1_pressure_aligned_with_shock_does_not_qualify(self) -> None:
        decision = self.qualify(self.shock(l1_imbalance_close=0.20))
        self.assertFalse(decision.qualified)
        self.assertEqual(
            decision.reason,
            "CLOSING_L1_PRESSURE_DID_NOT_RESIST_INITIATIVE",
        )

    def test_stale_positioning_observation_does_not_qualify(self) -> None:
        decision = self.qualify(self.shock(metrics_age_seconds=301.0))
        self.assertFalse(decision.qualified)
        self.assertEqual(decision.reason, "OPEN_INTEREST_OBSERVATION_STALE")

    def test_shock_bar_cannot_confirm_itself(self) -> None:
        with self.assertRaises(ValueError):
            advance_crowded_shock(
                self.state(),
                LaterFailureObservation(
                    bar_index=10,
                    ts_event=1,
                    open=102.0,
                    high=102.2,
                    low=101.0,
                    close=101.5,
                    flow_60s=-0.10,
                    l1_imbalance_close=-0.20,
                ),
                maximum_close_extension_atr=0.05,
            )

    def test_strictly_later_price_flow_and_l1_failure_confirms(self) -> None:
        state = advance_crowded_shock(
            self.state(),
            LaterFailureObservation(
                bar_index=11,
                ts_event=2,
                open=102.0,
                high=102.2,
                low=101.0,
                close=101.5,
                flow_60s=-0.10,
                l1_imbalance_close=-0.20,
            ),
            maximum_close_extension_atr=0.05,
        )
        self.assertEqual(state.decision, CrowdedDecision.CONFIRMED)
        self.assertEqual(state.observations, 1)

    def test_acceptance_beyond_shock_extreme_invalidates(self) -> None:
        state = advance_crowded_shock(
            self.state(),
            LaterFailureObservation(
                bar_index=11,
                ts_event=2,
                open=102.0,
                high=103.3,
                low=101.9,
                close=103.2,
                flow_60s=0.20,
                l1_imbalance_close=0.20,
            ),
            maximum_close_extension_atr=0.05,
        )
        self.assertEqual(state.decision, CrowdedDecision.INVALIDATED)

    def test_short_shock_is_mirror_symmetric(self) -> None:
        qualification = self.qualify(
            self.shock(
                high=101.0,
                low=97.0,
                close=98.0,
                ret_60s_bps=-25.0,
                flow_60s=-0.20,
                l1_imbalance_close=0.20,
            ),
        )
        self.assertTrue(qualification.qualified)
        self.assertEqual(qualification.fade_side, 1)
        state = advance_crowded_shock(
            self.state(-1),
            LaterFailureObservation(
                bar_index=11,
                ts_event=2,
                open=98.0,
                high=99.0,
                low=97.8,
                close=98.5,
                flow_60s=0.10,
                l1_imbalance_close=0.20,
            ),
            maximum_close_extension_atr=0.05,
        )
        self.assertEqual(state.decision, CrowdedDecision.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
