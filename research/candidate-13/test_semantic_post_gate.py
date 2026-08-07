from __future__ import annotations

from types import SimpleNamespace
import unittest

from logic import Direction, Scenario, TradePlan
from market_leadership import LeadershipDecision
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from semantic_market_leadership import FAR_EXHAUSTION_QUORUM, FAR_EXHAUSTION_UNANIMOUS
from semantic_post_gate import amend_after_leadership


class PostGateExecutionTests(unittest.TestCase):
    def plan(self, *, eligible: bool = True, entry_order_type: str = "LIMIT") -> TradePlan:
        return TradePlan(
            scenario_id="S1",
            scenario=Scenario.FAR,
            direction=Direction.SHORT,
            observed_ts_ns=2,
            expected_entry=2.0895,
            stop_price=2.1041,
            target_price=2.0559,
            atr=0.00279,
            loss_per_unit=0.018,
            gain_per_unit=0.032,
            net_r=1.86,
            reason_code="PASSIVE",
            expire_ts_ns=60,
            entry_order_type=entry_order_type,
            entry_post_only=entry_order_type != "MARKET",
            details={
                "void_repair_candidate": {
                    "eligible": eligible,
                    "entry": 2.0853,
                    "stop": 2.0905,
                    "target": 2.0559,
                    "loss_per_unit": 0.00856,
                    "gain_per_unit": 0.02691,
                    "net_r": 3.14,
                },
            },
        )

    def decision(self, *, approved: bool = True, reason: str = FAR_EXHAUSTION_UNANIMOUS):
        return LeadershipDecision(
            approved=approved,
            reason=reason,
            leader="BTCUSDT",
            symbol="XRPUSDT",
            scenario="FAR",
            direction="SHORT",
            sweep_ts_ns=1,
            confirmation_ts_ns=2,
            peer_returns={"BTCUSDT": -0.004, "ETHUSDT": -0.003, "SOLUSDT": -0.002},
            directional_returns={
                "BTCUSDT": -0.01,
                "ETHUSDT": -0.01,
                "SOLUSDT": -0.01,
                "XRPUSDT": -0.01,
            },
            directional_trend_scores={
                "BTCUSDT": -0.8,
                "ETHUSDT": -0.9,
                "SOLUSDT": -1.0,
                "XRPUSDT": -0.4,
            },
            candidate_event_move=0.006,
            peer_event_median=0.003,
            confirmation_impulse=1.8,
            trailing_direction_rank=2,
            event_direction_rank=1,
            event_path_efficiency=0.20,
            event_standardized_displacement=0.90,
        )

    def engine(self):
        event = SimpleNamespace(
            scenario_id="S1",
            event_type="TRADE_PLAN_CONFIRMED",
            details={},
        )
        return SimpleNamespace(events=[event])

    def test_unanimous_exhaustion_activates_void_repair_market(self):
        engine = self.engine()
        amended = amend_after_leadership(engine, self.plan(), self.decision())
        self.assertEqual(amended.entry_order_type, "MARKET")
        self.assertFalse(amended.entry_post_only)
        self.assertEqual(amended.expire_ts_ns, MARKET_ENTRY_SENTINEL_NS)
        self.assertAlmostEqual(amended.expected_entry, 2.0853)
        self.assertAlmostEqual(amended.stop_price, 2.0905)
        self.assertAlmostEqual(amended.net_r, 3.14)
        self.assertTrue(engine.events[0].details["post_leadership_execution_reclassified"])

    def test_dominant_quorum_retains_structural_execution(self):
        plan = self.plan()
        amended = amend_after_leadership(
            self.engine(),
            plan,
            self.decision(reason=FAR_EXHAUSTION_QUORUM),
        )
        self.assertIs(amended, plan)

    def test_ineligible_void_candidate_retains_passive_execution(self):
        plan = self.plan(eligible=False)
        amended = amend_after_leadership(self.engine(), plan, self.decision())
        self.assertIs(amended, plan)

    def test_rejected_semantics_cannot_activate_market(self):
        plan = self.plan()
        amended = amend_after_leadership(
            self.engine(),
            plan,
            self.decision(approved=False),
        )
        self.assertIs(amended, plan)

    def test_existing_structural_market_is_not_rewritten(self):
        plan = self.plan(entry_order_type="MARKET")
        amended = amend_after_leadership(self.engine(), plan, self.decision())
        self.assertIs(amended, plan)


if __name__ == "__main__":
    unittest.main()
