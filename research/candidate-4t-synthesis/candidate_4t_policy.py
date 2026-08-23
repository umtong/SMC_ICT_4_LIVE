#!/usr/bin/env python3
"""Candidate 4t: counterfactual auction ownership and one-account routing.

A broad crypto-market impulse can coincide with a local liquidity interaction,
but coincidence does not establish that the interacted auction owns the move.
Candidate 4t estimates full, local-only and common-market-only ownership beliefs,
removes positive evidence explainable by the common market alone, and accumulates
the residual through the causal episode. Belief resets only on contradictory
price/volume response, never elapsed time. Immutable action geometry is priced
after ownership attribution. Separate models estimate fill, resolution,
target-before-stop and account occupation. The only entry criterion is positive
expected post-cost log NAV growth versus cash.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

RISK = 0.03
EPS = 1e-12
NS_PER_MINUTE = 60_000_000_000
CASCADE_MINUTES = 4

ID_OR_ABSOLUTE = {
    "action_id", "state_id", "episode_id", "symbol", "period",
    "entry", "stop", "target", "route_price", "arm_index",
    "fill_index", "resolution_index", "departure_index",
    "departure_time_ns", "order_time_ns", "fill_time_ns",
    "resolution_time_ns", "order_terminal_time_ns", "terminal_ns",
    "interaction_time_ns", "emission_time_ns",
}
LABEL_TOKENS = (
    "outcome", "fill_state", "filled", "resolved", "target_first",
    "win", "net_r", "mfe", "mae", "holding", "entry_wait",
    "actual_", "future_", "terminal_minutes_label", "realized",
    "diagnostic_", "oracle", "label",
)
GEOMETRY_COLUMNS = {
    "gross_rr", "risk_bps", "route_rr", "planned_target_net_r",
    "target_net_r", "stop_net_r", "entry_geometry", "route_kind",
    "route_utilization", "exact_route_target",
}
COMMON_MARKET_TOKENS = (
    "common_", "breadth", "cross_symbol", "cross_market", "market_factor",
    "market_return", "benchmark", "btc_lead", "leader_", "laggard_",
    "systematic_", "index_return", "factor_", "universe_",
)
CONTRADICTION_PHASES = {"FAILED_REENTRY", "INVALIDATED", "ABANDONED"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _sigmoid(values: np.ndarray | float) -> np.ndarray:
    x = np.clip(np.asarray(values, dtype=float), -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-x))


def _logit(values: np.ndarray | float) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 1e-5, 1.0 - 1e-5)
    return np.log(p / (1.0 - p))


def _numeric_series(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column in frame:
        return pd.to_numeric(frame[column], errors="coerce")
    return pd.Series(default, index=frame.index, dtype=float)


def _period_from_path(path: Path, root: Path) -> str:
    candidates = [path.parent.name, *[item.name for item in path.parents if item != root.parent]]
    for name in candidates:
        low = name.lower()
        if any(token in low for token in ("dev-", "fresh-", "holdout", "202")):
            for prefix in (
                "candidate-4t-dev-", "candidate-4t-fresh-",
                "candidate-1k-dev-", "candidate-1k-fresh-",
            ):
                if low.startswith(prefix):
                    return name[len(prefix):]
            return name
    return path.parent.name


def _read_candidate_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path, low_memory=False)
    except Exception:
        return None
    required = {"action_id", "state_id", "episode_id", "order_time_ns"}
    return frame if required.issubset(frame.columns) else None


def load_actions(root: Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob("*.csv")):
        frame = _read_candidate_csv(path)
        if frame is None or frame.empty:
            continue
        frame = frame.copy()
        frame["period"] = _period_from_path(path, root)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No action CSVs below {root}")
    output = pd.concat(frames, ignore_index=True, sort=False)
    output = output.drop_duplicates("action_id", keep="last").reset_index(drop=True)
    output["order_time_ns"] = pd.to_numeric(output.order_time_ns, errors="coerce")
    output = output[output.order_time_ns.notna()].copy()
    output["order_time_ns"] = output.order_time_ns.astype(np.int64)

    fill_state = output.get("fill_state", pd.Series("", index=output.index)).fillna("").astype(str)
    outcome = output.get("outcome", pd.Series("", index=output.index)).fillna("").astype(str)
    if "filled" in output:
        output["filled"] = output.filled.astype(str).str.lower().isin({"true", "1", "yes"})
    else:
        output["filled"] = ~fill_state.str.contains("UNFILLED|CANCELED|NOT_AVAILABLE|EXPIRED", regex=True)
    if "resolved" in output:
        output["resolved"] = output.resolved.astype(str).str.lower().isin({"true", "1", "yes"})
    else:
        output["resolved"] = outcome.str.contains("TARGET|STOP", regex=True)
    if "win" in output:
        raw = output.win
        if pd.api.types.is_bool_dtype(raw):
            output["win"] = raw.astype(float)
        else:
            numeric = pd.to_numeric(raw, errors="coerce")
            text = raw.astype(str).str.upper()
            output["win"] = numeric.where(numeric.notna(), text.str.contains("TRUE|TARGET").astype(float))
    else:
        output["win"] = outcome.str.contains("TARGET", regex=True).astype(float)
    output.loc[~(output.filled & output.resolved), "win"] = np.nan
    output["net_r"] = _numeric_series(output, "net_r", np.nan)

    for column in ("fill_time_ns", "resolution_time_ns", "terminal_ns", "order_terminal_time_ns"):
        if column in output:
            output[column] = pd.to_numeric(output[column], errors="coerce")
    if "terminal_ns" not in output:
        if "resolution_time_ns" in output:
            output["terminal_ns"] = output.resolution_time_ns
        elif "order_terminal_time_ns" in output:
            output["terminal_ns"] = output.order_terminal_time_ns
        else:
            output["terminal_ns"] = np.nan
    fallback = (
        output.order_terminal_time_ns
        if "order_terminal_time_ns" in output
        else output.order_time_ns + 24 * 60 * NS_PER_MINUTE
    )
    output["terminal_ns"] = pd.to_numeric(output.terminal_ns, errors="coerce")
    output["terminal_ns"] = output.terminal_ns.fillna(pd.to_numeric(fallback, errors="coerce"))
    output["terminal_ns"] = output.terminal_ns.fillna(output.order_time_ns + 24 * 60 * NS_PER_MINUTE)
    output["terminal_minutes_label"] = np.maximum(
        1.0,
        (output.terminal_ns.to_numpy(float) - output.order_time_ns.to_numpy(float)) / NS_PER_MINUTE,
    )
    output["resolved_after_fill_label"] = np.where(output.filled, output.resolved.astype(float), np.nan)
    for column in ("auction_phase", "family", "side", "entry_geometry", "route_kind"):
        if column not in output:
            output[column] = "UNKNOWN"
    return output.replace([np.inf, -np.inf], np.nan)


def _state_label(group: pd.DataFrame) -> pd.Series:
    resolved = group[group.filled & group.resolved & group.win.notna()]
    if resolved.empty:
        label = np.nan
        confidence = 0.0
        median_net = np.nan
    else:
        wins = pd.to_numeric(resolved.win, errors="coerce").dropna()
        net = pd.to_numeric(resolved.net_r, errors="coerce").dropna()
        rate = float(wins.mean()) if len(wins) else 0.0
        median_net = float(net.median()) if len(net) else np.nan
        label = float(rate >= 0.5 and (not math.isfinite(median_net) or median_net > 0.0))
        confidence = min(1.0, math.sqrt(len(resolved)) / 2.0)
    first = group.sort_values(["order_time_ns", "action_id"]).iloc[0].copy()
    first["ownership_label"] = label
    first["ownership_weight"] = max(0.25, confidence)
    first["state_resolved_actions"] = int(len(resolved))
    first["state_median_net_r"] = median_net
    return first


def state_table(actions: pd.DataFrame) -> pd.DataFrame:
    rows = [_state_label(group) for _, group in actions.groupby(["period", "state_id"], sort=False)]
    states = pd.DataFrame(rows) if rows else actions.iloc[:0].copy()
    return states.sort_values(["period", "episode_id", "order_time_ns", "state_id"]).reset_index(drop=True)


def _is_label(column: str) -> bool:
    low = column.lower()
    return any(token in low for token in LABEL_TOKENS)


def numeric_features(frame: pd.DataFrame, *, ownership: bool) -> list[str]:
    output: list[str] = []
    periods = list(frame.period.astype(str).unique()) if "period" in frame else []
    for column in frame.columns:
        if column in ID_OR_ABSOLUTE or _is_label(column):
            continue
        if ownership and column in GEOMETRY_COLUMNS:
            continue
        low = column.lower()
        if low.endswith("_time_ns") or low.endswith("_index"):
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().sum() < max(30, int(0.04 * len(frame))):
            continue
        if values.nunique(dropna=True) <= 1:
            continue
        if periods:
            stable = 0
            for period in periods:
                section = values[frame.period.astype(str) == period]
                if section.notna().sum() >= 15 and section.nunique(dropna=True) > 1:
                    stable += 1
            if stable < min(2, len(periods)):
                continue
        output.append(column)
    return output


def common_features(columns: Sequence[str]) -> list[str]:
    return [column for column in columns if any(token in column.lower() for token in COMMON_MARKET_TOKENS)]


def local_features(columns: Sequence[str]) -> list[str]:
    common = set(common_features(columns))
    return [column for column in columns if column not in common]


@dataclass
class MatrixEncoder:
    numeric: list[str]
    categorical: list[str]
    medians: np.ndarray
    scales: np.ndarray
    missing_indicators: list[str]
    categories: dict[str, list[str]]

    @classmethod
    def fit(cls, frame: pd.DataFrame, numeric: Sequence[str], categorical: Sequence[str]) -> "MatrixEncoder":
        numeric = [column for column in numeric if column in frame]
        categorical = [column for column in categorical if column in frame]
        medians: list[float] = []
        scales: list[float] = []
        missing: list[str] = []
        for column in numeric:
            values = pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
            finite = values[np.isfinite(values)]
            median = float(np.median(finite)) if finite.size else 0.0
            mad = float(np.median(np.abs(finite - median))) if finite.size else 1.0
            medians.append(median)
            scales.append(max(1e-6, 1.4826 * mad))
            if np.mean(~np.isfinite(values)) >= 0.04:
                missing.append(column)
        categories: dict[str, list[str]] = {}
        for column in categorical:
            counts = frame[column].fillna("__NA__").astype(str).value_counts()
            categories[column] = sorted(
                counts[counts >= max(3, int(0.002 * len(frame)))].index.tolist()
            )[:48]
        return cls(
            list(numeric), list(categorical), np.asarray(medians), np.asarray(scales), missing, categories,
        )

    def transform(self, frame: pd.DataFrame) -> np.ndarray:
        parts: list[np.ndarray] = [np.ones((len(frame), 1), dtype=float)]
        if self.numeric:
            raw = np.column_stack([
                pd.to_numeric(frame[column], errors="coerce").to_numpy(float)
                if column in frame else np.full(len(frame), np.nan)
                for column in self.numeric
            ])
            finite = np.isfinite(raw)
            filled = np.where(finite, raw, self.medians)
            parts.append(np.clip((filled - self.medians) / self.scales, -12.0, 12.0))
            if self.missing_indicators:
                indices = [self.numeric.index(column) for column in self.missing_indicators]
                parts.append((~finite[:, indices]).astype(float))
        for column in self.categorical:
            values = (
                frame[column] if column in frame else pd.Series("__NA__", index=frame.index)
            ).fillna("__NA__").astype(str)
            cats = self.categories.get(column, [])
            if cats:
                parts.append(np.column_stack([(values == value).to_numpy(float) for value in cats]))
        return np.column_stack(parts)


@dataclass
class LogisticEnsemble:
    encoder: MatrixEncoder
    coefficients: list[np.ndarray]
    prior: float
    prior_uncertainty: float

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.coefficients:
            return np.full(len(frame), self.prior), np.full(len(frame), self.prior_uncertainty)
        x = self.encoder.transform(frame)
        matrix = np.vstack([_sigmoid(x @ beta) for beta in self.coefficients])
        mean = 0.88 * matrix.mean(axis=0) + 0.12 * self.prior
        std = np.sqrt(matrix.var(axis=0) + self.prior_uncertainty**2)
        return np.clip(mean, 0.005, 0.995), std


def fit_logistic(
    frame: pd.DataFrame,
    label: str,
    numeric: Sequence[str],
    categorical: Sequence[str],
    weight: str | None = None,
) -> LogisticEnsemble:
    work = frame[frame[label].notna()].copy()
    positives = float(pd.to_numeric(work[label], errors="coerce").fillna(0.0).sum())
    prior = (positives + 6.0) / (len(work) + 12.0) if len(work) else 0.5
    uncertainty = math.sqrt(prior * (1.0 - prior) / max(len(work) + 12.0, 1.0))
    encoder = MatrixEncoder.fit(work, numeric, categorical)
    if len(work) < 80 or work[label].nunique() < 2:
        return LogisticEnsemble(encoder, [], prior, uncertainty)
    x = encoder.transform(work)
    y = pd.to_numeric(work[label], errors="coerce").to_numpy(float)
    if weight and weight in work:
        w = pd.to_numeric(work[weight], errors="coerce").fillna(1.0).clip(0.05, 10.0).to_numpy(float)
    else:
        w = np.ones(len(work), dtype=float)
    if "state_id" in work and label != "ownership_label":
        counts = work.groupby("state_id").state_id.transform("size").to_numpy(float)
        w = w / np.maximum(counts, 1.0)
    w = w / max(float(w.mean()), EPS)
    coefficients: list[np.ndarray] = []
    for l2 in (0.8, 2.5, 8.0):
        beta = np.zeros(x.shape[1], dtype=float)
        beta[0] = float(_logit(prior))
        previous = np.inf
        for iteration in range(360):
            p = _sigmoid(x @ beta)
            gradient = (x.T @ ((p - y) * w)) / max(float(w.sum()), 1.0)
            regularization = beta.copy()
            regularization[0] = 0.0
            gradient += (l2 / max(len(work), 1)) * regularization
            curvature = np.mean(w * p * (1.0 - p))
            step = min(0.28, 0.035 / max(curvature, 0.03)) / math.sqrt(1.0 + iteration / 90.0)
            beta -= step * np.clip(gradient, -4.0, 4.0)
            if iteration % 20 == 0:
                loss = -float(np.mean(w * (y * np.log(p + EPS) + (1.0 - y) * np.log(1.0 - p + EPS))))
                if abs(previous - loss) < 1e-7:
                    break
                previous = loss
        coefficients.append(beta)
    return LogisticEnsemble(encoder, coefficients, prior, uncertainty)


@dataclass
class LinearEnsemble:
    encoder: MatrixEncoder
    coefficients: list[np.ndarray]
    fallback: float

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        if not self.coefficients:
            return np.full(len(frame), self.fallback), np.zeros(len(frame))
        x = self.encoder.transform(frame)
        matrix = np.vstack([np.expm1(np.clip(x @ beta, 0.0, 10.0)) for beta in self.coefficients])
        return np.maximum(1.0, matrix.mean(axis=0)), matrix.std(axis=0)


def fit_duration(frame: pd.DataFrame, numeric: Sequence[str], categorical: Sequence[str]) -> LinearEnsemble:
    work = frame[_numeric_series(frame, "terminal_minutes_label", np.nan).notna()].copy()
    fallback = float(_numeric_series(work, "terminal_minutes_label", 60.0).median()) if len(work) else 60.0
    encoder = MatrixEncoder.fit(work, numeric, categorical)
    if len(work) < 80:
        return LinearEnsemble(encoder, [], fallback)
    x = encoder.transform(work)
    y = np.log1p(_numeric_series(work, "terminal_minutes_label", 60.0).clip(lower=1.0).to_numpy(float))
    w = 1.0 / np.maximum(work.groupby("state_id").state_id.transform("size").to_numpy(float), 1.0)
    root = np.sqrt(w)[:, None]
    xw = x * root
    yw = y * root[:, 0]
    identity = np.eye(x.shape[1])
    identity[0, 0] = 0.0
    coefficients: list[np.ndarray] = []
    for l2 in (2.0, 10.0):
        matrix = xw.T @ xw + l2 * identity
        vector = xw.T @ yw
        try:
            beta = np.linalg.solve(matrix, vector)
        except np.linalg.LinAlgError:
            beta = np.linalg.pinv(matrix) @ vector
        coefficients.append(beta)
    return LinearEnsemble(encoder, coefficients, fallback)


def _ownership_categorical(frame: pd.DataFrame) -> list[str]:
    return [
        column for column in (
            "family", "side", "auction_phase", "setup_kind", "location_kind", "source_pool_kind",
        ) if column in frame
    ]


def fit_ownership_models(states: pd.DataFrame) -> dict[str, Any]:
    numeric = numeric_features(states, ownership=True)
    common = common_features(numeric)
    local = local_features(numeric)
    categorical = _ownership_categorical(states)
    return {
        "full": fit_logistic(states, "ownership_label", numeric, categorical, "ownership_weight"),
        "local": fit_logistic(states, "ownership_label", local, categorical, "ownership_weight"),
        "common": fit_logistic(states, "ownership_label", common, [], "ownership_weight"),
        "numeric": numeric,
        "common_columns": common,
        "local_columns": local,
        "categorical": categorical,
    }


def contradiction_mask(states: pd.DataFrame) -> np.ndarray:
    phase = states.get("auction_phase", pd.Series("UNKNOWN", index=states.index)).fillna("UNKNOWN").astype(str)
    progress = _numeric_series(states, "auction_progress_r").fillna(0.0)
    failure = _numeric_series(states, "auction_failure_pressure").fillna(0.0)
    retrace = _numeric_series(states, "auction_retrace_fraction").fillna(0.0)
    efficiency = _numeric_series(states, "auction_path_efficiency").fillna(0.0)
    return (
        phase.isin(CONTRADICTION_PHASES)
        | (progress < -0.08)
        | ((failure > 0.90) & (retrace > 0.55))
        | ((retrace > 0.90) & (efficiency < 0.0))
    ).to_numpy(bool)


def predict_ownership(models: dict[str, Any], states: pd.DataFrame) -> pd.DataFrame:
    output = states.reset_index(drop=True).copy()
    full, full_std = models["full"].predict(output)
    local, local_std = models["local"].predict(output)
    common, common_std = models["common"].predict(output)
    prior = float(models["full"].prior)
    prior_logit = float(_logit(prior))
    common_uplift = np.maximum(0.0, _logit(common) - prior_logit)
    residual_logit = _logit(full) - common_uplift
    counterfactual = _sigmoid(residual_logit)
    support = np.sqrt(np.clip(full * local, 1e-6, 1.0))
    counterfactual = np.minimum(counterfactual, support + 0.08)
    uncertainty = np.sqrt(full_std**2 + local_std**2 + common_std**2)
    counterfactual = np.clip(counterfactual - 0.20 * uncertainty, 0.005, 0.995)

    output["p_ownership_full"] = full
    output["p_ownership_local"] = local
    output["p_ownership_common"] = common
    output["p_ownership_counterfactual"] = counterfactual
    output["common_only_positive_logit_uplift"] = common_uplift
    output["ownership_uncertainty"] = uncertainty

    contradiction = contradiction_mask(output)
    sequential = np.full(len(output), prior, dtype=float)
    groups = output.groupby(["period", "episode_id"], sort=False).groups
    for positions in groups.values():
        ordered = sorted(
            positions,
            key=lambda index: (int(output.at[index, "order_time_ns"]), str(output.at[index, "state_id"])),
        )
        belief = prior_logit
        for position in ordered:
            if contradiction[position]:
                belief = prior_logit
            evidence = float(_logit(counterfactual[position]) - prior_logit)
            belief = prior_logit + 0.62 * (belief - prior_logit) + evidence
            belief = float(np.clip(belief, -8.0, 8.0))
            sequential[position] = float(_sigmoid(belief))
    output["auction_contradiction"] = contradiction
    output["p_ownership_sequential"] = np.clip(sequential, 0.005, 0.995)
    return output


def training_sequence_predictions(states: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    periods = sorted(states.period.astype(str).unique())
    for period in periods:
        test = states[states.period.astype(str) == period]
        train = states[states.period.astype(str) != period]
        if train.empty:
            train = states
        pieces.append(predict_ownership(fit_ownership_models(train), test))
    return pd.concat(pieces, ignore_index=True, sort=False) if pieces else states.iloc[:0].copy()


def merge_state_belief(actions: pd.DataFrame, beliefs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "period", "state_id", "p_ownership_full", "p_ownership_local",
        "p_ownership_common", "p_ownership_counterfactual",
        "p_ownership_sequential", "common_only_positive_logit_uplift",
        "ownership_uncertainty", "auction_contradiction",
    ]
    available = [column for column in columns if column in beliefs]
    return actions.merge(
        beliefs[available].drop_duplicates(["period", "state_id"]),
        on=["period", "state_id"], how="left",
    )


def action_feature_sets(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    numeric = numeric_features(frame, ownership=False)
    raw_common = set(common_features(numeric))
    numeric = [column for column in numeric if column not in raw_common]
    for column in (
        "p_ownership_counterfactual", "p_ownership_sequential",
        "ownership_uncertainty", "common_only_positive_logit_uplift",
    ):
        if column in frame and column not in numeric:
            numeric.append(column)
    categorical = [
        column for column in (
            "family", "side", "auction_phase", "entry_geometry", "route_kind",
            "setup_kind", "location_kind", "source_pool_kind",
        ) if column in frame
    ]
    return numeric, categorical


def score_actions(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    numeric, categorical = action_feature_sets(train)
    fill_model = fit_logistic(train, "filled", numeric, categorical)
    filled_train = train[train.filled].copy()
    resolve_model = fit_logistic(
        filled_train, "resolved_after_fill_label", numeric, categorical,
    )
    resolved_train = train[train.filled & train.resolved & train.win.notna()].copy()
    win_model = fit_logistic(resolved_train, "win", numeric, categorical)
    duration_model = fit_duration(train, numeric, categorical)

    output = test.copy()
    p_fill, fill_std = fill_model.predict(output)
    p_resolve, resolve_std = resolve_model.predict(output)
    p_win, win_std = win_model.predict(output)
    duration, duration_std = duration_model.predict(output)
    output["p_fill"] = np.clip(p_fill - 0.15 * fill_std, 0.005, 0.995)
    output["p_resolve"] = np.clip(p_resolve - 0.15 * resolve_std, 0.005, 0.995)
    output["p_target_first"] = np.clip(p_win - 0.20 * win_std, 0.005, 0.995)
    output["predicted_terminal_minutes"] = np.maximum(1.0, duration + 0.15 * duration_std)

    target_source = "planned_target_net_r" if "planned_target_net_r" in output else "target_net_r"
    target_r = _numeric_series(output, target_source).fillna(0.0).clip(lower=0.0).to_numpy(float)
    stop_r = _numeric_series(output, "stop_net_r", -1.0).fillna(-1.0).clip(-2.0, -0.05).to_numpy(float)
    win_log = np.log1p(RISK * target_r)
    loss_log = np.log1p(RISK * stop_r)
    denominator = np.maximum(win_log - loss_log, EPS)
    output["break_even_target_probability"] = np.clip(-loss_log / denominator, 0.0, 1.0)
    output["target_probability_edge"] = (
        output.p_target_first.to_numpy(float)
        - output.break_even_target_probability.to_numpy(float)
    )
    terminal_expectation = (
        output.p_target_first.to_numpy(float) * win_log
        + (1.0 - output.p_target_first.to_numpy(float)) * loss_log
    )
    output["expected_log_growth"] = (
        output.p_fill.to_numpy(float)
        * output.p_resolve.to_numpy(float)
        * terminal_expectation
    )
    output["expected_log_growth_per_hour"] = (
        output.expected_log_growth.to_numpy(float)
        / np.maximum(output.predicted_terminal_minutes.to_numpy(float) / 60.0, 1.0 / 60.0)
    )
    output["planned_win_log"] = win_log
    output["planned_loss_log"] = loss_log
    return output


def _build_training_actions(actions: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    return merge_state_belief(actions, training_sequence_predictions(states))


def outer_fold_score(development: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    periods = sorted(development.period.astype(str).unique())
    for period in periods:
        train_actions = development[development.period.astype(str) != period].copy()
        test_actions = development[development.period.astype(str) == period].copy()
        train_states = state_table(train_actions)
        test_states = state_table(test_actions)
        train_ready = _build_training_actions(train_actions, train_states)
        test_beliefs = predict_ownership(fit_ownership_models(train_states), test_states)
        pieces.append(score_actions(train_ready, merge_state_belief(test_actions, test_beliefs)))
    return pd.concat(pieces, ignore_index=True, sort=False)


def fresh_score(development: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    development_states = state_table(development)
    fresh_states = state_table(fresh)
    train_ready = _build_training_actions(development, development_states)
    fresh_beliefs = predict_ownership(fit_ownership_models(development_states), fresh_states)
    return score_actions(train_ready, merge_state_belief(fresh, fresh_beliefs))


def _state_best(scored: pd.DataFrame) -> pd.DataFrame:
    work = scored[_numeric_series(scored, "gross_rr").fillna(0.0) >= 1.0].copy()
    sort_columns = [
        column for column in (
            "period", "state_id", "expected_log_growth_per_hour", "expected_log_growth",
            "p_target_first", "planned_target_net_r",
        ) if column in work
    ]
    ascending = [column in {"period", "state_id"} for column in sort_columns]
    work = work.sort_values(sort_columns, ascending=ascending)
    return work.drop_duplicates(["period", "state_id"], keep="first").reset_index(drop=True)


def _cluster_market_episodes(states: pd.DataFrame) -> pd.DataFrame:
    output = states.sort_values(["period", "order_time_ns", "state_id"]).copy()
    cluster_ids: dict[int, str] = {}
    for period, group in output.groupby("period", sort=True):
        cluster_start: dict[str, int] = {}
        current: dict[str, int] = {}
        counters = {"LONG": 0, "SHORT": 0, "UNKNOWN": 0}
        for index, row in group.iterrows():
            side = str(row.get("side", "UNKNOWN"))
            timestamp = int(row.order_time_ns)
            start = cluster_start.get(side)
            if start is None or timestamp - start > CASCADE_MINUTES * NS_PER_MINUTE:
                counters.setdefault(side, 0)
                counters[side] += 1
                current[side] = counters[side]
                cluster_start[side] = timestamp
            cluster_ids[index] = f"{period}:{side}:MKT{current[side]}"
    output["market_episode_id"] = pd.Series(cluster_ids)
    return output


def route(scored: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    states = _cluster_market_episodes(_state_best(scored))
    eligible = states[
        (_numeric_series(states, "expected_log_growth") > 0.0)
        & (_numeric_series(states, "expected_log_growth_per_hour") > 0.0)
        & ~states.auction_phase.astype(str).isin(CONTRADICTION_PHASES)
        & ~states.auction_contradiction.fillna(False).astype(bool)
    ].copy()
    first_positive = (
        eligible.sort_values(
            ["period", "episode_id", "order_time_ns", "expected_log_growth_per_hour"],
            ascending=[True, True, True, False],
        )
        .drop_duplicates(["period", "episode_id"], keep="first")
        .sort_values(
            ["period", "order_time_ns", "expected_log_growth_per_hour", "expected_log_growth"],
            ascending=[True, True, False, False],
        )
        .reset_index(drop=True)
    )

    selected: list[pd.Series] = []
    replacements: list[pd.Series] = []
    for _, group in first_positive.groupby("period", sort=True):
        active: pd.Series | None = None
        used_episodes: set[str] = set()
        used_market: set[str] = set()
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            timestamp = int(timestamp)
            pool = simultaneous[
                ~simultaneous.episode_id.astype(str).isin(used_episodes)
                & ~simultaneous.market_episode_id.astype(str).isin(used_market)
            ]
            if pool.empty:
                continue
            candidate = pool.sort_values(
                ["expected_log_growth_per_hour", "expected_log_growth", "p_target_first", "planned_target_net_r"],
                ascending=[False, False, False, False],
            ).iloc[0]
            if active is not None:
                terminal = int(_finite(active.get("terminal_ns"), timestamp))
                fill_time = _finite(active.get("fill_time_ns"), np.inf)
                if timestamp >= terminal:
                    selected.append(active)
                    used_episodes.add(str(active.episode_id))
                    used_market.add(str(active.market_episode_id))
                    active = None
                elif fill_time <= timestamp:
                    continue
                else:
                    independent = (
                        str(candidate.episode_id) != str(active.episode_id)
                        and str(candidate.market_episode_id) != str(active.market_episode_id)
                    )
                    stronger = (
                        float(candidate.expected_log_growth_per_hour)
                        > float(active.expected_log_growth_per_hour) + EPS
                    )
                    if independent and stronger:
                        replaced = active.copy()
                        replaced["replacement_time_ns"] = timestamp
                        replaced["replacement_reason"] = "HIGHER_CAUSAL_ACCOUNT_TIME_VALUE"
                        replacements.append(replaced)
                        used_episodes.add(str(active.episode_id))
                        used_market.add(str(active.market_episode_id))
                        active = candidate
                    continue
            if active is None:
                active = candidate
        if active is not None:
            selected.append(active)

    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else first_positive.iloc[:0].copy()
    replacement_frame = (
        pd.DataFrame(replacements).reset_index(drop=True)
        if replacements else orders.iloc[:0].copy()
    )
    trades = orders[orders.filled & orders.resolved & orders.net_r.notna()].copy().reset_index(drop=True)

    nav = peak = 1.0
    maximum_drawdown = 0.0
    for _, trade in trades.sort_values(["period", "terminal_ns", "order_time_ns"]).iterrows():
        nav *= max(EPS, 1.0 + RISK * float(trade.net_r))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    if len(scored):
        t0 = int(_numeric_series(scored, "order_time_ns").min())
        t1 = int(_numeric_series(scored, "terminal_ns").max())
        calendar_days = max(1, int(math.ceil((t1 - t0) / (1440 * NS_PER_MINUTE))))
    else:
        calendar_days = 0
    summary = {
        "selected_orders": int(len(orders)),
        "replaced_pending_orders": int(len(replacement_frame)),
        "closed_trades": int(len(trades)),
        "calendar_days": int(calendar_days),
        "trades_per_day": float(len(trades) / max(calendar_days, 1)),
        "target_first_rate": float(_numeric_series(trades, "win").mean()) if len(trades) else None,
        "mean_net_r": float(_numeric_series(trades, "net_r").mean()) if len(trades) else None,
        "median_net_r": float(_numeric_series(trades, "net_r").median()) if len(trades) else None,
        "mean_planned_gross_rr": float(_numeric_series(trades, "gross_rr").mean()) if len(trades) else None,
        "median_hold_minutes": (
            float(_numeric_series(trades, "holding_minutes").median())
            if len(trades) and "holding_minutes" in trades else None
        ),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "independent_market_episodes_traded": int(trades.market_episode_id.nunique()) if len(trades) else 0,
        "by_period": (
            trades.groupby("period").agg(
                trades=("net_r", "size"),
                target_first_rate=("win", "mean"),
                mean_net_r=("net_r", "mean"),
            ).reset_index().to_dict("records") if len(trades) else []
        ),
        "by_family": (
            trades.groupby("family").agg(
                trades=("net_r", "size"),
                target_first_rate=("win", "mean"),
                mean_net_r=("net_r", "mean"),
            ).reset_index().to_dict("records") if len(trades) else []
        ),
        "by_phase": (
            trades.groupby("auction_phase").agg(
                trades=("net_r", "size"),
                target_first_rate=("win", "mean"),
                mean_net_r=("net_r", "mean"),
            ).reset_index().to_dict("records") if len(trades) else []
        ),
    }
    return orders, trades, replacement_frame, summary


def _run_ablation(scored: pd.DataFrame, mode: str) -> dict[str, Any]:
    frame = scored.copy()
    if mode == "independent_state":
        frame["p_ownership_sequential"] = frame.p_ownership_counterfactual
    elif mode == "common_not_removed":
        frame["p_ownership_counterfactual"] = frame.p_ownership_full
        frame["p_ownership_sequential"] = frame.p_ownership_full
    if mode in {"independent_state", "common_not_removed"}:
        baseline = np.clip(_numeric_series(scored, "p_ownership_sequential", 0.5).fillna(0.5).to_numpy(float), 0.01, 0.99)
        changed = np.clip(_numeric_series(frame, "p_ownership_sequential", 0.5).fillna(0.5).to_numpy(float), 0.01, 0.99)
        frame["p_target_first"] = _sigmoid(
            _logit(frame.p_target_first.to_numpy(float))
            + _logit(changed) - _logit(baseline)
        )
        terminal = (
            frame.p_target_first.to_numpy(float) * frame.planned_win_log.to_numpy(float)
            + (1.0 - frame.p_target_first.to_numpy(float)) * frame.planned_loss_log.to_numpy(float)
        )
        frame["expected_log_growth"] = (
            frame.p_fill.to_numpy(float) * frame.p_resolve.to_numpy(float) * terminal
        )
        frame["expected_log_growth_per_hour"] = (
            frame.expected_log_growth.to_numpy(float)
            / np.maximum(frame.predicted_terminal_minutes.to_numpy(float) / 60.0, 1.0 / 60.0)
        )
    return route(frame)[3]


def write_outputs(name: str, scored: pd.DataFrame, output: Path) -> dict[str, Any]:
    orders, trades, replacements, summary = route(scored)
    scored.to_csv(output / f"{name}_scored.csv", index=False)
    orders.to_csv(output / f"{name}_orders.csv", index=False)
    trades.to_csv(output / f"{name}_trades.csv", index=False)
    replacements.to_csv(output / f"{name}_replacements.csv", index=False)
    return summary


def run(development_root: Path, fresh_root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    development = load_actions(development_root)
    fresh = load_actions(fresh_root)
    development_scored = outer_fold_score(development)
    fresh_scored = fresh_score(development, fresh)
    development_summary = write_outputs("development", development_scored, output)
    fresh_summary = write_outputs("fresh", fresh_scored, output)
    result = {
        "policy": "CANDIDATE_4T_COUNTERFACTUAL_SEQUENTIAL_AUCTION_OWNERSHIP",
        "development_oof": development_summary,
        "fresh_untouched": fresh_summary,
        "fresh_ablation": {
            "without_sequential_belief": _run_ablation(fresh_scored, "independent_state"),
            "without_common_market_counterfactual": _run_ablation(fresh_scored, "common_not_removed"),
        },
        "fixed_account_rules": {
            "risk_fraction": RISK,
            "one_global_slot": True,
            "planned_gross_rr_minimum": 1.0,
            "scale_in_or_out": False,
            "forced_time_exit": False,
            "filled_position_replacement": False,
        },
    }
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    fresh_result = fresh_summary
    lines = [
        "# Candidate 4t short diagnostic", "",
        "This is a development diagnostic, not a long-run performance claim.", "",
        f"- closed trades: {fresh_result['closed_trades']}",
        f"- trades/day: {fresh_result['trades_per_day']:.4f}",
        f"- target-first rate: {fresh_result['target_first_rate']}",
        f"- mean net R: {fresh_result['mean_net_r']}",
        f"- mean planned gross RR: {fresh_result['mean_planned_gross_rr']}",
        f"- ending NAV multiplier: {fresh_result['ending_nav_multiplier']:.6f}",
        f"- maximum drawdown: {fresh_result['maximum_drawdown']:.6f}", "",
        "summary.json compares the same immutable action universe with sequential",
        "ownership removed and with common-market counterfactual attribution removed.",
    ]
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.development_root, args.fresh_root, args.output)


if __name__ == "__main__":
    main()
