from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from global_initiative_continuation import (  # noqa: E402
    CONTINUATION_MODULE,
    GlobalInitiativeRouter,
    InitiativeContinuationEngine,
    InitiativeState,
    _Pivot,
)
from logic import (  # noqa: E402
    BarObs,
    Direction,
    LogicConfig,
    Scenario,
    Side,
    StructuralBar,
    TradePlan,
)
from market_leadership import LeadershipDecision  # noqa: E402
from semantic_market_leadership import SemanticMarketLeadershipGate  # noqa: E402
from v6_market_leadership import OwnershipMarketLeadershipGate  # noqa: E402


class V6OwnershipGateTests(unittest.TestCase):
    @staticmethod
    def decision(rank: int) -> LeadershipDecision:
        return LeadershipDecision(
            approved=True,
            reason="SEMANTIC_FAR_MODERATE_COUNTERTREND_UNANIMOUS",
            leader="BTCUSDT",
            symbol="SOLUSDT",
            scenario="FAR",
            direction="LONG",
            sweep_ts_ns=1,
            confirmation_ts_ns=2,
            peer_returns={"BTCUSDT": 0.01, "ETHUSDT": 0.008, "XRPUSDT": 0.006},
            directional_returns={
                "BTCUSDT": 0.01,
                "ETHUSDT": 0.008,
                "SOLUSDT": 0.012,
                "XRPUSDT": 0.006,
            },
            directional_trend_scores={
                "BTCUSDT": -0.3,
                "ETHUSDT": -0.2,
                "SOLUSDT": -0.4,
                "XRPUSDT": -0.1,
            },
            candidate_event_move=0.012,
            peer_event_median=0.008,
            confirmation_impulse=1.2,
            trailing_direction_rank=2,
            event_direction_rank=rank,
            event_path_efficiency=0.5,
            event_standardized_displacement=1.0,
        )

    def test_second_mover_cannot_transfer_global_initiative(self) -> None:
        gate = OwnershipMarketLeadershipGate(
            ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        )
        with patch.object(
            SemanticMarketLeadershipGate,
            "decide",
            return_value=self.decision(2),
        ):
            result = gate.decide(
                symbol="SOLUSDT",
                scenario="FAR",
                direction="LONG",
                sweep_ts_ns=1,
                confirmation_ts_ns=2,
            )
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "V6_FAR_REQUIRES_EVENT_DIRECTION_OWNER")

    def test_event_owner_can_transfer_after_preserved_semantics(self) -> None:
        gate = OwnershipMarketLeadershipGate(
            ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"),
        )
        with patch.object(
            SemanticMarketLeadershipGate,
            "decide",
            return_value=self.decision(1),
        ):
            result = gate.decide(
                symbol="SOLUSDT",
                scenario="FAR",
                direction="LONG",
                sweep_ts_ns=1,
                confirmation_ts_ns=2,
            )
        self.assertTrue(result.approved)
        self.assertEqual(result.reason, "V6_FAR_EVENT_OWNER_CONFIRMS_TRANSFER")


