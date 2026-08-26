#!/usr/bin/env python3
"""Causal route-completion target policy over candidate-4 liquidity episodes.

The inherited deterministic engine owns the market hypothesis: direction, liquidity
source, auction acceptance/failure, defended return entry, structural stop, and the
first live opposing structural route.  This research layer does two things only with
information available before entry:

1. choose one immutable realization checkpoint on that structural route; and
2. decide whether its target-before-stop distribution is worth occupying the single
   account slot at 3% account risk after costs.

Route checkpoints are counterfactual research siblings, never simultaneous orders.
Every sibling preserves the inherited entry and stop and lies on the already-known
route to the inherited opposing-liquidity target.  Labels use the realized path only
after an immutable checkpoint has been proposed.  Model features exclude symbol,
absolute price, outcome, MFE/MAE, fills, holding time, resolution and NAV fields.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

RISK_FRACTION = 0.03
EPS = 1e-12
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
ROUTE_FRACTIONS = (0.25, 0.35, 0.50, 0.65, 0.80, 1.00)
MIN_TRAIN_DECISIONS = 120
MIN_CLASS_DECISIONS = 14
ENTROPIC_ETA = 1.0 / RISK_FRACTION
MODEL_VERSION = "ml-first-causal-route-completion-v1"

POST_DECISION_TOKENS = (
    "outcome",
    "net_r",
    "mfe",
    "mae",
    "resolution",
    "holding",
    "fill_delay",
    "entry_time",
    "entry_index",
    "nav_",
    "drawdown",
    "actual_",
    "future_",
    "counterfactual_",
    "label",
)
IDENTIFIER_TOKENS = (
    "episode_id",
    "original_row_id",
    "source_id",
    "target_level_id",
    "diagnostic_period",
    "diagnostic_role",
)
ABSOLUTE_PRICE_FIELDS = {
    "source_price",
    "source_lower",
    "source_upper",
    "zone_lower",
    "zone_upper",
    "event_extreme",
    "event_vwap",
    "entry_limit",
    "target_structure_price",
    "target_zone_lower",
    "target_zone_upper",
    "entry",
    "stop",
    "stop_fill",
    "target",
    "cf_target",
}
CATEGORICAL_FIELDS = (
    "side",
    "family",
    "source_kind",
    "source_side",
    "source_confluence_kinds",
    "source_confluence_timeframes",
    "location_kind",
    "target_kind",
    "response_kind",
    "entry_type",
)


def _number(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan).fillna(default)


def _logit(values: Any) -> np.ndarray:
    p = np.clip(np.asarray(values, dtype=float), 1e-5, 1.0 - 1e-5)
    return np.log(p / (1.0 - p))


def _sigmoid(values: Any) -> np.ndarray:
    x = np.clip(np.asarray(values, dtype=float), -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-x))


def _period_order(frame: pd.DataFrame) -> list[str]:
    return (
        frame.groupby("diagnostic_period")["order_time_ns"]
        .min()
        .sort_values()
        .index.astype(str)
        .tolist()
    )


def _calendar_days(periods: Iterable[str]) -> int:
    total = 0
    for period in periods:
        try:
            start_text, end_text = str(period).split("_", 1)
            total += max(1, int((pd.Timestamp(end_text) - pd.Timestamp(start_text)).days))
        except Exception:
            total += 7
    return total


def load_trades(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    required = {
        "episode_id",
        "diagnostic_period",
        "order_time_ns",
        "resolution_time_ns",
        "entry",
        "stop",
        "stop_fill",
        "target",
        "gross_rr",
        "target_net_r",
        "net_r",
        "mfe_r",
        "outcome",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing inherited trade fields: {missing}")
    for name in (
        "order_time_ns",
        "resolution_time_ns",
        "interaction_time_ns",
        "entry",
        "stop",
        "stop_fill",
        "target",
        "gross_rr",
        "target_net_r",
        "net_r",
        "mfe_r",
    ):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    frame = frame[
        frame.order_time_ns.notna()
        & frame.resolution_time_ns.notna()
        & frame.net_r.notna()
    ].copy()
    frame["diagnostic_period"] = frame.diagnostic_period.astype(str)
    frame["decision_id"] = frame.diagnostic_period + ":" + frame.episode_id.astype(str)
    frame["original_row_id"] = np.arange(len(frame), dtype=int)
    return assign_parent_episodes(frame)


def assign_parent_episodes(frame: pd.DataFrame) -> pd.DataFrame:
    """Causally cluster repeated entries from one source episode without symbol pooling."""
    output = frame.sort_values(
        ["diagnostic_period", "symbol", "interaction_time_ns", "order_time_ns", "episode_id"]
    ).copy()
    identifiers: dict[int, str] = {}
    for (period, symbol), group in output.groupby(["diagnostic_period", "symbol"], sort=False):
        cluster = 0
        prior_time: int | None = None
        prior_price: float | None = None
        prior_side: str | None = None
        for index, row in group.iterrows():
            event_ns = int(float(row.get("interaction_time_ns", row.order_time_ns)))
            source_price = float(row.get("source_price", row.entry))
            source_side = str(row.get("source_side", ""))
            same = False
            if prior_time is not None and prior_price is not None:
                elapsed = (event_ns - prior_time) / 60e9
                distance = abs(source_price - prior_price) / max(abs(source_price), EPS)
                same = elapsed <= 90.0 and distance <= 0.015 and source_side == prior_side
            if not same:
                cluster += 1
            identifiers[index] = f"{period}:{symbol}:PARENT:{cluster}"
            prior_time, prior_price, prior_side = event_ns, source_price, source_side
    output["parent_episode_id"] = pd.Series(identifiers)
    return output.sort_values(["order_time_ns", "episode_id"]).reset_index(drop=True)


def expand_route_checkpoints(frame: pd.DataFrame) -> pd.DataFrame:
    pieces: list[pd.DataFrame] = []
    for fraction in ROUTE_FRACTIONS:
        sibling = frame.copy()
        sibling["route_fraction"] = float(fraction)
        direction = np.where(sibling.side.astype(str).eq("LONG"), 1.0, -1.0)
        sibling["cf_target"] = sibling.entry + fraction * (sibling.target - sibling.entry)
        planned_risk = (sibling.entry - sibling.stop).abs().clip(lower=EPS)
        cash_risk = (sibling.entry - sibling.stop_fill).abs().clip(lower=EPS)
        sibling["cf_gross_rr"] = (sibling.cf_target - sibling.entry).abs() / planned_risk
        entry_fee = MAKER_FEE * sibling.entry.abs()
        raw_stop = (
            direction * (sibling.stop_fill - sibling.entry) / cash_risk
            - (entry_fee + TAKER_FEE * sibling.stop_fill.abs()) / cash_risk
        )
        normalization = np.maximum(np.abs(raw_stop), EPS)
        raw_target = (
            direction * (sibling.cf_target - sibling.entry) / cash_risk
            - (entry_fee + MAKER_FEE * sibling.cf_target.abs()) / cash_risk
        )
        sibling["cf_target_net_r"] = raw_target / normalization
        sibling["cf_price_reach_r"] = (
            direction * (sibling.cf_target - sibling.entry) / cash_risk / normalization
        )
        ambiguous = sibling.outcome.astype(str).str.startswith("AMBIGUOUS")
        original_win = sibling.outcome.astype(str).eq("TARGET_FIRST")
        if math.isclose(fraction, 1.0):
            reached = original_win
        else:
            reached = (~ambiguous) & (_number(sibling, "mfe_r", -np.inf) + 1e-10 >= sibling.cf_price_reach_r)
        sibling["target_first_label"] = reached.astype(int)
        sibling["cf_net_r"] = np.where(reached, sibling.cf_target_net_r, -1.0)
        pieces.append(sibling)
    expanded = pd.concat(pieces, ignore_index=True, sort=False)
    expanded = expanded[
        expanded.cf_gross_rr.ge(1.0 - 1e-10)
        & expanded.cf_target_net_r.gt(0.0)
    ].copy()
    expanded["target_candidate_count"] = expanded.groupby("decision_id").decision_id.transform("size")
    expanded["target_candidate_rank"] = expanded.groupby("decision_id").cf_target_net_r.rank(method="dense")
    expanded["target_frontier_percentile"] = np.where(
        expanded.target_candidate_count.gt(1),
        (expanded.target_candidate_rank - 1.0) / (expanded.target_candidate_count - 1.0),
        0.0,
    )
    expanded["decision_weight"] = 1.0 / expanded.target_candidate_count.clip(lower=1).astype(float)
    expanded["checkpoint_id"] = (
        expanded.decision_id + ":ROUTE:" + expanded.route_fraction.map(lambda value: f"{value:.2f}")
    )
    return expanded.sort_values(["order_time_ns", "decision_id", "route_fraction"]).reset_index(drop=True)


def enrich_causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = frame.copy()
    sign = np.where(output.side.astype(str).eq("LONG"), 1.0, -1.0)
    entry = _number(output, "entry", 1.0).abs().clip(lower=EPS)
    planned_risk = (_number(output, "entry") - _number(output, "stop")).abs().clip(lower=EPS)
    output["risk_bps"] = planned_risk / entry * 10_000.0
    output["zone_width_bps"] = (
        _number(output, "zone_upper") - _number(output, "zone_lower")
    ).abs() / entry * 10_000.0
    output["source_width_bps"] = (
        _number(output, "source_upper") - _number(output, "source_lower")
    ).abs() / entry * 10_000.0
    output["structure_alignment"] = sign * _number(output, "structure_multiscale_trend_vote")
    output["event_response_flow_sum"] = _number(output, "event_flow_signed") + _number(output, "response_flow_signed")
    output["event_to_response_flow_change"] = _number(output, "response_flow_signed") - _number(output, "event_flow_signed")
    output["event_response_strength_product"] = _number(output, "event_strength") * _number(output, "response_strength")
    output["acceptance_mass"] = (
        _number(output, "event_outside_volume_ratio")
        * np.maximum(_number(output, "event_value_migration_atr"), 0.0)
        * np.log1p(np.maximum(_number(output, "event_consecutive_outside_closes"), 0.0))
    )
    output["route_value_density"] = (
        np.log1p(np.maximum(_number(output, "cf_target_net_r"), 0.0))
        * _number(output, "route_clarity", 0.0).clip(0.0, 1.0)
        * (1.0 - 0.5 * _number(output, "route_volume_congestion", 0.0).clip(0.0, 1.0))
    )
    output["confluence_log"] = np.log1p(np.maximum(_number(output, "source_confluence_count"), 0.0))
    output["semantic_weight_log"] = np.log1p(np.maximum(_number(output, "source_confluence_weight"), 0.0))
    output["target_weight_log"] = np.log1p(np.maximum(_number(output, "target_semantic_weight"), 0.0))
    output["route_barrier_pressure"] = (
        np.log1p(np.maximum(_number(output, "route_barrier_count"), 0.0))
        * np.maximum(_number(output, "route_strongest_barrier_ratio"), 0.0)
    )
    failed = output.family.astype(str).eq("FAILED_AUCTION_REVERSAL").astype(float)
    accepted = output.family.astype(str).eq("ACCEPTED_AUCTION_CONTINUATION").astype(float)
    output["failed_reclaim_completion"] = failed * (
        np.maximum(_number(output, "event_penetration_atr"), 0.0)
        + np.maximum(_number(output, "response_strength"), 0.0)
        + np.maximum(_number(output, "event_to_response_flow_change"), 0.0)
    )
    output["accepted_value_completion"] = accepted * (
        np.maximum(_number(output, "acceptance_mass"), 0.0)
        + np.maximum(_number(output, "response_strength"), 0.0)
        + np.maximum(_number(output, "structure_alignment"), 0.0)
    )
    timestamp = pd.to_datetime(_number(output, "order_time_ns", np.nan), unit="ns", utc=True, errors="coerce")
    minute = timestamp.dt.hour.fillna(0) * 60 + timestamp.dt.minute.fillna(0)
    output["decision_time_sin"] = np.sin(2.0 * np.pi * minute / 1440.0)
    output["decision_time_cos"] = np.cos(2.0 * np.pi * minute / 1440.0)
    output["decision_weekday_sin"] = np.sin(2.0 * np.pi * timestamp.dt.dayofweek.fillna(0) / 7.0)
    output["decision_weekday_cos"] = np.cos(2.0 * np.pi * timestamp.dt.dayofweek.fillna(0) / 7.0)
    return output


def _eligible_numeric_columns(frame: pd.DataFrame) -> list[str]:
    output: list[str] = []
    for name in frame.columns:
        low = str(name).lower()
        if name in ABSOLUTE_PRICE_FIELDS or name in IDENTIFIER_TOKENS or name == "symbol":
            continue
        if low.endswith("_time_ns") or low.endswith("_index"):
            continue
        if any(token in low for token in POST_DECISION_TOKENS):
            continue
        if name in CATEGORICAL_FIELDS:
            continue
        if pd.api.types.is_numeric_dtype(frame[name]) and frame[name].nunique(dropna=True) > 1:
            output.append(name)
    return sorted(set(output))


def design_matrices(train: pd.DataFrame, test: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    train = enrich_causal_features(train)
    test = enrich_causal_features(test)
    numeric = _eligible_numeric_columns(train)
    x_train = pd.DataFrame(index=train.index)
    x_test = pd.DataFrame(index=test.index)
    for name in numeric:
        train_values = pd.to_numeric(train[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
        test_values = pd.to_numeric(test[name], errors="coerce").replace([np.inf, -np.inf], np.nan)
        median = float(train_values.median()) if train_values.notna().any() else 0.0
        train_values = train_values.fillna(median)
        test_values = test_values.fillna(median)
        if train_values.nunique() > 10:
            low, high = train_values.quantile([0.005, 0.995])
            if math.isfinite(float(low)) and math.isfinite(float(high)) and high > low:
                train_values = train_values.clip(float(low), float(high))
                test_values = test_values.clip(float(low), float(high))
        x_train[name] = train_values.astype(float)
        x_test[name] = test_values.astype(float)
    for name in CATEGORICAL_FIELDS:
        if name not in train:
            continue
        train_values = train[name].fillna("MISSING").astype(str)
        test_values = test[name].fillna("MISSING").astype(str)
        train_dummies = pd.get_dummies(train_values, prefix=name, dtype=float)
        test_dummies = pd.get_dummies(test_values, prefix=name, dtype=float)
        columns = list(train_dummies.columns)
        x_train = pd.concat([x_train, train_dummies], axis=1)
        x_test = pd.concat([x_test, test_dummies.reindex(columns=columns, fill_value=0.0)], axis=1)
    x_test = x_test.reindex(columns=x_train.columns, fill_value=0.0)
    return x_train.astype(float), x_test.astype(float), list(x_train.columns)


def _effective_decisions(frame: pd.DataFrame) -> float:
    return float(_number(frame, "decision_weight", 1.0).sum())


def model_ready(frame: pd.DataFrame) -> bool:
    y = _number(frame, "target_first_label", np.nan)
    weights = _number(frame, "decision_weight", 1.0)
    positive = float(weights[y.eq(1)].sum())
    negative = float(weights[y.eq(0)].sum())
    return (
        _effective_decisions(frame) >= MIN_TRAIN_DECISIONS
        and positive >= MIN_CLASS_DECISIONS
        and negative >= MIN_CLASS_DECISIONS
        and y.nunique() >= 2
    )


def fit_predict_models(train: pd.DataFrame, test: pd.DataFrame, seed_offset: int = 0) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    if not model_ready(train):
        raise RuntimeError(
            f"Insufficient causal decisions for target model: decisions={_effective_decisions(train):.1f}"
        )
    x_train, x_test, columns = design_matrices(train, test)
    y = train.target_first_label.astype(int).to_numpy()
    weights = _number(train, "decision_weight", 1.0).to_numpy(float)
    prior = float(np.sum(weights * y) / max(weights.sum(), EPS))
    predictions: list[np.ndarray] = []
    model_names: list[str] = []
    for sequence, seed in enumerate((41,)):
        model = HistGradientBoostingClassifier(
            learning_rate=0.035,
            max_iter=70,
            max_leaf_nodes=9,
            min_samples_leaf=max(18, min(45, len(train) // 35)),
            l2_regularization=18.0,
            early_stopping=False,
            random_state=seed + seed_offset,
        )
        model.fit(x_train, y, sample_weight=weights)
        predictions.append(model.predict_proba(x_test)[:, 1])
        model_names.append(f"hgb_{sequence}")
    for sequence, seed in enumerate((53,)):
        model = ExtraTreesClassifier(
            n_estimators=90,
            max_depth=7,
            min_samples_leaf=max(5, min(16, len(train) // 120)),
            max_features=0.65,
            bootstrap=True,
            n_jobs=-1,
            random_state=seed + seed_offset,
        )
        model.fit(x_train, y, sample_weight=weights)
        predictions.append(model.predict_proba(x_test)[:, 1])
        model_names.append(f"extra_{sequence}")
    matrix = np.vstack(predictions)
    shrink = _effective_decisions(train) / (_effective_decisions(train) + 120.0)
    mean = prior + shrink * (matrix.mean(axis=0) - prior)
    uncertainty = np.sqrt(matrix.var(axis=0) + prior * (1.0 - prior) / max(_effective_decisions(train) + 12.0, 1.0))

    family_used: dict[str, int] = {}
    return np.clip(mean, 0.003, 0.997), uncertainty, {
        "features": len(columns),
        "effective_decisions": _effective_decisions(train),
        "prior": prior,
        "shrink": shrink,
        "models": model_names,
        "family_experts": family_used,
    }


def calibrate_probability(history_scored: pd.DataFrame, test: pd.DataFrame, raw: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    if len(history_scored) < 160 or history_scored.target_first_label.nunique() < 2:
        return np.clip(raw, 0.003, 0.997), {"method": "uncalibrated_first_causal_fold", "rows": int(len(history_scored))}
    train = history_scored.copy()
    x_train = pd.DataFrame(
        {
            "raw_logit": _logit(train.raw_probability),
            "log_target": np.log1p(np.maximum(_number(train, "cf_target_net_r"), 0.0)),
            "route_fraction": _number(train, "route_fraction"),
        },
        index=train.index,
    )
    x_test = pd.DataFrame(
        {
            "raw_logit": _logit(raw),
            "log_target": np.log1p(np.maximum(_number(test, "cf_target_net_r"), 0.0)),
            "route_fraction": _number(test, "route_fraction"),
        },
        index=test.index,
    )
    calibrator = LogisticRegression(C=0.08, max_iter=1000)
    calibrator.fit(
        x_train,
        train.target_first_label.astype(int),
        sample_weight=_number(train, "decision_weight", 1.0),
    )
    return np.clip(calibrator.predict_proba(x_test)[:, 1], 0.003, 0.997), {
        "method": "rolling_logistic",
        "rows": int(len(train)),
        "effective_decisions": _effective_decisions(train),
    }


@dataclass
class SupportCurve:
    curve: IsotonicRegression
    effective: float

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = np.log1p(np.maximum(_number(frame, "cf_target_net_r"), 0.0))
        return np.clip(self.curve.predict(x), 0.003, 0.997)


def fit_period_support_curves(history: pd.DataFrame) -> dict[str, SupportCurve]:
    curves: dict[str, SupportCurve] = {}
    for period, group in history.groupby("diagnostic_period", sort=True):
        weights = _number(group, "decision_weight", 1.0).to_numpy(float)
        y = group.target_first_label.astype(int).to_numpy()
        effective = float(weights.sum())
        positive = float(np.sum(weights * y))
        negative = effective - positive
        if effective < 35.0 or positive < 4.0 or negative < 4.0:
            continue
        x = np.log1p(np.maximum(_number(group, "cf_target_net_r"), 0.0))
        curve = IsotonicRegression(
            increasing=False,
            out_of_bounds="clip",
            y_min=0.003,
            y_max=0.997,
        )
        curve.fit(x, y, sample_weight=weights)
        curves[str(period)] = SupportCurve(curve, effective)
    return curves


def monotone_scenarios(frame: pd.DataFrame, scenarios: np.ndarray) -> np.ndarray:
    output = np.asarray(scenarios, dtype=float).copy()
    for row in range(output.shape[0]):
        values = pd.Series(output[row], index=frame.index, dtype=float)
        for _, group in frame.groupby("decision_id", sort=False):
            ordered = group.sort_values(["cf_target_net_r", "route_fraction", "checkpoint_id"])
            values.loc[ordered.index] = np.minimum.accumulate(values.loc[ordered.index].to_numpy(float))
        output[row] = values.to_numpy(float)
    return output


def probability_scenarios(history: pd.DataFrame, test: pd.DataFrame, calibrated: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    curves = fit_period_support_curves(history)
    if len(curves) < 2:
        return calibrated[None, :], {"support_periods": sorted(curves), "scenario_count": 1}
    names = sorted(curves)
    support = np.vstack([curves[name].predict(test) for name in names])
    median = np.median(support, axis=0)
    update = _logit(calibrated) - _logit(median)
    scenarios = _sigmoid(_logit(support) + update[None, :])
    return monotone_scenarios(test, scenarios), {
        "support_periods": names,
        "scenario_count": int(len(names)),
    }


def entropic_ce(utilities: np.ndarray) -> np.ndarray:
    scaled = -ENTROPIC_ETA * np.asarray(utilities, dtype=float)
    maximum = scaled.max(axis=0)
    return -(maximum + np.log(np.exp(scaled - maximum[None, :]).mean(axis=0))) / ENTROPIC_ETA


def score_period(
    history: pd.DataFrame,
    test: pd.DataFrame,
    prior_scored: pd.DataFrame,
    sequence: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    raw, uncertainty, model_diag = fit_predict_models(history, test, seed_offset=sequence * 1009)
    calibrated, calibration_diag = calibrate_probability(prior_scored, test, raw)
    scenarios, support_diag = probability_scenarios(history, test, calibrated)
    target_r = _number(test, "cf_target_net_r").to_numpy(float)
    win_log = np.log1p(RISK_FRACTION * target_r)
    loss_log = math.log(1.0 - RISK_FRACTION)
    utilities = scenarios * win_log[None, :] + (1.0 - scenarios) * loss_log
    output = test.copy()
    output["raw_probability"] = raw
    output["model_uncertainty"] = uncertainty
    output["p_target_mean"] = scenarios.mean(axis=0)
    output["p_target_worst"] = scenarios.min(axis=0)
    output["p_target_std"] = scenarios.std(axis=0)
    output["mean_expected_log_growth"] = utilities.mean(axis=0)
    output["robust_expected_log_growth"] = entropic_ce(utilities)
    output["break_even_probability"] = -loss_log / np.maximum(win_log - loss_log, EPS)
    output["probability_edge"] = output.p_target_mean - output.break_even_probability
    return output, {
        "model": model_diag,
        "calibration": calibration_diag,
        "support": support_diag,
    }


def choose_targets(scored: pd.DataFrame, variant: str) -> pd.DataFrame:
    if scored.empty:
        return scored.copy()
    work = scored.copy()
    if variant == "dynamic_robust":
        sort_columns = ["decision_id", "robust_expected_log_growth", "p_target_worst", "cf_target_net_r"]
        ascending = [True, False, False, False]
    elif variant == "dynamic_mean":
        sort_columns = ["decision_id", "mean_expected_log_growth", "p_target_mean", "cf_target_net_r"]
        ascending = [True, False, False, False]
    elif variant == "completion_first":
        work["completion_score"] = (
            work.p_target_worst
            + 0.20 * np.tanh(30.0 * work.robust_expected_log_growth)
            + 0.04 * np.log1p(np.maximum(work.cf_target_net_r, 0.0))
        )
        sort_columns = ["decision_id", "completion_score", "robust_expected_log_growth", "cf_target_net_r"]
        ascending = [True, False, False, False]
    elif variant == "minimum_route_checkpoint":
        sort_columns = ["decision_id", "cf_gross_rr", "p_target_worst"]
        ascending = [True, True, False]
    elif variant == "full_structural_route":
        work = work[np.isclose(work.route_fraction, 1.0)].copy()
        sort_columns = ["decision_id", "robust_expected_log_growth"]
        ascending = [True, False]
    else:
        raise ValueError(variant)
    return (
        work.sort_values(sort_columns, ascending=ascending)
        .drop_duplicates("decision_id", keep="first")
        .sort_values(["order_time_ns", "episode_id"])
        .reset_index(drop=True)
    )


def _candidate_gates() -> list[tuple[float, float]]:
    p_floors = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
    ce_floors = (0.0,)
    return [(p, ce) for p in p_floors for ce in ce_floors]


def continuous_metrics(frame: pd.DataFrame, periods: Sequence[str] | None = None) -> dict[str, Any]:
    values = pd.to_numeric(frame.get("cf_net_r", pd.Series(dtype=float)), errors="coerce").dropna()
    nav = peak = 1.0
    maximum_drawdown = 0.0
    logs: list[float] = []
    for value in values:
        multiplier = max(EPS, 1.0 + RISK_FRACTION * float(value))
        nav *= multiplier
        logs.append(math.log(multiplier))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    if periods is None:
        periods = sorted(frame.diagnostic_period.astype(str).unique()) if len(frame) else []
    days = _calendar_days(periods)
    return {
        "trades": int(len(values)),
        "calendar_days": int(days),
        "trades_per_day": float(len(values) / max(days, 1)),
        "target_first_rate": float((values > 0.0).mean()) if len(values) else None,
        "mean_net_r": float(values.mean()) if len(values) else None,
        "median_net_r": float(values.median()) if len(values) else None,
        "average_win_r": float(wins.mean()) if len(wins) else None,
        "profit_factor_r": float(wins.sum() / abs(losses.sum())) if len(wins) and len(losses) else (math.inf if len(wins) else 0.0),
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "log_growth_per_day": float(sum(logs) / max(days, 1)),
        "mean_route_fraction": float(_number(frame, "route_fraction").mean()) if len(frame) else None,
        "mean_planned_gross_rr": float(_number(frame, "cf_gross_rr").mean()) if len(frame) else None,
        "mean_planned_target_net_r": float(_number(frame, "cf_target_net_r").mean()) if len(frame) else None,
    }


def online_route(decisions: pd.DataFrame, p_floor: float, ce_floor: float) -> pd.DataFrame:
    eligible = decisions[
        decisions.p_target_worst.ge(p_floor)
        & decisions.robust_expected_log_growth.gt(ce_floor)
        & decisions.probability_edge.gt(0.0)
    ].sort_values(["order_time_ns", "robust_expected_log_growth", "episode_id"], ascending=[True, False, True])
    selected: list[pd.Series] = []
    busy_until = -np.inf
    used_parent: set[str] = set()
    for _, row in eligible.iterrows():
        order_time = float(row.order_time_ns)
        if order_time < busy_until:
            continue
        parent = str(row.parent_episode_id)
        if parent in used_parent:
            continue
        selected.append(row)
        used_parent.add(parent)
        busy_until = max(order_time, float(row.resolution_time_ns))
    return pd.DataFrame(selected).reset_index(drop=True) if selected else eligible.iloc[:0].copy()



def fast_history_route(decisions: pd.DataFrame, p_floor: float, ce_floor: float) -> pd.DataFrame:
    """Fast gate search on the inherited already-one-account trade stream."""
    eligible = decisions[
        decisions.p_target_worst.ge(p_floor)
        & decisions.robust_expected_log_growth.gt(ce_floor)
        & decisions.probability_edge.gt(0.0)
    ].sort_values(["order_time_ns", "robust_expected_log_growth", "episode_id"], ascending=[True, False, True])
    return eligible.drop_duplicates("parent_episode_id", keep="first").reset_index(drop=True)

def select_gate(history_decisions: pd.DataFrame) -> dict[str, Any]:
    periods = _period_order(history_decisions) if len(history_decisions) else []
    if len(history_decisions) == 0:
        return {"p_floor": 0.50, "ce_floor": 0.0, "source": "precommitted_completion_dominance"}
    records: list[dict[str, Any]] = []
    for p_floor, ce_floor in _candidate_gates():
        selected = fast_history_route(history_decisions, p_floor, ce_floor)
        metrics = continuous_metrics(selected, periods)
        by_period = []
        for period in periods:
            group = selected[selected.diagnostic_period.astype(str).eq(period)]
            by_period.append(continuous_metrics(group, [period])["log_growth_per_day"])
        daily_mean = float(np.mean(by_period)) if by_period else -math.inf
        daily_std = float(np.std(by_period)) if by_period else math.inf
        frequency_penalty = max(0.0, 1.0 - metrics["trades_per_day"])
        objective = daily_mean - 0.55 * daily_std - 0.010 * frequency_penalty
        records.append(
            {
                "p_floor": p_floor,
                "ce_floor": ce_floor,
                "objective": objective,
                "daily_mean": daily_mean,
                "daily_std": daily_std,
                **metrics,
            }
        )
    frame = pd.DataFrame(records)
    frequent = frame[frame.trades_per_day.ge(1.0)]
    pool = frequent if len(frequent) else frame[frame.trades_per_day.ge(0.65)]
    if not len(pool):
        pool = frame
    best = pool.sort_values(
        ["objective", "log_growth_per_day", "target_first_rate", "trades_per_day"],
        ascending=[False, False, False, False],
    ).iloc[0].to_dict()
    best["source"] = "causal_prior_oos_gate_optimization"
    return best


def group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    if frame.empty or key not in frame:
        return {}
    return {
        str(value): continuous_metrics(group, sorted(group.diagnostic_period.astype(str).unique()))
        for value, group in frame.groupby(key, dropna=False)
    }


def evaluate(trades: pd.DataFrame, output: Path) -> dict[str, Any]:
    expanded = expand_route_checkpoints(trades)
    periods = _period_order(expanded)
    if len(periods) < 5:
        raise RuntimeError(f"Need at least five chronological short windows, found {periods}")

    scored_pieces: list[pd.DataFrame] = []
    diagnostics: dict[str, Any] = {}
    variant_decisions: dict[str, list[pd.DataFrame]] = {
        name: []
        for name in (
            "dynamic_robust",
            "dynamic_mean",
            "completion_first",
            "minimum_route_checkpoint",
            "full_structural_route",
        )
    }
    prior_scored = expanded.iloc[:0].copy()
    for sequence, period in enumerate(periods):
        test = expanded[expanded.diagnostic_period.astype(str).eq(period)].copy()
        history = expanded[
            expanded.order_time_ns.lt(test.order_time_ns.min())
        ].copy()
        if sequence < 2 or not model_ready(history):
            diagnostics[period] = {
                "role": "model_warmup",
                "history_effective_decisions": _effective_decisions(history),
            }
            continue
        scored, diag = score_period(history, test, prior_scored, sequence)
        scored_pieces.append(scored)
        diagnostics[period] = {"role": "causal_scored", **diag}
        for variant in variant_decisions:
            chosen = choose_targets(scored, variant)
            chosen["policy_variant"] = variant
            variant_decisions[variant].append(chosen)
        prior_scored = pd.concat([prior_scored, scored], ignore_index=True, sort=False)

    scored_all = pd.concat(scored_pieces, ignore_index=True, sort=False)
    scored_periods = _period_order(scored_all)
    # The first causal prediction window is policy-development. Remaining windows are
    # sequential short evaluations; every gate sees only earlier causal predictions.
    evaluation_periods = scored_periods[1:]
    summaries: dict[str, Any] = {}
    all_selected: list[pd.DataFrame] = []
    all_decisions: list[pd.DataFrame] = []
    for variant, pieces in variant_decisions.items():
        decisions = pd.concat(pieces, ignore_index=True, sort=False)
        selected_pieces: list[pd.DataFrame] = []
        development_decisions = decisions[
            decisions.diagnostic_period.astype(str).eq(scored_periods[0])
        ].copy()
        frozen_gate = select_gate(development_decisions)
        gate_by_period: dict[str, Any] = {
            period: frozen_gate for period in scored_periods
        }
        for period in evaluation_periods:
            current = decisions[decisions.diagnostic_period.astype(str).eq(period)].copy()
            selected = online_route(
                current,
                float(frozen_gate["p_floor"]),
                float(frozen_gate["ce_floor"]),
            )
            selected["policy_variant"] = variant
            selected["gate_p_floor"] = float(frozen_gate["p_floor"])
            selected["gate_ce_floor"] = float(frozen_gate["ce_floor"])
            selected_pieces.append(selected)
        selected_all = pd.concat(selected_pieces, ignore_index=True, sort=False) if selected_pieces else decisions.iloc[:0].copy()
        selected_all = selected_all.sort_values(["order_time_ns", "episode_id"]).reset_index(drop=True)
        summaries[variant] = {
            "account": continuous_metrics(selected_all, evaluation_periods),
            "by_period": group_metrics(selected_all, "diagnostic_period"),
            "by_family": group_metrics(selected_all, "family"),
            "by_source_kind": group_metrics(selected_all, "source_kind"),
            "by_location": group_metrics(selected_all, "location_kind"),
            "by_response": group_metrics(selected_all, "response_kind"),
            "gate_by_period": gate_by_period,
        }
        all_selected.append(selected_all)
        all_decisions.append(decisions)

    # The integrated system is the variant with the strongest prior-predicted policy
    # objective, chosen without consulting the final evaluation period.  To avoid a
    # post-hoc winner, select using all evaluation periods except the last, then report
    # the untouched final period separately.
    selection_periods = evaluation_periods[:-1]
    final_period = evaluation_periods[-1]
    variant_selection: list[dict[str, Any]] = []
    for variant, selected in zip(variant_decisions, all_selected):
        prior = selected[selected.diagnostic_period.astype(str).isin(selection_periods)]
        metrics = continuous_metrics(prior, selection_periods)
        period_growth = [
            continuous_metrics(prior[prior.diagnostic_period.astype(str).eq(period)], [period])["log_growth_per_day"]
            for period in selection_periods
        ]
        objective = metrics["log_growth_per_day"] - 0.55 * float(np.std(period_growth))
        variant_selection.append({"variant": variant, "objective": objective, **metrics})
    variant_frame = pd.DataFrame(variant_selection).sort_values(
        ["objective", "log_growth_per_day", "target_first_rate", "trades_per_day"],
        ascending=[False, False, False, False],
    )
    integrated_variant = str(variant_frame.iloc[0].variant)
    integrated_index = list(variant_decisions).index(integrated_variant)
    integrated = all_selected[integrated_index].copy()
    final = integrated[integrated.diagnostic_period.astype(str).eq(final_period)].copy()

    summary = {
        "policy_version": MODEL_VERSION,
        "inherited_market_hypothesis": (
            "confluent liquidity source -> accepted or failed auction -> final price-volume "
            "control -> defended return -> structural stop -> opposing structural route"
        ),
        "missing_piece": (
            "learn target-before-stop reachability for immutable checkpoints on the causal "
            "structural route, then route one account by post-cost robust log growth"
        ),
        "risk_fraction": RISK_FRACTION,
        "one_global_account": True,
        "partial_entries_or_exits": False,
        "one_pre_entry_target": True,
        "gross_rr_floor": 1.0,
        "symbol_identity_is_feature": False,
        "absolute_price_is_feature": False,
        "post_decision_path_is_feature": False,
        "route_fractions_researched": list(ROUTE_FRACTIONS),
        "warmup_periods": periods[:2],
        "policy_development_period": scored_periods[0],
        "evaluation_periods": evaluation_periods,
        "variant_selection_periods": selection_periods,
        "final_untouched_period": final_period,
        "integrated_variant": integrated_variant,
        "integrated_account": continuous_metrics(integrated, evaluation_periods),
        "integrated_final_period": continuous_metrics(final, [final_period]),
        "integrated_by_period": group_metrics(integrated, "diagnostic_period"),
        "integrated_by_family": group_metrics(integrated, "family"),
        "variants": summaries,
        "variant_selection": variant_frame.to_dict("records"),
        "model_diagnostics": diagnostics,
        "input_trades": int(len(trades)),
        "expanded_checkpoint_rows": int(len(expanded)),
        "causal_scored_checkpoint_rows": int(len(scored_all)),
    }

    output.mkdir(parents=True, exist_ok=True)
    scored_all.to_csv(output / "scored_route_checkpoints.csv.gz", index=False, compression="gzip")
    pd.concat(all_decisions, ignore_index=True, sort=False).to_csv(
        output / "chosen_target_by_variant.csv.gz", index=False, compression="gzip"
    )
    pd.concat(all_selected, ignore_index=True, sort=False).to_csv(output / "selected_trades_all_variants.csv", index=False)
    integrated.to_csv(output / "integrated_selected_trades.csv", index=False)
    variant_frame.to_csv(output / "variant_selection.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evaluate(load_trades(args.trades), args.output)


if __name__ == "__main__":
    main()
