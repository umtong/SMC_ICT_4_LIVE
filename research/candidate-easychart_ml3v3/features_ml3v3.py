"""ML3v3 causal feature schema with explicit 4-hour and 24-hour context.

The source material repeatedly separates broad direction from small-frame entry.
The account runner supplies completed 60-minute bars, so 4h/24h context is
constructed only from completed hourly observations.  No resampled bucket uses
an unfinished future hour and no symbol identity is exposed to the model.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any

from ml1_features import (
    CausalFeatureBook,
    FEATURE_CLIP_RANGES as BASE_CLIP_RANGES,
    FEATURE_DEFAULTS as BASE_DEFAULTS,
    FEATURE_NAMES as BASE_FEATURE_NAMES,
    build_plan_features,
)


CONTEXT_FEATURE_NAMES = (
    "ctx4h_available",
    "ctx24h_available",
    "ctx4h_side_return_z",
    "ctx24h_side_return_z",
    "ctx4h_side_consistency",
    "ctx24h_side_consistency",
    "ctx4h_range_ratio_log",
    "ctx24h_range_ratio_log",
    "ctx4h_volume_ratio_log",
    "ctx24h_volume_ratio_log",
    "ctx4h_24h_alignment",
    "ctx4h_reversal_against_24h",
)
FEATURE_NAMES = tuple(BASE_FEATURE_NAMES) + CONTEXT_FEATURE_NAMES
FEATURE_DEFAULTS = dict(BASE_DEFAULTS)
FEATURE_DEFAULTS.update({name: 0.0 for name in CONTEXT_FEATURE_NAMES})
FEATURE_CLIP_RANGES = dict(BASE_CLIP_RANGES)
FEATURE_CLIP_RANGES.update(
    {
        "ctx4h_available": (0.0, 1.0),
        "ctx24h_available": (0.0, 1.0),
        "ctx4h_side_return_z": (-12.0, 12.0),
        "ctx24h_side_return_z": (-12.0, 12.0),
        "ctx4h_side_consistency": (0.0, 1.0),
        "ctx24h_side_consistency": (0.0, 1.0),
        "ctx4h_range_ratio_log": (-12.0, 12.0),
        "ctx24h_range_ratio_log": (-12.0, 12.0),
        "ctx4h_volume_ratio_log": (-12.0, 12.0),
        "ctx24h_volume_ratio_log": (-12.0, 12.0),
        "ctx4h_24h_alignment": (-1.0, 1.0),
        "ctx4h_reversal_against_24h": (0.0, 1.0),
    }
)


def _side_sign(side: Any) -> float:
    text = str(getattr(side, "name", side)).upper()
    if text.endswith("LONG") or text == "BUY":
        return 1.0
    if text.endswith("SHORT") or text == "SELL":
        return -1.0
    raise ValueError(f"unknown side {side!r}")


def _clip(value: float, lower: float = -12.0, upper: float = 12.0) -> float:
    return min(upper, max(lower, float(value)))


def _safe_log_ratio(value: float, baseline: float) -> float:
    if value <= 0.0 or baseline <= 0.0:
        return 0.0
    return _clip(math.log(value / baseline))


class ML3V3FeatureBook(CausalFeatureBook):
    """Base causal state plus completed-hour multi-scale context."""

    def context_features(self, plan: Any) -> dict[str, float]:
        output = {name: 0.0 for name in CONTEXT_FEATURE_NAMES}
        state = self._states.get((str(plan.symbol), 60))
        if state is None:
            return output
        history = list(state.history)
        if len(history) < 4:
            return output
        sign = _side_sign(plan.side)
        returns = [float(item.close_return) for item in history]
        ranges = [max(float(item.range_fraction), 1e-12) for item in history]
        volumes = [max(float(item.volume_ratio), 1e-12) for item in history]
        scale_source = [abs(item) for item in returns[-72:-24]] or [abs(item) for item in returns[:-4]]
        return_scale = max(median(scale_source) if scale_source else 1e-8, 1e-8)
        prior_ranges = ranges[-72:-24] or ranges[:-4]
        prior_volumes = volumes[-72:-24] or volumes[:-4]
        range_baseline = max(median(prior_ranges) if prior_ranges else median(ranges), 1e-12)
        volume_baseline = max(median(prior_volumes) if prior_volumes else median(volumes), 1e-12)

        def aggregate(hours: int) -> tuple[float, float, float, float]:
            window_returns = returns[-hours:]
            window_ranges = ranges[-hours:]
            window_volumes = volumes[-hours:]
            side_return = sign * sum(window_returns)
            normalized = _clip(side_return / (return_scale * math.sqrt(hours)))
            consistency = sum(sign * value > 0.0 for value in window_returns) / len(window_returns)
            range_ratio = _safe_log_ratio(sum(window_ranges) / len(window_ranges), range_baseline)
            volume_ratio = _safe_log_ratio(sum(window_volumes) / len(window_volumes), volume_baseline)
            return normalized, consistency, range_ratio, volume_ratio

        four = aggregate(4)
        output.update(
            {
                "ctx4h_available": 1.0,
                "ctx4h_side_return_z": four[0],
                "ctx4h_side_consistency": four[1],
                "ctx4h_range_ratio_log": four[2],
                "ctx4h_volume_ratio_log": four[3],
            }
        )
        if len(history) < 24:
            return output
        day = aggregate(24)
        four_sign = 1.0 if four[0] > 0.0 else -1.0 if four[0] < 0.0 else 0.0
        day_sign = 1.0 if day[0] > 0.0 else -1.0 if day[0] < 0.0 else 0.0
        output.update(
            {
                "ctx24h_available": 1.0,
                "ctx24h_side_return_z": day[0],
                "ctx24h_side_consistency": day[1],
                "ctx24h_range_ratio_log": day[2],
                "ctx24h_volume_ratio_log": day[3],
                "ctx4h_24h_alignment": four_sign * day_sign,
                "ctx4h_reversal_against_24h": 1.0
                if four_sign > 0.0 and day_sign < 0.0
                else 0.0,
            }
        )
        return output


def build_ml3v3_features(
    plan: Any,
    *,
    feature_book: ML3V3FeatureBook,
    macro_side: Any,
    factor_state: Any,
    flow_observation: Any,
) -> dict[str, float]:
    features = build_plan_features(
        plan,
        feature_book=feature_book,
        macro_side=macro_side,
        factor_state=factor_state,
        flow_observation=flow_observation,
    )
    features.update(feature_book.context_features(plan))
    missing = set(FEATURE_NAMES) - set(features)
    extra = set(features) - set(FEATURE_NAMES)
    if missing or extra:
        raise RuntimeError(
            f"ML3v3 feature schema mismatch missing={sorted(missing)} extra={sorted(extra)}"
        )
    return {name: float(features[name]) for name in FEATURE_NAMES}


__all__ = [
    "CONTEXT_FEATURE_NAMES",
    "FEATURE_CLIP_RANGES",
    "FEATURE_DEFAULTS",
    "FEATURE_NAMES",
    "ML3V3FeatureBook",
    "build_ml3v3_features",
]