class GlobalInitiativeRouterTests(unittest.TestCase):
    @staticmethod
    def long_plan() -> TradePlan:
        return TradePlan(
            scenario_id="core-long",
            scenario=Scenario.FAR,
            direction=Direction.LONG,
            observed_ts_ns=200,
            expected_entry=100.0,
            stop_price=98.0,
            target_price=105.0,
            atr=1.0,
            loss_per_unit=2.2,
            gain_per_unit=4.8,
            net_r=2.18,
            reason_code="TEST",
            expire_ts_ns=1000,
            details={"pool_level": 99.0, "sweep_ts_ns": 100},
        )

    def test_source_reacceptance_terminates_long_initiative(self) -> None:
        router = GlobalInitiativeRouter()
        state = router.observe_owned_plan(
            plan=self.long_plan(),
            symbol="BTCUSDT",
            leadership={"event_direction_rank": 1},
            observed_ts_ns=200,
        )
        self.assertIsNotNone(state)
        router.observe_batch(
            300,
            {
                "BTCUSDT": BarObs(300, 100.0, 101.0, 99.5, 100.5, 10.0, 6.0),
            },
        )
        self.assertIsNotNone(router.state)
        router.observe_batch(
            400,
            {
                "BTCUSDT": BarObs(400, 100.0, 100.2, 98.5, 98.9, 10.0, 4.0),
            },
        )
        self.assertIsNone(router.state)
        self.assertEqual(router.events[-1].reason_code, "SOURCE_BOUNDARY_REACCEPTED")

    def test_declared_target_delivery_terminates_state(self) -> None:
        router = GlobalInitiativeRouter()
        router.observe_owned_plan(
            plan=self.long_plan(),
            symbol="BTCUSDT",
            leadership={"event_direction_rank": 1},
            observed_ts_ns=200,
        )
        router.observe_batch(
            300,
            {
                "BTCUSDT": BarObs(300, 103.0, 105.1, 102.5, 104.8, 10.0, 7.0),
            },
        )
        self.assertIsNone(router.state)
        self.assertEqual(
            router.events[-1].reason_code,
            "DECLARED_EXTERNAL_TARGET_DELIVERED",
        )


class InitiativeContinuationTests(unittest.TestCase):
    def test_fresh_mss_fvg_builds_costed_structural_plan(self) -> None:
        config = LogicConfig()
        engine = InitiativeContinuationEngine(
            config,
            "BTCUSDT-PERP.BINANCE",
            symbol="BTCUSDT",
            logic_key="BTCUSDT::GLOBAL_INITIATIVE_CONTINUATION",
        )
        first = StructuralBar(
            start_ts_ns=10,
            end_ts_ns=20,
            open=99.0,
            high=100.0,
            low=98.5,
            close=99.5,
            volume=100.0,
            taker_buy_volume=55.0,
            high_ts_ns=15,
            low_ts_ns=12,
        )
        middle = StructuralBar(
            start_ts_ns=20,
            end_ts_ns=30,
            open=100.5,
            high=101.0,
            low=99.5,
            close=99.8,
            volume=100.0,
            taker_buy_volume=45.0,
            high_ts_ns=22,
            low_ts_ns=28,
        )
        displacement = StructuralBar(
            start_ts_ns=30,
            end_ts_ns=40,
            open=100.5,
            high=104.0,
            low=100.5,
            close=103.0,
            volume=100.0,
            taker_buy_volume=70.0,
            high_ts_ns=39,
            low_ts_ns=31,
        )
        engine._bars = [first, middle, displacement]
        engine._ranges = deque([2.0] * 30, maxlen=30)
        engine._pivot_highs = [_Pivot(known_ts_ns=25, price=101.0)]
        state = InitiativeState(
            scenario_id="GI-1",
            source_plan_id="core",
            source_symbol="BTCUSDT",
            direction=Direction.LONG,
            source_level=97.0,
            target_level=110.0,
            activated_ts_ns=1,
            source_scenario="FAR",
            leadership={"event_direction_rank": 1},
        )
        target = SimpleNamespace(
            consumed=False,
            external=True,
            confirmed_ts_ns=20,
            source="PREVIOUS_UTC_DAY",
            level=106.0,
            side=Side.HIGH,
            strength=3,
            scenario_id="target-high",
        )
        plan = engine._build_plan(
            completed=displacement,
            observed_ts_ns=50,
            state=state,
            external_engine=SimpleNamespace(pools=[target]),
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.direction, Direction.LONG)
        self.assertAlmostEqual(plan.expected_entry, 100.25)
        self.assertEqual(plan.target_price, 106.0)
        self.assertGreaterEqual(plan.net_r, config.min_net_r)
        self.assertEqual(plan.details["module"], CONTINUATION_MODULE)
        self.assertEqual(plan.details["target_pool_id"], "target-high")


if __name__ == "__main__":
    unittest.main()
