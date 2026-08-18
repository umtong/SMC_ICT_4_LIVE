"""Causal features for the non-linear latent auction policy."""
from __future__ import annotations

import math
from typing import Any, Iterable

from features_integrated import (
    FEATURE_CLIP_RANGES as INTEGRATED_CLIP_RANGES,
    FEATURE_DEFAULTS as INTEGRATED_DEFAULTS,
    FEATURE_NAMES as INTEGRATED_FEATURE_NAMES,
    ML3V3FeatureBook,
    build_integrated_features,
)


LATENT_FEATURE_NAMES = (
    "latent_available",
    "latent_regime_trend_up",
    "latent_regime_trend_down",
    "latent_regime_range",
    "latent_regime_transition",
    "latent_regime_mixed",
    "latent_draw_alignment",
    "latent_draw_balance_side",
    "latent_trend_60_alignment",
    "latent_trend_15_alignment",
    "latent_trend_min_alignment",
    "latent_trend_disagreement",
    "latent_factor_alignment",
    "latent_location_alignment",
    "latent_acceptance_x_trend_min",
    "latent_acceptance_x_draw",
    "latent_rejection_x_location",
    "latent_rejection_x_old_trend_opposition",
)
FEATURE_NAMES = tuple(INTEGRATED_FEATURE_NAMES) + LATENT_FEATURE_NAMES
FEATURE_DEFAULTS = dict(INTEGRATED_DEFAULTS)
FEATURE_DEFAULTS.update({name: 0.0 for name in LATENT_FEATURE_NAMES})
FEATURE_CLIP_RANGES = dict(INTEGRATED_CLIP_RANGES)
FEATURE_CLIP_RANGES.update(
    {
        "latent_available": (0.0, 1.0),
        "latent_regime_trend_up": (0.0, 1.0),
        "latent_regime_trend_down": (0.0, 1.0),
        "latent_regime_range": (0.0, 1.0),
        "latent_regime_transition": (0.0, 1.0),
        "latent_regime_mixed": (0.0, 1.0),
        "latent_draw_alignment": (-1.0, 1.0),
        "latent_draw_balance_side": (-1.0, 1.0),
        "latent_trend_60_alignment": (-2.0, 2.0),
        "latent_trend_15_alignment": (-2.0, 2.0),
        "latent_trend_min_alignment": (-2.0, 2.0),
        "latent_trend_disagreement": (0.0, 4.0),
        "latent_factor_alignment": (-1.0, 1.0),
        "latent_location_alignment": (-2.0, 2.0),
        "latent_acceptance_x_trend_min": (-2.0, 2.0),
        "latent_acceptance_x_draw": (-1.0, 1.0),
        "latent_rejection_x_location": (-2.0, 2.0),
        "latent_rejection_x_old_trend_opposition": (0.0, 2.0),
    }
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _state_from_provenance(provenance: Iterable[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for raw in provenance:
        text = str(raw)
        if not text.startswith("RESEARCH_STATE:") or "=" not in text:
            continue
        key, value = text[len("RESEARCH_STATE:") :].split("=", 1)
        if key == "LATENT_REGIME":
            output[key] = value
        elif key in {
            "LIQUIDITY_DRAW_ALIGNMENT",
            "LIQUIDITY_DRAW_BALANCE",
            "TREND_60_ALIGNMENT",
            "TREND_15_ALIGNMENT",
            "FACTOR_ALIGNMENT",
            "LOCATION_ALIGNMENT",
        }:
            output[key] = _finite(value)
    return output


def latent_context_features(
    *,
    side: Any,
    scenario_path: Any,
    regime: Any = "MIXED",
    draw_alignment: Any = 0.0,
    draw_balance: Any = 0.0,
    trend_60_alignment: Any = 0.0,
    trend_15_alignment: Any = 0.0,
    factor_alignment: Any = 0.0,
    location_alignment: Any = 0.0,
    available: bool = True,
) -> dict[str, float]:
    del side  # alignments emitted by the policy are already plan-side relative
    regime_text = str(regime).upper()
    scenario = str(getattr(scenario_path, "value", scenario_path)).upper()
    acceptance = 1.0 if "ACCEPT" in scenario else 0.0
    rejection = 1.0 if "REJECT" in scenario else 0.0
    draw = max(-1.0, min(1.0, _finite(draw_alignment)))
    balance = max(-1.0, min(1.0, _finite(draw_balance)))
    t60 = max(-2.0, min(2.0, _finite(trend_60_alignment)))
    t15 = max(-2.0, min(2.0, _finite(trend_15_alignment)))
    minimum = min(t60, t15)
    factor = max(-1.0, min(1.0, _finite(factor_alignment)))
    location = max(-2.0, min(2.0, _finite(location_alignment)))
    return {
        "latent_available": 1.0 if available else 0.0,
        "latent_regime_trend_up": 1.0 if regime_text == "TREND_UP" else 0.0,
        "latent_regime_trend_down": 1.0 if regime_text == "TREND_DOWN" else 0.0,
        "latent_regime_range": 1.0 if regime_text == "RANGE" else 0.0,
        "latent_regime_transition": 1.0 if regime_text == "TRANSITION" else 0.0,
        "latent_regime_mixed": 1.0 if regime_text == "MIXED" else 0.0,
        "latent_draw_alignment": draw,
        "latent_draw_balance_side": draw * abs(balance),
        "latent_trend_60_alignment": t60,
        "latent_trend_15_alignment": t15,
        "latent_trend_min_alignment": minimum,
        "latent_trend_disagreement": abs(t60 - t15),
        "latent_factor_alignment": factor,
        "latent_location_alignment": location,
        "latent_acceptance_x_trend_min": acceptance * minimum,
        "latent_acceptance_x_draw": acceptance * draw,
        "latent_rejection_x_location": rejection * location,
        "latent_rejection_x_old_trend_opposition": rejection * max(0.0, -minimum),
    }


def plan_latent_context_features(plan: Any) -> dict[str, float]:
    state = _state_from_provenance(getattr(plan, "rule_provenance", ()))
    available = "LATENT_REGIME" in state
    return latent_context_features(
        side=plan.side,
        scenario_path=plan.scenario_path,
        regime=state.get("LATENT_REGIME", "MIXED"),
        draw_alignment=state.get("LIQUIDITY_DRAW_ALIGNMENT", 0.0),
        draw_balance=state.get("LIQUIDITY_DRAW_BALANCE", 0.0),
        trend_60_alignment=state.get("TREND_60_ALIGNMENT", 0.0),
        trend_15_alignment=state.get("TREND_15_ALIGNMENT", 0.0),
        factor_alignment=state.get("FACTOR_ALIGNMENT", 0.0),
        location_alignment=state.get("LOCATION_ALIGNMENT", 0.0),
        available=available,
    )


def build_latent_features(
    plan: Any,
    *,
    feature_book: ML3V3FeatureBook,
    macro_side: Any,
    factor_state: Any,
    flow_observation: Any,
) -> dict[str, float]:
    features = build_integrated_features(
        plan,
        feature_book=feature_book,
        macro_side=macro_side,
        factor_state=factor_state,
        flow_observation=flow_observation,
    )
    features.update(plan_latent_context_features(plan))
    missing = set(FEATURE_NAMES) - set(features)
    extra = set(features) - set(FEATURE_NAMES)
    if missing or extra:
        raise RuntimeError(
            f"latent feature schema mismatch missing={sorted(missing)} extra={sorted(extra)}"
        )
    return {name: float(features[name]) for name in FEATURE_NAMES}


__all__ = [
    "FEATURE_CLIP_RANGES",
    "FEATURE_DEFAULTS",
    "FEATURE_NAMES",
    "LATENT_FEATURE_NAMES",
    "ML3V3FeatureBook",
    "build_latent_features",
    "latent_context_features",
    "plan_latent_context_features",
]
