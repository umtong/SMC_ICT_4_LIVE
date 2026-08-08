from __future__ import annotations

import unittest

from forced_exhaustion_router import ForcedDecision
from forced_exhaustion_router import ForcedEpisode
from forced_exhaustion_router import ForcedObservation
from forced_exhaustion_router import ForcedResponseThresholds
from forced_exhaustion_router import ForcedShockEvidence
from forced_exhaustion_router import ForcedShockThresholds
from forced_exhaustion_router import advance_forced_episode
from forced_exhaustion_router import classify_forced_shock


class ForcedShockTests(unittest.TestCase):
    def test_shock_requires_deleveraging_and_premium_extension(self) -> None:
        thresholds = ForcedShockThresholds()
        base = dict(
            move_atr=-1.5,
            notional_burst=2.0,
            flow_3m=-0.5,
            efficiency_60s=0.7,
            oi_change_15m=-0.02,
            premium_change_5m=-0.0002,
        )
        self.assertEqual(
            classify_forced_shock(ForcedShockEvidence(**base), thresholds),
            -1,
        )
        self.assertEqual(
            classify_forced_shock(
                ForcedShockEvidence(**{**base, "oi_change_15m": 0.01}),
                thresholds,
            ),
            0,
        )
        self.assertEqual(
            classify_forced_shock(
                ForcedShockEvidence(**{**base, "premium_change_5m": 0.0002}),
                thresholds,
            ),
            0,
        )


class ForcedResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.thresholds = ForcedResponseThresholds()
        self.state = ForcedEpisode(
            scenario_id="down-1",
            shock_direction=-1,
            shock_index=10,
            last_index=10,
            expires_index=16,
            origin_price=100.0,
            event_high=100.0,
            event_low=90.0,
            event_close=91.0,
            atr=5.0,
            event_efficiency=0.8,
            event_oi_change_15m=-0.02,
            event_premium_change_5m=-0.001,
            event_notional_burst=2.0,
            event_flow_3m=-0.5,
            latest_high=100.0,
            latest_low=90.0,
        )

    def test_shock_bar_cannot_confirm_itself(self) -> None:
        with self.assertRaises(ValueError):
            advance_forced_episode(
                self.state,
                ForcedObservation(
                    bar_index=10,
                    high=92.0,
                    low=90.0,
                    close=91.5,
                    flow_60s=-0.4,
                    flow_3m=-0.3,
                    ret_60s_bps=-5.0,
                    efficiency_60s=0.5,
                    depth_imbalance_1=0.2,
                    defending_depth_change_1m=0.1,
                    oi_change_15m=-0.02,
                    premium_change_1m=-0.0001,
                ),
                self.thresholds,
            )

    def test_exhaustion_then_later_reprice_confirms(self) -> None:
        exhausted = advance_forced_episode(
            self.state,
            ForcedObservation(
                bar_index=11,
                high=92.0,
                low=89.5,
                close=91.0,
                flow_60s=-0.45,
                flow_3m=-0.4,
                ret_60s_bps=2.0,
                efficiency_60s=0.2,
                depth_imbalance_1=0.25,
                defending_depth_change_1m=0.08,
                oi_change_15m=-0.03,
                premium_change_1m=-0.0001,
            ),
            self.thresholds,
        )
        self.assertEqual(exhausted.decision, ForcedDecision.WAITING_REVERSAL)
        confirmed = advance_forced_episode(
            exhausted,
            ForcedObservation(
                bar_index=12,
                high=95.0,
                low=91.0,
                close=94.0,
                flow_60s=0.35,
                flow_3m=0.10,
                ret_60s_bps=20.0,
                efficiency_60s=0.6,
                depth_imbalance_1=0.20,
                defending_depth_change_1m=0.04,
                oi_change_15m=-0.02,
                premium_change_1m=0.0002,
            ),
            self.thresholds,
        )
        self.assertEqual(confirmed.decision, ForcedDecision.CONFIRMED)

    def test_target_consumed_before_confirmation_invalidates(self) -> None:
        invalid = advance_forced_episode(
            self.state,
            ForcedObservation(
                bar_index=11,
                high=101.0,
                low=91.0,
                close=99.0,
                flow_60s=0.3,
                flow_3m=0.2,
                ret_60s_bps=30.0,
                efficiency_60s=0.8,
                depth_imbalance_1=0.2,
                defending_depth_change_1m=0.1,
                oi_change_15m=-0.02,
                premium_change_1m=0.0002,
            ),
            self.thresholds,
        )
        self.assertEqual(invalid.decision, ForcedDecision.INVALIDATED)

    def test_short_side_symmetry(self) -> None:
        state = ForcedEpisode(
            scenario_id="up-1",
            shock_direction=1,
            shock_index=20,
            last_index=20,
            expires_index=26,
            origin_price=100.0,
            event_high=110.0,
            event_low=100.0,
            event_close=109.0,
            atr=5.0,
            event_efficiency=0.8,
            event_oi_change_15m=-0.02,
            event_premium_change_5m=0.001,
            event_notional_burst=2.0,
            event_flow_3m=0.5,
            latest_high=110.0,
            latest_low=100.0,
        )
        exhausted = advance_forced_episode(
            state,
            ForcedObservation(
                bar_index=21,
                high=110.5,
                low=108.0,
                close=109.0,
                flow_60s=0.4,
                flow_3m=0.3,
                ret_60s_bps=-2.0,
                efficiency_60s=0.2,
                depth_imbalance_1=-0.2,
                defending_depth_change_1m=0.05,
                oi_change_15m=-0.02,
                premium_change_1m=0.0001,
            ),
            self.thresholds,
        )
        confirmed = advance_forced_episode(
            exhausted,
            ForcedObservation(
                bar_index=22,
                high=109.0,
                low=105.0,
                close=106.0,
                flow_60s=-0.3,
                flow_3m=-0.1,
                ret_60s_bps=-20.0,
                efficiency_60s=0.6,
                depth_imbalance_1=-0.2,
                defending_depth_change_1m=0.04,
                oi_change_15m=-0.01,
                premium_change_1m=-0.0002,
            ),
            self.thresholds,
        )
        self.assertEqual(confirmed.decision, ForcedDecision.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
