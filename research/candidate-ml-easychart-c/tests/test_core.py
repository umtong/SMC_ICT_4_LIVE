from __future__ import annotations

from pathlib import Path
import sys
import unittest

import numpy as np

HERE = Path(__file__).resolve().parent
CANDIDATE = HERE.parent
RESEARCH = CANDIDATE.parent
for path in (
    CANDIDATE,
    RESEARCH / "candidate-easychart-ml-system",
    RESEARCH / "candidate-easychart_re1",
    RESEARCH / "candidate-easychart-v5",
    RESEARCH / "candidate-easychart-v3",
):
    sys.path.insert(0, str(path))

from contracts_v5 import V5TradePlan  # noqa: E402
from domain import Side  # noqa: E402
from easychart_c.core import (  # noqa: E402
    CausalResponseRouter,
    FEATURES,
    feature_frame,
    replace_with_first_objective,
)


class DummyModel:
    def __init__(self, probability: float) -> None:
        self.probability_value = probability

    def predict_proba(self, matrix):  # noqa: ANN001
        return np.asarray([[1.0 - self.probability_value, self.probability_value]])


def plan(side: Side) -> V5TradePlan:
    entry, stop, target = (
        (100.0, 99.0, 103.0) if side is Side.LONG else (100.0, 101.0, 97.0)
    )
    return V5TradePlan(
        plan_id=f"plan-{side.name}",
        causal_event_id=f"event-{side.name}",
        symbol="BTCUSDT",
        family="FLOW|TEST",
        side=side,
        observed_time_ns=10,
        entry=entry,
        stop=stop,
        target=target,
        gross_rr=3.0,
        setup_id="setup",
        higher_zone_id="higher",
        higher_zone_kind="HORIZONTAL_RESISTANCE",
        higher_strength_ratio=2.0,
        lower_zone_id="lower",
        lower_zone_kind="ORDER_BLOCK",
        lower_strength_ratio=2.0,
        trigger_zone_id="trigger",
        trigger_strength_ratio=2.0,
        target_zone_id="target",
        target_zone_kind="HORIZONTAL_SUPPORT",
        overlap_lower=99.8,
        overlap_upper=100.2,
        interaction_time_ns=8,
        trigger_time_ns=9,
        scenario_path="REJECTION",
        setup_observed_time_ns=7,
        trigger_zone_kind="FLOW_SELL_INITIATIVE",
        source_rule_count=1,
        rule_provenance=("SOURCE_TEST",),
        scale_name="HORIZONTAL",
        higher_timeframe_minutes=60,
        decision_timeframe_minutes=15,
        trigger_timeframe_minutes=1,
    )


class CoreTests(unittest.TestCase):
    def metadata(self) -> dict[str, object]:
        return {
            "features": list(FEATURES),
            "probability_threshold": 0.60,
            "first_objective_r": 1.0,
            "max_target_cost_r": 0.25,
            "risk_fraction": 0.03,
            "trained_through_ns": 1,
            "excluded_trigger_kinds": ["FLOW_BUY_INITIATIVE"],
            "excluded_higher_zone_kinds": [
                "ASCENDING_CHANNEL_UPPER",
                "PREVIOUS_H4_LOW",
            ],
        }

    def test_first_objective_preserves_stop_and_builds_one_r_long(self) -> None:
        original = plan(Side.LONG)
        transformed = replace_with_first_objective(original, tick_size=0.1)
        self.assertEqual(transformed.stop, original.stop)
        self.assertEqual(transformed.entry, original.entry)
        self.assertAlmostEqual(transformed.target, 101.0)
        self.assertAlmostEqual(transformed.gross_rr, 1.0)
        self.assertEqual(transformed.causal_event_id, original.causal_event_id)

    def test_first_objective_preserves_stop_and_builds_one_r_short(self) -> None:
        original = plan(Side.SHORT)
        transformed = replace_with_first_objective(original, tick_size=0.1)
        self.assertEqual(transformed.stop, original.stop)
        self.assertEqual(transformed.entry, original.entry)
        self.assertAlmostEqual(transformed.target, 99.0)
        self.assertAlmostEqual(transformed.gross_rr, 1.0)

    def test_feature_schema_is_fixed(self) -> None:
        matrix = feature_frame([{"gross_rr": 1.5, "family": "FLOW|TEST"}])
        self.assertEqual(tuple(matrix.columns), FEATURES)
        self.assertEqual(len(matrix), 1)

    def test_router_accepts_post_cost_positive_confirmed_response(self) -> None:
        router = CausalResponseRouter(DummyModel(0.82), self.metadata())
        decision = router.decision(
            {
                "gross_rr": 1.5,
                "target_net_r": 1.30,
                "trigger_zone_kind": "FLOW_SELL_INITIATIVE",
                "higher_zone_kind": "HORIZONTAL_RESISTANCE",
            },
            fixed_target_economics={"target_net_r": 0.82, "stop_net_r": -1.05},
        )
        self.assertTrue(decision.accepted)
        self.assertGreater(decision.expected_net_r, 0.0)
        self.assertGreater(decision.expected_log_growth, 0.0)

    def test_router_rejects_incomplete_higher_timeframe_transfer(self) -> None:
        router = CausalResponseRouter(DummyModel(0.99), self.metadata())
        decision = router.decision(
            {
                "gross_rr": 1.5,
                "target_net_r": 1.30,
                "trigger_zone_kind": "FLOW_SELL_INITIATIVE",
                "higher_zone_kind": "ASCENDING_CHANNEL_UPPER",
            },
            fixed_target_economics={"target_net_r": 0.82, "stop_net_r": -1.05},
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(
            decision.reason,
            "INCOMPLETE_HIGHER_TIMEFRAME_CONTROL_TRANSFER",
        )

    def test_router_rejects_when_cost_consumes_structure_risk(self) -> None:
        router = CausalResponseRouter(DummyModel(0.99), self.metadata())
        decision = router.decision(
            {
                "gross_rr": 1.5,
                "target_net_r": 1.20,
                "trigger_zone_kind": "FLOW_SELL_INITIATIVE",
                "higher_zone_kind": "HORIZONTAL_RESISTANCE",
            },
            fixed_target_economics={"target_net_r": 0.82, "stop_net_r": -1.05},
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(
            decision.reason,
            "EXECUTION_COST_CONSUMES_TOO_MUCH_STRUCTURE_RISK",
        )


if __name__ == "__main__":
    unittest.main()
