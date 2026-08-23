#!/usr/bin/env python3
"""Candidate 4t sequential competing-hypothesis auction control.

Consumes immutable action rows from the 1k/2c harvester and separates auction
ownership, execution geometry, and the decision to enter now versus wait. Future
bars are used only by the historical order resolver after entry/stop/target exist.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-9
NS_PER_MINUTE = 60_000_000_000
NS_PER_HOUR = 60 * NS_PER_MINUTE
ID_OR_ABSOLUTE = {
    "symbol", "period", "action_id", "state_id", "episode_id", "entry", "stop",
    "target", "route_price", "arm_index", "departure_time_ns", "order_time_ns",
    "order_terminal_time_ns", "fill_time_ns", "resolution_time_ns", "terminal_ns",
}
LABEL_TOKENS = (
    "outcome", "fill_state", "filled", "resolved", "win", "net_r", "mfe", "mae",
    "holding", "entry_wait", "actual_", "future_", "terminal_minutes_label",
    "realized", "diagnostic_", "label",
)
ACTION_GEOMETRY = {
    "entry_geometry", "gross_rr", "risk_bps", "planned_target_net_r",
    "target_net_r", "stop_net_r", "route_rr", "route_utilization",
}
CATEGORICAL = {
    "ownership": ["family", "side", "auction_phase", "setup_kind", "source_pool_kind"],
    "execution": ["family", "side", "auction_phase", "setup_kind", "source_pool_kind",
                  "entry_geometry", "location_kind", "route_kind"],
    "continuation": ["family", "side", "auction_phase", "setup_kind", "source_pool_kind"],
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def sigmoid(value: np.ndarray) -> np.ndarray:
    value = np.clip(value, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-value))


def logit(value: np.ndarray | float) -> np.ndarray:
    value = np.clip(np.asarray(value, dtype=float), 0.002, 0.998)
    return np.log(value / (1.0 - value))


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False).astype(bool)
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        return numeric.fillna(0.0).ne(0.0)
    return series.fillna("").astype(str).str.lower().isin({"true", "t", "1", "yes"})


def period_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        match = re.search(r"((?:dev|fresh)-\d{4}-[a-z0-9-]+)", part.lower())
        if match:
            return match.group(1)
    return path.parent.name


def load_actions(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    required = {"state_id", "episode_id", "action_id", "order_time_ns"}
    for path in sorted(root.rglob("*.csv")):
        try:
            probe = pd.read_csv(path, nrows=3, low_memory=False)
        except Exception:
            continue
        if not required.issubset(probe.columns):
            continue
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        if "period" not in frame:
            frame["period"] = period_from_path(path)
        frame["_source_file"] = str(path)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"no immutable action CSVs found below {root}")
    data = pd.concat(frames, ignore_index=True, sort=False)
    data = data.drop_duplicates(subset=["period", "action_id"], keep="first")
    for column in ("filled", "resolved", "win"):
        if column not in data:
            data[column] = False
        data[column] = bool_series(data[column])
    for column in ("order_time_ns", "terminal_ns", "fill_time_ns", "resolution_time_ns",
                   "net_r", "gross_rr", "planned_target_net_r", "target_net_r", "stop_net_r"):
        if column in data:
            data[column] = pd.to_numeric(data[column], errors="coerce")
    if "terminal_ns" not in data:
        alternatives = [c for c in ("order_terminal_time_ns", "resolution_time_ns") if c in data]
        data["terminal_ns"] = data[alternatives].bfill(axis=1).iloc[:, 0] if alternatives else data.order_time_ns
    data["terminal_ns"] = pd.to_numeric(data.terminal_ns, errors="coerce").fillna(data.order_time_ns)
    if "fill_time_ns" not in data:
        data["fill_time_ns"] = np.nan
    if "planned_target_net_r" not in data:
        source = "target_net_r" if "target_net_r" in data else "gross_rr"
        data["planned_target_net_r"] = pd.to_numeric(data.get(source, 1.0), errors="coerce")
    if "stop_net_r" not in data:
        data["stop_net_r"] = -1.0
    data["planned_target_net_r"] = pd.to_numeric(data.planned_target_net_r, errors="coerce")
    data["stop_net_r"] = pd.to_numeric(data.stop_net_r, errors="coerce").fillna(-1.0)
    data["terminal_minutes_label"] = (
        data.terminal_ns.astype(float) - data.order_time_ns.astype(float)
    ) / NS_PER_MINUTE
    data["terminal_minutes_label"] = data.terminal_minutes_label.clip(lower=1.0)
    if "auction_phase" not in data:
        data["auction_phase"] = "UNKNOWN"
    data["auction_phase"] = data.auction_phase.fillna("UNKNOWN").astype(str)
    for column in ("period", "state_id", "episode_id", "action_id"):
        data[column] = data[column].astype(str)
    data = data[np.isfinite(pd.to_numeric(data.order_time_ns, errors="coerce"))]
    return data.reset_index(drop=True)


def decision_weights(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty:
        return np.zeros(0)
    state_size = frame.groupby(["period", "state_id"]).state_id.transform("size").astype(float)
    period_size = frame.groupby("period").period.transform("size").astype(float)
    weights = (1.0 / state_size.clip(lower=1.0)) * (len(frame) / period_size.clip(lower=1.0))
    values = weights.to_numpy(float)
    return values / max(float(values.mean()), EPS)


@dataclass
class Encoder:
    mode: str
    numeric: list[str]
    categorical_levels: dict[str, list[str]]
    median: np.ndarray
    scale: np.ndarray
    lower: np.ndarray
    upper: np.ndarray
    names: list[str]

    @classmethod
    def fit(cls, frame: pd.DataFrame, target: np.ndarray, weights: np.ndarray,
            mode: str, max_numeric: int = 42) -> "Encoder":
        candidates: list[str] = []
        for column in frame.columns:
            low = column.lower()
            if column in ID_OR_ABSOLUTE or column.startswith("_"):
                continue
            if any(token in low for token in LABEL_TOKENS):
                continue
            if low.endswith("_time_ns") or low.endswith("_index"):
                continue
            if mode == "ownership" and (
                column in ACTION_GEOMETRY
                or any(token in low for token in ("entry", "stop", "target", "route", "risk", "geometry"))
                or low.endswith("_rr")
            ):
                continue
            if not pd.api.types.is_numeric_dtype(frame[column]):
                continue
            values = pd.to_numeric(frame[column], errors="coerce")
            if values.notna().sum() < max(30, int(0.04 * len(frame))) or values.nunique(dropna=True) <= 1:
                continue
            candidates.append(column)
        target_mean = np.average(target, weights=weights) if len(target) else 0.0
        centered_target = target - target_mean
        scores: list[tuple[float, str]] = []
        for column in candidates:
            values = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
            finite = np.isfinite(values)
            if finite.sum() < 10:
                continue
            values = np.where(finite, values, float(np.nanmedian(values[finite])))
            centered = values - np.average(values, weights=weights)
            numerator = abs(float(np.sum(weights * centered * centered_target)))
            denominator = math.sqrt(
                max(float(np.sum(weights * centered * centered)), EPS)
                * max(float(np.sum(weights * centered_target * centered_target)), EPS)
            )
            scores.append((numerator / max(denominator, EPS), column))
        numeric = [name for _, name in sorted(scores, reverse=True)[:max_numeric]]
        raw = np.column_stack([
            pd.to_numeric(frame[column], errors="coerce").to_numpy(float) for column in numeric
        ]) if numeric else np.empty((len(frame), 0))
        if raw.shape[1]:
            lower = np.nanquantile(raw, 0.015, axis=0)
            upper = np.nanquantile(raw, 0.985, axis=0)
            median = np.nanmedian(raw, axis=0)
            raw = np.where(np.isfinite(raw), raw, median)
            raw = np.clip(raw, lower, upper)
            q25, q75 = np.quantile(raw, 0.25, axis=0), np.quantile(raw, 0.75, axis=0)
            scale = np.where((q75 - q25) > 1e-8, q75 - q25, np.std(raw, axis=0))
            scale = np.where(scale > 1e-8, scale, 1.0)
        else:
            lower = upper = median = scale = np.zeros(0)
        levels: dict[str, list[str]] = {}
        for column in CATEGORICAL[mode]:
            if column not in frame:
                continue
            values = frame[column].fillna("__NA__").astype(str)
            counts: dict[str, float] = {}
            for value, weight in zip(values, weights):
                counts[value] = counts.get(value, 0.0) + float(weight)
            levels[column] = [v for v, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:10]]
        names = list(numeric)
        for column, values in levels.items():
            names.extend([f"{column}={value}" for value in values])
        return cls(mode, numeric, levels, median, scale, lower, upper, names)

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        pieces: list[np.ndarray] = []
        if self.numeric:
            raw = np.column_stack([
                pd.to_numeric(
                    frame[column] if column in frame else pd.Series(np.nan, index=frame.index),
                    errors="coerce",
                ).to_numpy(float)
                for column in self.numeric
            ])
            raw = np.where(np.isfinite(raw), raw, self.median)
            pieces.append((np.clip(raw, self.lower, self.upper) - self.median) / self.scale)
        for column, levels in self.categorical_levels.items():
            values = frame.get(column, pd.Series("__NA__", index=frame.index)).fillna("__NA__").astype(str)
            pieces.append(np.column_stack([(values == level).to_numpy(float) for level in levels]))
        return np.column_stack(pieces) if pieces else np.empty((len(frame), 0))


@dataclass
class LogisticModel:
    encoder: Encoder
    beta: np.ndarray
    prior: float

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = np.column_stack([np.ones(len(frame)), self.encoder.transform(frame)])
        return np.clip(sigmoid(x @ self.beta), 0.003, 0.997)


@dataclass
class RidgeModel:
    encoder: Encoder
    beta: np.ndarray

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = np.column_stack([np.ones(len(frame)), self.encoder.transform(frame)])
        return x @ self.beta


def fit_logistic(frame: pd.DataFrame, target: np.ndarray, weights: np.ndarray,
                 mode: str, l2: float) -> LogisticModel:
    target, weights = np.asarray(target, float), np.asarray(weights, float)
    prior = float((np.sum(weights * target) + 4.0) / (np.sum(weights) + 8.0))
    encoder = Encoder.fit(frame, target, weights, mode)
    x = np.column_stack([np.ones(len(frame)), encoder.transform(frame)])
    beta = np.zeros(x.shape[1])
    beta[0] = float(logit(prior))
    penalty = np.ones(x.shape[1]) * l2
    penalty[0] = 0.0
    for _ in range(45):
        probability = sigmoid(x @ beta)
        variance = np.clip(probability * (1.0 - probability), 1e-5, None)
        gradient = x.T @ (weights * (probability - target)) + penalty * beta
        hessian = x.T @ ((weights * variance)[:, None] * x)
        hessian.flat[:: hessian.shape[0] + 1] += penalty + 1e-6
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(hessian) @ gradient
        norm = float(np.linalg.norm(step))
        if norm > 5.0:
            step *= 5.0 / norm
        beta -= step
        if float(np.max(np.abs(step))) < 1e-6:
            break
    return LogisticModel(encoder, beta, prior)


def fit_ridge(frame: pd.DataFrame, target: np.ndarray, weights: np.ndarray,
              mode: str, l2: float = 18.0) -> RidgeModel:
    target, weights = np.asarray(target, float), np.asarray(weights, float)
    clipped = np.clip(target, np.nanquantile(target, 0.01), np.nanquantile(target, 0.99))
    encoder = Encoder.fit(frame, clipped, weights, mode)
    x = np.column_stack([np.ones(len(frame)), encoder.transform(frame)])
    penalty = np.eye(x.shape[1]) * l2
    penalty[0, 0] = 1e-6
    lhs = x.T @ (weights[:, None] * x) + penalty
    rhs = x.T @ (weights * target)
    try:
        beta = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        beta = np.linalg.pinv(lhs) @ rhs
    return RidgeModel(encoder, beta)


def ensemble_probability(train: pd.DataFrame, test: pd.DataFrame, target_column: str,
                         mode: str, mask: pd.Series | np.ndarray | None = None):
    subset = train.copy() if mask is None else train.loc[np.asarray(mask, bool)].copy()
    subset = subset[subset[target_column].notna()].copy()
    if subset.empty:
        return np.full(len(test), 0.5), np.zeros(len(test)), 0.5, []
    target = pd.to_numeric(subset[target_column], errors="coerce").fillna(0.0).to_numpy(float)
    weights = decision_weights(subset)
    if len(subset) < 90 or np.unique(target).size < 2:
        prior = float((np.sum(weights * target) + 4.0) / (np.sum(weights) + 8.0))
        return np.full(len(test), prior), np.zeros(len(test)), prior, []
    predictions, priors, names = [], [], []
    for l2 in (6.0, 20.0, 64.0):
        model = fit_logistic(subset, target, weights, mode, l2)
        predictions.append(model.predict(test))
        priors.append(model.prior)
        names = model.encoder.names
    matrix = np.vstack(predictions)
    return matrix.mean(axis=0), matrix.std(axis=0), float(np.mean(priors)), names


def reliability_shrink(mean: np.ndarray, std: np.ndarray, prior: float) -> np.ndarray:
    mean = np.clip(np.asarray(mean, float), 0.003, 0.997)
    variance = np.asarray(std, float) ** 2 + 2.5e-4
    effective = np.clip(mean * (1.0 - mean) / variance - 1.0, 1.0, 180.0)
    return np.clip((effective * mean + 14.0 * prior) / (effective + 14.0), 0.003, 0.997)


def score_train_test(train: pd.DataFrame, test: pd.DataFrame):
    train = train.copy()
    train["resolved_after_fill"] = np.where(train.filled, train.resolved.astype(float), np.nan)
    own_mean, own_std, own_prior, own_features = ensemble_probability(
        train, test, "win", "ownership", train.filled & train.resolved & train.net_r.notna()
    )
    fill_mean, fill_std, fill_prior, fill_features = ensemble_probability(
        train, test, "filled", "execution"
    )
    resolve_mean, resolve_std, resolve_prior, resolve_features = ensemble_probability(
        train, test, "resolved_after_fill", "execution", train.filled
    )
    duration_train = train[np.isfinite(train.terminal_minutes_label)].copy()
    if len(duration_train) >= 90:
        duration_model = fit_ridge(
            duration_train, np.log1p(duration_train.terminal_minutes_label.to_numpy(float)),
            decision_weights(duration_train), "execution", 24.0,
        )
        duration = np.expm1(duration_model.predict(test))
        duration_features = duration_model.encoder.names
    else:
        duration = np.full(len(test), float(duration_train.terminal_minutes_label.median()) if len(duration_train) else 60.0)
        duration_features = []
    output = test.copy()
    output["p_ownership_raw"] = reliability_shrink(own_mean, own_std, own_prior)
    output["p_ownership_model_std"] = own_std
    output["p_fill"] = reliability_shrink(fill_mean, fill_std, fill_prior)
    output["p_resolve"] = reliability_shrink(resolve_mean, resolve_std, resolve_prior)
    output["predicted_terminal_minutes"] = np.maximum(1.0, duration)
    output["ownership_prior"] = own_prior
    manifest = {
        "ownership_prior": own_prior, "fill_prior": fill_prior, "resolve_prior": resolve_prior,
        "ownership_features": own_features, "fill_features": fill_features,
        "resolve_features": resolve_features, "duration_features": duration_features,
    }
    return output, manifest


def state_soft_targets(frame: pd.DataFrame) -> pd.DataFrame:
    resolved = frame[frame.filled & frame.resolved & frame.net_r.notna()].copy()
    if resolved.empty:
        return frame.iloc[:0].copy()
    keys = ["period", "episode_id", "state_id", "order_time_ns"]
    named = {
        "target": ("win", "mean"), "raw": ("p_ownership_raw", "mean"),
        "prior": ("ownership_prior", "mean"), "phase": ("auction_phase", "first"),
    }
    named["progress"] = ("auction_progress_r", "mean") if "auction_progress_r" in resolved else ("gross_rr", lambda _: 0.0)
    named["failure"] = ("auction_failure_pressure", "mean") if "auction_failure_pressure" in resolved else ("gross_rr", lambda _: 0.0)
    return resolved.groupby(keys, as_index=False).agg(**named).sort_values(keys)


def filter_states(frame: pd.DataFrame, decay: float, evidence_weight: float) -> pd.Series:
    result = pd.Series(index=frame.index, dtype=float)
    for _, episode in frame.groupby(["period", "episode_id"], sort=False):
        episode = episode.sort_values(["order_time_ns", "state_id"])
        previous, previous_progress = None, 0.0
        for index, row in episode.iterrows():
            prior_l = float(logit(safe_float(row.get("prior"), 0.5)))
            raw_l = float(logit(safe_float(row.get("raw"), 0.5)))
            progress = safe_float(row.get("progress"), 0.0)
            failure = safe_float(row.get("failure"), 0.0)
            phase = str(row.get("phase", "UNKNOWN"))
            contradiction = (
                phase == "FAILED_REENTRY" or progress < -0.12
                or (previous_progress > 0.25 and progress < -0.03)
                or (failure > 1.0 and progress <= 0.0)
            )
            if previous is None or contradiction:
                previous = prior_l
            posterior = prior_l + decay * (previous - prior_l) + evidence_weight * (raw_l - prior_l)
            posterior = float(np.clip(posterior, -6.0, 6.0))
            result.loc[index] = float(sigmoid(np.asarray([posterior]))[0])
            previous, previous_progress = posterior, progress
    return result


def choose_filter_parameters(training_states: pd.DataFrame) -> tuple[float, float]:
    if training_states.empty:
        return 0.55, 0.85
    best = (math.inf, 0.55, 0.85)
    target = training_states.target.to_numpy(float)
    for decay in (0.0, 0.35, 0.60, 0.80):
        for evidence in (0.55, 0.80, 1.00, 1.25):
            probability = np.clip(filter_states(training_states, decay, evidence).to_numpy(float), 0.003, 0.997)
            loss = float(-np.mean(target * np.log(probability) + (1.0 - target) * np.log(1.0 - probability)))
            if (loss, decay, evidence) < best:
                best = (loss, decay, evidence)
    return float(best[1]), float(best[2])


def attach_filtered_ownership(scored: pd.DataFrame, parameter_training: pd.DataFrame):
    named = {
        "raw": ("p_ownership_raw", "mean"), "prior": ("ownership_prior", "mean"),
        "phase": ("auction_phase", "first"),
    }
    named["progress"] = ("auction_progress_r", "mean") if "auction_progress_r" in scored else ("gross_rr", lambda _: 0.0)
    named["failure"] = ("auction_failure_pressure", "mean") if "auction_failure_pressure" in scored else ("gross_rr", lambda _: 0.0)
    state = scored.groupby(["period", "episode_id", "state_id", "order_time_ns"], as_index=False).agg(**named)
    decay, evidence = choose_filter_parameters(parameter_training)
    state["p_ownership"] = filter_states(state, decay, evidence).to_numpy(float)
    output = scored.merge(state[["period", "state_id", "p_ownership"]],
                          on=["period", "state_id"], how="left", validate="many_to_one")
    return output, (decay, evidence)


def attach_enter_values(scored: pd.DataFrame) -> pd.DataFrame:
    output = scored.copy()
    target_r = pd.to_numeric(output.planned_target_net_r, errors="coerce").clip(lower=0).fillna(0).to_numpy(float)
    stop_r = pd.to_numeric(output.stop_net_r, errors="coerce").fillna(-1).clip(upper=-0.01).to_numpy(float)
    win_log = np.log(np.maximum(EPS, 1.0 + RISK * target_r))
    loss_log = np.log(np.maximum(EPS, 1.0 + RISK * stop_r))
    control = output.p_ownership.to_numpy(float)
    conditional = control * win_log + (1.0 - control) * loss_log
    output["expected_enter_log"] = output.p_fill.to_numpy(float) * output.p_resolve.to_numpy(float) * conditional
    hours = np.maximum(output.predicted_terminal_minutes.to_numpy(float) / 60.0, 1.0 / 60.0)
    output["expected_enter_log_per_hour"] = output.expected_enter_log.to_numpy(float) / hours
    output["break_even_ownership"] = np.clip(-loss_log / np.maximum(win_log - loss_log, EPS), 0.0, 1.0)
    output["ownership_edge"] = output.p_ownership - output.break_even_ownership
    return output


def state_best(frame: pd.DataFrame) -> pd.DataFrame:
    return (
        frame.sort_values(
            ["period", "state_id", "expected_enter_log", "expected_enter_log_per_hour",
             "p_fill", "planned_target_net_r", "action_id"],
            ascending=[True, True, False, False, False, False, True],
        )
        .drop_duplicates(["period", "state_id"], keep="first")
        .sort_values(["period", "episode_id", "order_time_ns", "state_id"])
        .reset_index(drop=True)
    )


def continuation_targets(states: pd.DataFrame) -> pd.DataFrame:
    output = states.copy()
    output["current_enter_log"] = output.expected_enter_log
    output["current_enter_rate"] = output.expected_enter_log_per_hour
    output["continuation_target"] = 0.0
    for _, episode in output.groupby(["period", "episode_id"], sort=False):
        episode = episode.sort_values(["order_time_ns", "state_id"])
        indices = list(episode.index)
        values, times = episode.expected_enter_log.to_numpy(float), episode.order_time_ns.to_numpy(float)
        for left in range(len(indices)):
            best = 0.0
            for right in range(left + 1, len(indices)):
                hours = max(0.0, (times[right] - times[left]) / NS_PER_HOUR)
                best = max(best, max(0.0, values[right]) * math.exp(-0.035 * hours))
            output.loc[indices[left], "continuation_target"] = best
    return output


def attach_continuation(train_states: pd.DataFrame, test_states: pd.DataFrame) -> pd.DataFrame:
    output = test_states.copy()
    if len(train_states) < 60:
        output["expected_wait_log"] = 0.0
    else:
        model = fit_ridge(train_states, train_states.continuation_target.to_numpy(float),
                          decision_weights(train_states), "continuation", 28.0)
        output["expected_wait_log"] = np.maximum(0.0, model.predict(output))
    output["stopping_advantage"] = output.expected_enter_log - output.expected_wait_log
    return output


def group_summary(frame: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    if frame.empty or column not in frame:
        return []
    return frame.groupby(column).agg(
        trades=("net_r", "size"), target_first_rate=("win", "mean"), mean_net_r=("net_r", "mean")
    ).reset_index().to_dict("records")


def route(states: pd.DataFrame):
    eligible = states[
        (states.expected_enter_log > 0.0) & (states.stopping_advantage > 0.0)
        & ~states.auction_phase.astype(str).eq("FAILED_REENTRY")
    ].copy()
    eligible = eligible.sort_values(
        ["period", "order_time_ns", "stopping_advantage", "expected_enter_log_per_hour",
         "p_ownership", "action_id"], ascending=[True, True, False, False, False, True]
    )
    selected, replaced = [], []
    for _, period_frame in eligible.groupby("period", sort=True):
        active, used = None, set()
        for timestamp, simultaneous in period_frame.groupby("order_time_ns", sort=True):
            timestamp = float(timestamp)
            pool = simultaneous[~simultaneous.episode_id.astype(str).isin(used)]
            if pool.empty:
                continue
            candidate = pool.iloc[0]
            if active is not None:
                terminal = safe_float(active.get("terminal_ns"), timestamp)
                fill_time = safe_float(active.get("fill_time_ns"), math.inf)
                if not bool(active.get("filled", False)):
                    fill_time = math.inf
                if timestamp >= terminal:
                    selected.append(active)
                    used.add(str(active.episode_id))
                    active = None
                elif fill_time <= timestamp:
                    continue
                else:
                    if (
                        str(candidate.episode_id) != str(active.episode_id)
                        and float(candidate.expected_enter_log_per_hour)
                        > float(active.expected_enter_log_per_hour) + 1e-12
                    ):
                        old = active.copy()
                        old["replacement_time_ns"] = timestamp
                        old["replacement_reason"] = "BETTER_INDEPENDENT_CAUSAL_OPPORTUNITY"
                        replaced.append(old)
                        used.add(str(active.episode_id))
                        active = candidate
                    continue
            if active is None:
                active = candidate
        if active is not None:
            selected.append(active)
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else eligible.iloc[:0].copy()
    replacements = pd.DataFrame(replaced).reset_index(drop=True) if replaced else eligible.iloc[:0].copy()
    trades = orders[orders.resolved & orders.net_r.notna()].copy().sort_values("terminal_ns").reset_index(drop=True)
    nav = peak = 1.0
    maximum_drawdown = 0.0
    for result in pd.to_numeric(trades.net_r, errors="coerce").dropna():
        nav *= max(EPS, 1.0 + RISK * float(result))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    calendar_days = 0
    for _, group in states.groupby("period"):
        values = pd.to_numeric(group.order_time_ns, errors="coerce").dropna().astype(np.int64)
        if len(values):
            calendar_days += max(1, int(math.ceil((values.max() - values.min()) / (24 * NS_PER_HOUR))) + 1)
    negative = abs(float(trades.loc[trades.net_r < 0, "net_r"].sum())) if len(trades) else 0.0
    summary = {
        "selected_orders": int(len(orders)), "replaced_pending_orders": int(len(replacements)),
        "closed_trades": int(len(trades)), "calendar_days": int(calendar_days),
        "trades_per_day": float(len(trades) / max(calendar_days, 1)),
        "target_first_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_net_r": float(trades.net_r.median()) if len(trades) else None,
        "mean_planned_gross_rr": float(trades.gross_rr.mean()) if len(trades) and "gross_rr" in trades else None,
        "median_hold_minutes": float(trades.holding_minutes.median()) if len(trades) and "holding_minutes" in trades else None,
        "mean_hold_minutes": float(trades.holding_minutes.mean()) if len(trades) and "holding_minutes" in trades else None,
        "ending_nav_multiplier": float(nav), "maximum_drawdown": float(maximum_drawdown),
        "profit_factor_r": float(trades.loc[trades.net_r > 0, "net_r"].sum()) / max(negative, EPS) if len(trades) else None,
        "by_period": group_summary(trades, "period"), "by_family": group_summary(trades, "family"),
        "by_phase": group_summary(trades, "auction_phase"), "by_symbol": group_summary(trades, "symbol"),
    }
    return orders, trades, replacements, summary


def keep_columns(frame: pd.DataFrame) -> list[str]:
    preferred = [
        "period", "symbol", "episode_id", "state_id", "action_id", "family", "side",
        "auction_phase", "order_time_ns", "entry_geometry", "entry", "stop", "target",
        "gross_rr", "planned_target_net_r", "p_ownership_raw", "p_ownership", "p_fill",
        "p_resolve", "expected_enter_log", "expected_enter_log_per_hour", "expected_wait_log",
        "stopping_advantage", "fill_state", "outcome", "filled", "resolved", "win", "net_r",
        "entry_wait_minutes", "holding_minutes", "auction_progress_r", "auction_retrace_fraction",
        "auction_outside_close_ratio", "auction_outside_volume_ratio", "auction_path_efficiency",
        "auction_effort_result", "terminal_ns", "fill_time_ns",
    ]
    return [column for column in preferred if column in frame]


def diagnostics(states: pd.DataFrame, orders: pd.DataFrame, trades: pd.DataFrame):
    keep = keep_columns(states)
    losses = trades[pd.to_numeric(trades.net_r, errors="coerce") <= 0.0][keep].copy() if len(trades) else states.iloc[:0][keep].copy()
    selected = set(zip(orders.period.astype(str), orders.state_id.astype(str))) if len(orders) else set()
    missed = states[states.resolved & states.net_r.notna() & (pd.to_numeric(states.net_r, errors="coerce") > 0.0)].copy()
    mask = [(str(p), str(s)) not in selected for p, s in zip(missed.period, missed.state_id)]
    missed = missed.loc[mask, keep].sort_values(["period", "net_r"], ascending=[True, False])
    missed = missed.groupby("period", as_index=False, group_keys=False).head(200)
    return losses, missed


def input_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.csv"))
    }


def run(development_root: Path, fresh_root: Path | None, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = load_actions(development_root)
    periods = sorted(development.period.unique())
    if len(periods) < 2:
        raise ValueError("candidate 4t requires at least two separated development periods")
    oof_parts, manifests = [], {}
    for period in periods:
        scored, manifest = score_train_test(
            development[development.period != period], development[development.period == period]
        )
        oof_parts.append(scored)
        manifests[f"development_holdout:{period}"] = manifest
    development_scored = pd.concat(oof_parts, ignore_index=True, sort=False)
    state_targets = state_soft_targets(development_scored)
    filtered_parts, filter_parameters = [], {}
    for period in periods:
        part, params = attach_filtered_ownership(
            development_scored[development_scored.period == period].copy(),
            state_targets[state_targets.period != period],
        )
        filtered_parts.append(part)
        filter_parameters[f"development_holdout:{period}"] = {
            "decay": params[0], "evidence_weight": params[1]
        }
    development_scored = attach_enter_values(pd.concat(filtered_parts, ignore_index=True, sort=False))
    development_states_raw = continuation_targets(state_best(development_scored))
    state_parts = []
    for period in periods:
        state_parts.append(attach_continuation(
            development_states_raw[development_states_raw.period != period],
            development_states_raw[development_states_raw.period == period],
        ))
    development_states = pd.concat(state_parts, ignore_index=True, sort=False)
    dev_orders, dev_trades, dev_replacements, dev_summary = route(development_states)
    dev_losses, dev_missed = diagnostics(development_states, dev_orders, dev_trades)
    result: dict[str, Any] = {
        "policy": "CANDIDATE_4T_SEQUENTIAL_COMPETING_HYPOTHESIS_AUCTION_CONTROL",
        "development_oof": dev_summary, "filter_parameters": filter_parameters,
        "manifests": manifests, "input_hashes": {"development": input_hashes(development_root)},
    }
    development_scored.to_csv(output / "development_action_scores.csv", index=False)
    development_states.to_csv(output / "development_states.csv", index=False)
    dev_orders.to_csv(output / "development_orders.csv", index=False)
    dev_trades.to_csv(output / "development_trades.csv", index=False)
    dev_replacements.to_csv(output / "development_replacements.csv", index=False)
    dev_losses.to_csv(output / "development_loss_clinic.csv", index=False)
    dev_missed.to_csv(output / "development_missed_opportunity_clinic.csv", index=False)
    if fresh_root is not None:
        fresh = load_actions(fresh_root)
        fresh_scored, manifest = score_train_test(development, fresh)
        manifests["fresh"] = manifest
        fresh_scored, params = attach_filtered_ownership(fresh_scored, state_targets)
        filter_parameters["fresh"] = {"decay": params[0], "evidence_weight": params[1]}
        fresh_scored = attach_enter_values(fresh_scored)
        fresh_states = attach_continuation(
            development_states_raw, continuation_targets(state_best(fresh_scored))
        )
        orders, trades, replacements, summary = route(fresh_states)
        losses, missed = diagnostics(fresh_states, orders, trades)
        fresh_scored.to_csv(output / "fresh_action_scores.csv", index=False)
        fresh_states.to_csv(output / "fresh_states.csv", index=False)
        orders.to_csv(output / "fresh_orders.csv", index=False)
        trades.to_csv(output / "fresh_trades.csv", index=False)
        replacements.to_csv(output / "fresh_replacements.csv", index=False)
        losses.to_csv(output / "fresh_loss_clinic.csv", index=False)
        missed.to_csv(output / "fresh_missed_opportunity_clinic.csv", index=False)
        result["fresh"] = summary
        result["input_hashes"]["fresh"] = input_hashes(fresh_root)
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (output / "model_manifest.json").write_text(
        json.dumps({"manifests": manifests, "filter_parameters": filter_parameters},
                   ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    (output / "RESULT.md").write_text(
        "# Candidate 4t diagnostic result\n\n"
        "Actual one-account route from the committed policy. Separated development windows "
        "are diagnostics; `fresh` is untouched by model fitting.\n\n```json\n"
        + json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n```\n",
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
