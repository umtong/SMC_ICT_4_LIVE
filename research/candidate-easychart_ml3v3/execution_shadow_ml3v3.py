"""Nonselective ML3v3 feature harvest over the opportunity union."""
from __future__ import annotations

from typing import Any

from execution_ml1 import EasyChartML1Strategy, _ScoredPlan
from features_ml3v3 import FEATURE_NAMES, ML3V3FeatureBook, build_ml3v3_features


class EasyChartML3V3ShadowStrategy(EasyChartML1Strategy):
    """Reuse audited shadow execution while emitting the expanded causal schema."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.ml_feature_book = ML3V3FeatureBook()

    def _score_plan(self, instrument_id: Any, plan: Any) -> _ScoredPlan:
        features = build_ml3v3_features(
            plan,
            feature_book=self.ml_feature_book,
            macro_side=self._macro_side(instrument_id),
            factor_state=self.factor_state,
            flow_observation=self._flow_observation(instrument_id),
        )
        economics = self._economics(instrument_id, plan)
        decision = self.ml_model.decide(features, economics)
        baseline_eligible = self._baseline_context_allows(instrument_id, plan)
        event_values: dict[str, Any] = {
            "plan_id": plan.plan_id,
            "causal_event_id": plan.causal_event_id,
            "instrument_id": str(instrument_id),
            "symbol": plan.symbol,
            "family": plan.family,
            "side": plan.side.name,
            "scenario_path": plan.scenario_path,
            "scale_name": plan.scale_name,
            "model_id": self.ml_model.model_id,
            "model_status": self.ml_model.status,
            "ml_mode": "shadow_union",
            "ml_raw_probability": decision.raw_probability,
            "ml_target_probability": decision.target_probability,
            "ml_tree_probability_std": decision.tree_probability_std,
            "ml_required_probability": decision.required_probability,
            "ml_expected_net_r": decision.expected_net_r,
            "ml_model_accepted": decision.accepted,
            "ml_baseline_eligible": baseline_eligible,
            "ml_win_net_r": economics.win_net_r,
            "ml_loss_net_r": economics.loss_net_r,
            "ml_break_even_probability": economics.break_even_probability,
            "ml3v3_feature_count": len(FEATURE_NAMES),
        }
        event_values.update({f"mlf_{name}": value for name, value in features.items()})
        self._record("ml_plan", **event_values)
        self._minc("plans_scored")
        return _ScoredPlan(
            instrument_id=instrument_id,
            plan=plan,
            features=features,
            economics=economics,
            decision=decision,
            baseline_eligible=baseline_eligible,
        )


StrategyClass = EasyChartML3V3ShadowStrategy
