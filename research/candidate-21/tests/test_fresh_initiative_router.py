from pathlib import Path
import unittest

from fresh_initiative_router import (
    FreshDecision,
    FreshEpisode,
    FreshEvidence,
    FreshObservation,
    advance_fresh_episode,
    classify_fresh_initiative,
    mirror_evidence,
)


class FreshInitiativeRouterTests(unittest.TestCase):
    def evidence(self) -> FreshEvidence:
        return FreshEvidence(
            flow_60s=0.20,
            flow_price_alignment_60s=20.0,
            notional_burst=2.0,
            efficiency_60s=0.40,
            depth_imbalance_1=-0.20,
            oi_change_5m=0.0001,
            premium_change_5m=0.0001,
            premium_index=0.0002,
            prior_30m_return_bps=10.0,
        )

    def long_episode(self) -> FreshEpisode:
        return FreshEpisode(
            scenario_id="long",
            side=1,
            event_index=10,
            expires_index=13,
            event_high=101.0,
            event_low=99.0,
            event_close=100.5,
            origin_price=100.0,
            stop_price=99.0,
            target_price=105.0,
            target_pool_id="pool-high",
        )

    def test_classifier_is_long_short_symmetric(self) -> None:
        long = classify_fresh_initiative(self.evidence())
        short = classify_fresh_initiative(mirror_evidence(self.evidence()))
        self.assertEqual(long.side, 1)
        self.assertEqual(short.side, -1)
        self.assertEqual(long.reason, short.reason)

    def test_forced_oi_and_overextended_trend_are_rejected(self) -> None:
        base = self.evidence()
        forced = FreshEvidence(
            base.flow_60s,
            base.flow_price_alignment_60s,
            base.notional_burst,
            base.efficiency_60s,
            base.depth_imbalance_1,
            -0.005,
            base.premium_change_5m,
            base.premium_index,
            base.prior_30m_return_bps,
        )
        extended = FreshEvidence(
            base.flow_60s,
            base.flow_price_alignment_60s,
            base.notional_burst,
            base.efficiency_60s,
            base.depth_imbalance_1,
            base.oi_change_5m,
            base.premium_change_5m,
            base.premium_index,
            100.0,
        )
        self.assertEqual(classify_fresh_initiative(forced).side, 0)
        self.assertEqual(classify_fresh_initiative(extended).side, 0)

    def test_event_bar_cannot_confirm_itself(self) -> None:
        episode = self.long_episode()
        same_bar = advance_fresh_episode(
            episode,
            FreshObservation(10, 100.5, 102.0, 100.0, 101.8),
        )
        self.assertIs(same_bar, episode)
        self.assertEqual(same_bar.decision, FreshDecision.WAITING)

    def test_strictly_later_acceptance_confirms_both_directions(self) -> None:
        long = advance_fresh_episode(
            self.long_episode(),
            FreshObservation(11, 101.0, 102.5, 100.8, 102.0),
        )
        short_episode = FreshEpisode(
            scenario_id="short",
            side=-1,
            event_index=10,
            expires_index=13,
            event_high=101.0,
            event_low=99.0,
            event_close=99.5,
            origin_price=100.0,
            stop_price=101.0,
            target_price=95.0,
            target_pool_id="pool-low",
        )
        short = advance_fresh_episode(
            short_episode,
            FreshObservation(11, 99.0, 99.2, 97.5, 98.0),
        )
        self.assertEqual(long.decision, FreshDecision.CONFIRMED)
        self.assertEqual(short.decision, FreshDecision.CONFIRMED)

    def test_target_consumption_precedes_confirmation(self) -> None:
        result = advance_fresh_episode(
            self.long_episode(),
            FreshObservation(11, 101.0, 105.0, 100.8, 104.0),
        )
        self.assertEqual(result.decision, FreshDecision.TARGET_CONSUMED)

    def test_strategy_reuses_mature_execution_and_natural_target(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "candidate21_fresh_initiative_strategy.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ScenarioValidEntryStrategy", source)
        self.assertIn("_submit_price_capped_bracket", source)
        self.assertIn('target_source=f"POOL:', source)
        self.assertIn("advance_fresh_episode", source)
        self.assertNotIn("BacktestEngine", source)
        self.assertNotIn("matching engine", source.lower())


if __name__ == "__main__":
    unittest.main()
