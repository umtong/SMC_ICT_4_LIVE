"""Portable plan-only scorer for EasyChart ML_a.

The scorer consumes only immutable V5TradePlan fields. It never changes entry,
stop, target or risk and it has no access to future labels.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping


def _kind(value: Any) -> str:
    return str(getattr(value, "value", value))


def plan_features(plan: Any) -> dict[str, Any]:
    risk = abs(float(plan.entry) - float(plan.stop))
    reward = abs(float(plan.target) - float(plan.entry))
    observed = int(plan.observed_time_ns)
    hour = (observed / 3_600_000_000_000.0) % 24.0
    decision = float(plan.decision_timeframe_minutes)
    trigger = float(plan.trigger_timeframe_minutes)
    return {
        "symbol": str(plan.symbol),
        "side": _kind(plan.side),
        "family": str(plan.family),
        "scale_name": str(plan.scale_name),
        "scenario_path": str(plan.scenario_path),
        "higher_zone_kind": _kind(plan.higher_zone_kind),
        "lower_zone_kind": _kind(plan.lower_zone_kind),
        "trigger_zone_kind": _kind(plan.trigger_zone_kind),
        "target_zone_kind": _kind(plan.target_zone_kind),
        "higher_strength_ratio": float(plan.higher_strength_ratio),
        "lower_strength_ratio": float(plan.lower_strength_ratio),
        "trigger_strength_ratio": float(plan.trigger_strength_ratio),
        "source_rule_count": float(plan.source_rule_count),
        "higher_timeframe_minutes": float(plan.higher_timeframe_minutes),
        "decision_timeframe_minutes": decision,
        "trigger_timeframe_minutes": trigger,
        "f_gross_rr": float(plan.gross_rr),
        "f_log_gross_rr": math.log1p(max(float(plan.gross_rr), 0.0)),
        "f_rr_excess_above_3": max(float(plan.gross_rr) - 3.0, 0.0),
        "f_risk_fraction": risk / max(abs(float(plan.entry)), 1e-18),
        "f_reward_fraction": reward / max(abs(float(plan.entry)), 1e-18),
        "f_overlap_width_r": abs(float(plan.overlap_upper) - float(plan.overlap_lower)) / max(risk, 1e-18),
        "f_setup_age_min": (observed - int(plan.setup_observed_time_ns)) / 60_000_000_000.0,
        "f_interaction_age_min": (observed - int(plan.interaction_time_ns)) / 60_000_000_000.0,
        "f_trigger_age_min": (observed - int(plan.trigger_time_ns)) / 60_000_000_000.0,
        "f_higher_decision_ratio": float(plan.higher_timeframe_minutes) / max(decision, 1e-18),
        "f_decision_trigger_ratio": decision / max(trigger, 1e-18),
        "f_hour_sin": math.sin(2.0 * math.pi * hour / 24.0),
        "f_hour_cos": math.cos(2.0 * math.pi * hour / 24.0),
    }


@dataclass(frozen=True, slots=True)
class PortableLogisticPlanScorer:
    intercept: float
    numeric: tuple[dict[str, Any], ...]
    categorical: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: str | Path) -> "PortableLogisticPlanScorer":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("format") != "ML_A_LOGISTIC_V1":
            raise ValueError("unsupported ML_a model format")
        return cls(
            intercept=float(payload["intercept"]),
            numeric=tuple(payload.get("numeric", ())),
            categorical=tuple(payload.get("categorical", ())),
        )

    def decision_function(self, features: Mapping[str, Any]) -> float:
        value = self.intercept
        for item in self.numeric:
            raw = features.get(item["feature"])
            try:
                number = float(raw)
                if not math.isfinite(number):
                    raise ValueError
            except (TypeError, ValueError):
                number = float(item["impute"])
            scale = max(abs(float(item["scale"])), 1e-18)
            value += float(item["weight"]) * ((number - float(item["mean"])) / scale)
        for item in self.categorical:
            raw = features.get(item["feature"], item["impute"])
            value += float(item["weights"].get(str(raw), 0.0))
        return value

    def probability(self, features: Mapping[str, Any]) -> float:
        logit = self.decision_function(features)
        if logit >= 0.0:
            return 1.0 / (1.0 + math.exp(-logit))
        exp_value = math.exp(logit)
        return exp_value / (1.0 + exp_value)

    def score_plan(self, plan: Any) -> float:
        return self.probability(plan_features(plan))
