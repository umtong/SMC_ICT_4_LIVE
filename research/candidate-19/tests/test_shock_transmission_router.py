from __future__ import annotations

import unittest

from shock_transmission_router import ShockDecision
from shock_transmission_router import ShockObservation
from shock_transmission_router import ShockTransmission
from shock_transmission_router import advance_shock_transmission


def state(side: int = 1) -> ShockTransmission:
    return ShockTransmission(
        scenario_id="s1",
        side=side,
        shock_index=10,
        last_index=10,
        expires_index=12,
        failure_high=101.0,
        failure_low=99.0,
        parent_extreme=98.0 if side > 0 else 102.0,
        shock_close=102.0 if side > 0 else 98.0,
    )


class ShockTransmissionTests(unittest.TestCase):
    def test_strictly_later_price_flow_queue_transmission_confirms(self) -> None:
        result = advance_shock_transmission(
            state(),
            ShockObservation(
                bar_index=11,
                high=104.0,
                low=101.5,
                close=103.0,
                flow_60s=0.4,
                ret_60s_bps=8.0,
                depth_imbalance_1=0.2,
                liquidity_ahead_change_1m=-0.1,
            ),
        )
        self.assertIs(result.decision, ShockDecision.CONFIRMED)

    def test_shock_bar_cannot_confirm_itself(self) -> None:
        with self.assertRaises(ValueError):
            advance_shock_transmission(
                state(),
                ShockObservation(
                    bar_index=10,
                    high=104.0,
                    low=101.5,
                    close=103.0,
                    flow_60s=0.4,
                    ret_60s_bps=8.0,
                    depth_imbalance_1=0.2,
                    liquidity_ahead_change_1m=-0.1,
                ),
            )

    def test_close_back_inside_failure_bar_invalidates(self) -> None:
        result = advance_shock_transmission(
            state(),
            ShockObservation(
                bar_index=11,
                high=102.4,
                low=100.0,
                close=100.5,
                flow_60s=-0.1,
                ret_60s_bps=-2.0,
                depth_imbalance_1=-0.1,
                liquidity_ahead_change_1m=0.1,
            ),
        )
        self.assertIs(result.decision, ShockDecision.INVALIDATED)
        self.assertIn("FAILED_TO_HOLD", result.reason)

    def test_same_side_aggression_without_progress_is_absorption(self) -> None:
        result = advance_shock_transmission(
            state(),
            ShockObservation(
                bar_index=11,
                high=102.5,
                low=101.1,
                close=101.5,
                flow_60s=0.5,
                ret_60s_bps=-1.0,
                depth_imbalance_1=0.2,
                liquidity_ahead_change_1m=-0.1,
            ),
        )
        self.assertIs(result.decision, ShockDecision.INVALIDATED)
        self.assertIn("ABSORBED", result.reason)

    def test_incomplete_support_expires_at_registered_window(self) -> None:
        first = advance_shock_transmission(
            state(),
            ShockObservation(
                bar_index=11,
                high=103.0,
                low=101.5,
                close=102.4,
                flow_60s=-0.1,
                ret_60s_bps=1.0,
                depth_imbalance_1=0.1,
                liquidity_ahead_change_1m=-0.1,
            ),
        )
        self.assertIs(first.decision, ShockDecision.WAITING)
        result = advance_shock_transmission(
            first,
            ShockObservation(
                bar_index=12,
                high=103.2,
                low=101.5,
                close=102.5,
                flow_60s=-0.1,
                ret_60s_bps=1.0,
                depth_imbalance_1=0.1,
                liquidity_ahead_change_1m=-0.1,
            ),
        )
        self.assertIs(result.decision, ShockDecision.EXPIRED)

    def test_short_is_exact_directional_mirror(self) -> None:
        result = advance_shock_transmission(
            state(side=-1),
            ShockObservation(
                bar_index=11,
                high=98.5,
                low=96.0,
                close=97.0,
                flow_60s=-0.4,
                ret_60s_bps=-8.0,
                depth_imbalance_1=-0.2,
                liquidity_ahead_change_1m=-0.1,
            ),
        )
        self.assertIs(result.decision, ShockDecision.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
