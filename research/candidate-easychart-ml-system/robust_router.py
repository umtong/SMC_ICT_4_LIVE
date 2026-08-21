"""Environment-robust causal plan router for the EasyChart ML system.

This model does not forecast an unconstrained future return. A deterministic
auction engine has already produced a complete, immutable entry/stop/target
plan. The router estimates whether that exact target is likely to trade before
that exact stop, conditional on the causal auction state available at plan
emission.

Robustness comes from the learning construction rather than calendar features:

* no symbol or date enters the feature vector;
* every development environment and symbol receives equal aggregate weight;
* duplicate alternatives from one causal episode share one unit of weight;
* inference is the median of full, leave-one-environment and leave-one-symbol
  nonlinear models;
* calibration is fitted only from environment-held-out predictions.

Execution ranks candidates by expected log NAV growth under the project's fixed
3% stop-risk contract. Win rate, trade frequency and user examples are not
hard-coded objectives or filters.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from causal_state import HORIZONS, SIGNED_STATE_FEATURES, STATE_FEATURES

MODEL_VERSION = "easychart-ml-system-robust-ensemble-v1"
MISSING_CATEGORY = "__MISSING__"
OTHER_CATEGORY = "__OTHER__"
NS_PER_MINUTE = 60_000_000_000
DEFAULT_RISK_FRACTION = 0.03

PLAN_NUMERIC_FEATURES: tuple[str, ...] = (
    "gross_rr",
    "risk_bps",
    "target_bps",
    "target_net_r",
    "stop_net_r",
    "post_cost_reward_risk",
    "zero_drift_target_first_prior",
    "post_cost_break_even_target_probability",
    "required_target_probability_premium",
    "overlap_width_r",
    "entry_location_in_overlap",
    "higher_strength_ratio",
    "lower_strength_ratio",
    "trigger_strength_ratio",
    "source_rule_count",
    "setup_age_minutes",
    "interaction_age_minutes",
    "trigger_age_minutes",
    "higher_timeframe_minutes",
    "decision_timeframe_minutes",
    "trigger_timeframe_minutes",
    "risk_in_state_sigma_1m",
    "target_in_state_sigma_1m",
    "risk_in_state_range_1m",
    "target_in_state_range_1m",
)

TRACE_NUMERIC_FEATURES: tuple[str, ...] = (
    "trace_flow_strength",
    "trace_flow_activity_ratio",
    "trace_flow_trade_count_ratio",
    "trace_flow_trade_size_ratio",
    "trace_flow_delta_share",
    "trace_flow_delta_ratio",
    "trace_flow_body_ratio",
    "trace_flow_range_ratio",
    "trace_flow_close_location_signed",
    "trace_flow_impact_per_activity",
    "trace_flow_episode_bars",
    "trace_flow_episode_cumulative_delta",
    "trace_flow_episode_net_price_progress",
    "trace_adverse_quote",
    "trace_aligned_quote",
    "trace_penetration",
    "trace_recovery",
    "trace_adverse_impact_per_quote",
    "trace_recovery_impact_per_quote",
    "trace_impact_efficiency_ratio",
    "trace_episode_bars",
    "trace_decision_minutes",
    "trace_boundary_reclaimed",
    "trace_balance_reclaimed",
)

BREADTH_FEATURES: tuple[str, ...] = tuple(
    name
    for horizon in HORIZONS
    for name in (
        f"mls_aligned_breadth_{horizon}m",
        f"mls_opposed_breadth_{horizon}m",
    )
)

NUMERIC_FEATURES: tuple[str, ...] = (
    PLAN_NUMERIC_FEATURES + TRACE_NUMERIC_FEATURES + STATE_FEATURES + BREADTH_FEATURES
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "mechanism_owner",
    "family",
    "scenario_path",
    "scale_name",
    "higher_zone_kind",
    "lower_zone_kind",
    "trigger_zone_kind",
    "target_zone_kind",
    "trace_event",
    "trace_flow_kind",
    "trace_flow_mechanism",
    "trace_state_before_flow",
    "trace_acceptance",
    "trace_mechanism",
)

SIGNED_TRACE_FEATURES: frozenset[str] = frozenset(
    (
        "trace_flow_delta_share",
        "trace_flow_episode_cumulative_delta",
        "trace_flow_episode_net_price_progress",
    ),
)

ROW_ALIASES: dict[str, tuple[str, ...]] = {
    "target_net_r": ("counterfactual_target_net_r",),
    "stop_net_r": ("counterfactual_stop_net_r",),
    "trace_event": ("trace_scenario_kind",),
    "trace_flow_strength": ("trace_flow_strength", "flow_strength"),
    "trace_flow_activity_ratio": ("trace_flow_activity_ratio", "flow_activity_ratio"),
    "trace_flow_trade_count_ratio": (
        "trace_flow_trade_count_ratio",
        "flow_trade_count_ratio",
        "trace_flow_trade_count",
    ),
    "trace_flow_trade_size_ratio": (
        "trace_flow_trade_size_ratio",
        "flow_trade_size_ratio",
    ),
    "trace_flow_delta_share": ("trace_flow_delta_share", "flow_delta_share"),
    "trace_flow_delta_ratio": ("trace_flow_delta_ratio", "flow_delta_ratio"),
    "trace_flow_body_ratio": ("trace_flow_body_ratio", "flow_body_ratio"),
    "trace_flow_range_ratio": ("trace_flow_range_ratio", "flow_range_ratio"),
    "trace_flow_close_location_signed": (
        "trace_flow_close_location_signed",
        "trace_flow_close_location",
        "flow_close_location",
    ),
    "trace_flow_impact_per_activity": (
        "trace_flow_impact_per_activity",
        "flow_impact_per_activity",
    ),
    "trace_flow_episode_bars": ("trace_flow_episode_bars", "flow_episode_bars"),
    "trace_flow_episode_cumulative_delta": (
        "trace_flow_episode_cumulative_delta",
        "flow_episode_cumulative_delta",
    ),
    "trace_flow_episode_net_price_progress": (
        "trace_flow_episode_net_price_progress",
        "flow_episode_net_price_progress",
    ),
}


def _plain(value: Any) -> Any:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name is not None and not isinstance(value, str):
        return name
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, str):
        return enum_value
    return value


def _number(value: Any) -> float:
    value = _plain(value)
    if isinstance(value, bool):
        return float(value)
    if value is None:
        return float("nan")
    try:
        result = float(value)
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        if text in {"true", "yes"}:
            return 1.0
        if text in {"false", "no"}:
            return 0.0
        return float("nan")
    return result if math.isfinite(result) else float("nan")


def _category(value: Any) -> str:
    value = _plain(value)
    if value is None:
        return MISSING_CATEGORY
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return MISSING_CATEGORY
    return text


def _lookup(record: Mapping[str, Any], name: str) -> Any:
    if name in record:
        return record[name]
    for alias in ROW_ALIASES.get(name, ()):
        if alias in record:
            return record[alias]
    if name.startswith("trace_"):
        raw = name.removeprefix("trace_")
        if raw in record:
            return record[raw]
    return None


def _trace_value(trace: Mapping[str, Any] | None, name: str) -> Any:
    if trace is None:
        return None
    for key in (name, name.removeprefix("trace_")):
        if key in trace:
            return trace[key]
    return None


def _side_sign(side: Any) -> float:
    text = _category(side).upper()
    if text in {"LONG", "1", "SIDE.LONG"}:
        return 1.0
    if text in {"SHORT", "-1", "SIDE.SHORT"}:
        return -1.0
    raise ValueError(f"unsupported side {side!r}")


def _owner_from_record(record: Mapping[str, Any]) -> str:
    explicit = _category(record.get("mechanism_owner"))
    if explicit != MISSING_CATEGORY:
        return explicit
    for key in ("family", "plan_id", "causal_event_id"):
        value = _category(record.get(key))
        if "|" in value:
            return value.split("|", 1)[0]
        if value.startswith("mlsys-"):
            parts = value.split("-", 2)
            if len(parts) >= 2:
                return parts[1]
    return MISSING_CATEGORY


def economic_geometry(
    *,
    side: Any,
    entry: float,
    stop: float,
    target: float,
    tick_size: float,
    entry_slippage_ticks: int,
    target_slippage_ticks: int,
    stop_slippage_ticks: int,
    entry_fee_rate: float,
    exit_fee_rate: float,
) -> dict[str, float]:
    sign = _side_sign(side)
    entry = float(entry)
    stop = float(stop)
    target = float(target)
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0.0 or reward <= 0.0 or entry == 0.0:
        raise ValueError("nonpositive plan geometry")
    tick = float(tick_size)

    def net_r(exit_price: float, slippage_ticks: int) -> float:
        actual_entry = entry + sign * int(entry_slippage_ticks) * tick
        actual_exit = exit_price - sign * int(slippage_ticks) * tick
        gross = sign * (actual_exit - actual_entry) / risk
        fees = (
            float(entry_fee_rate) * abs(actual_entry)
            + float(exit_fee_rate) * abs(actual_exit)
        ) / risk
        return float(gross - fees)

    target_net_r = net_r(target, target_slippage_ticks)
    stop_net_r = net_r(stop, stop_slippage_ticks)
    denominator = target_net_r - stop_net_r
    break_even = -stop_net_r / denominator if denominator > 0.0 else float("nan")
    zero_drift = risk / (risk + reward)
    return {
        "risk_bps": 10_000.0 * risk / abs(entry),
        "target_bps": 10_000.0 * reward / abs(entry),
        "target_net_r": target_net_r,
        "stop_net_r": stop_net_r,
        "post_cost_reward_risk": (
            target_net_r / abs(stop_net_r)
            if target_net_r > 0.0 and stop_net_r < 0.0
            else float("nan")
        ),
        "zero_drift_target_first_prior": zero_drift,
        "post_cost_break_even_target_probability": break_even,
        "required_target_probability_premium": break_even - zero_drift,
    }


def _state_record(state: Mapping[str, Any], sign: float) -> dict[str, float]:
    output: dict[str, float] = {}
    for name in STATE_FEATURES:
        value = _number(state.get(name))
        if name in SIGNED_STATE_FEATURES and math.isfinite(value):
            value *= sign
        output[name] = value
    for horizon in HORIZONS:
        positive = _number(state.get(f"mls_common_positive_breadth_{horizon}m"))
        negative = _number(state.get(f"mls_common_negative_breadth_{horizon}m"))
        output[f"mls_aligned_breadth_{horizon}m"] = positive if sign > 0.0 else negative
        output[f"mls_opposed_breadth_{horizon}m"] = negative if sign > 0.0 else positive
    return output


def _normalize_trace_numeric(name: str, value: Any, sign: float) -> float:
    number = _number(value)
    if name == "trace_flow_close_location_signed" and math.isfinite(number):
        if 0.0 <= number <= 1.0:
            number = 2.0 * number - 1.0
        number *= sign
    elif name in SIGNED_TRACE_FEATURES and math.isfinite(number):
        number *= sign
    return number


def live_feature_record(
    plan: Any,
    *,
    trace: Mapping[str, Any] | None,
    economics: Mapping[str, float],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    side = getattr(plan.side, "name", plan.side)
    sign = _side_sign(side)
    entry = float(plan.entry)
    stop = float(plan.stop)
    target = float(plan.target)
    risk = abs(entry - stop)
    reward = abs(target - entry)
    lower = float(plan.overlap_lower)
    upper = float(plan.overlap_upper)
    width = max(0.0, upper - lower)
    observed = int(plan.observed_time_ns)
    output: dict[str, Any] = {
        "gross_rr": float(plan.gross_rr),
        **dict(economics),
        "overlap_width_r": width / risk if risk > 0.0 else float("nan"),
        "entry_location_in_overlap": (entry - lower) / width if width > 0.0 else 0.5,
        "higher_strength_ratio": _number(plan.higher_strength_ratio),
        "lower_strength_ratio": _number(plan.lower_strength_ratio),
        "trigger_strength_ratio": _number(plan.trigger_strength_ratio),
        "source_rule_count": _number(plan.source_rule_count),
        "setup_age_minutes": max(
            0.0,
            (observed - int(plan.setup_observed_time_ns)) / NS_PER_MINUTE,
        ),
        "interaction_age_minutes": max(
            0.0,
            (observed - int(plan.interaction_time_ns)) / NS_PER_MINUTE,
        ),
        "trigger_age_minutes": max(
            0.0,
            (observed - int(plan.trigger_time_ns)) / NS_PER_MINUTE,
        ),
        "higher_timeframe_minutes": _number(plan.higher_timeframe_minutes),
        "decision_timeframe_minutes": _number(plan.decision_timeframe_minutes),
        "trigger_timeframe_minutes": _number(plan.trigger_timeframe_minutes),
        "mechanism_owner": _owner_from_record(
            {
                "family": plan.family,
                "plan_id": plan.plan_id,
                "causal_event_id": plan.causal_event_id,
            },
        ),
        "family": plan.family,
        "scenario_path": plan.scenario_path,
        "scale_name": plan.scale_name,
        "higher_zone_kind": plan.higher_zone_kind,
        "lower_zone_kind": plan.lower_zone_kind,
        "trigger_zone_kind": plan.trigger_zone_kind,
        "target_zone_kind": plan.target_zone_kind,
    }
    output.update(_state_record(state, sign))
    sigma = _number(state.get("mls_prior_sigma_1m"))
    prior_range = _number(state.get("mls_prior_range_fraction_1m"))
    entry_abs = abs(entry)
    risk_fraction = risk / entry_abs if entry_abs > 0.0 else float("nan")
    target_fraction = reward / entry_abs if entry_abs > 0.0 else float("nan")
    output["risk_in_state_sigma_1m"] = (
        risk_fraction / sigma if math.isfinite(sigma) and sigma > 0.0 else float("nan")
    )
    output["target_in_state_sigma_1m"] = (
        target_fraction / sigma if math.isfinite(sigma) and sigma > 0.0 else float("nan")
    )
    output["risk_in_state_range_1m"] = (
        risk_fraction / prior_range
        if math.isfinite(prior_range) and prior_range > 0.0
        else float("nan")
    )
    output["target_in_state_range_1m"] = (
        target_fraction / prior_range
        if math.isfinite(prior_range) and prior_range > 0.0
        else float("nan")
    )
    for name in TRACE_NUMERIC_FEATURES:
        output[name] = _normalize_trace_numeric(name, _trace_value(trace, name), sign)
    for name in CATEGORICAL_FEATURES:
        if name.startswith("trace_"):
            output[name] = _trace_value(trace, name)
    return output


def row_feature_record(row: Mapping[str, Any]) -> dict[str, Any]:
    sign = _side_sign(row.get("side"))
    state = {name: _lookup(row, name) for name in STATE_FEATURES}
    output: dict[str, Any] = _state_record(state, sign)
    for name in PLAN_NUMERIC_FEATURES:
        output[name] = _lookup(row, name)
    for name in TRACE_NUMERIC_FEATURES:
        output[name] = _normalize_trace_numeric(name, _lookup(row, name), sign)
    for name in CATEGORICAL_FEATURES:
        output[name] = _lookup(row, name)
    output["mechanism_owner"] = _owner_from_record(row)

    entry = _number(row.get("entry"))
    stop = _number(row.get("stop"))
    target = _number(row.get("target"))
    risk = abs(entry - stop)
    reward = abs(target - entry)
    lower = _number(row.get("overlap_lower"))
    upper = _number(row.get("overlap_upper"))
    width = upper - lower
    if not math.isfinite(_number(output.get("overlap_width_r"))):
        output["overlap_width_r"] = width / risk if risk > 0.0 and width >= 0.0 else float("nan")
    if not math.isfinite(_number(output.get("entry_location_in_overlap"))):
        output["entry_location_in_overlap"] = (entry - lower) / width if width > 0.0 else 0.5
    observed = _number(row.get("observed_time_ns"))
    if not math.isfinite(observed):
        observed = _number(row.get("ts_ns"))
    for target_name, source_name in (
        ("setup_age_minutes", "setup_observed_time_ns"),
        ("interaction_age_minutes", "interaction_time_ns"),
        ("trigger_age_minutes", "trigger_time_ns"),
    ):
        if not math.isfinite(_number(output.get(target_name))):
            source = _number(row.get(source_name))
            output[target_name] = (
                max(0.0, (observed - source) / NS_PER_MINUTE)
                if math.isfinite(observed) and math.isfinite(source)
                else float("nan")
            )
    sigma = _number(row.get("mls_prior_sigma_1m"))
    prior_range = _number(row.get("mls_prior_range_fraction_1m"))
    entry_abs = abs(entry)
    risk_fraction = risk / entry_abs if entry_abs > 0.0 else float("nan")
    target_fraction = reward / entry_abs if entry_abs > 0.0 else float("nan")
    derived = {
        "risk_in_state_sigma_1m": risk_fraction / sigma if sigma > 0.0 else float("nan"),
        "target_in_state_sigma_1m": target_fraction / sigma if sigma > 0.0 else float("nan"),
        "risk_in_state_range_1m": risk_fraction / prior_range if prior_range > 0.0 else float("nan"),
        "target_in_state_range_1m": target_fraction / prior_range if prior_range > 0.0 else float("nan"),
    }
    for name, value in derived.items():
        if not math.isfinite(_number(output.get(name))):
            output[name] = value
    return output


def _sigmoid(values: np.ndarray | float) -> np.ndarray | float:
    array = np.asarray(values, dtype=np.float64)
    output = np.empty_like(array)
    positive = array >= 0.0
    output[positive] = 1.0 / (1.0 + np.exp(-np.minimum(array[positive], 700.0)))
    exp_value = np.exp(np.maximum(array[~positive], -700.0))
    output[~positive] = exp_value / (1.0 + exp_value)
    if np.isscalar(values):
        return float(output)
    return output


def _logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(probability, 1e-6, 1.0 - 1e-6)
    return np.log(clipped / (1.0 - clipped))


@dataclass(slots=True)
class FeatureEncoder:
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...]
    medians: dict[str, float]
    scales: dict[str, float]
    category_levels: dict[str, tuple[str, ...]]

    @property
    def dimension(self) -> int:
        return len(self.numeric_features) + sum(
            len(self.category_levels[name]) for name in self.categorical_features
        )

    @classmethod
    def fit(
        cls,
        records: Sequence[Mapping[str, Any]],
        *,
        min_category_count: int = 8,
    ) -> "FeatureEncoder":
        medians: dict[str, float] = {}
        scales: dict[str, float] = {}
        for name in NUMERIC_FEATURES:
            values = np.asarray([_number(_lookup(row, name)) for row in records], dtype=np.float64)
            finite = values[np.isfinite(values)]
            center = float(np.median(finite)) if finite.size else 0.0
            mad = float(np.median(np.abs(finite - center))) if finite.size else 0.0
            scale = 1.4826 * mad
            if not math.isfinite(scale) or scale < 1e-9:
                scale = float(finite.std(ddof=0)) if finite.size else 1.0
            if not math.isfinite(scale) or scale < 1e-9:
                scale = 1.0
            medians[name] = center
            scales[name] = scale
        levels: dict[str, tuple[str, ...]] = {}
        for name in CATEGORICAL_FEATURES:
            counts: dict[str, int] = {}
            for row in records:
                value = _category(_lookup(row, name))
                counts[value] = counts.get(value, 0) + 1
            retained = sorted(
                value
                for value, count in counts.items()
                if count >= min_category_count and value not in {MISSING_CATEGORY, OTHER_CATEGORY}
            )
            levels[name] = tuple([MISSING_CATEGORY, OTHER_CATEGORY, *retained])
        return cls(NUMERIC_FEATURES, CATEGORICAL_FEATURES, medians, scales, levels)

    def vectorize(self, record: Mapping[str, Any]) -> np.ndarray:
        values: list[float] = []
        for name in self.numeric_features:
            value = _number(_lookup(record, name))
            if not math.isfinite(value):
                value = self.medians[name]
            standardized = (value - self.medians[name]) / self.scales[name]
            values.append(float(np.clip(standardized, -12.0, 12.0)))
        for name in self.categorical_features:
            value = _category(_lookup(record, name))
            levels = self.category_levels[name]
            if value not in levels:
                value = OTHER_CATEGORY
            values.extend(1.0 if value == level else 0.0 for level in levels)
        vector = np.asarray(values, dtype=np.float64)
        if vector.shape != (self.dimension,):
            raise RuntimeError(f"encoder dimension mismatch {vector.shape} != {(self.dimension,)}")
        return vector

    def matrix(self, records: Sequence[Mapping[str, Any]]) -> np.ndarray:
        return np.vstack([self.vectorize(record) for record in records])

    def to_dict(self) -> dict[str, Any]:
        return {
            "numeric_features": list(self.numeric_features),
            "categorical_features": list(self.categorical_features),
            "medians": self.medians,
            "scales": self.scales,
            "category_levels": {key: list(value) for key, value in self.category_levels.items()},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FeatureEncoder":
        return cls(
            numeric_features=tuple(payload["numeric_features"]),
            categorical_features=tuple(payload["categorical_features"]),
            medians={key: float(value) for key, value in payload["medians"].items()},
            scales={key: float(value) for key, value in payload["scales"].items()},
            category_levels={key: tuple(value) for key, value in payload["category_levels"].items()},
        )


@dataclass(slots=True)
class SymmetricTree:
    features: tuple[int, ...]
    thresholds: tuple[float, ...]
    leaf_values: np.ndarray

    def leaf_indices(self, matrix: np.ndarray) -> np.ndarray:
        indices = np.zeros(matrix.shape[0], dtype=np.int64)
        for depth, (feature, threshold) in enumerate(zip(self.features, self.thresholds, strict=True)):
            indices |= (matrix[:, feature] > threshold).astype(np.int64) << depth
        return indices

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return self.leaf_values[self.leaf_indices(matrix)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": list(self.features),
            "thresholds": list(self.thresholds),
            "leaf_values": self.leaf_values.tolist(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SymmetricTree":
        return cls(
            features=tuple(int(value) for value in payload["features"]),
            thresholds=tuple(float(value) for value in payload["thresholds"]),
            leaf_values=np.asarray(payload["leaf_values"], dtype=np.float64),
        )


@dataclass(slots=True)
class BoostedLogitModel:
    intercept: float
    learning_rate: float
    trees: list[SymmetricTree]
    model_name: str
    excluded_environment: str | None = None
    excluded_symbol: str | None = None

    def predict_logits(self, matrix: np.ndarray) -> np.ndarray:
        logits = np.full(matrix.shape[0], self.intercept, dtype=np.float64)
        for tree in self.trees:
            logits += self.learning_rate * tree.predict(matrix)
        return logits

    def predict_probability(self, matrix: np.ndarray) -> np.ndarray:
        return np.asarray(_sigmoid(self.predict_logits(matrix)), dtype=np.float64)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intercept": self.intercept,
            "learning_rate": self.learning_rate,
            "trees": [tree.to_dict() for tree in self.trees],
            "model_name": self.model_name,
            "excluded_environment": self.excluded_environment,
            "excluded_symbol": self.excluded_symbol,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BoostedLogitModel":
        return cls(
            intercept=float(payload["intercept"]),
            learning_rate=float(payload["learning_rate"]),
            trees=[SymmetricTree.from_dict(item) for item in payload["trees"]],
            model_name=str(payload["model_name"]),
            excluded_environment=payload.get("excluded_environment"),
            excluded_symbol=payload.get("excluded_symbol"),
        )


def _candidate_thresholds(column: np.ndarray, maximum: int = 9) -> np.ndarray:
    finite = column[np.isfinite(column)]
    if finite.size < 2:
        return np.empty(0, dtype=np.float64)
    unique = np.unique(finite)
    if unique.size <= maximum + 1:
        return (unique[:-1] + unique[1:]) / 2.0
    quantiles = np.linspace(0.1, 0.9, maximum)
    return np.unique(np.quantile(finite, quantiles))


def fit_boosted_model(
    matrix: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
    *,
    model_name: str,
    excluded_environment: str | None = None,
    excluded_symbol: str | None = None,
    trees: int = 44,
    depth: int = 2,
    learning_rate: float = 0.075,
    l2_leaf: float = 5.0,
    feature_subsample: int = 56,
    seed: int = 17,
) -> BoostedLogitModel:
    if matrix.ndim != 2 or labels.shape != (matrix.shape[0],):
        raise ValueError("invalid boosted training shapes")
    if matrix.shape[0] < 20 or labels.min() == labels.max():
        raise ValueError("boosted training requires at least 20 rows and both labels")
    weights = np.asarray(weights, dtype=np.float64)
    weights = weights / max(weights.mean(), 1e-12)
    base_rate = float(np.average(labels, weights=weights))
    intercept = float(_logit(np.asarray([np.clip(base_rate, 1e-5, 1.0 - 1e-5)]))[0])
    logits = np.full(matrix.shape[0], intercept, dtype=np.float64)
    rng = np.random.default_rng(seed)
    thresholds = [_candidate_thresholds(matrix[:, feature]) for feature in range(matrix.shape[1])]
    fitted: list[SymmetricTree] = []

    for _ in range(trees):
        probability = np.asarray(_sigmoid(logits), dtype=np.float64)
        gradient = weights * (labels - probability)
        hessian = weights * probability * (1.0 - probability)
        leaf_index = np.zeros(matrix.shape[0], dtype=np.int64)
        chosen_features: list[int] = []
        chosen_thresholds: list[float] = []
        feature_count = min(feature_subsample, matrix.shape[1])
        features = rng.choice(matrix.shape[1], size=feature_count, replace=False)

        for level in range(depth):
            best_gain = -math.inf
            best_feature = None
            best_threshold = None
            current_leaves = 1 << level
            parent_score = 0.0
            for leaf in range(current_leaves):
                mask = leaf_index == leaf
                g = float(gradient[mask].sum())
                h = float(hessian[mask].sum())
                parent_score += g * g / (h + l2_leaf)
            for feature in features:
                for threshold in thresholds[int(feature)]:
                    gain_score = 0.0
                    split = matrix[:, feature] > threshold
                    valid = True
                    for leaf in range(current_leaves):
                        parent = leaf_index == leaf
                        left = parent & ~split
                        right = parent & split
                        if not left.any() or not right.any():
                            valid = False
                            break
                        for child in (left, right):
                            g = float(gradient[child].sum())
                            h = float(hessian[child].sum())
                            gain_score += g * g / (h + l2_leaf)
                    if valid:
                        gain = gain_score - parent_score
                        if gain > best_gain:
                            best_gain = gain
                            best_feature = int(feature)
                            best_threshold = float(threshold)
            if best_feature is None or best_threshold is None:
                break
            chosen_features.append(best_feature)
            chosen_thresholds.append(best_threshold)
            leaf_index |= (matrix[:, best_feature] > best_threshold).astype(np.int64) << level

        if not chosen_features:
            break
        leaf_values = np.zeros(1 << len(chosen_features), dtype=np.float64)
        for leaf in range(leaf_values.size):
            mask = leaf_index == leaf
            g = float(gradient[mask].sum())
            h = float(hessian[mask].sum())
            leaf_values[leaf] = float(np.clip(g / (h + l2_leaf), -3.0, 3.0))
        tree = SymmetricTree(tuple(chosen_features), tuple(chosen_thresholds), leaf_values)
        update = tree.predict(matrix)
        if float(np.max(np.abs(update))) < 1e-9:
            break
        logits += learning_rate * update
        fitted.append(tree)

    return BoostedLogitModel(
        intercept=intercept,
        learning_rate=learning_rate,
        trees=fitted,
        model_name=model_name,
        excluded_environment=excluded_environment,
        excluded_symbol=excluded_symbol,
    )


@dataclass(frozen=True, slots=True)
class PlattCalibrator:
    slope: float
    intercept: float

    def predict(self, probability: float | np.ndarray) -> float | np.ndarray:
        values = np.asarray(probability, dtype=np.float64)
        calibrated = _sigmoid(self.intercept + self.slope * _logit(values))
        if np.isscalar(probability):
            return float(calibrated)
        return np.asarray(calibrated, dtype=np.float64)

    @classmethod
    def fit(
        cls,
        probabilities: np.ndarray,
        labels: np.ndarray,
        weights: np.ndarray,
    ) -> "PlattCalibrator":
        if probabilities.size < 30 or labels.min() == labels.max():
            return cls(1.0, 0.0)
        x = _logit(np.asarray(probabilities, dtype=np.float64))
        y = np.asarray(labels, dtype=np.float64)
        w = np.asarray(weights, dtype=np.float64)
        slope = 1.0
        intercept = 0.0
        ridge = 0.25
        for _ in range(80):
            z = intercept + slope * x
            p = np.asarray(_sigmoid(z), dtype=np.float64)
            residual = y - p
            h = w * p * (1.0 - p)
            g0 = float(np.sum(w * residual)) - ridge * intercept
            g1 = float(np.sum(w * residual * x)) - ridge * (slope - 1.0)
            h00 = float(np.sum(h)) + ridge
            h01 = float(np.sum(h * x))
            h11 = float(np.sum(h * x * x)) + ridge
            determinant = h00 * h11 - h01 * h01
            if determinant <= 1e-12:
                break
            delta0 = (g0 * h11 - g1 * h01) / determinant
            delta1 = (g1 * h00 - g0 * h01) / determinant
            intercept += delta0
            slope += delta1
            slope = float(np.clip(slope, 0.05, 5.0))
            if max(abs(delta0), abs(delta1)) < 1e-8:
                break
        return cls(float(slope), float(intercept))

    def to_dict(self) -> dict[str, float]:
        return {"slope": self.slope, "intercept": self.intercept}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PlattCalibrator":
        return cls(float(payload["slope"]), float(payload["intercept"]))


@dataclass(frozen=True, slots=True)
class RouterDecision:
    probability_target_first: float
    probability_dispersion: float
    target_net_r: float
    stop_net_r: float
    break_even_probability: float
    expected_net_r: float
    expected_log_growth: float


@dataclass(slots=True)
class RobustPlanRouter:
    encoder: FeatureEncoder
    models: list[BoostedLogitModel]
    calibrator: PlattCalibrator
    trained_through_ns: int
    risk_fraction: float
    training_metadata: dict[str, Any]

    @property
    def dimension(self) -> int:
        return self.encoder.dimension

    def model_probabilities(self, record: Mapping[str, Any]) -> np.ndarray:
        matrix = self.encoder.vectorize(record).reshape(1, -1)
        return np.asarray(
            [model.predict_probability(matrix)[0] for model in self.models],
            dtype=np.float64,
        )

    def predict_probability(self, record: Mapping[str, Any]) -> tuple[float, float]:
        probabilities = self.model_probabilities(record)
        center = float(np.median(probabilities))
        calibrated = float(self.calibrator.predict(center))
        return float(np.clip(calibrated, 1e-6, 1.0 - 1e-6)), float(probabilities.std(ddof=0))

    def decision(self, record: Mapping[str, Any]) -> RouterDecision:
        probability, dispersion = self.predict_probability(record)
        target_net_r = _number(_lookup(record, "target_net_r"))
        stop_net_r = _number(_lookup(record, "stop_net_r"))
        break_even = _number(_lookup(record, "post_cost_break_even_target_probability"))
        if not all(math.isfinite(value) for value in (target_net_r, stop_net_r, break_even)):
            raise ValueError("missing pre-entry economic geometry")
        target_nav = 1.0 + self.risk_fraction * target_net_r
        stop_nav = 1.0 + self.risk_fraction * stop_net_r
        if target_nav <= 0.0 or stop_nav <= 0.0:
            raise ValueError("risk contract implies nonpositive NAV state")
        expected_net = probability * target_net_r + (1.0 - probability) * stop_net_r
        expected_log = probability * math.log(target_nav) + (1.0 - probability) * math.log(stop_nav)
        return RouterDecision(
            probability_target_first=probability,
            probability_dispersion=dispersion,
            target_net_r=target_net_r,
            stop_net_r=stop_net_r,
            break_even_probability=break_even,
            expected_net_r=expected_net,
            expected_log_growth=expected_log,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "encoder": self.encoder.to_dict(),
            "models": [model.to_dict() for model in self.models],
            "calibrator": self.calibrator.to_dict(),
            "trained_through_ns": self.trained_through_ns,
            "risk_fraction": self.risk_fraction,
            "training_metadata": self.training_metadata,
        }

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RobustPlanRouter":
        if payload.get("model_version") != MODEL_VERSION:
            raise ValueError(f"unsupported model version {payload.get('model_version')!r}")
        model = cls(
            encoder=FeatureEncoder.from_dict(payload["encoder"]),
            models=[BoostedLogitModel.from_dict(item) for item in payload["models"]],
            calibrator=PlattCalibrator.from_dict(payload["calibrator"]),
            trained_through_ns=int(payload["trained_through_ns"]),
            risk_fraction=float(payload["risk_fraction"]),
            training_metadata=dict(payload.get("training_metadata", {})),
        )
        if not model.models:
            raise ValueError("robust router has no ensemble members")
        return model

    @classmethod
    def load(cls, path: str | Path) -> "RobustPlanRouter":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _weighted_log_loss(labels: np.ndarray, probabilities: np.ndarray, weights: np.ndarray) -> float:
    p = np.clip(probabilities, 1e-9, 1.0 - 1e-9)
    loss = -(labels * np.log(p) + (1.0 - labels) * np.log(1.0 - p))
    return float(np.average(loss, weights=weights))


def _weighted_brier(labels: np.ndarray, probabilities: np.ndarray, weights: np.ndarray) -> float:
    return float(np.average((probabilities - labels) ** 2, weights=weights))


def train_robust_router(
    records: Sequence[Mapping[str, Any]],
    labels: Sequence[int],
    timestamps_ns: Sequence[int],
    environments: Sequence[str],
    symbols: Sequence[str],
    sample_weights: Sequence[float],
    *,
    min_category_count: int = 8,
    risk_fraction: float = DEFAULT_RISK_FRACTION,
    trees: int = 44,
    depth: int = 2,
    feature_subsample: int = 56,
    seed: int = 17,
    metadata: Mapping[str, Any] | None = None,
) -> RobustPlanRouter:
    count = len(records)
    if not (
        count == len(labels) == len(timestamps_ns) == len(environments) == len(symbols) == len(sample_weights)
    ):
        raise ValueError("training arrays must have equal length")
    if count < 80:
        raise ValueError("robust router needs at least 80 resolved plans")
    y = np.asarray(labels, dtype=np.float64)
    ts = np.asarray(timestamps_ns, dtype=np.int64)
    env = np.asarray(environments, dtype=object)
    symbol = np.asarray(symbols, dtype=object)
    weights = np.asarray(sample_weights, dtype=np.float64)
    if y.min() == y.max():
        raise ValueError("both target-first and stop-first labels are required")
    encoder = FeatureEncoder.fit(records, min_category_count=min_category_count)
    matrix = encoder.matrix(records)
    models: list[BoostedLogitModel] = []

    def fit_mask(mask: np.ndarray, name: str, **exclusion: Any) -> BoostedLogitModel | None:
        if int(mask.sum()) < 60 or np.unique(y[mask]).size < 2:
            return None
        return fit_boosted_model(
            matrix[mask],
            y[mask],
            weights[mask],
            model_name=name,
            excluded_environment=exclusion.get("excluded_environment"),
            excluded_symbol=exclusion.get("excluded_symbol"),
            trees=trees,
            depth=depth,
            feature_subsample=feature_subsample,
            seed=seed + len(models) * 101,
        )

    full = fit_mask(np.ones(count, dtype=bool), "full")
    if full is None:
        raise RuntimeError("failed to fit full robust model")
    models.append(full)
    environment_models: dict[str, BoostedLogitModel] = {}
    unique_env = sorted(set(str(value) for value in env))
    for value in unique_env:
        model = fit_mask(
            env != value,
            f"leave_environment:{value}",
            excluded_environment=value,
        )
        if model is not None:
            models.append(model)
            environment_models[value] = model
    for value in sorted(set(str(item) for item in symbol)):
        model = fit_mask(
            symbol != value,
            f"leave_symbol:{value}",
            excluded_symbol=value,
        )
        if model is not None:
            models.append(model)

    oof = np.full(count, np.nan, dtype=np.float64)
    for value, model in environment_models.items():
        mask = env == value
        oof[mask] = model.predict_probability(matrix[mask])
    valid_oof = np.isfinite(oof)
    if int(valid_oof.sum()) >= 30 and np.unique(y[valid_oof]).size == 2:
        calibrator = PlattCalibrator.fit(oof[valid_oof], y[valid_oof], weights[valid_oof])
        calibrated_oof = np.asarray(calibrator.predict(oof[valid_oof]), dtype=np.float64)
        oof_metrics = {
            "rows": int(valid_oof.sum()),
            "log_loss_raw": _weighted_log_loss(y[valid_oof], oof[valid_oof], weights[valid_oof]),
            "log_loss_calibrated": _weighted_log_loss(y[valid_oof], calibrated_oof, weights[valid_oof]),
            "brier_raw": _weighted_brier(y[valid_oof], oof[valid_oof], weights[valid_oof]),
            "brier_calibrated": _weighted_brier(y[valid_oof], calibrated_oof, weights[valid_oof]),
        }
    else:
        calibrator = PlattCalibrator(1.0, 0.0)
        oof_metrics = {"rows": int(valid_oof.sum()), "status": "insufficient_environment_oof"}

    training_metadata = {
        "rows": count,
        "target_first": int(y.sum()),
        "stop_or_ambiguous": int(count - y.sum()),
        "environments": {value: int((env == value).sum()) for value in unique_env},
        "symbols_for_weighting_only": {
            value: int((symbol == value).sum()) for value in sorted(set(str(item) for item in symbol))
        },
        "ensemble_members": [model.model_name for model in models],
        "environment_held_out_calibration": oof_metrics,
        "feature_dimension": encoder.dimension,
        "numeric_feature_count": len(NUMERIC_FEATURES),
        "categorical_feature_count": len(CATEGORICAL_FEATURES),
        "symbol_and_calendar_features_forbidden": True,
        "selection_objective": "MAXIMUM_POSITIVE_EXPECTED_LOG_NAV_GROWTH_AT_FIXED_3_PERCENT_STOP_RISK",
        **dict(metadata or {}),
    }
    return RobustPlanRouter(
        encoder=encoder,
        models=models,
        calibrator=calibrator,
        trained_through_ns=int(ts.max()),
        risk_fraction=float(risk_fraction),
        training_metadata=training_metadata,
    )
