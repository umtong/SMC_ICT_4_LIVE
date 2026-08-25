#!/usr/bin/env python3
"""Evidence-supported structural-destination controller for candidate ML-k.

This is the missing decision layer between a causal EasyChart/SMC episode and
an executable one-account trade.  The deterministic engine owns direction,
liquidity event, first-return entry, structural invalidation and the set of live
opposing structural frontiers.  ML is not allowed to invent any of those.

For each immutable target sibling this controller estimates:
* limit-fill probability;
* target-before-structural-stop probability conditional on fill;
* account occupation time.

The target forecast is deliberately distributional.  Every earlier development
regime supplies a monotone first-passage support curve.  A local causal model
contributes only an odds update relative to those curves.  The executed target
maximizes the entropic certainty equivalent of post-cost log NAV growth across
regime scenarios.  With eta = 1 / account-risk, a remote high-R destination
cannot win merely because a small probability error makes its arithmetic
break-even probability tiny.

No symbol identity, absolute price, fill/outcome field, MFE/MAE or post-decision
path statistic is a model feature.  Multiple target rows are counterfactual
research siblings, never simultaneous orders.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_DIR = REPO_ROOT / "research/candidate-liquidity-episode-policy-v1"
CANDIDATE_DIR = Path(__file__).resolve().parent
for path in (CANDIDATE_DIR, BASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import counterfactual_destination_router as v1  # noqa: E402
import route_episode_policy as base  # noqa: E402

try:
    from sklearn.ensemble import (  # noqa: E402
        HistGradientBoostingClassifier,
        HistGradientBoostingRegressor,
    )
    from sklearn.isotonic import IsotonicRegression  # noqa: E402
    from sklearn.linear_model import LogisticRegression  # noqa: E402
except Exception as exc:  # pragma: no cover
    raise RuntimeError("scikit-learn is required") from exc

MODEL_VERSION = "candidate-ml-k-evidence-supported-destination-v2"
RISK_FRACTION = 0.03
ENTROPIC_RISK_AVERSION = 1.0 / RISK_FRACTION
EPS = 1e-12
NS_PER_MINUTE = 60_000_000_000
MIN_MODEL_ROWS = 60
MIN_CLASS_ROWS = 10
MIN_SUPPORT_WEIGHT = 18.0
MIN_OOF_CALIBRATION_ROWS = 120
CASCADE_MINUTES = 4

# Explicitly forbidden even if a source artifact contains the field.
FORBIDDEN_TOKENS = (
    "outcome",
    "fill_state",
    "fill_index",
    "fill_time",
    "resolution_",
    "terminal_",
    "mfe",
    "mae",
    "actual_",
    "net_r",
    "future_",
    "diagnostic_target_structure_price",
    "diagnostic_source_lower",
    "diagnostic_source_upper",
    "diagnostic_zone_lower",
    "diagnostic_zone_upper",
    "diagnostic_event_extreme",
)
ABSOLUTE_PRICE_FIELDS = {
    "entry",
    "stop",
    "target",
    "route_price",
    "exact_route_target",
}
COMMON_MARKET_TOKENS = (
    "common_return",
    "common_breadth",
    "factor_return",
    "breadth_signed",
    "index_return",
    "futures_return",
    "market_alignment",
)
TARGET_FIELDS = {
    "gross_rr",
    "planned_target_net_r",
    "risk_bps",
    "route_rr",
    "route_scale",
    "route_scale_log",
    "route_to_source_log_ratio",
    "target_scale_minutes",
    "target_strength_ratio",
    "target_candidate_rank",
    "target_candidate_count",
    "target_frontier_percentile",
    "target_distance_atr",
    "frontier_spacing_atr",
    "route_profile_entry_density",
    "route_profile_target_density",
    "route_profile_path_mean_density",
    "route_profile_path_low_volume_fraction",
    "route_profile_path_max_density",
    "volume_route_node_count",
    "volume_route_history_bars",
    "route_obstacle_is_semantic_liquidity",
    "route_obstacle_is_volume_node",
    "route_obstacle_distance_bps",
    "route_obstacle_strength",
    "volume_route_target_node_share",
    "volume_route_target_zone_width_bps",
    "volume_route_target_distance_bps",
    "volume_route_total_profile_range_bps",
    "auction_route_headroom_r",
    "route_utilization",
    "cost_drag_r",
}
LOCAL_PREFIXES = (
    "liquidity_",
    "semantic_",
    "structure_",
    "approach_",
    "event_",
    "confirmation_",
    "departure_",
    "clock_",
    "vwap_",
    "sequence_block_",
    "source_",
    "arm_",
    "auction_",
    "control_",
    "ctx_",
    "relative_",
    "metric_",
    "basis_",
    "oi_",
    "family_",
    "geometry_",
)
CATEGORICAL_FIELDS = (
    "side",
    "family",
    "setup_kind",
    "location_kind",
    "source_pool_kind",
    "source_kind",
    "route_kind",
    "route_family",
    "route_scale_bucket",
    "entry_geometry",
    "auction_phase",
    "narrative_branch",
    "scenario_family",
)
RESOLVED_OUTCOMES = set(base.RESOLVED_OUTCOMES)


def _series(frame: pd.DataFrame, name: str, default: Any = 0.0) -> pd.Series:
    return frame[name] if name in frame else pd.Series(default, index=frame.index)


def _number(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(_series(frame, name, default), errors="coerce").fillna(default)


def _time(frame: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_datetime(
        pd.to_numeric(_series(frame, name, np.nan), errors="coerce"),
        unit="ns",
        utc=True,
        errors="coerce",
    )


def _logit(value: Any) -> np.ndarray:
    p = np.clip(np.asarray(value, dtype=float), 1e-5, 1.0 - 1e-5)
    return np.log(p / (1.0 - p))


def _sigmoid(value: Any) -> np.ndarray:
    x = np.clip(np.asarray(value, dtype=float), -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-x))


def _decision_key(frame: pd.DataFrame) -> pd.Series:
    if "decision_state_id" in frame:
        return frame.decision_state_id.astype(str)
    if "state_id" in frame:
        return frame.state_id.astype(str)
    return (
        frame.episode_id.astype(str)
        + ":DECISION:"
        + _number(frame, "order_time_ns").astype("int64").astype(str)
    )


def _support_group(frame: pd.DataFrame) -> pd.Series:
    scenario = _series(frame, "scenario_family", "").astype(str)
    setup = _series(frame, "setup_kind", "").astype(str)
    family = _series(frame, "family", "UNKNOWN").astype(str)
    mechanism = scenario.where(scenario.ne(""), setup.where(setup.ne(""), family))
    scale = _series(frame, "route_scale_bucket", "ANY").astype(str)
    # The mechanism owns most of the reachability information.  Scale is retained
    # only when the generator supplies a stable categorical bucket.
    return mechanism + "|" + scale


def _eligible_numeric_columns(
    frame: pd.DataFrame,
    *,
    include_common: bool,
    include_target: bool,
) -> list[str]:
    output: list[str] = []
    for name in frame.columns:
        lower = str(name).lower()
        if name in ABSOLUTE_PRICE_FIELDS or name == "symbol":
            continue
        if any(token in lower for token in FORBIDDEN_TOKENS):
            continue
        is_target = name in TARGET_FIELDS
        is_common = any(token in lower for token in COMMON_MARKET_TOKENS)
        is_local = name.startswith(LOCAL_PREFIXES) or name in v1.LOCAL_NUMERIC
        if is_target and include_target:
            output.append(name)
        elif is_common and include_common:
            output.append(name)
        elif is_local and not is_common:
            output.append(name)
    return sorted(dict.fromkeys(output))


def _transform_numeric(name: str, series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).fillna(0.0).to_numpy(float)
    scale_tokens = (
        "minutes",
        "scale",
        "age",
        "distance",
        "density",
        "strength",
        "activity",
        "ratio",
        "width",
        "count",
        "members",
        "defense",
        "gross_rr",
        "target_net_r",
        "risk_bps",
        "route_rr",
        "cost_drag",
    )
    if any(token in name for token in scale_tokens):
        values = np.sign(values) * np.log1p(np.abs(values))
    return pd.Series(values, index=series.index, name=name)


def causal_features(
    frame: pd.DataFrame,
    *,
    include_common: bool,
    include_target: bool,
) -> pd.DataFrame:
    """Event-relative, symbol-invariant features available at order decision."""
    output = pd.DataFrame(index=frame.index)
    for name in _eligible_numeric_columns(
        frame,
        include_common=include_common,
        include_target=include_target,
    ):
        output[name] = _transform_numeric(name, frame[name])
    for name in CATEGORICAL_FIELDS:
        if name not in frame:
            continue
        values = frame[name].fillna("MISSING").astype(str)
        dummies = pd.get_dummies(values, prefix=name, dtype=float)
        output = pd.concat([output, dummies], axis=1)
    return output.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


def aligned_features(
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    include_common: bool,
    include_target: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train_x = causal_features(
        train, include_common=include_common, include_target=include_target
    )
    test_x = causal_features(
        test, include_common=include_common, include_target=include_target
    )
    columns = list(train_x.columns)
    return (
        train_x.reindex(columns=columns, fill_value=0.0),
        test_x.reindex(columns=columns, fill_value=0.0),
    )


def prepare_labels(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    output["decision_key"] = _decision_key(output)
    output["support_group"] = _support_group(output)
    output["order_time"] = base._period_start(output)
    fill_time = _time(output, "fill_time_ns")
    terminal_time = _time(output, "order_terminal_time_ns")
    resolution_time = _time(output, "resolution_time_ns")
    outcome = _series(output, "outcome", "").astype(str)
    output["fill_label"] = fill_time.notna().astype(int)
    output["resolved_label"] = outcome.isin(RESOLVED_OUTCOMES)
    output["target_label"] = outcome.eq("TARGET_FIRST").astype(int)
    output["fill_label_available_time"] = fill_time.where(
        output.fill_label.eq(1), terminal_time
    )
    output["target_label_available_time"] = resolution_time.where(
        output.resolved_label
    )
    output["terminal_label_available_time"] = terminal_time
    order_ns = _number(output, "order_time_ns", np.nan)
    terminal_ns = _number(output, "order_terminal_time_ns", np.nan)
    output["terminal_minutes_label"] = np.maximum(
        1.0, (terminal_ns - order_ns) / NS_PER_MINUTE
    )
    output["target_candidate_count"] = output.groupby(
        ["period", "decision_key"]
    ).action_id.transform("size")
    if "target_candidate_rank" not in output:
        output["target_candidate_rank"] = output.groupby(
            ["period", "decision_key"]
        ).gross_rr.rank(method="dense", ascending=True)
    if "target_frontier_percentile" not in output:
        output["target_frontier_percentile"] = np.where(
            output.target_candidate_count.gt(1),
            (output.target_candidate_rank - 1.0)
            / (output.target_candidate_count - 1.0),
            0.0,
        )
    if "cost_drag_r" not in output:
        output["cost_drag_r"] = _number(output, "gross_rr") - _number(
            output, "planned_target_net_r"
        )
    counts = output.groupby(["period", "decision_key"]).action_id.transform(
        "size"
    )
    output["decision_weight"] = 1.0 / np.maximum(counts.astype(float), 1.0)
    return output


@dataclass
class ProbabilityModel:
    model: HistGradientBoostingClassifier | None
    prior: float
    shrink: float
    ready: bool

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            return np.full(len(features), self.prior, dtype=float)
        raw = self.model.predict_proba(features)[:, 1]
        return np.clip(
            self.prior + self.shrink * (raw - self.prior), 0.003, 0.997
        )


def fit_probability_model(
    train: pd.DataFrame,
    train_x: pd.DataFrame,
    label: str,
    *,
    seed: int,
) -> ProbabilityModel:
    y = pd.to_numeric(_series(train, label, np.nan), errors="coerce")
    valid = y.notna()
    y = y.loc[valid].astype(int)
    x = train_x.loc[valid]
    weights = _number(train.loc[valid], "decision_weight", 1.0).to_numpy(float)
    positive = float(np.sum(weights * y.to_numpy(float)))
    total = float(np.sum(weights))
    prior = float((positive + 6.0) / (total + 12.0)) if total else 0.5
    if (
        len(y) < MIN_MODEL_ROWS
        or y.nunique() < 2
        or int(y.sum()) < MIN_CLASS_ROWS
        or int((1 - y).sum()) < MIN_CLASS_ROWS
        or x.shape[1] == 0
    ):
        return ProbabilityModel(None, prior, 0.0, False)
    model = HistGradientBoostingClassifier(
        learning_rate=0.035,
        max_iter=170,
        max_leaf_nodes=8,
        min_samples_leaf=max(16, min(48, len(y) // 16)),
        l2_regularization=18.0,
        early_stopping=False,
        random_state=seed,
    )
    model.fit(x, y, sample_weight=weights)
    shrink = float(total / (total + 180.0))
    return ProbabilityModel(model, prior, shrink, True)


def period_probability_scenarios(
    train: pd.DataFrame,
    test: pd.DataFrame,
    label: str,
    *,
    include_target: bool,
    seed: int,
) -> tuple[np.ndarray, list[str], bool]:
    matrices: list[np.ndarray] = []
    names: list[str] = []
    ready = True
    for sequence, period in enumerate(sorted(train.period.astype(str).unique())):
        subset = train[train.period.astype(str).eq(period)]
        train_x, test_x = aligned_features(
            subset,
            test,
            include_common=False,
            include_target=include_target,
        )
        fitted = fit_probability_model(
            subset, train_x, label, seed=seed + sequence * 101
        )
        if fitted.ready:
            matrices.append(fitted.predict(test_x))
            names.append(period)
        else:
            ready = False
    # Pooled model supplies local state ranking, but is not treated as another
    # regime in the support distribution.
    pooled_x, test_x = aligned_features(
        train,
        test,
        include_common=False,
        include_target=include_target,
    )
    pooled = fit_probability_model(train, pooled_x, label, seed=seed + 9001)
    if not matrices:
        return np.empty((0, len(test))), names, False
    return np.vstack(matrices), names, bool(ready and pooled.ready)


def pooled_probability(
    train: pd.DataFrame,
    test: pd.DataFrame,
    label: str,
    *,
    include_target: bool,
    seed: int,
) -> tuple[np.ndarray, bool]:
    train_x, test_x = aligned_features(
        train,
        test,
        include_common=False,
        include_target=include_target,
    )
    fitted = fit_probability_model(train, train_x, label, seed=seed)
    return fitted.predict(test_x), fitted.ready


@dataclass
class SupportCurve:
    global_curve: IsotonicRegression
    group_curves: dict[str, tuple[IsotonicRegression, float]]

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = np.log1p(np.maximum(_number(frame, "gross_rr").to_numpy(float), 0.0))
        global_probability = np.clip(
            self.global_curve.predict(x), 0.003, 0.997
        )
        output = global_probability.copy()
        groups = _support_group(frame).astype(str).to_numpy()
        for group in np.unique(groups):
            fitted = self.group_curves.get(str(group))
            if fitted is None:
                continue
            positions = np.flatnonzero(groups == group)
            curve, shrink = fitted
            local = np.clip(curve.predict(x[positions]), 0.003, 0.997)
            output[positions] = (
                global_probability[positions]
                + shrink * (local - global_probability[positions])
            )
        return np.clip(output, 0.003, 0.997)


def _fit_isotonic(
    x: np.ndarray, y: np.ndarray, weights: np.ndarray
) -> IsotonicRegression:
    curve = IsotonicRegression(
        increasing=False,
        out_of_bounds="clip",
        y_min=0.003,
        y_max=0.997,
    )
    curve.fit(x, y, sample_weight=weights)
    return curve


def fit_support_curves(train: pd.DataFrame) -> dict[str, SupportCurve]:
    resolved = train[train.resolved_label.fillna(False)].copy()
    curves: dict[str, SupportCurve] = {}
    for period, period_frame in resolved.groupby("period", sort=True):
        x = np.log1p(np.maximum(_number(period_frame, "gross_rr").to_numpy(), 0.0))
        y = _number(period_frame, "target_label").to_numpy(float)
        w = _number(period_frame, "decision_weight", 1.0).to_numpy(float)
        global_curve = _fit_isotonic(x, y, w)
        groups: dict[str, tuple[IsotonicRegression, float]] = {}
        keys = _support_group(period_frame).astype(str)
        for group, indices in keys.groupby(keys).groups.items():
            local = period_frame.loc[list(indices)]
            lw = _number(local, "decision_weight", 1.0).to_numpy(float)
            ly = _number(local, "target_label").to_numpy(float)
            effective = float(lw.sum())
            if (
                effective < MIN_SUPPORT_WEIGHT
                or ly.sum() < 3.0
                or (len(ly) - ly.sum()) < 3.0
            ):
                continue
            lx = np.log1p(
                np.maximum(_number(local, "gross_rr").to_numpy(float), 0.0)
            )
            shrink = float(effective / (effective + 40.0))
            groups[str(group)] = (_fit_isotonic(lx, ly, lw), shrink)
        curves[str(period)] = SupportCurve(global_curve, groups)
    return curves


def support_scenarios(
    curves: dict[str, SupportCurve], test: pd.DataFrame
) -> tuple[np.ndarray, list[str]]:
    names = sorted(curves)
    if not names:
        return np.empty((0, len(test))), names
    return np.vstack([curves[name].predict(test) for name in names]), names


def _monotone_by_decision(
    frame: pd.DataFrame, matrix: np.ndarray
) -> np.ndarray:
    output = np.asarray(matrix, dtype=float).copy()
    for scenario in range(output.shape[0]):
        values = pd.Series(output[scenario], index=frame.index, dtype=float)
        for _, group in frame.groupby(["period", "decision_key"], sort=False):
            ordered = group.sort_values(
                ["gross_rr", "target_candidate_rank", "action_id"]
            )
            current = values.loc[ordered.index].to_numpy(float)
            values.loc[ordered.index] = np.minimum.accumulate(current)
        output[scenario] = values.to_numpy(float)
    return output


def _calibration_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    output["logit_local"] = _logit(_number(frame, "p_target_local", 0.5))
    output["logit_support"] = _logit(_number(frame, "p_support_median", 0.5))
    output["local_minus_support"] = output.logit_local - output.logit_support
    output["support_dispersion"] = _number(frame, "p_support_std")
    output["log_gross_rr"] = np.log1p(
        np.maximum(_number(frame, "gross_rr").to_numpy(float), 0.0)
    )
    output["log_cost_drag"] = np.log1p(
        np.maximum(_number(frame, "cost_drag_r").to_numpy(float), 0.0)
    )
    for name in ("family", "setup_kind", "route_family", "route_scale_bucket"):
        if name in frame:
            output = pd.concat(
                [
                    output,
                    pd.get_dummies(
                        frame[name].fillna("MISSING").astype(str),
                        prefix=name,
                        dtype=float,
                    ),
                ],
                axis=1,
            )
    return output.astype(float)


def rolling_oof_calibration_rows(history: pd.DataFrame) -> pd.DataFrame:
    periods = (
        history.groupby("period").order_time.min().sort_values().index.tolist()
    )
    pieces: list[pd.DataFrame] = []
    for sequence in range(1, len(periods)):
        prior = history[history.period.isin(periods[:sequence])].copy()
        test = history[history.period.eq(periods[sequence])].copy()
        target_train = prior[prior.resolved_label].copy()
        local, local_ready = pooled_probability(
            target_train,
            test,
            "target_label",
            include_target=True,
            seed=73000 + sequence,
        )
        support, _ = support_scenarios(fit_support_curves(prior), test)
        if not local_ready or support.shape[0] == 0:
            continue
        test["p_target_local"] = local
        test["p_support_median"] = np.median(support, axis=0)
        test["p_support_std"] = np.std(support, axis=0)
        pieces.append(test[test.resolved_label].copy())
    return pd.concat(pieces, ignore_index=True, sort=False) if pieces else history.iloc[:0]


def calibrated_target_probability(
    history: pd.DataFrame,
    test: pd.DataFrame,
    local: np.ndarray,
    support: np.ndarray,
) -> tuple[np.ndarray, bool, dict[str, Any]]:
    support_median = np.median(support, axis=0)
    oof = rolling_oof_calibration_rows(history)
    diagnostics = {"oof_rows": int(len(oof)), "ready": False}
    if (
        len(oof) < MIN_OOF_CALIBRATION_ROWS
        or oof.target_label.nunique() < 2
    ):
        # No heuristic probability fallback.  The support distribution remains
        # diagnostic, but the account cannot trade until rolling calibration is mature.
        return support_median, False, diagnostics
    train_x = _calibration_features(oof)
    scored = test.copy()
    scored["p_target_local"] = local
    scored["p_support_median"] = support_median
    scored["p_support_std"] = np.std(support, axis=0)
    test_x = _calibration_features(scored).reindex(
        columns=train_x.columns, fill_value=0.0
    )
    weights = _number(oof, "decision_weight", 1.0).to_numpy(float)
    calibrator = LogisticRegression(C=0.01, max_iter=1000)
    calibrator.fit(train_x, oof.target_label.astype(int), sample_weight=weights)
    diagnostics["ready"] = True
    return np.clip(calibrator.predict_proba(test_x)[:, 1], 0.003, 0.997), True, diagnostics


def target_probability_scenarios(
    history: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, dict[str, Any], bool]:
    target_train = history[history.resolved_label].copy()
    local, local_ready = pooled_probability(
        target_train,
        test,
        "target_label",
        include_target=True,
        seed=81001,
    )
    support, support_periods = support_scenarios(
        fit_support_curves(history), test
    )
    if support.shape[0] < 2 or not local_ready:
        return np.empty((0, len(test))), {
            "support_periods": support_periods,
            "local_ready": local_ready,
            "calibration_ready": False,
        }, False
    calibrated, calibration_ready, calibration = calibrated_target_probability(
        history, test, local, support
    )
    median_support = np.median(support, axis=0)
    local_odds_update = _logit(calibrated) - _logit(median_support)
    scenarios = _sigmoid(_logit(support) + local_odds_update[None, :])
    scenarios = _monotone_by_decision(test, scenarios)
    return scenarios, {
        "support_periods": support_periods,
        "local_ready": local_ready,
        "calibration_ready": calibration_ready,
        "calibration": calibration,
    }, bool(calibration_ready)


def fit_duration(
    history: pd.DataFrame, test: pd.DataFrame
) -> tuple[np.ndarray, bool]:
    valid = history.terminal_minutes_label.notna() & history.terminal_minutes_label.gt(0)
    train = history.loc[valid]
    if len(train) < 80:
        return np.full(len(test), 60.0), False
    train_x, test_x = aligned_features(
        train, test, include_common=False, include_target=True
    )
    model = HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.04,
        max_iter=150,
        max_leaf_nodes=7,
        min_samples_leaf=max(20, min(50, len(train) // 30)),
        l2_regularization=20.0,
        early_stopping=False,
        random_state=91001,
    )
    model.fit(
        train_x,
        np.log1p(train.terminal_minutes_label.to_numpy(float)),
        sample_weight=_number(train, "decision_weight", 1.0).to_numpy(float),
    )
    return np.maximum(1.0, np.expm1(model.predict(test_x))), True


def entropic_certainty_equivalent(utilities: np.ndarray) -> np.ndarray:
    """Soft worst-regime expected log growth, stable under exponentiation."""
    values = np.asarray(utilities, dtype=float)
    scaled = -ENTROPIC_RISK_AVERSION * values
    maximum = scaled.max(axis=0)
    return -(
        maximum
        + np.log(np.exp(scaled - maximum[None, :]).mean(axis=0))
    ) / ENTROPIC_RISK_AVERSION


def strict_causal_score(
    orders: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = prepare_labels(orders)
    periods = output.groupby("period").order_time.min().sort_values().index.tolist()
    pieces: list[pd.DataFrame] = []
    diagnostics: dict[str, Any] = {}
    for sequence, period in enumerate(periods):
        test = output[output.period.eq(period)].copy()
        test_start = test.order_time.min()
        history = output[
            output.role.astype(str).isin({"dev", "eval"})
            & output.order_time.lt(test_start)
        ].copy()
        fill_history = history[
            history.fill_label_available_time.notna()
            & history.fill_label_available_time.lt(test_start)
        ]
        target_history = history[
            history.target_label_available_time.notna()
            & history.target_label_available_time.lt(test_start)
        ]
        duration_history = history[
            history.terminal_label_available_time.notna()
            & history.terminal_label_available_time.lt(test_start)
        ]

        fill_probability, fill_ready = pooled_probability(
            fill_history,
            test,
            "fill_label",
            include_target=True,
            seed=101000 + sequence,
        )
        target_scenarios, target_diag, target_ready = target_probability_scenarios(
            target_history, test
        )
        duration, duration_ready = fit_duration(duration_history, test)
        ready = bool(fill_ready and target_ready and duration_ready)

        test["p_fill"] = fill_probability
        test["predicted_terminal_minutes"] = duration
        test["models_ready"] = ready
        test["prediction_source"] = (
            MODEL_VERSION if ready else "INSUFFICIENT_MATURE_REGIME_SUPPORT"
        )
        if target_scenarios.shape[0]:
            test["p_target_if_filled"] = np.mean(target_scenarios, axis=0)
            test["p_target_worst_regime"] = np.min(target_scenarios, axis=0)
            test["p_target_regime_std"] = np.std(target_scenarios, axis=0)
        else:
            test["p_target_if_filled"] = np.nan
            test["p_target_worst_regime"] = np.nan
            test["p_target_regime_std"] = np.nan

        target_r = _number(test, "planned_target_net_r")
        win_log = np.log(np.maximum(EPS, 1.0 + RISK_FRACTION * target_r))
        loss_log = math.log(1.0 - RISK_FRACTION)
        denominator = win_log - loss_log
        test["break_even_target_probability"] = np.where(
            denominator > EPS, -loss_log / denominator, 1.0
        )
        if target_scenarios.shape[0]:
            utilities = fill_probability[None, :] * (
                target_scenarios * win_log.to_numpy()[None, :]
                + (1.0 - target_scenarios) * loss_log
            )
            test["mean_expected_log_growth"] = utilities.mean(axis=0)
            test["robust_expected_log_growth"] = entropic_certainty_equivalent(
                utilities
            )
        else:
            test["mean_expected_log_growth"] = np.nan
            test["robust_expected_log_growth"] = np.nan
        test["robust_log_growth_per_hour"] = (
            test.robust_expected_log_growth
            / np.maximum(test.predicted_terminal_minutes / 60.0, 1.0 / 60.0)
        )
        test["completion_weighted_log_growth_per_hour"] = (
            test.robust_log_growth_per_hour
            * test.p_target_worst_regime.clip(lower=0.0, upper=1.0)
        )
        test["policy_eligible"] = (
            test.models_ready.fillna(False)
            & _number(test, "gross_rr").ge(1.0)
            & target_r.gt(0.0)
            & test.p_target_if_filled.gt(0.50)
            & test.robust_expected_log_growth.gt(0.0)
        )
        diagnostics[str(period)] = {
            "test_start": str(test_start),
            "history_rows": int(len(history)),
            "history_periods": sorted(history.period.astype(str).unique().tolist()),
            "fill_ready": fill_ready,
            "target": target_diag,
            "duration_ready": duration_ready,
            "models_ready": ready,
        }
        pieces.append(test)
    return (
        pd.concat(pieces, ignore_index=True, sort=False)
        if pieces
        else output.iloc[:0].copy(),
        diagnostics,
    )


def best_destination_per_decision(scored: pd.DataFrame) -> pd.DataFrame:
    work = scored[scored.models_ready.fillna(False)].copy()
    if work.empty:
        return work
    if "completion_weighted_log_growth_per_hour" not in work:
        work["completion_weighted_log_growth_per_hour"] = (
            _number(work, "robust_log_growth_per_hour")
            * _number(work, "p_target_worst_regime").clip(0.0, 1.0)
        )
    work = work.sort_values(
        [
            "period",
            "decision_key",
            "p_target_worst_regime",
            "completion_weighted_log_growth_per_hour",
            "robust_expected_log_growth",
            "planned_target_net_r",
            "action_id",
        ],
        ascending=[True, True, False, False, False, False, True],
    )
    return work.drop_duplicates(
        ["period", "decision_key"], keep="first"
    ).reset_index(drop=True)


def assign_market_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.sort_values(["period", "order_time_ns", "episode_id"]).copy()
    event_time = pd.to_numeric(
        _series(output, "diagnostic_event_time_ns", np.nan), errors="coerce"
    ).fillna(_number(output, "departure_time_ns", np.nan)).fillna(
        _number(output, "order_time_ns")
    )
    identifiers: dict[int, str] = {}
    for period, group in output.groupby("period", sort=True):
        cluster = 0
        anchor: int | None = None
        for index in group.index:
            timestamp = int(event_time.loc[index])
            if anchor is None or timestamp - anchor > CASCADE_MINUTES * NS_PER_MINUTE:
                cluster += 1
                anchor = timestamp
            identifiers[index] = f"{period}:CAUSAL_MARKET_EVENT:{cluster}"
    output["market_episode_id"] = pd.Series(identifiers)
    return output


def route_one_account(
    scored: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    best = best_destination_per_decision(scored)
    eligible = best[best.policy_eligible.fillna(False)].copy()
    if eligible.empty:
        empty = best.iloc[:0].copy()
        return empty, empty, empty, {
            "selected_orders": 0,
            "closed_trades": 0,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }
    eligible = assign_market_episodes(eligible)
    eligible["order_time"] = _time(eligible, "order_time_ns")
    eligible["fill_time"] = _time(eligible, "fill_time_ns")
    eligible["terminal_time"] = _time(eligible, "order_terminal_time_ns")
    eligible = eligible.sort_values(
        [
            "order_time",
            "completion_weighted_log_growth_per_hour",
            "robust_expected_log_growth",
            "episode_id",
        ],
        ascending=[True, False, False, True],
    )

    selected: list[pd.Series] = []
    replaced: list[pd.Series] = []
    active: pd.Series | None = None
    traded_episodes: set[str] = set()
    traded_market_events: set[str] = set()

    for timestamp, simultaneous in eligible.groupby("order_time", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if active is not None:
            terminal = pd.Timestamp(active.terminal_time)
            if pd.isna(terminal):
                terminal = timestamp
            if timestamp >= terminal:
                selected.append(active)
                if not pd.isna(active.fill_time):
                    traded_episodes.add(str(active.episode_id))
                    traded_market_events.add(str(active.market_episode_id))
                active = None

        pool = simultaneous[
            ~simultaneous.episode_id.astype(str).isin(traded_episodes)
            & ~simultaneous.market_episode_id.astype(str).isin(
                traded_market_events
            )
        ]
        if pool.empty:
            continue
        candidate = pool.iloc[0].copy()
        if active is None:
            active = candidate
            continue
        # A filled position is immutable.  A pending order may be replaced by a
        # stronger newly observed causal plan, including a newer state of the same
        # still-unfilled episode.
        if not pd.isna(active.fill_time) and active.fill_time <= timestamp:
            continue
        if (
            float(candidate.completion_weighted_log_growth_per_hour)
            > float(active.completion_weighted_log_growth_per_hour) + EPS
        ):
            old = active.copy()
            old["replacement_time_ns"] = int(timestamp.value)
            old["replacement_reason"] = (
                "HIGHER_COMPLETION_WEIGHTED_EVIDENCE_SUPPORTED_LOG_GROWTH_PER_HOUR"
            )
            replaced.append(old)
            active = candidate
    if active is not None:
        selected.append(active)

    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else eligible.iloc[:0]
    replacements = pd.DataFrame(replaced).reset_index(drop=True) if replaced else eligible.iloc[:0]
    outcome = _series(orders, "outcome", "").astype(str)
    trades = orders[
        outcome.isin(RESOLVED_OUTCOMES)
        & pd.to_numeric(_series(orders, "net_r", np.nan), errors="coerce").notna()
    ].copy()
    trades = trades.sort_values(
        ["order_terminal_time_ns", "order_time_ns"]
    ).reset_index(drop=True)
    trades["net_r"] = pd.to_numeric(trades.net_r, errors="coerce")

    nav = 1.0
    peak = 1.0
    drawdown = 0.0
    before: list[float] = []
    after: list[float] = []
    for result in trades.net_r.astype(float):
        before.append(nav)
        nav *= max(EPS, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        drawdown = max(drawdown, 1.0 - nav / peak)
        after.append(nav)
    trades["nav_before"] = before
    trades["nav_after"] = after
    wins = trades.outcome.astype(str).eq("TARGET_FIRST")
    summary = {
        "eligible_decision_plans": int(len(eligible)),
        "selected_orders": int(len(orders)),
        "replaced_pending_orders": int(len(replacements)),
        "closed_trades": int(len(trades)),
        "target_first": int(wins.sum()),
        "target_first_rate": float(wins.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "mean_planned_gross_rr": float(_number(trades, "gross_rr").mean()) if len(trades) else None,
        "median_holding_minutes": float(_number(trades, "holding_minutes").median()) if len(trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(drawdown),
        "independent_market_episodes_traded": int(trades.market_episode_id.nunique()) if len(trades) else 0,
        "risk_fraction": RISK_FRACTION,
    }
    return orders, trades, replacements, summary


def risk_sized_quantity(
    *, nav: float, entry: float, stop: float, quantity_step: float
) -> dict[str, float]:
    return v1.risk_sized_quantity(
        nav=nav, entry=entry, stop=stop, quantity_step=quantity_step
    )


def _group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    if frame.empty or key not in frame:
        return {}
    output: dict[str, Any] = {}
    for value, group in frame.groupby(key, dropna=False):
        wins = group.outcome.astype(str).eq("TARGET_FIRST")
        output[str(value)] = {
            "trades": int(len(group)),
            "target_first_rate": float(wins.mean()),
            "mean_net_r": float(pd.to_numeric(group.net_r).mean()),
            "mean_gross_rr": float(_number(group, "gross_rr").mean()),
            "median_holding_minutes": float(_number(group, "holding_minutes").median()),
        }
    return output


def route_research(root: Path, output_dir: Path) -> dict[str, Any]:
    episodes, period_days, source_summaries = base.load_universe(root)
    if episodes.empty:
        raise RuntimeError(f"No destination artifacts below {root}")
    orders = episodes[base._bool_series(episodes["order_exists"])].copy()
    scored, model_diagnostics = strict_causal_score(orders)
    account_scored = scored[~scored.role.astype(str).eq("dev")].copy()
    selected, trades, replacements, account = route_one_account(account_scored)
    evaluated_periods = set(account_scored.period.astype(str).unique())
    calendar_days = int(sum(days for period, days in period_days.items() if str(period) in evaluated_periods))
    account.update(
        {
            "diagnostic_calendar_days": calendar_days,
            "closed_trades_per_diagnostic_day": float(len(trades) / calendar_days) if calendar_days else 0.0,
            "by_period": _group_metrics(trades, "period"),
            "by_family": _group_metrics(trades, "family"),
            "by_setup": _group_metrics(trades, "setup_kind"),
            "by_symbol": _group_metrics(trades, "symbol"),
            "by_route_family": _group_metrics(trades, "route_family"),
        }
    )
    summary = {
        "policy_version": MODEL_VERSION,
        "decision_policy": (
            "causal direction/liquidity/structure -> first defended return -> "
            "structural stop -> exact opposing structural frontiers -> local causal "
            "odds update over period-specific monotone first-passage support -> "
            "entropic post-cost log-NAV destination utility -> one continuous account"
        ),
        "risk_fraction": RISK_FRACTION,
        "entropic_risk_aversion": ENTROPIC_RISK_AVERSION,
        "one_global_account_slot": True,
        "one_filled_trade_per_causal_episode": True,
        "one_target_selected_before_entry": True,
        "gross_planned_rr_floor": 1.0,
        "partial_entries_or_exits": False,
        "forced_time_exit": False,
        "daily_loss_limit": False,
        "symbol_identity_is_model_feature": False,
        "absolute_price_is_model_feature": False,
        "future_information_is_model_feature": False,
        "raw_common_market_uplift_can_create_target_edge": False,
        "target_probability_monotone_in_distance": True,
        "causal_completion_dominance": (
            "posterior target-before-structural-stop probability must exceed 0.50"
        ),
        "remote_target_tail_error_resistance": (
            "period-specific support scenarios plus entropic certainty equivalent"
        ),
        "candidate_rows": int(len(orders)),
        "decision_states": int(prepare_labels(orders).decision_key.nunique()),
        "causal_episodes": int(orders.episode_id.nunique()),
        "model_diagnostics": model_diagnostics,
        "account": account,
        "period_days": period_days,
        "source_summaries": source_summaries,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output_dir / "scored_destination_candidates.csv.gz", index=False, compression="gzip")
    best_destination_per_decision(scored).to_csv(output_dir / "best_destination_per_decision.csv.gz", index=False, compression="gzip")
    selected.to_csv(output_dir / "selected_orders.csv", index=False)
    trades.to_csv(output_dir / "closed_trades.csv", index=False)
    replacements.to_csv(output_dir / "replaced_pending_orders.csv", index=False)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(route_research(args.root, args.output), ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
