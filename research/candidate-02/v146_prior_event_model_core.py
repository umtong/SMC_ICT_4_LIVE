"""Prior-only event-success model for the causal v142 dual-auction signals.

The model is signal selection, not a backtest engine.  Candidate route signals
are built causally by v142.  Historical labels use only bars after each old
signal and are admitted to training only when their complete holding horizon
ended before the evaluation week.  A frozen regularized logistic model then
filters the evaluation signals.  NautilusTrader remains the sole execution,
fee, position, and NAV engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
import pandas as pd

from v53_nt_core import CostConfig, RotationSignal
from v142_dual_auction_core import (
    DualAuctionConfig,
    build_rotation_signals as _build_dual_signals,
    build_state as _build_dual_state,
)

UTC = "UTC"


@dataclass(frozen=True, slots=True)
class PriorEventModelConfig(DualAuctionConfig):
    model_history_days: int = 120
    model_min_training_samples: int = 60
    model_probability_threshold: float = 0.60
    model_l2: float = 0.50
    model_iterations: int = 500
    model_purge_minutes: int = 60

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "PriorEventModelConfig":
        data = dict(values)
        data["auction_windows_5m"] = tuple(int(value) for value in data["auction_windows_5m"])
        allowed = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - allowed)
        if unknown:
            raise ValueError(f"unknown v146 config keys: {unknown}")
        return cls(**data)

    def __post_init__(self) -> None:
        DualAuctionConfig.__post_init__(self)
        if self.model_history_days < 30:
            raise ValueError("v146 history must span at least 30 days")
        if self.model_min_training_samples < 30:
            raise ValueError("v146 requires at least 30 training events")
        if not 0.5 <= self.model_probability_threshold < 1:
            raise ValueError("v146 probability threshold must be in [0.5,1)")
        if self.model_l2 < 0 or self.model_iterations <= 0:
            raise ValueError("v146 optimizer settings are invalid")
        if self.model_purge_minutes < self.maximum_holding_minutes:
            raise ValueError("v146 purge must cover the full maximum holding period")


def build_state(features: pd.DataFrame, config: PriorEventModelConfig) -> pd.DataFrame:
    state = _build_dual_state(features, config)
    close = state["close"].replace(0.0, np.nan)
    state["v146_return_1h"] = close / close.shift(12) - 1.0
    state["v146_return_4h"] = close / close.shift(48) - 1.0
    path_4h = close.diff().abs().rolling(48, min_periods=48).sum()
    state["v146_efficiency_4h"] = (close - close.shift(48)).abs() / path_4h.replace(0.0, np.nan)
    return state


def _normalize(value: pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(value)
    return value.tz_localize(UTC) if value.tzinfo is None else value.tz_convert(UTC)


def _feature_vector(signal: RotationSignal, state: pd.DataFrame) -> np.ndarray | None:
    feature_time = pd.Timestamp(signal.source_feature_open_time_ns, unit="ns", tz="UTC")
    if feature_time not in state.index:
        return None
    row = state.loc[feature_time]
    details = signal.details
    width_threshold = float(details.get("width_threshold", math.nan))
    efficiency_threshold = float(details.get("efficiency_threshold", math.nan))
    flow_cap = float(details.get("flow_cap", math.nan))
    vpin_threshold = float(details.get("vpin_threshold", math.nan))
    oi_threshold = float(details.get("oi_abs_threshold", math.nan))
    denominators = (width_threshold, efficiency_threshold, flow_cap, vpin_threshold, oi_threshold)
    if any(not math.isfinite(value) or value <= 0 for value in denominators):
        return None
    timestamp = pd.Timestamp(signal.observed_time_ns, unit="ns", tz="UTC")
    hour_angle = 2.0 * math.pi * (timestamp.hour * 60 + timestamp.minute) / 1440.0
    values = np.array([
        1.0 if details.get("competition_result") == "CONTINUATION" else 0.0,
        float(signal.cost_after_reward_risk),
        abs(float(details.get("z_previous", math.nan))),
        float(details.get("auction_width_pct", math.nan)) / width_threshold,
        float(details.get("auction_efficiency", math.nan)) / efficiency_threshold,
        float(details.get("excursion_flow", math.nan)) / flow_cap,
        float(details.get("confirmation_return", math.nan)),
        float(details.get("confirmation_depth", math.nan)),
        float(details.get("vpin", math.nan)) / vpin_threshold,
        abs(float(details.get("oi_change_1h", math.nan))) / oi_threshold,
        float(row.get("v146_return_1h", math.nan)),
        float(row.get("v146_return_4h", math.nan)),
        float(row.get("v146_efficiency_4h", math.nan)),
        math.sin(hour_angle),
        math.cos(hour_angle),
    ], dtype=float)
    return values if np.isfinite(values).all() else None


def _conservative_label(
    signal: RotationSignal,
    raw: pd.DataFrame,
) -> tuple[int, int]:
    start = pd.Timestamp(signal.observed_time_ns, unit="ns", tz="UTC")
    horizon = start + pd.Timedelta(minutes=signal.max_hold_minutes)
    bars = raw.loc[(raw.index > start) & (raw.index <= horizon)]
    label = 0
    resolution = horizon
    for timestamp, bar in bars.iterrows():
        high = float(bar["high"])
        low = float(bar["low"])
        if signal.side == "BUY":
            target_hit = high >= signal.target_price
            stop_hit = low <= signal.stop_price
        else:
            target_hit = low <= signal.target_price
            stop_hit = high >= signal.stop_price
        # Ambiguous one-minute bars are labeled as losses.  This cannot make
        # the classifier look better than the executable engine.
        if stop_hit:
            label, resolution = 0, timestamp
            break
        if target_hit:
            label, resolution = 1, timestamp
            break
    return label, int(resolution.value)


def _fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    *,
    l2: float,
    iterations: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-9] = 1.0
    z = (x - mean) / scale
    z = np.column_stack([np.ones(len(z)), z])
    weight = np.zeros(z.shape[1], dtype=float)
    positives = max(1, int(y.sum()))
    negatives = max(1, int(len(y) - y.sum()))
    sample_weight = np.where(y > 0.5, 0.5 / positives, 0.5 / negatives)
    sample_weight *= len(y)
    regularizer = np.ones_like(weight)
    regularizer[0] = 0.0
    for iteration in range(iterations):
        score = np.clip(z @ weight, -35.0, 35.0)
        probability = 1.0 / (1.0 + np.exp(-score))
        gradient = z.T @ (sample_weight * (probability - y)) / len(y)
        gradient += l2 * regularizer * weight / len(y)
        step = 0.20 / math.sqrt(1.0 + iteration / 50.0)
        weight -= step * gradient
    return weight, mean, scale


def _predict(vector: np.ndarray, model: tuple[np.ndarray, np.ndarray, np.ndarray]) -> float:
    weight, mean, scale = model
    z = np.concatenate([[1.0], (vector - mean) / scale])
    score = float(np.clip(z @ weight, -35.0, 35.0))
    return 1.0 / (1.0 + math.exp(-score))


def build_rotation_signals(
    *,
    state: pd.DataFrame,
    raw: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: PriorEventModelConfig,
    costs: CostConfig,
) -> list[RotationSignal]:
    start = _normalize(evaluation_start)
    end = _normalize(evaluation_end)
    history_start = max(
        _normalize(state.index.min()) + pd.Timedelta(days=5),
        start - pd.Timedelta(days=config.model_history_days),
    )
    candidates = _build_dual_signals(
        state=state,
        raw=raw,
        evaluation_start=history_start,
        evaluation_end=end,
        config=config,
        costs=costs,
    )

    training_x: list[np.ndarray] = []
    training_y: list[int] = []
    training_resolution: list[int] = []
    last_admitted_ns: int | None = None
    purge_ns = config.model_purge_minutes * 60_000_000_000
    for signal in candidates:
        if signal.observed_time_ns >= int(start.value):
            continue
        if last_admitted_ns is not None and signal.observed_time_ns - last_admitted_ns < purge_ns:
            continue
        vector = _feature_vector(signal, state)
        if vector is None:
            continue
        label, resolution_ns = _conservative_label(signal, raw)
        if resolution_ns >= int(start.value):
            continue
        training_x.append(vector)
        training_y.append(label)
        training_resolution.append(resolution_ns)
        last_admitted_ns = signal.observed_time_ns

    if len(training_x) < config.model_min_training_samples:
        return []
    y = np.asarray(training_y, dtype=float)
    if y.min() == y.max():
        return []
    x = np.vstack(training_x)
    model = _fit_logistic(x, y, l2=config.model_l2, iterations=config.model_iterations)
    base_rate = float(y.mean())

    result: list[RotationSignal] = []
    for signal in candidates:
        if not int(start.value) <= signal.observed_time_ns < int(end.value):
            continue
        vector = _feature_vector(signal, state)
        if vector is None:
            continue
        probability = _predict(vector, model)
        if probability < config.model_probability_threshold:
            continue
        signal.details.update({
            "v146_model_training_samples":len(training_x),
            "v146_model_training_wins":int(y.sum()),
            "v146_model_training_base_rate":base_rate,
            "v146_model_latest_resolution_ns":max(training_resolution),
            "v146_model_probability":probability,
            "v146_model_probability_threshold":config.model_probability_threshold,
            "v146_model_history_days":config.model_history_days,
            "v146_model_frozen_at_ns":int(start.value),
        })
        if max(training_resolution) >= int(start.value):
            raise AssertionError("v146 training label crosses evaluation start")
        result.append(signal)
    return result
