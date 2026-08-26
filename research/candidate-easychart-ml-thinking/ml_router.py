"""Causal post-cost plan selector for the EasyChart ML-thinking branch.

The deterministic EasyChart engines remain responsible for market direction,
entry, structural invalidation and target. This module learns only the
conditional probability that the predeclared target is reached before the
predeclared stop, using information available when the plan is emitted.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

MODEL_VERSION = "easychart-ml-thinking-logit-v1"
MISSING_CATEGORY = "__MISSING__"
OTHER_CATEGORY = "__OTHER__"
NS_PER_MINUTE = 60_000_000_000

NUMERIC_FEATURES: tuple[str, ...] = (
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
    "trace_flow_activity_ratio",
    "trace_flow_trade_count_ratio",
    "trace_flow_trade_size_ratio",
    "trace_flow_delta_share",
    "trace_flow_delta_ratio",
    "trace_flow_body_ratio",
    "trace_flow_range_ratio",
    "trace_flow_close_location",
    "trace_flow_impact_per_activity",
    "trace_flow_episode_bars",
    "trace_flow_episode_cumulative_delta",
    "trace_flow_episode_net_price_progress",
)

CATEGORICAL_FEATURES: tuple[str, ...] = (
    "family",
    "scenario_path",
    "side",
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
)

ROW_ALIASES: dict[str, tuple[str, ...]] = {
    "target_net_r": ("counterfactual_target_net_r",),
    "stop_net_r": ("counterfactual_stop_net_r",),
    "post_cost_break_even_target_probability": (
        "post_cost_break_even_target_probability",
    ),
    "zero_drift_target_first_prior": ("zero_drift_target_first_prior",),
    "post_cost_reward_risk": ("post_cost_reward_risk",),
    "required_target_probability_premium": (
        "required_target_probability_premium",
    ),
}


def _plain(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "name") and not isinstance(value, str):
        return value.name
    return value


def _number(value: Any) -> float:
    value = _plain(value)
    if value is None:
        return float("nan")
    try:
        result = float(value)
    except (TypeError, ValueError):
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
    return None


def _trace_value(trace: Mapping[str, Any] | None, feature_name: str) -> Any:
    if trace is None:
        return None
    if feature_name in trace:
        return trace[feature_name]
    return trace.get(feature_name.removeprefix("trace_"))


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
    """Return known pre-entry target and stop economics in risk units."""

    side_text = _category(side).upper()
    sign = 1.0 if side_text == "LONG" else -1.0 if side_text == "SHORT" else 0.0
    if sign == 0.0:
        raise ValueError(f"unknown side {side!r}")
    risk = abs(float(entry) - float(stop))
    reward = abs(float(target) - float(entry))
    if risk <= 0.0 or reward <= 0.0 or entry == 0.0:
        raise ValueError("nonpositive plan geometry")
    tick = float(tick_size)

    def net_r(exit_price: float, exit_slippage_ticks: int) -> float:
        actual_entry = float(entry) + sign * int(entry_slippage_ticks) * tick
        actual_exit = float(exit_price) - sign * int(exit_slippage_ticks) * tick
        gross = sign * (actual_exit - actual_entry) / risk
        fees = (
            float(entry_fee_rate) * abs(actual_entry)
            + float(exit_fee_rate) * abs(actual_exit)
        ) / risk
        return float(gross - fees)

    target_net_r = net_r(float(target), int(target_slippage_ticks))
    stop_net_r = net_r(float(stop), int(stop_slippage_ticks))
    denominator = target_net_r - stop_net_r
    break_even = -stop_net_r / denominator if denominator > 0.0 else float("nan")
    zero_drift = risk / (risk + reward)
    return {
        "risk_bps": 10_000.0 * risk / abs(float(entry)),
        "target_bps": 10_000.0 * reward / abs(float(entry)),
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


def live_feature_record(
    plan: Any,
    *,
    trace: Mapping[str, Any] | None,
    economics: Mapping[str, float],
) -> dict[str, Any]:
    """Build the exact pre-entry feature dictionary used by live/backtest routing."""

    entry = float(plan.entry)
    stop = float(plan.stop)
    risk = abs(entry - stop)
    overlap_lower = float(plan.overlap_lower)
    overlap_upper = float(plan.overlap_upper)
    width = max(0.0, overlap_upper - overlap_lower)
    observed_ns = int(plan.observed_time_ns)
    output: dict[str, Any] = {
        "gross_rr": float(plan.gross_rr),
        **dict(economics),
        "overlap_width_r": width / risk if risk > 0.0 else float("nan"),
        "entry_location_in_overlap": (
            (entry - overlap_lower) / width if width > 0.0 else 0.5
        ),
        "higher_strength_ratio": float(plan.higher_strength_ratio),
        "lower_strength_ratio": float(plan.lower_strength_ratio),
        "trigger_strength_ratio": float(plan.trigger_strength_ratio),
        "source_rule_count": float(plan.source_rule_count),
        "setup_age_minutes": max(
            0.0,
            (observed_ns - int(plan.setup_observed_time_ns)) / NS_PER_MINUTE,
        ),
        "interaction_age_minutes": max(
            0.0,
            (observed_ns - int(plan.interaction_time_ns)) / NS_PER_MINUTE,
        ),
        "trigger_age_minutes": max(
            0.0,
            (observed_ns - int(plan.trigger_time_ns)) / NS_PER_MINUTE,
        ),
        "higher_timeframe_minutes": float(plan.higher_timeframe_minutes),
        "decision_timeframe_minutes": float(plan.decision_timeframe_minutes),
        "trigger_timeframe_minutes": float(plan.trigger_timeframe_minutes),
        "family": plan.family,
        "scenario_path": plan.scenario_path,
        "side": plan.side,
        "scale_name": plan.scale_name,
        "higher_zone_kind": plan.higher_zone_kind,
        "lower_zone_kind": plan.lower_zone_kind,
        "trigger_zone_kind": plan.trigger_zone_kind,
        "target_zone_kind": plan.target_zone_kind,
    }
    for name in NUMERIC_FEATURES:
        if name.startswith("trace_"):
            output[name] = _trace_value(trace, name)
    for name in CATEGORICAL_FEATURES:
        if name.startswith("trace_"):
            output[name] = _trace_value(trace, name)
    return output


def row_feature_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one counterfactual CSV row to the live feature schema."""

    output = {
        name: _lookup(row, name)
        for name in NUMERIC_FEATURES + CATEGORICAL_FEATURES
    }
    entry = _number(row.get("entry"))
    stop = _number(row.get("stop"))
    overlap_lower = _number(row.get("overlap_lower"))
    overlap_upper = _number(row.get("overlap_upper"))
    risk = abs(entry - stop)
    width = overlap_upper - overlap_lower
    if not math.isfinite(_number(output.get("overlap_width_r"))):
        output["overlap_width_r"] = (
            width / risk if risk > 0.0 and width >= 0.0 else float("nan")
        )
    if not math.isfinite(_number(output.get("entry_location_in_overlap"))):
        output["entry_location_in_overlap"] = (
            (entry - overlap_lower) / width if width > 0.0 else 0.5
        )
    observed_ns = _number(row.get("observed_time_ns"))
    if not math.isfinite(observed_ns):
        observed_ns = _number(row.get("ts_ns"))
    for target_name, source_name in (
        ("setup_age_minutes", "setup_observed_time_ns"),
        ("interaction_age_minutes", "interaction_time_ns"),
        ("trigger_age_minutes", "trigger_time_ns"),
    ):
        if not math.isfinite(_number(output.get(target_name))):
            source = _number(row.get(source_name))
            output[target_name] = (
                max(0.0, (observed_ns - source) / NS_PER_MINUTE)
                if math.isfinite(observed_ns) and math.isfinite(source)
                else float("nan")
            )
    return output


