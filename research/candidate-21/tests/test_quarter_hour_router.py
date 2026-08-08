from __future__ import annotations

import unittest

from quarter_hour_router import (
    ClockAuction,
    ClockDecision,
    ClockObservation,
    ClockThresholds,
    advance_clock_auction,
)


class QuarterHourRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = ClockThresholds(max_wait_bars=3)

    @staticmethod
    def long_state() -> ClockAuction:
        return ClockAuction(
            scenario_id="qh-1",
            direction=1,
            boundary_index=100,
            last_index=100,
            expires_index=103,
            boundary_level=100.0,
            range_opposite=98.0,
            acceptance_target=102.0,
            rejection_target=98.0,
            atr=1.0,
            event_high=100.45,
            event_low=99.85,
            event_close=100.20,
            event_open_flow=0.30,
            event_phase_burst=2.0,
            event_efficiency=0.40,
            event_extension_atr=0.45,
            max_extension_atr=0.45,
        )

    @staticmethod
    def short_state() -> ClockAuction:
        return ClockAuction(
            scenario_id="qh-2",
            direction=-1,
            boundary_index=200,
            last_index=200,
            expires_index=203,
            boundary_level=100.0,
            range_opposite=102.0,
            acceptance_target=98.0,
            rejection_target=102.0,
            atr=1.0,
            event_high=100.15,
            event_low=99.55,
            event_close=99.80,
            event_open_flow=-0.30,
            event_phase_burst=2.0,
            event_efficiency=0.40,
            event_extension_atr=0.45,
            max_extension_atr=0.45,
        )

    def test_boundary_bar_cannot_confirm_itself(self) -> None:
        with self.assertRaises(ValueError):
            advance_clock_auction(
                self.long_state(),
                ClockObservation(
                    bar_index=100,
                    high=100.5,
                    low=100.0,
                    close=100.4,
                    flow_60s=0.2,
                    ret_60s_bps=3.0,
                    efficiency_60s=0.6,
                    depth_imbalance_1=0.1,
                    liquidity_ahead_change_1m=-0.1,
                ),
                self.thresholds,
            )

    def test_later_price_flow_and_book_transmission_confirms_acceptance(self) -> None:
        result = advance_clock_auction(
            self.long_state(),
            ClockObservation(
                bar_index=101,
                high=100.85,
                low=100.10,
                close=100.70,
                flow_60s=0.18,
                ret_60s_bps=4.0,
                efficiency_60s=0.65,
                depth_imbalance_1=0.12,
                liquidity_ahead_change_1m=-0.08,
            ),
            self.thresholds,
        )
        self.assertEqual(result.decision, ClockDecision.ACCEPTANCE)
        self.assertGreater(result.latest_progress_atr, 0.0)

    def test_absorbed_reclaim_with_opposite_initiative_confirms_failure(self) -> None:
        state = self.long_state()
        state = ClockAuction(
            **{
                **{field: getattr(state, field) for field in state.__dataclass_fields__},
                "event_efficiency": 0.20,
                "event_extension_atr": 0.22,
                "max_extension_atr": 0.22,
            },
        )
        result = advance_clock_auction(
            state,
            ClockObservation(
                bar_index=101,
                high=100.25,
                low=99.25,
                close=99.40,
                flow_60s=-0.20,
                ret_60s_bps=-5.0,
                efficiency_60s=0.70,
                depth_imbalance_1=-0.15,
                liquidity_ahead_change_1m=0.12,
            ),
            self.thresholds,
        )
        self.assertEqual(result.decision, ClockDecision.FAILED_AUCTION)

    def test_mixed_response_stays_waiting_then_closes_unresolved(self) -> None:
        state = self.long_state()
        first = advance_clock_auction(
            state,
            ClockObservation(
                bar_index=101,
                high=100.55,
                low=99.95,
                close=100.10,
                flow_60s=0.01,
                ret_60s_bps=-0.2,
                efficiency_60s=0.10,
                depth_imbalance_1=0.0,
                liquidity_ahead_change_1m=0.0,
            ),
            self.thresholds,
        )
        self.assertEqual(first.decision, ClockDecision.WAITING)
        second = advance_clock_auction(
            first,
            ClockObservation(
                bar_index=103,
                high=100.40,
                low=99.80,
                close=100.02,
                flow_60s=0.0,
                ret_60s_bps=0.0,
                efficiency_60s=0.0,
                depth_imbalance_1=0.0,
                liquidity_ahead_change_1m=0.0,
            ),
            self.thresholds,
        )
        self.assertEqual(second.decision, ClockDecision.UNRESOLVED)

    def test_target_consumed_before_confirmation_invalidates(self) -> None:
        result = advance_clock_auction(
            self.long_state(),
            ClockObservation(
                bar_index=101,
                high=102.10,
                low=100.10,
                close=101.70,
                flow_60s=0.20,
                ret_60s_bps=8.0,
                efficiency_60s=0.80,
                depth_imbalance_1=0.20,
                liquidity_ahead_change_1m=-0.20,
            ),
            self.thresholds,
        )
        self.assertEqual(result.decision, ClockDecision.INVALIDATED)

    def test_short_side_is_symmetric(self) -> None:
        result = advance_clock_auction(
            self.short_state(),
            ClockObservation(
                bar_index=201,
                high=99.90,
                low=99.15,
                close=99.30,
                flow_60s=-0.18,
                ret_60s_bps=-4.0,
                efficiency_60s=0.65,
                depth_imbalance_1=-0.12,
                liquidity_ahead_change_1m=-0.08,
            ),
            self.thresholds,
        )
        self.assertEqual(result.decision, ClockDecision.ACCEPTANCE)


if __name__ == "__main__":
    unittest.main()
