"""Causal model features for the integrated EasyChart auction policy.

The model is not asked to rediscover direction, liquidity or a setup from raw
bars.  Those responsibilities belong to :mod:`integrated_auction`.  These
features expose the state that existed when a complete immutable plan was
created so period-robust learning can compare coherent opportunities without
using symbol identity or a user supplied target win rate.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from features_ml3v3 import (
    FEATURE_CLIP_RANGES as BASE_CLIP_RANGES,
    FEATURE_DEFAULTS as BASE_DEFAULTS,
    FEATURE_NAMES as BASE_FEATURE_NAMES,
    ML3V3FeatureBook,
    build_ml3v3_features,
)


INTEGRATED_FEATURE_NAMES = (
    "integrated_available",
    "integrated_structure_60_raw",
    "integrated_structure_15_raw",
    "integrated_structure_60_side",
    "integrated_structure_15_side",
    "integrated_structure_min_side",
    "integrated_structure_max_side",
    "integrated_structure_agreement",
    "integrated_structure_disagreement",
    "integrated_channel_confluence",
    "integrated_channel_x_min_alignment",
    "integrated_noise_to_risk_log",
)
FEATURE_NAMES = tuple(BASE_FEATURE_NAMES) + INTEGRATED_FEATURE_NAMES
FEATURE_DEFAULTS = dict(BASE_DEFAULTS)
FEATURE_DEFAULTS.update({name: 0.0 for name in INTEGRATED_FEATURE_NAMES})
FEATURE_CLIP_RANGES = dict(BASE_CLIP_RANGES)
FEATURE_CLIP_RANGES.update(
    {
        "integrated_available": (0.0, 1.0),
        "integrated_structure_60_raw": (-2.0, 2.0),
        "integrated_structure_15_raw": (-2.0, 2.0),
        "integrated_structure_60_side": (-2.0, 2.0),
        "integrated_structure_15_side": (-2.0, 2.0),
        "integrated_structure_min_side": (-2.0, 2.0),
        "integrated_structure_max_side": (-2.0, 2.0),
        "integrated_structure_agreement": (-1.0, 1.0),
        "integrated_structure_disagreement": (0.0, 4.0),
        "integrated_channel_confluence": (0.0, 1.5),
        "integrated_channel_x_min_alignment": (-3.0, 3.0),
        "integrated_noise_to_risk_log": (-8.0, 8.0),
    }
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _side_sign(side: Any) -> float:
    text = str(getattr(side, "name", side)).upper()
    if text.endswith("LONG") or text == "BUY":
        return 1.0
    if text.endswith("SHORT") or text == "SELL":
        return -1.0
    raise ValueError(f"unknown side {side!r}")


def _state_from_provenance(provenance: Iterable[Any]) -> dict[str, float]:
    output: dict[str, float] = {}
    prefix = "RESEARCH_STATE:"
    for raw in provenance:
        text = str(raw)
        if not text.startswith(prefix) or "=" not in text:
            continue
        key, value = text[len(prefix) :].split("=", 1)
        if key in {
            "STRUCTURE_60",
            "STRUCTURE_15",
            "CHANNEL_CONFLUENCE",
            "CAUSAL_NOISE_BUFFER",
        }:
            output[key] = _finite(value)
    return output


def integrated_context_features(
    *,
    side: Any,
    entry: float,
    stop: float,
    structure_60: Any = 0.0,
    structure_15: Any = 0.0,
    channel_confluence: Any = 0.0,
    causal_noise_buffer: Any = 0.0,
    available: bool = True,
) -> dict[str, float]:
    sign = _side_sign(side)
    s60 = max(-2.0, min(2.0, _finite(structure_60)))
    s15 = max(-2.0, min(2.0, _finite(structure_15)))
    aligned_60 = sign * s60
    aligned_15 = sign * s15
    minimum = min(aligned_60, aligned_15)
    maximum = max(aligned_60, aligned_15)
    agreement = 1.0 if s60 * s15 > 0.0 else -1.0 if s60 * s15 < 0.0 else 0.0
    disagreement = abs(s60 - s15)
    channel = max(0.0, min(1.5, _finite(channel_confluence)))
    risk = max(abs(_finite(entry) - _finite(stop)), 1e-12)
    noise = max(0.0, _finite(causal_noise_buffer))
    ratio = max(noise / risk, 1e-8)
    return {
        "integrated_available": 1.0 if available else 0.0,
        "integrated_structure_60_raw": s60,
        "integrated_structure_15_raw": s15,
        "integrated_structure_60_side": aligned_60,
        "integrated_structure_15_side": aligned_15,
        "integrated_structure_min_side": minimum,
        "integrated_structure_max_side": maximum,
        "integrated_structure_agreement": agreement,
        "integrated_structure_disagreement": disagreement,
        "integrated_channel_confluence": channel,
        "integrated_channel_x_min_alignment": channel * minimum,
        "integrated_noise_to_risk_log": max(-8.0, min(8.0, math.log(ratio))),
    }


def plan_integrated_context_features(plan: Any) -> dict[str, float]:
    state = _state_from_provenance(getattr(plan, "rule_provenance", ()))
    available = "STRUCTURE_60" in state and "STRUCTURE_15" in state
    return integrated_context_features(
        side=plan.side,
        entry=float(plan.entry),
        stop=float(plan.stop),
        structure_60=state.get("STRUCTURE_60", 0.0),
        structure_15=state.get("STRUCTURE_15", 0.0),
        channel_confluence=state.get("CHANNEL_CONFLUENCE", 0.0),
        causal_noise_buffer=state.get("CAUSAL_NOISE_BUFFER", 0.0),
        available=available,
    )


def build_integrated_features(
    plan: Any,
    *,
    feature_book: ML3V3FeatureBook,
    macro_side: Any,
    factor_state: Any,
    flow_observation: Any,
) -> dict[str, float]:
    features = build_ml3v3_features(
        plan,
        feature_book=feature_book,
        macro_side=macro_side,
        factor_state=factor_state,
        flow_observation=flow_observation,
    )
    features.update(plan_integrated_context_features(plan))
    missing = set(FEATURE_NAMES) - set(features)
    extra = set(features) - set(FEATURE_NAMES)
    if missing or extra:
        raise RuntimeError(
            f"integrated feature schema mismatch missing={sorted(missing)} extra={sorted(extra)}"
        )
    return {name: float(features[name]) for name in FEATURE_NAMES}


__all__ = [
    "FEATURE_CLIP_RANGES",
    "FEATURE_DEFAULTS",
    "FEATURE_NAMES",
    "INTEGRATED_FEATURE_NAMES",
    "ML3V3FeatureBook",
    "build_integrated_features",
    "integrated_context_features",
    "plan_integrated_context_features",
]