@dataclass(frozen=True, slots=True)
class RouterDecision:
    probability_target_first: float
    target_net_r: float
    stop_net_r: float
    break_even_probability: float
    expected_net_r: float
    probability_edge: float


@dataclass(slots=True)
class CausalLogitRouter:
    numeric_features: tuple[str, ...]
    categorical_features: tuple[str, ...]
    medians: dict[str, float]
    scales: dict[str, float]
    category_levels: dict[str, tuple[str, ...]]
    intercept: float
    coefficients: np.ndarray
    trained_through_ns: int
    training_metadata: dict[str, Any]

    @property
    def dimension(self) -> int:
        return len(self.numeric_features) + sum(
            len(self.category_levels[name]) for name in self.categorical_features
        )

    def vectorize(self, record: Mapping[str, Any]) -> np.ndarray:
        values: list[float] = []
        for name in self.numeric_features:
            value = _number(_lookup(record, name))
            if not math.isfinite(value):
                value = self.medians[name]
            values.append((value - self.medians[name]) / self.scales[name])
        for name in self.categorical_features:
            value = _category(_lookup(record, name))
            levels = self.category_levels[name]
            if value not in levels:
                value = OTHER_CATEGORY
            values.extend(1.0 if value == level else 0.0 for level in levels)
        vector = np.asarray(values, dtype=np.float64)
        if vector.shape != (self.dimension,):
            raise RuntimeError(
                f"feature dimension mismatch: {vector.shape} != {(self.dimension,)}",
            )
        return vector

    def predict_probability(self, record: Mapping[str, Any]) -> float:
        score = float(self.intercept + self.vectorize(record) @ self.coefficients)
        if score >= 0.0:
            return float(1.0 / (1.0 + math.exp(-min(score, 700.0))))
        exp_score = math.exp(max(score, -700.0))
        return float(exp_score / (1.0 + exp_score))

    def decision(self, record: Mapping[str, Any]) -> RouterDecision:
        probability = self.predict_probability(record)
        target_net_r = _number(_lookup(record, "target_net_r"))
        stop_net_r = _number(_lookup(record, "stop_net_r"))
        break_even = _number(
            _lookup(record, "post_cost_break_even_target_probability"),
        )
        if not all(math.isfinite(v) for v in (target_net_r, stop_net_r, break_even)):
            raise ValueError("missing economic geometry in router record")
        expected = probability * target_net_r + (1.0 - probability) * stop_net_r
        return RouterDecision(
            probability_target_first=probability,
            target_net_r=target_net_r,
            stop_net_r=stop_net_r,
            break_even_probability=break_even,
            expected_net_r=expected,
            probability_edge=probability - break_even,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "numeric_features": list(self.numeric_features),
            "categorical_features": list(self.categorical_features),
            "medians": self.medians,
            "scales": self.scales,
            "category_levels": {
                key: list(value) for key, value in self.category_levels.items()
            },
            "intercept": self.intercept,
            "coefficients": self.coefficients.tolist(),
            "trained_through_ns": self.trained_through_ns,
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
    def from_dict(cls, payload: Mapping[str, Any]) -> "CausalLogitRouter":
        if payload.get("model_version") != MODEL_VERSION:
            raise ValueError(
                f"unsupported model version {payload.get('model_version')!r}",
            )
        model = cls(
            numeric_features=tuple(payload["numeric_features"]),
            categorical_features=tuple(payload["categorical_features"]),
            medians={key: float(value) for key, value in payload["medians"].items()},
            scales={key: float(value) for key, value in payload["scales"].items()},
            category_levels={
                key: tuple(value) for key, value in payload["category_levels"].items()
            },
            intercept=float(payload["intercept"]),
            coefficients=np.asarray(payload["coefficients"], dtype=np.float64),
            trained_through_ns=int(payload["trained_through_ns"]),
            training_metadata=dict(payload.get("training_metadata", {})),
        )
        if model.coefficients.shape != (model.dimension,):
            raise ValueError(
                f"invalid coefficient shape {model.coefficients.shape}; expected {(model.dimension,)}",
            )
        return model

    @classmethod
    def load(cls, path: str | Path) -> "CausalLogitRouter":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)


