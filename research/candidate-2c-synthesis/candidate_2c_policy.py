#!/usr/bin/env python3
"""Candidate 2c causal event/action-value policy.

The harvester emits immutable entry/stop/target plans at event-time states.  This module
solves the missing decision problem without looking ahead:

* estimate passive fill, post-fill resolution, and target-before-stop separately;
* price every complete plan by post-cost expected logarithmic account growth;
* carry only past ownership evidence forward through an auction episode;
* compare all executable actions available at the current state with cash;
* arm the first positive state in an episode, never the best future state;
* arbitrate BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT through one live order or position.

Labels may use future bars because they describe what happened to an already immutable
order.  Features, event-state ranking and routing use information available no later than
``order_time_ns``.
"""
from __future__ import annotations

import argparse
import json
import math
import hashlib
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-12
NS_MINUTE = 60_000_000_000
NS_DAY = 86_400_000_000_000

# Absolute prices, identifiers, labels and post-decision timestamps must not enter ML.
EXACT_EXCLUDE = {
    "symbol", "period", "action_id", "state_id", "episode_id",
    "event_id", "event_state_id", "order_time_ns", "departure_time_ns",
    "entry", "stop", "target", "route_price", "source_price", "boundary_price",
    "actual_entry", "actual_target_net_r", "actual_stop_net_r", "actual_gross_rr",
}
LEAK_TOKENS = (
    "outcome", "fill_state", "filled", "resolved", "resolution_", "terminal_",
    "order_terminal", "fill_time", "fill_index", "entry_wait", "holding_",
    "net_r", "mfe", "mae", "actual_", "future_", "label", "selected_",
    "ending_nav", "drawdown", "diagnostic_", "oracle", "known_actions",
)
CATEGORICAL_CANDIDATES = (
    "side", "family", "entry_geometry", "entry_style", "route_kind",
    "auction_phase", "event_type", "decision_stage", "setup_kind",
    "location_kind", "source_pool_kind", "objective_kind", "profile_state",
    "shock_state", "state_regime", "narrative_branch", "session",
    "decision_weekday",
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(0.0).ne(0.0)
    values = series.astype(str).str.strip().str.lower()
    return values.isin({"1", "true", "t", "yes", "y"})


def _first_existing(frame: pd.DataFrame, names: Iterable[str]) -> str | None:
    return next((name for name in names if name in frame.columns), None)


def _series(frame: pd.DataFrame, names: str | Sequence[str], default: Any = np.nan) -> pd.Series:
    candidates = (names,) if isinstance(names, str) else tuple(names)
    for name in candidates:
        if name in frame.columns:
            return frame[name]
    return pd.Series(default, index=frame.index)


def _action_paths(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    preferred = sorted(root.rglob("departure_actions.csv.gz"))
    if preferred:
        return preferred
    for name in ("actions.csv.gz", "actions.csv", "departure_actions.csv"):
        found = sorted(root.rglob(name))
        if found:
            return found
    raise FileNotFoundError(f"No aggregate action file below {root}")


def _period_from_path(path: Path, root: Path) -> str:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    ignored = {"development", "fresh", "artifacts", "actions", "output"}
    for part in reversed(parts[:-1]):
        low = part.lower()
        if low not in ignored and not low.startswith("candidate-2c-"):
            return part
        if low.startswith("candidate-2c-"):
            return part.removeprefix("candidate-2c-")
    return path.parent.name or "period"


def load_actions(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in _action_paths(root):
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        if "period" not in frame:
            frame["period"] = _period_from_path(path, root)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True, sort=False)
    time_col = _first_existing(
        frame,
        ("order_time_ns", "emission_time_ns", "departure_time_ns", "interaction_time_ns"),
    )
    if time_col is None:
        raise ValueError("No causal order-time column in action tables")
    frame["order_time_ns"] = pd.to_numeric(frame[time_col], errors="coerce")
    frame = frame[frame.order_time_ns.notna()].copy()
    frame["order_time_ns"] = frame.order_time_ns.astype("int64")
    for required in ("symbol", "side"):
        if required not in frame:
            raise ValueError(f"Missing required column: {required}")
    if "family" not in frame:
        frame["family"] = frame.get("narrative_branch", "UNKNOWN")
    frame["symbol"] = frame.symbol.astype(str)
    frame["side"] = frame.side.astype(str).str.upper()
    frame["family"] = frame.family.astype(str)
    frame["period"] = frame.period.astype(str)
    # Aggregate files from overlapping artifact layouts can be downloaded twice.
    dedupe = [c for c in ("period", "action_id") if c in frame]
    if len(dedupe) == 2:
        frame = frame.drop_duplicates(dedupe, keep="last")
    return prepare_choice_sets(frame)


def prepare_choice_sets(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    ts = pd.to_datetime(data.order_time_ns, unit="ns", utc=True)
    minute = ts.dt.hour * 60 + ts.dt.minute
    data["decision_time_sin"] = np.sin(2.0 * np.pi * minute / 1440.0)
    data["decision_time_cos"] = np.cos(2.0 * np.pi * minute / 1440.0)
    data["decision_weekday"] = ts.dt.dayofweek.astype(str)

    if "episode_id" in data:
        episode = data.episode_id.fillna("").astype(str)
        missing = episode.eq("") | episode.eq("nan")
    else:
        episode = pd.Series("", index=data.index)
        missing = pd.Series(True, index=data.index)
    # Fallback is stable at source-event scale, not a future-dependent price cluster.
    fallback = (
        data.symbol.astype(str)
        + ":"
        + (pd.to_numeric(_series(data, ("diagnostic_event_time_ns", "order_time_ns")), errors="coerce")
           .fillna(data.order_time_ns).astype("int64")).astype(str)
        + ":"
        + _series(data, "source_pool_kind", "NA").astype(str)
    )
    episode = episode.where(~missing, fallback)
    data["event_id"] = data.period.astype(str) + ":" + episode

    if "state_id" in data:
        state = data.state_id.fillna("").astype(str)
        state_missing = state.eq("") | state.eq("nan")
    else:
        state = pd.Series("", index=data.index)
        state_missing = pd.Series(True, index=data.index)
    state_fallback = data.event_id + ":" + data.order_time_ns.astype(str)
    state = state.where(~state_missing, state_fallback)
    data["event_state_id"] = data.period.astype(str) + ":" + state

    # Relative evidence is calculated only among plans that coexist at the same state.
    relative_candidates = (
        "gross_rr", "planned_target_net_r", "route_rr", "risk_bps",
        "auction_route_headroom_r", "source_strength_ratio", "source_semantic_weight",
        "arm_activity_ratio", "arm_flow_share_signed", "arm_path_efficiency",
        "auction_acceptance_strength", "auction_failure_pressure", "auction_effort_result",
        "liquidity_attraction_normalized", "dealing_range_position",
        "common_return_5m", "common_breadth", "basis_change_3m_bps",
        "mark_basis_change_3m_bps", "metric_oi_log_change_1",
    )
    groups = data.groupby("event_state_id", sort=False)
    data["choice_set_size"] = groups.event_state_id.transform("size").astype(float)
    for name in relative_candidates:
        if name not in data:
            continue
        values = pd.to_numeric(data[name], errors="coerce")
        data[f"relative_{name}_rank"] = values.groupby(data.event_state_id).rank(
            pct=True, method="average"
        )
        median = values.groupby(data.event_state_id).transform("median")
        mad = values.groupby(data.event_state_id).transform(
            lambda x: (x - x.median()).abs().median()
        )
        data[f"relative_{name}_mad"] = (values - median) / mad.replace(0.0, np.nan)

    target_r = pd.to_numeric(
        _series(data, ("planned_target_net_r", "gross_rr")), errors="coerce"
    ).clip(lower=0.0)
    win_log = np.log1p(RISK * target_r)
    loss_log = math.log(1.0 - RISK)
    data["break_even_target_probability"] = (-loss_log / (win_log - loss_log)).clip(0.0, 1.0)
    return data.replace([np.inf, -np.inf], np.nan)


def labels(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    fill_index = pd.to_numeric(_series(frame, "fill_index"), errors="coerce")
    fill_time = pd.to_numeric(_series(frame, "fill_time_ns"), errors="coerce")
    state = _series(frame, "fill_state", "").astype(str).str.upper()
    filled_col = _first_existing(frame, ("filled", "is_filled"))
    explicit_filled = _bool_series(frame[filled_col]) if filled_col else pd.Series(False, index=frame.index)
    # Only an explicit FILLED state or a recorded fill timestamp/index counts as a fill.
    filled = explicit_filled | fill_index.notna() | fill_time.notna() | state.str.startswith("FILLED")

    net_col = _first_existing(frame, ("net_r", "realized_net_r"))
    net = pd.to_numeric(frame[net_col], errors="coerce") if net_col else pd.Series(np.nan, index=frame.index)
    resolved_col = _first_existing(frame, ("resolved", "is_resolved"))
    resolved = _bool_series(frame[resolved_col]) if resolved_col else pd.Series(False, index=frame.index)
    resolved = filled & (resolved | net.notna())

    result["filled_label"] = filled.astype(int)
    result["resolved_label"] = np.where(filled, resolved.astype(float), np.nan)
    result["target_label"] = np.where(resolved, net.gt(0.0).astype(float), np.nan)

    terminal_col = _first_existing(
        frame,
        ("order_terminal_time_ns", "terminal_ns", "resolution_time_ns", "expiry_time_ns"),
    )
    terminal = (
        pd.to_numeric(frame[terminal_col], errors="coerce")
        if terminal_col
        else pd.Series(np.nan, index=frame.index)
    )
    duration = (terminal - frame.order_time_ns) / NS_MINUTE
    result["occupancy_minutes_label"] = duration.where(duration >= 0.0).clip(lower=1.0)
    return result


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric: list[str] = []
    categorical: list[str] = []
    categorical_set = set(CATEGORICAL_CANDIDATES)
    for column in frame.columns:
        low = column.lower()
        if column in EXACT_EXCLUDE or low.endswith("_time_ns") or low.endswith("_index"):
            continue
        if any(token in low for token in LEAK_TOKENS):
            continue
        if column in categorical_set:
            if frame[column].nunique(dropna=True) <= 200:
                categorical.append(column)
            continue
        if pd.api.types.is_numeric_dtype(frame[column]):
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.notna().sum() >= max(30, int(0.02 * len(frame))) and values.nunique(dropna=True) > 1:
                numeric.append(column)
    if len(numeric) > 280:
        priority: list[tuple[float, str]] = []
        for column in numeric:
            low = column.lower()
            structural = any(
                token in low
                for token in (
                    "relative_", "auction_", "arm_", "liquidity_", "structure_",
                    "route", "risk", "rr", "flow", "activity", "delta", "volume",
                    "basis", "oi_", "breadth", "vwap", "sequence", "source_",
                )
            )
            coverage = float(pd.to_numeric(frame[column], errors="coerce").notna().mean())
            priority.append((2.0 * float(structural) + coverage, column))
        numeric = [column for _, column in sorted(priority, reverse=True)[:280]]
    return sorted(set(numeric)), sorted(set(categorical))


def _weights(frame: pd.DataFrame) -> np.ndarray:
    # A state can expose several immutable prices.  It is still one market observation.
    duplicate = 1.0 / frame.groupby("event_state_id").event_state_id.transform("size").to_numpy(float)
    time = pd.to_numeric(frame.order_time_ns, errors="coerce").to_numpy(float)
    latest = np.nanmax(time)
    age_days = np.maximum(0.0, (latest - time) / NS_DAY)
    recency = np.exp(-math.log(2.0) * age_days / 365.0).clip(0.25, 1.0)
    return duplicate * recency


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    total = float(np.sum(weights))
    if total <= EPS:
        return float(np.mean(values)) if len(values) else 0.0
    return float(np.sum(weights * values) / total)


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    if not len(values):
        return 0.0
    order = np.argsort(values)
    values = values[order]
    weights = np.maximum(weights[order], 0.0)
    total = float(weights.sum())
    if total <= EPS:
        return float(np.median(values))
    return float(values[np.searchsorted(np.cumsum(weights), 0.5 * total, side="left")])


def _stable_bucket(text: str, dimension: int) -> tuple[int, float]:
    digest = hashlib.blake2b(text.encode("utf-8", errors="ignore"), digest_size=8).digest()
    number = int.from_bytes(digest, "little", signed=False)
    return number % dimension, (1.0 if ((number >> 17) & 1) else -1.0)


def _design_spec(
    train: pd.DataFrame,
    target: np.ndarray,
    sample_weight: np.ndarray,
    numeric: Sequence[str],
    categorical: Sequence[str],
    *,
    max_numeric: int = 128,
    nonlinear_numeric: int = 32,
    categorical_hash_dim: int = 64,
) -> dict[str, Any]:
    """Choose and scale causal features using training data only.

    Rich auction features carry the nonlinearity.  A robust, low-dimensional design and
    regularized probability model is deliberately used instead of importing a large ML
    stack into the research image.  This also makes probability calibration and every
    transformation explicit.
    """
    candidates: list[tuple[float, str, float, float]] = []
    weight = np.maximum(np.asarray(sample_weight, dtype=float), 0.0)
    weight_sum = max(float(weight.sum()), EPS)
    y_mean = float(np.sum(weight * target) / weight_sum)
    y_center = target - y_mean
    y_var = float(np.sum(weight * y_center * y_center) / weight_sum)
    for column in numeric:
        values = pd.to_numeric(train[column], errors="coerce").to_numpy(float)
        finite = np.isfinite(values)
        if int(finite.sum()) < max(30, int(0.02 * len(values))):
            continue
        median = float(np.nanmedian(values))
        q25, q75 = np.nanpercentile(values, [25.0, 75.0])
        scale = float(q75 - q25)
        if not math.isfinite(scale) or scale <= EPS:
            scale = float(np.nanstd(values))
        if not math.isfinite(scale) or scale <= EPS:
            continue
        z = np.clip(np.where(finite, (values - median) / scale, 0.0), -8.0, 8.0)
        z_mean = float(np.sum(weight * z) / weight_sum)
        z_center = z - z_mean
        z_var = float(np.sum(weight * z_center * z_center) / weight_sum)
        covariance = float(np.sum(weight * z_center * y_center) / weight_sum)
        correlation = abs(covariance) / math.sqrt(max(z_var * y_var, EPS))
        low = column.lower()
        structural = float(any(
            token in low for token in (
                "relative_", "auction_", "arm_", "liquidity_", "structure_",
                "route", "risk", "rr", "flow", "activity", "delta", "volume",
                "basis", "oi_", "breadth", "vwap", "sequence", "source_",
                "dealing_range", "break_even", "choice_set",
            )
        ))
        coverage = float(finite.mean())
        score = correlation + 0.025 * structural + 0.010 * coverage
        candidates.append((score, column, median, scale))
    candidates.sort(reverse=True)
    chosen = candidates[:max_numeric]
    nonlinear = [column for _, column, _, _ in chosen[:nonlinear_numeric]]
    return {
        "numeric": [column for _, column, _, _ in chosen],
        "median": {column: median for _, column, median, _ in chosen},
        "scale": {column: scale for _, column, _, scale in chosen},
        "nonlinear": nonlinear,
        "categorical": list(categorical),
        "categorical_hash_dim": int(categorical_hash_dim),
    }


def _design_matrix(frame: pd.DataFrame, spec: dict[str, Any]) -> np.ndarray:
    n = len(frame)
    numeric_columns = list(spec["numeric"])
    nonlinear_columns = list(spec["nonlinear"])
    hash_dim = int(spec["categorical_hash_dim"])
    width = 1 + len(numeric_columns) + 2 * len(nonlinear_columns) + hash_dim
    output = np.zeros((n, width), dtype=np.float64)
    output[:, 0] = 1.0
    position = 1
    standardized: dict[str, np.ndarray] = {}
    for column in numeric_columns:
        raw = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
        median = float(spec["median"][column])
        scale = max(float(spec["scale"][column]), EPS)
        values = np.clip(np.where(np.isfinite(raw), (raw - median) / scale, 0.0), -8.0, 8.0)
        output[:, position] = values
        standardized[column] = values
        position += 1
    for column in nonlinear_columns:
        values = standardized[column]
        output[:, position] = np.tanh(values)
        output[:, position + 1] = np.clip(values * values, 0.0, 16.0) - 1.0
        position += 2
    if hash_dim:
        hashed = output[:, position:position + hash_dim]
        for column in spec["categorical"]:
            if column not in frame:
                continue
            values = frame[column].fillna("__NA__").astype(str).to_numpy()
            for row_index, value in enumerate(values):
                bucket, sign = _stable_bucket(f"{column}={value}", hash_dim)
                hashed[row_index, bucket] += sign
        np.clip(hashed, -4.0, 4.0, out=hashed)
    return output


def _sigmoid_array(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-values))


def _calibrate_intercept(logits: np.ndarray, weights: np.ndarray, target_mean: float) -> float:
    target_mean = float(np.clip(target_mean, 1e-5, 1.0 - 1e-5))
    lower, upper = -12.0, 12.0
    for _ in range(50):
        middle = 0.5 * (lower + upper)
        probability = _weighted_mean(_sigmoid_array(logits + middle), weights)
        if probability < target_mean:
            lower = middle
        else:
            upper = middle
    return 0.5 * (lower + upper)


def _fit_logistic_adam(
    matrix: np.ndarray,
    target: np.ndarray,
    sample_weight: np.ndarray,
    *,
    prior: float,
    seed: int,
    steps: int = 180,
    l2: float = 0.018,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    weight = np.maximum(np.asarray(sample_weight, dtype=float), 0.0)
    # Bayesian bootstrap provides ensemble diversity without changing chronology.
    weight = weight * rng.gamma(shape=1.0, scale=1.0, size=len(weight))
    weight_sum = max(float(weight.sum()), EPS)
    beta = np.zeros(matrix.shape[1], dtype=float)
    beta[0] = math.log(max(prior, 1e-5) / max(1.0 - prior, 1e-5))
    first = np.zeros_like(beta)
    second = np.zeros_like(beta)
    for step in range(1, steps + 1):
        probability = _sigmoid_array(matrix @ beta)
        residual = probability - target
        gradient = matrix.T @ (weight * residual) / weight_sum
        gradient[1:] += l2 * beta[1:]
        np.clip(gradient, -4.0, 4.0, out=gradient)
        first = 0.9 * first + 0.1 * gradient
        second = 0.999 * second + 0.001 * gradient * gradient
        first_hat = first / (1.0 - 0.9**step)
        second_hat = second / (1.0 - 0.999**step)
        learning_rate = 0.035 * (0.25 + 0.75 * (1.0 - step / steps))
        beta -= learning_rate * first_hat / (np.sqrt(second_hat) + 1e-8)
    beta[0] += _calibrate_intercept(matrix @ beta, weight, prior)
    return beta


def _binary_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: pd.Series,
    numeric: Sequence[str],
    categorical: Sequence[str],
    *,
    seeds: Sequence[int],
) -> tuple[np.ndarray, np.ndarray, float]:
    valid = target.notna()
    train = train.loc[valid].copy()
    y = target.loc[valid].astype(float).to_numpy()
    raw_weight = _weights(train) if len(train) else np.empty(0, dtype=float)
    positives = float(np.sum(raw_weight * y)) if len(y) else 0.0
    denominator = float(raw_weight.sum()) if len(y) else 0.0
    prior = (positives + 8.0) / (denominator + 16.0) if denominator > 0.0 else 0.5
    if len(train) < 180 or len(np.unique(y)) < 2:
        return np.full(len(test), prior), np.zeros(len(test)), prior
    spec = _design_spec(train, y, raw_weight, numeric, categorical)
    x_train = _design_matrix(train, spec)
    x_test = _design_matrix(test, spec)
    predictions: list[np.ndarray] = []
    for seed in seeds:
        beta = _fit_logistic_adam(
            x_train, y, raw_weight, prior=prior, seed=int(seed)
        )
        predictions.append(_sigmoid_array(x_test @ beta))
    matrix = np.vstack(predictions)
    mean = 0.88 * matrix.mean(axis=0) + 0.12 * prior
    prior_uncertainty = math.sqrt(prior * (1.0 - prior) / max(denominator + 16.0, 1.0))
    uncertainty = np.sqrt(matrix.var(axis=0) + prior_uncertainty**2)
    return np.clip(mean, 0.002, 0.998), uncertainty, prior


def _fit_huber_adam(
    matrix: np.ndarray,
    target: np.ndarray,
    sample_weight: np.ndarray,
    *,
    seed: int,
    steps: int = 170,
    l2: float = 0.020,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    weight = np.maximum(np.asarray(sample_weight, dtype=float), 0.0)
    weight = weight * rng.gamma(shape=1.0, scale=1.0, size=len(weight))
    weight_sum = max(float(weight.sum()), EPS)
    beta = np.zeros(matrix.shape[1], dtype=float)
    beta[0] = _weighted_median(target, weight)
    first = np.zeros_like(beta)
    second = np.zeros_like(beta)
    for step in range(1, steps + 1):
        residual = matrix @ beta - target
        derivative = np.clip(residual, -1.0, 1.0)
        gradient = matrix.T @ (weight * derivative) / weight_sum
        gradient[1:] += l2 * beta[1:]
        np.clip(gradient, -5.0, 5.0, out=gradient)
        first = 0.9 * first + 0.1 * gradient
        second = 0.999 * second + 0.001 * gradient * gradient
        first_hat = first / (1.0 - 0.9**step)
        second_hat = second / (1.0 - 0.999**step)
        learning_rate = 0.030 * (0.25 + 0.75 * (1.0 - step / steps))
        beta -= learning_rate * first_hat / (np.sqrt(second_hat) + 1e-8)
    beta[0] += _weighted_median(target - matrix @ beta, weight)
    return beta


def _duration_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: pd.Series,
    numeric: Sequence[str],
    categorical: Sequence[str],
) -> tuple[np.ndarray, np.ndarray]:
    valid = target.notna()
    train = train.loc[valid].copy()
    y_minutes = target.loc[valid].clip(lower=1.0).to_numpy(float)
    fallback = float(np.median(y_minutes)) if len(y_minutes) else 60.0
    if len(train) < 180:
        return np.full(len(test), fallback), np.zeros(len(test))
    y = np.log1p(y_minutes)
    raw_weight = _weights(train)
    spec = _design_spec(
        train, y, raw_weight, numeric, categorical,
        max_numeric=96, nonlinear_numeric=24, categorical_hash_dim=48,
    )
    x_train = _design_matrix(train, spec)
    x_test = _design_matrix(test, spec)
    predictions: list[np.ndarray] = []
    for seed in (71, 257):
        beta = _fit_huber_adam(x_train, y, raw_weight, seed=seed)
        predicted = np.expm1(np.clip(x_test @ beta, 0.0, math.log1p(7.0 * 24.0 * 60.0)))
        predictions.append(predicted)
    matrix = np.vstack(predictions)
    return np.maximum(1.0, matrix.mean(axis=0)), matrix.std(axis=0)


def score_fold(
    train: pd.DataFrame,
    test: pd.DataFrame,
    numeric: Sequence[str],
    categorical: Sequence[str],
) -> pd.DataFrame:
    train_labels = labels(train)
    p_fill, s_fill, prior_fill = _binary_predict(
        train, test, train_labels.filled_label, numeric, categorical, seeds=(41, 173, 389)
    )
    p_resolve, s_resolve, prior_resolve = _binary_predict(
        train, test, train_labels.resolved_label, numeric, categorical, seeds=(53, 181, 397)
    )
    p_target, s_target, prior_target = _binary_predict(
        train, test, train_labels.target_label, numeric, categorical, seeds=(61, 211, 431)
    )
    duration, s_duration = _duration_predict(
        train, test, train_labels.occupancy_minutes_label, numeric, categorical
    )

    output = test.copy()
    output["p_fill"] = np.clip(p_fill - 0.15 * s_fill, 0.002, 0.998)
    output["p_resolve_given_fill"] = np.clip(p_resolve - 0.15 * s_resolve, 0.002, 0.998)
    output["p_target_given_resolved_fill"] = np.clip(p_target - 0.25 * s_target, 0.002, 0.998)
    output["predicted_occupancy_minutes"] = np.maximum(1.0, duration + 0.15 * s_duration)
    output["probability_uncertainty"] = np.sqrt(s_fill**2 + s_resolve**2 + s_target**2)
    output["training_priors"] = json.dumps(
        {"fill": prior_fill, "resolve": prior_resolve, "target": prior_target},
        sort_keys=True,
    )

    target_r = pd.to_numeric(
        _series(output, ("planned_target_net_r", "gross_rr")), errors="coerce"
    ).fillna(0.0).clip(lower=0.0).to_numpy(float)
    stop_r = pd.to_numeric(
        _series(output, ("planned_stop_net_r", "actual_stop_net_r"), -1.0),
        errors="coerce",
    ).fillna(-1.0).to_numpy(float)
    stop_r = np.where(stop_r < 0.0, stop_r, -1.0)
    win_log = np.log1p(RISK * target_r)
    loss_log = np.log(np.maximum(EPS, 1.0 + RISK * stop_r))
    conditional = (
        output.p_target_given_resolved_fill.to_numpy(float) * win_log
        + (1.0 - output.p_target_given_resolved_fill.to_numpy(float)) * loss_log
    )
    unresolved_penalty = 0.20 * loss_log
    output["expected_log_growth"] = (
        output.p_fill.to_numpy(float)
        * (
            output.p_resolve_given_fill.to_numpy(float) * conditional
            + (1.0 - output.p_resolve_given_fill.to_numpy(float)) * unresolved_penalty
        )
    )
    output["expected_log_growth_per_hour"] = (
        output.expected_log_growth / (output.predicted_occupancy_minutes / 60.0)
    )
    output["win_probability_edge"] = (
        output.p_target_given_resolved_fill
        - pd.to_numeric(output.break_even_target_probability, errors="coerce").fillna(1.0)
    )
    return apply_sequential_ownership(output)


def _logit(value: float) -> float:
    value = min(max(float(value), 1e-5), 1.0 - 1e-5)
    return math.log(value / (1.0 - value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def apply_sequential_ownership(scored: pd.DataFrame) -> pd.DataFrame:
    """Carry only prior event evidence into the current state.

    The update is performed once per event-state and then copied to alternative prices.
    A direction/family contradiction resets stale belief instead of allowing old evidence
    to bleed into a new auction resolution.
    """
    if scored.empty:
        return scored
    output = scored.copy()
    state_rows = (
        output.sort_values(
            ["event_id", "order_time_ns", "expected_log_growth", "planned_target_net_r"],
            ascending=[True, True, False, False],
        )
        .groupby("event_state_id", as_index=False)
        .first()
        .sort_values(["event_id", "order_time_ns", "event_state_id"])
    )
    belief_by_state: dict[str, float] = {}
    previous_key: dict[str, tuple[str, str] | None] = {}
    belief: dict[str, float] = {}
    for _, row in state_rows.iterrows():
        event = str(row.event_id)
        key = (str(row.get("side", "")), str(row.get("family", "")))
        current = belief.get(event, 0.0)
        if previous_key.get(event) not in (None, key):
            current = 0.0
        # The current action is priced with belief accumulated strictly before this state.
        belief_by_state[str(row.event_state_id)] = current
        edge = _logit(_finite(row.get("p_target_given_resolved_fill"), 0.5)) - _logit(
            _finite(row.get("break_even_target_probability"), 0.5)
        )
        # Persistence matters, but current evidence remains dominant.
        belief[event] = float(np.clip(0.62 * current + 0.38 * edge, -4.0, 4.0))
        previous_key[event] = key
    output["prior_ownership_log_odds"] = output.event_state_id.astype(str).map(belief_by_state).fillna(0.0)
    base_logit = output.p_target_given_resolved_fill.map(_logit).to_numpy(float)
    adjusted = np.array(
        [_sigmoid(a + 0.28 * b) for a, b in zip(base_logit, output.prior_ownership_log_odds)],
        dtype=float,
    )
    output["p_target_with_ownership"] = np.clip(adjusted, 0.002, 0.998)

    target_r = pd.to_numeric(
        _series(output, ("planned_target_net_r", "gross_rr")), errors="coerce"
    ).fillna(0.0).clip(lower=0.0).to_numpy(float)
    win_log = np.log1p(RISK * target_r)
    loss_log = math.log(1.0 - RISK)
    conditional = output.p_target_with_ownership.to_numpy(float) * win_log + (
        1.0 - output.p_target_with_ownership.to_numpy(float)
    ) * loss_log
    output["expected_log_growth"] = (
        output.p_fill.to_numpy(float)
        * (
            output.p_resolve_given_fill.to_numpy(float) * conditional
            + (1.0 - output.p_resolve_given_fill.to_numpy(float)) * (0.20 * loss_log)
        )
    )
    output["expected_log_growth_per_hour"] = (
        output.expected_log_growth / (output.predicted_occupancy_minutes / 60.0)
    )
    output["win_probability_edge"] = (
        output.p_target_with_ownership
        - pd.to_numeric(output.break_even_target_probability, errors="coerce").fillna(1.0)
    )
    return output


def _terminal_ns(row: pd.Series) -> float:
    for column in (
        "order_terminal_time_ns", "terminal_ns", "resolution_time_ns",
        "expiry_time_ns", "fill_expiry_time_ns",
    ):
        if column in row and pd.notna(row[column]):
            value = _finite(row[column], float(row.order_time_ns))
            return max(float(row.order_time_ns), value)
    return float(row.order_time_ns) + max(
        1.0, _finite(row.get("predicted_occupancy_minutes"), 60.0)
    ) * NS_MINUTE


def route(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if scored.empty:
        empty = scored.copy()
        return empty, empty, summarize(empty, empty, empty)
    selected_rows: list[pd.Series] = []

    # Disjoint development windows are separate diagnostics.  A continuous validation
    # period naturally has one period and therefore one uninterrupted account clock.
    for _, period_frame in scored.groupby("period", sort=False):
        period_frame = period_frame.sort_values(
            ["order_time_ns", "event_state_id", "expected_log_growth_per_hour"],
            ascending=[True, True, False],
        )
        armed_events: set[str] = set()
        busy_until = -np.inf
        for timestamp, clock in period_frame.groupby("order_time_ns", sort=True):
            timestamp = float(timestamp)
            if timestamp < busy_until:
                continue
            clock = clock[~clock.event_id.astype(str).isin(armed_events)].copy()
            if clock.empty:
                continue
            # First compare alternative prices/actions inside each current event state.
            state_best = (
                clock.sort_values(
                    [
                        "event_state_id", "expected_log_growth", "expected_log_growth_per_hour",
                        "p_fill", "planned_target_net_r",
                    ],
                    ascending=[True, False, False, False, False],
                )
                .groupby("event_state_id", as_index=False)
                .first()
            )
            state_best = state_best[state_best.expected_log_growth > 0.0]
            if state_best.empty:
                continue
            # Simultaneous symbols compete by expected compounding rate, then total edge.
            row = state_best.sort_values(
                ["expected_log_growth_per_hour", "expected_log_growth", "p_fill"],
                ascending=[False, False, False],
            ).iloc[0]
            selected_rows.append(row)
            armed_events.add(str(row.event_id))
            busy_until = _terminal_ns(row)

    orders = (
        pd.DataFrame(selected_rows).reset_index(drop=True)
        if selected_rows
        else scored.iloc[:0].copy()
    )
    net_col = _first_existing(orders, ("net_r", "realized_net_r"))
    if net_col:
        net = pd.to_numeric(orders[net_col], errors="coerce")
        trades = orders[net.notna()].copy().reset_index(drop=True)
        if net_col != "net_r":
            trades["net_r"] = pd.to_numeric(trades[net_col], errors="coerce")
    else:
        trades = orders.iloc[:0].copy()
    return orders, trades, summarize(trades, orders, scored)


def _calendar_days(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    days = 0
    for _, group in frame.groupby("period", sort=False):
        start = pd.to_numeric(group.order_time_ns, errors="coerce").min()
        end = pd.to_numeric(group.order_time_ns, errors="coerce").max()
        if pd.notna(start) and pd.notna(end):
            days += max(1, int(math.ceil((float(end) - float(start)) / NS_DAY)))
    return days


def summarize(
    trades: pd.DataFrame,
    orders: pd.DataFrame,
    all_rows: pd.DataFrame,
) -> dict[str, Any]:
    nav = peak = 1.0
    maximum_drawdown = 0.0
    ordered_trades = trades.copy()
    if len(ordered_trades):
        sort_col = _first_existing(ordered_trades, ("resolution_time_ns", "order_terminal_time_ns", "order_time_ns"))
        if sort_col:
            ordered_trades = ordered_trades.sort_values(sort_col)
    results = pd.to_numeric(_series(ordered_trades, "net_r"), errors="coerce").dropna()
    for result in results:
        nav *= max(EPS, 1.0 + RISK * float(result))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    wins = results > 0.0
    positive_sum = float(results[results > 0.0].sum()) if len(results) else 0.0
    negative_sum = float(-results[results < 0.0].sum()) if len(results) else 0.0
    output: dict[str, Any] = {
        "selected_orders": int(len(orders)),
        "filled_orders": int(labels(orders).filled_label.sum()) if len(orders) else 0,
        "closed_trades": int(len(ordered_trades)),
        "calendar_days": int(_calendar_days(all_rows)),
        "trades_per_day": float(len(ordered_trades) / max(_calendar_days(all_rows), 1)),
        "target_first_rate": float(wins.mean()) if len(results) else None,
        "mean_net_r": float(results.mean()) if len(results) else None,
        "median_net_r": float(results.median()) if len(results) else None,
        "profit_factor_r": float(positive_sum / negative_sum) if negative_sum > 0.0 else (math.inf if positive_sum > 0.0 else None),
        "mean_planned_gross_rr": float(pd.to_numeric(_series(ordered_trades, "gross_rr"), errors="coerce").mean()) if len(ordered_trades) else None,
        "median_hold_minutes": float(pd.to_numeric(_series(ordered_trades, "holding_minutes"), errors="coerce").median()) if len(ordered_trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
    }
    for column, name in (
        ("period", "by_period"), ("family", "by_family"),
        ("symbol", "by_symbol"), ("auction_phase", "by_phase"),
    ):
        if len(ordered_trades) and column in ordered_trades:
            grouped = (
                ordered_trades.assign(_win=pd.to_numeric(ordered_trades.net_r, errors="coerce") > 0.0)
                .groupby(column)
                .agg(
                    trades=("net_r", "size"),
                    target_first_rate=("_win", "mean"),
                    mean_net_r=("net_r", "mean"),
                )
                .reset_index()
            )
            output[name] = grouped.to_dict("records")
    return output


def chronological_blocks(frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    if frame.empty:
        return []
    periods = (
        frame.groupby("period").order_time_ns.min().sort_values().index.astype(str).tolist()
        if "period" in frame
        else []
    )
    blocks: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    if len(periods) > 1:
        for index in range(1, len(periods)):
            train = frame[frame.period.isin(periods[:index])].copy()
            test = frame[frame.period == periods[index]].copy()
            if len(train) >= 180 and len(test):
                blocks.append((periods[index], train, test))
        if blocks:
            return blocks
    # Fallback for a single contiguous development range: expanding chronological blocks.
    block_count = min(6, max(3, len(frame) // 600))
    rank = frame.order_time_ns.rank(method="first")
    block = pd.qcut(rank, q=block_count, labels=False, duplicates="drop")
    temp = frame.assign(_block=block)
    for index in sorted(temp._block.dropna().unique()):
        if index == 0:
            continue
        train = temp[temp._block < index].drop(columns="_block")
        test = temp[temp._block == index].drop(columns="_block")
        if len(train) >= 180 and len(test):
            blocks.append((str(index), train, test))
    return blocks


def run(development_root: Path, fresh_root: Path | None, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = load_actions(development_root)
    numeric, categorical = feature_columns(development)
    folds: list[pd.DataFrame] = []
    for evaluation_period, train, test in chronological_blocks(development):
        scored = score_fold(train, test, numeric, categorical)
        scored["evaluation_period"] = evaluation_period
        folds.append(scored)
    development_scored = (
        pd.concat(folds, ignore_index=True, sort=False)
        if folds
        else development.iloc[:0].copy()
    )
    development_orders, development_trades, development_summary = route(development_scored)
    result: dict[str, Any] = {
        "policy": "CANDIDATE_2C_CAUSAL_SEQUENTIAL_ACTION_VALUE",
        "development_walk_forward": development_summary,
        "development_rows": int(len(development)),
        "features": {"numeric": list(numeric), "categorical": list(categorical)},
    }
    development_scored.to_csv(output / "development_scored.csv.gz", index=False, compression="gzip")
    development_orders.to_csv(output / "development_orders.csv", index=False)
    development_trades.to_csv(output / "development_trades.csv", index=False)

    if fresh_root is not None:
        fresh = load_actions(fresh_root)
        fresh_scored = score_fold(development, fresh, numeric, categorical)
        fresh_orders, fresh_trades, fresh_summary = route(fresh_scored)
        fresh_scored.to_csv(output / "fresh_scored.csv.gz", index=False, compression="gzip")
        fresh_orders.to_csv(output / "fresh_orders.csv", index=False)
        fresh_trades.to_csv(output / "fresh_trades.csv", index=False)
        result["fresh"] = fresh_summary
        result["fresh_rows"] = int(len(fresh))

    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
