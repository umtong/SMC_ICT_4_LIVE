from __future__ import annotations

import unittest

from informed_initiative_router import ContinuationDecision
from informed_initiative_router import InformedContinuationState
from informed_initiative_router import InformedInitiativeObservation
from informed_initiative_router import LaterContinuationObservation
from informed_initiative_router import advance_informed_continuation
from informed_initiative_router import qualify_informed_initiative


class InformedInitiativeRouterTests(unittest.TestCase):
    def initiative(self, **changes) -> InformedInitiativeObservation:
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
            "l1_imbalance_close": 0.20,
        }
        values.update(changes)
        return InformedInitiativeObservation(**values)

    def qualify(self, observation: InformedInitiativeObservation):
        return qualify_informed_initiative(
            observation,
            economic_floor_bps=20.0,
            minimum_notional_burst=1.0,
            maximum_metrics_age_seconds=300.0,
        )

    def state(self, direction: int = 1) -> InformedContinuationState:
        if direction > 0:
            prices = dict(
                shock_open=100.0,
                shock_high=103.0,
                shock_low=99.0,
                shock_close=102.0,
                midpoint=101.0,
            )
        else:
            prices = dict(
                shock_open=100.0,
                shock_high=101.0,
                shock_low=97.0,
                shock_close=98.0,
                midpoint=99.0,
            )
        return InformedContinuationState(
            scenario_id="test",
            direction=direction,
            shock_index=10,
            last_index=10,
            expires_index=15,
            atr=2.0,
            **prices,
        )

    def later(self, **changes) -> LaterContinuationObservation:
        values = {
            "bar_index": 11,
            "ts_event": 2,
            "open": 102.0,
            "high": 102.2,
            "low": 101.2,
            "close": 101.5,
            "flow_60s": -0.05,
            "l1_imbalance_close": 0.05,
        }
        values.update(changes)
        return LaterContinuationObservation(**values)

    def test_cost_exceeding_new_oi_with_aligned_l1_qualifies(self) -> None:
        decision = self.qualify(self.initiative())
        self.assertTrue(decision.qualified)
        self.assertEqual(decision.direction, 1)

    def test_oi_contraction_is_not_informed_initiative(self) -> None:
        decision = self.qualify(self.initiative(oi_change_5m=-0.001))
        self.assertFalse(decision.qualified)
        self.assertEqual(decision.reason, "INITIATIVE_DID_NOT_ADD_OPEN_INTEREST")

    def test_opposing_l1_is_not_continuation_state(self) -> None:
        decision = self.qualify(self.initiative(l1_imbalance_close=-0.20))
        self.assertFalse(decision.qualified)
        self.assertEqual(
            decision.reason,
            "CLOSING_L1_PRESSURE_DID_NOT_SUPPORT_INITIATIVE",
        )

    def test_initiative_bar_cannot_confirm_itself(self) -> None:
        with self.assertRaises(ValueError):
            advance_informed_continuation(
                self.state(),
                self.later(bar_index=10),
            )

    def test_midpoint_loss_invalidates_before_entry(self) -> None:
        state = advance_informed_continuation(
            self.state(),
            self.later(close=100.9, low=100.7),
        )
        self.assertEqual(state.decision, ContinuationDecision.INVALIDATED)

    def test_counter_bar_holding_midpoint_arms_pullback(self) -> None:
        state = advance_informed_continuation(self.state(), self.later())
        self.assertEqual(state.decision, ContinuationDecision.PULLBACK_ARMED)
        self.assertEqual(state.pullback_index, 11)
        self.assertEqual(state.pullback_extreme, 101.2)
        self.assertEqual(state.pullback_boundary, 102.2)

    def test_strictly_later_breakout_flow_and_l1_confirm(self) -> None:
        armed = advance_informed_continuation(self.state(), self.later())
        confirmed = advance_informed_continuation(
            armed,
            self.later(
                bar_index=12,
                ts_event=3,
                open=101.6,
                high=102.6,
                low=101.5,
                close=102.4,
                flow_60s=0.10,
                l1_imbalance_close=0.20,
            ),
        )
        self.assertEqual(confirmed.decision, ContinuationDecision.CONFIRMED)
        self.assertEqual(confirmed.pullback_extreme, 101.2)

    def test_short_side_is_mirror_symmetric(self) -> None:
        decision = self.qualify(
            self.initiative(
                high=101.0,
                low=97.0,
                close=98.0,
                ret_60s_bps=-25.0,
                flow_60s=-0.20,
                l1_imbalance_close=-0.20,
            ),
        )
        self.assertTrue(decision.qualified)
        self.assertEqual(decision.direction, -1)
        armed = advance_informed_continuation(
            self.state(-1),
            self.later(
                open=98.0,
                high=98.8,
                low=97.8,
                close=98.5,
                flow_60s=0.05,
                l1_imbalance_close=-0.05,
            ),
        )
        self.assertEqual(armed.decision, ContinuationDecision.PULLBACK_ARMED)
        confirmed = advance_informed_continuation(
            armed,
            self.later(
                bar_index=12,
                ts_event=3,
                open=98.4,
                high=98.5,
                low=97.4,
                close=97.6,
                flow_60s=-0.10,
                l1_imbalance_close=-0.20,
            ),
        )
        self.assertEqual(confirmed.decision, ContinuationDecision.CONFIRMED)

    def test_no_pullback_expires(self) -> None:
        state = self.state()
        for index in range(11, 16):
            state = advance_informed_continuation(
                state,
                self.later(
                    bar_index=index,
                    ts_event=index,
                    open=102.0,
                    high=102.5,
                    low=101.8,
                    close=102.2,
                    flow_60s=0.10,
                    l1_imbalance_close=0.10,
                ),
            )
        self.assertEqual(state.decision, ContinuationDecision.EXPIRED)


if __name__ == "__main__":
    unittest.main()