def _robust_location_scale(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return 0.0, 1.0
    median = float(np.median(finite))
    q25, q75 = np.quantile(finite, [0.25, 0.75])
    scale = float(q75 - q25)
    if not math.isfinite(scale) or scale < 1e-9:
        mad = float(np.median(np.abs(finite - median)))
        scale = 1.4826 * mad
    if not math.isfinite(scale) or scale < 1e-9:
        scale = 1.0
    return median, scale


def build_schema(
    records: Sequence[Mapping[str, Any]],
    *,
    min_category_count: int = 5,
) -> tuple[dict[str, float], dict[str, float], dict[str, tuple[str, ...]]]:
    medians: dict[str, float] = {}
    scales: dict[str, float] = {}
    for name in NUMERIC_FEATURES:
        values = np.asarray([_number(_lookup(record, name)) for record in records])
        medians[name], scales[name] = _robust_location_scale(values)
    category_levels: dict[str, tuple[str, ...]] = {}
    for name in CATEGORICAL_FEATURES:
        counts: dict[str, int] = {}
        for record in records:
            value = _category(_lookup(record, name))
            counts[value] = counts.get(value, 0) + 1
        levels = sorted(
            value
            for value, count in counts.items()
            if count >= min_category_count and value != OTHER_CATEGORY
        )
        if MISSING_CATEGORY not in levels:
            levels.append(MISSING_CATEGORY)
        if OTHER_CATEGORY not in levels:
            levels.append(OTHER_CATEGORY)
        category_levels[name] = tuple(levels)
    return medians, scales, category_levels


def _design_matrix(
    records: Sequence[Mapping[str, Any]],
    medians: Mapping[str, float],
    scales: Mapping[str, float],
    category_levels: Mapping[str, tuple[str, ...]],
) -> np.ndarray:
    shell = CausalLogitRouter(
        numeric_features=NUMERIC_FEATURES,
        categorical_features=CATEGORICAL_FEATURES,
        medians=dict(medians),
        scales=dict(scales),
        category_levels=dict(category_levels),
        intercept=0.0,
        coefficients=np.zeros(
            len(NUMERIC_FEATURES) + sum(len(v) for v in category_levels.values()),
        ),
        trained_through_ns=0,
        training_metadata={},
    )
    return np.vstack([shell.vectorize(record) for record in records])


def _sigmoid(values: np.ndarray) -> np.ndarray:
    output = np.empty_like(values, dtype=np.float64)
    positive = values >= 0.0
    output[positive] = 1.0 / (1.0 + np.exp(-np.minimum(values[positive], 700.0)))
    exp_values = np.exp(np.maximum(values[~positive], -700.0))
    output[~positive] = exp_values / (1.0 + exp_values)
    return output


def fit_weighted_logit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    sample_weight: np.ndarray | None = None,
    ridge: float = 1.0,
    max_iterations: int = 80,
    tolerance: float = 1e-8,
) -> tuple[float, np.ndarray, int]:
    if x.ndim != 2 or y.shape != (x.shape[0],):
        raise ValueError("invalid design/label shape")
    if x.shape[0] == 0 or np.unique(y).size < 2:
        raise ValueError("training requires both target-first and stop-first examples")
    weights = (
        np.ones(x.shape[0], dtype=np.float64)
        if sample_weight is None
        else np.asarray(sample_weight, dtype=np.float64)
    )
    if weights.shape != (x.shape[0],) or np.any(weights <= 0.0):
        raise ValueError("sample weights must be positive and match rows")
    augmented = np.column_stack([np.ones(x.shape[0]), x])
    beta = np.zeros(augmented.shape[1], dtype=np.float64)
    penalty = np.full(beta.shape[0], float(ridge), dtype=np.float64)
    penalty[0] = 0.0
    for iteration in range(1, max_iterations + 1):
        probability = _sigmoid(augmented @ beta)
        curvature = np.maximum(probability * (1.0 - probability), 1e-7) * weights
        gradient = augmented.T @ ((probability - y) * weights) + penalty * beta
        hessian = (augmented.T * curvature) @ augmented
        hessian.flat[:: hessian.shape[0] + 1] += penalty + 1e-9
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        beta -= step
        if float(np.max(np.abs(step))) < tolerance:
            return float(beta[0]), beta[1:], iteration
    return float(beta[0]), beta[1:], max_iterations


def weighted_log_loss(y: np.ndarray, p: np.ndarray, weight: np.ndarray) -> float:
    p = np.clip(p, 1e-9, 1.0 - 1e-9)
    losses = -(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))
    return float(np.average(losses, weights=weight))


def train_router(
    records: Sequence[Mapping[str, Any]],
    labels: Sequence[int | bool],
    timestamps_ns: Sequence[int],
    *,
    sample_weights: Sequence[float] | None = None,
    ridge_grid: Sequence[float] = (0.1, 1.0, 10.0, 100.0),
    min_category_count: int = 5,
    cv_folds: int = 3,
    metadata: Mapping[str, Any] | None = None,
) -> CausalLogitRouter:
    if len(records) != len(labels) or len(records) != len(timestamps_ns):
        raise ValueError("records, labels and timestamps must have equal length")
    if len(records) < 30:
        raise ValueError("at least 30 resolved plans are required")
    order = np.argsort(np.asarray(timestamps_ns, dtype=np.int64), kind="stable")
    ordered_records = [records[int(index)] for index in order]
    y = np.asarray(labels, dtype=np.float64)[order]
    ts = np.asarray(timestamps_ns, dtype=np.int64)[order]
    weights = (
        np.ones(len(records), dtype=np.float64)
        if sample_weights is None
        else np.asarray(sample_weights, dtype=np.float64)[order]
    )
    medians, scales, category_levels = build_schema(
        ordered_records,
        min_category_count=min_category_count,
    )
    x = _design_matrix(ordered_records, medians, scales, category_levels)

    fold_boundaries = np.linspace(0, len(records), cv_folds + 2, dtype=int)
    ridge_scores: dict[float, list[float]] = {float(r): [] for r in ridge_grid}
    for fold in range(cv_folds):
        train_end = int(fold_boundaries[fold + 1])
        valid_end = int(fold_boundaries[fold + 2])
        if train_end < 20 or valid_end <= train_end or np.unique(y[:train_end]).size < 2:
            continue
        for ridge in ridge_grid:
            intercept, coefficients, _ = fit_weighted_logit(
                x[:train_end],
                y[:train_end],
                sample_weight=weights[:train_end],
                ridge=float(ridge),
            )
            prediction = _sigmoid(intercept + x[train_end:valid_end] @ coefficients)
            ridge_scores[float(ridge)].append(
                weighted_log_loss(
                    y[train_end:valid_end],
                    prediction,
                    weights[train_end:valid_end],
                ),
            )
    usable = {
        ridge: float(np.mean(scores))
        for ridge, scores in ridge_scores.items()
        if scores
    }
    selected_ridge = min(usable, key=usable.get) if usable else float(ridge_grid[0])
    intercept, coefficients, iterations = fit_weighted_logit(
        x,
        y,
        sample_weight=weights,
        ridge=selected_ridge,
    )
    fitted = _sigmoid(intercept + x @ coefficients)
    training_metadata = {
        "model_version": MODEL_VERSION,
        "rows": int(len(records)),
        "positive_rate": float(np.average(y, weights=weights)),
        "selected_ridge": selected_ridge,
        "cv_log_loss_by_ridge": {str(k): v for k, v in usable.items()},
        "in_sample_weighted_log_loss": weighted_log_loss(y, fitted, weights),
        "in_sample_weighted_brier": float(np.average((fitted - y) ** 2, weights=weights)),
        "iterations": iterations,
        **dict(metadata or {}),
    }
    return CausalLogitRouter(
        numeric_features=NUMERIC_FEATURES,
        categorical_features=CATEGORICAL_FEATURES,
        medians=medians,
        scales=scales,
        category_levels=category_levels,
        intercept=intercept,
        coefficients=coefficients,
        trained_through_ns=int(ts.max()),
        training_metadata=training_metadata,
    )
