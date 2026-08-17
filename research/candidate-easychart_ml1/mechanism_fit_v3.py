#!/usr/bin/env python3
"""Cross-environment selective policy for mechanism-level causal actions.

The model does not learn a symbol or a calendar. It estimates the competing
first-passage outcomes of already frozen entry/stop/target plans, prices their
full terminal log-return distribution, and selects one action per causal
cluster through one continuous account. All model choice and calibration use
period-held-out development predictions; the final period is never used for
selection.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression, Ridge

from mechanism_harvest_v3 import FEATURE_COLUMNS

RISK_FRACTION = 0.03
DEVELOPMENT_PREFIX = "dev-"
FINAL_PREFIX = "final-"
OUTCOMES = ("TARGET_FIRST", "STOP_FIRST", "TIMEOUT")
OUTCOME_TO_INT = {name: index for index, name in enumerate(OUTCOMES)}
GAMMA_GRID = (0.0, 10.0, 25.0, 50.0)
UNCERTAINTY_GRID = (0.0, 0.5, 1.0)
ACTION_DELAYS_FOR_FIT = frozenset((0, 1, 2, 4, 7, 10, 15))
MAX_OBJECTIVE_RANK_FOR_FIT = 3


@dataclass(frozen=True)
class RobustScale:
    median: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, values: np.ndarray) -> "RobustScale":
        median = np.nanmedian(values, axis=0)
        median[~np.isfinite(median)] = 0.0
        filled = np.where(np.isfinite(values), values, median)
        q25 = np.nanpercentile(filled, 25.0, axis=0)
        q75 = np.nanpercentile(filled, 75.0, axis=0)
        scale = q75 - q25
        scale[~np.isfinite(scale) | (scale < 1e-8)] = 1.0
        return cls(median=median, scale=scale)

    def transform(self, values: np.ndarray) -> np.ndarray:
        filled = np.where(np.isfinite(values), values, self.median)
        return np.clip((filled - self.median) / self.scale, -8.0, 8.0)


@dataclass
class OutcomeModel:
    kind: str
    scale: RobustScale
    classifier: Any
    timeout_regressor: Any
    duration_regressor: Any
    target_residual_r: float
    stop_r: float
    features: tuple[str, ...]

    @classmethod
    def fit(cls, frame: pd.DataFrame, kind: str, seed: int) -> "OutcomeModel":
        raw = _feature_matrix(frame)
        scale = RobustScale.fit(raw)
        x = scale.transform(raw)
        y = frame["outcome"].map(OUTCOME_TO_INT).to_numpy(int)
        weights = _sample_weight(frame)
        unique = np.unique(y)
        if len(unique) < 2:
            raise RuntimeError(f"training sample has only one outcome: {unique.tolist()}")

        if kind == "linear":
            classifier = LogisticRegression(
                C=0.12,
                max_iter=1200,
                solver="lbfgs",
                random_state=seed,
            )
            timeout_regressor: Any = Ridge(alpha=25.0)
            duration_regressor: Any = Ridge(alpha=30.0)
        elif kind == "tree":
            classifier = HistGradientBoostingClassifier(
                learning_rate=0.035,
                max_iter=150,
                max_leaf_nodes=7,
                max_depth=3,
                min_samples_leaf=100,
                l2_regularization=14.0,
                random_state=seed,
            )
            timeout_regressor = HistGradientBoostingRegressor(
                learning_rate=0.035,
                max_iter=120,
                max_leaf_nodes=7,
                max_depth=3,
                min_samples_leaf=80,
                l2_regularization=14.0,
                loss="squared_error",
                random_state=seed + 1000,
            )
            duration_regressor = HistGradientBoostingRegressor(
                learning_rate=0.035,
                max_iter=120,
                max_leaf_nodes=7,
                max_depth=3,
                min_samples_leaf=80,
                l2_regularization=14.0,
                loss="squared_error",
                random_state=seed + 2000,
            )
        else:
            raise ValueError(kind)

        classifier.fit(x, y, sample_weight=weights)

        timeout_mask = frame["outcome"].eq("TIMEOUT").to_numpy()
        timeout_target = np.log1p(
            np.clip(
                RISK_FRACTION * pd.to_numeric(frame["realized_r"], errors="raise").to_numpy(float),
                -0.80,
                2.00,
            )
        )
        if int(timeout_mask.sum()) >= 80:
            timeout_regressor.fit(
                x[timeout_mask],
                timeout_target[timeout_mask],
                sample_weight=weights[timeout_mask],
            )
        else:
            timeout_regressor = Ridge(alpha=35.0)
            timeout_regressor.fit(x, timeout_target, sample_weight=weights)

        duration_target = np.log1p(
            pd.to_numeric(frame["duration_minutes"], errors="raise").to_numpy(float)
        )
        duration_regressor.fit(x, duration_target, sample_weight=weights)

        target_rows = frame[frame["outcome"].eq("TARGET_FIRST")]
        if target_rows.empty:
            target_residual_r = 0.0
        else:
            residual = (
                pd.to_numeric(target_rows["realized_r"], errors="raise")
                - pd.to_numeric(target_rows["target_r"], errors="raise")
            )
            target_residual_r = float(np.average(residual, weights=_sample_weight(target_rows)))
        stop_rows = frame[frame["outcome"].eq("STOP_FIRST")]
        if stop_rows.empty:
            stop_r = -1.0
        else:
            stop_r = float(
                np.average(
                    pd.to_numeric(stop_rows["realized_r"], errors="raise"),
                    weights=_sample_weight(stop_rows),
                )
            )
        target_residual_r = float(np.clip(target_residual_r, -0.35, 0.05))
        stop_r = float(np.clip(stop_r, -1.35, -0.90))
        return cls(
            kind=kind,
            scale=scale,
            classifier=classifier,
            timeout_regressor=timeout_regressor,
            duration_regressor=duration_regressor,
            target_residual_r=target_residual_r,
            stop_r=stop_r,
            features=tuple(FEATURE_COLUMNS),
        )

    def predict(self, frame: pd.DataFrame) -> dict[str, np.ndarray]:
        x = self.scale.transform(_feature_matrix(frame))
        raw_probability = self.classifier.predict_proba(x)
        probability = np.zeros((len(frame), len(OUTCOMES)), dtype=float)
        for local_index, outcome_class in enumerate(self.classifier.classes_):
            probability[:, int(outcome_class)] = raw_probability[:, local_index]
        probability = np.clip(probability, 1e-6, None)
        probability /= probability.sum(axis=1, keepdims=True)

        timeout_log = np.asarray(self.timeout_regressor.predict(x), dtype=float)
        timeout_log = np.clip(timeout_log, math.log(0.80), math.log(1.20))
        duration = np.expm1(np.asarray(self.duration_regressor.predict(x), dtype=float))
        duration = np.clip(duration, 1.0, 720.0)
        target_r = pd.to_numeric(frame["target_r"], errors="raise").to_numpy(float)
        target_factor = 1.0 + RISK_FRACTION * (target_r + self.target_residual_r)
        target_log = np.log(np.clip(target_factor, 1e-6, None))
        stop_log = np.full(len(frame), math.log1p(RISK_FRACTION * self.stop_r), dtype=float)
        return {
            "probability": probability,
            "target_log": target_log,
            "stop_log": stop_log,
            "timeout_log": timeout_log,
            "duration": duration,
        }

    def description(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "features": list(self.features),
            "target_residual_r": self.target_residual_r,
            "stop_r": self.stop_r,
        }


def _feature_matrix(frame: pd.DataFrame) -> np.ndarray:
    missing = sorted(set(FEATURE_COLUMNS) - set(frame.columns))
    if missing:
        raise KeyError(f"missing feature columns: {missing}")
    return (
        frame.loc[:, FEATURE_COLUMNS]
        .apply(pd.to_numeric, errors="coerce")
        .to_numpy(float)
    )


def _sample_weight(frame: pd.DataFrame) -> np.ndarray:
    if frame.empty:
        return np.empty(0, dtype=float)
    if "episode_weight" in frame.columns:
        episode = (
            pd.to_numeric(frame["episode_weight"], errors="coerce")
            .fillna(0.0)
            .to_numpy(float)
        )
    else:
        episode = np.ones(len(frame), dtype=float)
    period_size = (
        frame.groupby("period")["action_id"]
        .transform("count")
        .to_numpy(float)
    )
    symbol_size = (
        frame.groupby("symbol")["action_id"]
        .transform("count")
        .to_numpy(float)
    )
    weight = episode / np.sqrt(np.maximum(period_size * symbol_size, 1.0))
    if not np.isfinite(weight).all() or float(weight.sum()) <= 0.0:
        weight = np.ones(len(frame), dtype=float)
    weight *= len(weight) / float(weight.sum())
    return weight


def _load(root: Path) -> pd.DataFrame:
    paths = sorted(root.rglob("actions.csv"))
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame["source_file"] = str(path)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"no non-empty actions.csv below {root}")
    data = pd.concat(frames, ignore_index=True, sort=False)
    for column in ("event_time", "snapshot_time", "entry_time", "exit_time"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")
    if data["action_id"].duplicated().any():
        duplicates = data.loc[data["action_id"].duplicated(), "action_id"].head(10).tolist()
        raise RuntimeError(f"duplicate action identities: {duplicates}")
    invalid = data[~data["outcome"].isin(OUTCOMES)]
    if not invalid.empty:
        raise RuntimeError(f"unknown outcomes: {sorted(invalid['outcome'].unique())}")
    if (data["entry_time"] <= data["snapshot_time"]).any():
        raise RuntimeError("an action enters before or at its decision ordering timestamp")
    return data.sort_values(["entry_time", "cluster_id", "action_id"]).reset_index(drop=True)


def _thin_for_fit(frame: pd.DataFrame) -> pd.DataFrame:
    delay = pd.to_numeric(frame["entry_delay_minutes"], errors="coerce").round().astype("Int64")
    rank = pd.to_numeric(frame["objective_rank"], errors="coerce")
    keep = delay.isin(ACTION_DELAYS_FOR_FIT) & rank.le(MAX_OBJECTIVE_RANK_FOR_FIT)
    thinned = frame[keep].copy()
    if len(thinned) < 2000 or thinned["outcome"].nunique() < 3:
        return frame.copy()
    return thinned


def _training_subsets(frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame]]:
    subsets: list[tuple[str, pd.DataFrame]] = [("ALL", frame)]
    for symbol in sorted(frame["symbol"].unique()):
        subset = frame[frame["symbol"] != symbol]
        if len(subset) >= 1500 and subset["outcome"].nunique() >= 2:
            subsets.append((f"LEAVE_SYMBOL_{symbol}", subset))
    return subsets


def _fit_ensemble(frame: pd.DataFrame, seed: int = 7) -> list[OutcomeModel]:
    models: list[OutcomeModel] = []
    fit_frame = _thin_for_fit(frame)
    for subset_index, (_, subset) in enumerate(_training_subsets(fit_frame)):
        for kind_index, kind in enumerate(("linear", "tree")):
            try:
                models.append(
                    OutcomeModel.fit(
                        subset,
                        kind=kind,
                        seed=seed + 101 * subset_index + kind_index,
                    )
                )
            except Exception:
                continue
    if not models:
        raise RuntimeError("no outcome model could be fitted")
    return models


def _entropic_value(
    probability: np.ndarray,
    target_log: np.ndarray,
    stop_log: np.ndarray,
    timeout_log: np.ndarray,
    gamma: float,
) -> np.ndarray:
    outcomes = np.column_stack((target_log, stop_log, timeout_log))
    if gamma <= 0.0:
        return np.sum(probability * outcomes, axis=1)
    scaled = np.clip(-gamma * outcomes, -60.0, 60.0)
    moment = np.sum(probability * np.exp(scaled), axis=1)
    return -np.log(np.clip(moment, 1e-15, None)) / gamma


def _score(frame: pd.DataFrame, models: list[OutcomeModel]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    predictions = [model.predict(frame) for model in models]
    probabilities = np.stack([item["probability"] for item in predictions], axis=2)
    durations = np.column_stack([item["duration"] for item in predictions])
    output = frame.copy()
    for outcome_index, outcome_name in enumerate(OUTCOMES):
        values = probabilities[:, outcome_index, :]
        prefix = outcome_name.lower()
        output[f"pred_{prefix}_median"] = np.median(values, axis=1)
        output[f"pred_{prefix}_lower"] = np.quantile(values, 0.20, axis=1)
        output[f"pred_{prefix}_upper"] = np.quantile(values, 0.80, axis=1)
    output["pred_duration_median"] = np.median(durations, axis=1)
    output["pred_duration_upper"] = np.quantile(durations, 0.80, axis=1)
    output["outcome_model_disagreement"] = np.mean(
        np.std(probabilities, axis=2),
        axis=1,
    )
    for gamma in GAMMA_GRID:
        values = np.column_stack(
            [
                _entropic_value(
                    item["probability"],
                    item["target_log"],
                    item["stop_log"],
                    item["timeout_log"],
                    gamma,
                )
                for item in predictions
            ]
        )
        suffix = _gamma_suffix(gamma)
        output[f"raw_ce_median_{suffix}"] = np.median(values, axis=1)
        output[f"raw_ce_lower_{suffix}"] = np.quantile(values, 0.20, axis=1)
        output[f"raw_ce_upper_{suffix}"] = np.quantile(values, 0.80, axis=1)
        output[f"raw_ce_std_{suffix}"] = np.std(values, axis=1)
    output["model_count"] = len(models)
    return output


def _gamma_suffix(gamma: float) -> str:
    return f"g{int(round(gamma))}"


def _oof_score(development: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    scored: list[pd.DataFrame] = []
    model_counts: dict[str, int] = {}
    for fold_index, held_period in enumerate(sorted(development["period"].unique())):
        train = development[development["period"] != held_period]
        test = development[development["period"] == held_period]
        models = _fit_ensemble(train, seed=31 + 1000 * fold_index)
        model_counts[str(held_period)] = len(models)
        scored.append(_score(test, models))
    return (
        pd.concat(scored, ignore_index=True, sort=False)
        .sort_values(["entry_time", "cluster_id", "action_id"])
        .reset_index(drop=True),
        model_counts,
    )


def _realized_log(frame: pd.DataFrame) -> np.ndarray:
    realized_r = pd.to_numeric(frame["realized_r"], errors="raise").to_numpy(float)
    factors = 1.0 + RISK_FRACTION * realized_r
    if np.any(factors <= 0.0):
        raise RuntimeError("non-positive realized account factor")
    return np.log(factors)


def _fit_isotonic(x: np.ndarray, y: np.ndarray, weight: np.ndarray) -> IsotonicRegression | None:
    finite = np.isfinite(x) & np.isfinite(y) & np.isfinite(weight) & (weight > 0.0)
    if int(finite.sum()) < 200 or len(np.unique(x[finite])) < 20:
        return None
    model = IsotonicRegression(out_of_bounds="clip", y_min=-0.10, y_max=0.10)
    model.fit(x[finite], y[finite], sample_weight=weight[finite])
    return model


def _leave_period_calibrate(
    scored: pd.DataFrame,
    gamma: float,
    uncertainty_multiplier: float,
) -> pd.DataFrame:
    output_parts: list[pd.DataFrame] = []
    suffix = _gamma_suffix(gamma)
    raw_column = f"raw_ce_lower_{suffix}"
    std_column = f"raw_ce_std_{suffix}"
    for held_period in sorted(scored["period"].unique()):
        train = scored[scored["period"] != held_period]
        test = scored[scored["period"] == held_period].copy()
        calibrator = _fit_isotonic(
            pd.to_numeric(train[raw_column], errors="coerce").to_numpy(float),
            _realized_log(train),
            _sample_weight(train),
        )
        raw_test = pd.to_numeric(test[raw_column], errors="coerce").to_numpy(float)
        if calibrator is None:
            calibrated = raw_test
        else:
            calibrated = calibrator.predict(raw_test)
        uncertainty = pd.to_numeric(test[std_column], errors="coerce").fillna(np.inf).to_numpy(float)
        test["calibrated_expected_log"] = calibrated
        test["decision_edge"] = calibrated - uncertainty_multiplier * uncertainty
        test["selected_gamma"] = gamma
        test["selected_uncertainty_multiplier"] = uncertainty_multiplier
        output_parts.append(test)
    return (
        pd.concat(output_parts, ignore_index=True, sort=False)
        .sort_values(["entry_time", "cluster_id", "action_id"])
        .reset_index(drop=True)
    )


def _final_calibrate(
    final_scored: pd.DataFrame,
    oof_scored: pd.DataFrame,
    gamma: float,
    uncertainty_multiplier: float,
) -> pd.DataFrame:
    suffix = _gamma_suffix(gamma)
    raw_column = f"raw_ce_lower_{suffix}"
    std_column = f"raw_ce_std_{suffix}"
    calibrator = _fit_isotonic(
        pd.to_numeric(oof_scored[raw_column], errors="coerce").to_numpy(float),
        _realized_log(oof_scored),
        _sample_weight(oof_scored),
    )
    output = final_scored.copy()
    raw = pd.to_numeric(output[raw_column], errors="coerce").to_numpy(float)
    calibrated = raw if calibrator is None else calibrator.predict(raw)
    uncertainty = pd.to_numeric(output[std_column], errors="coerce").fillna(np.inf).to_numpy(float)
    output["calibrated_expected_log"] = calibrated
    output["decision_edge"] = calibrated - uncertainty_multiplier * uncertainty
    output["selected_gamma"] = gamma
    output["selected_uncertainty_multiplier"] = uncertainty_multiplier
    return output


def _route(scored: pd.DataFrame) -> pd.DataFrame:
    eligible = scored[
        np.isfinite(pd.to_numeric(scored["decision_edge"], errors="coerce"))
        & pd.to_numeric(scored["decision_edge"], errors="coerce").gt(0.0)
    ].copy()
    if eligible.empty:
        return eligible
    duration = pd.to_numeric(eligible["pred_duration_median"], errors="coerce").clip(lower=1.0)
    eligible["value_rate"] = pd.to_numeric(eligible["decision_edge"], errors="coerce") / np.sqrt(duration)
    eligible = eligible.sort_values(
        ["entry_time", "value_rate", "decision_edge", "outcome_model_disagreement", "snapshot_time"],
        ascending=[True, False, False, True, True],
    )
    consumed_clusters: set[str] = set()
    active_until = pd.Timestamp.min.tz_localize("UTC")
    selected: list[pd.Series] = []
    for entry_time, group in eligible.groupby("entry_time", sort=True):
        timestamp = pd.Timestamp(entry_time)
        if timestamp < active_until:
            continue
        candidates = group[~group["cluster_id"].astype(str).isin(consumed_clusters)]
        if candidates.empty:
            continue
        chosen = candidates.iloc[0]
        consumed_clusters.add(str(chosen["cluster_id"]))
        active_until = pd.Timestamp(chosen["exit_time"])
        selected.append(chosen)
    if not selected:
        return eligible.iloc[0:0].copy()
    trades = pd.DataFrame(selected).sort_values("entry_time").reset_index(drop=True)
    if trades["cluster_id"].duplicated().any():
        raise RuntimeError("a causal cluster traded more than once")
    previous_exit = trades["exit_time"].shift(1)
    if (trades.loc[previous_exit.notna(), "entry_time"] < previous_exit.dropna()).any():
        raise RuntimeError("global account positions overlap")
    factors = 1.0 + RISK_FRACTION * pd.to_numeric(trades["realized_r"], errors="raise").to_numpy(float)
    if np.any(factors <= 0.0):
        raise RuntimeError("non-positive NAV factor")
    nav = np.cumprod(factors)
    peak = np.maximum.accumulate(np.concatenate(([1.0], nav))) [1:]
    trades["nav_after"] = nav
    trades["drawdown_after"] = nav / peak - 1.0
    return trades


def _calendar_days(frame: pd.DataFrame) -> int:
    if frame.empty:
        return 0
    dated = frame.assign(day=frame["entry_time"].dt.floor("D"))
    return int(dated.groupby("period")["day"].nunique().sum())


def _longest_losing_streak(realized: Iterable[float]) -> int:
    best = current = 0
    for value in realized:
        if float(value) <= 0.0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _metrics(trades: pd.DataFrame, calendar_days: int | None = None) -> dict[str, Any]:
    days = _calendar_days(trades) if calendar_days is None else int(calendar_days)
    if trades.empty:
        return {
            "trades": 0,
            "calendar_days": days,
            "trades_per_calendar_day": 0.0,
            "ending_nav": 1.0,
            "maximum_drawdown": 0.0,
        }
    realized = pd.to_numeric(trades["realized_r"], errors="raise").to_numpy(float)
    factors = 1.0 + RISK_FRACTION * realized
    if np.any(factors <= 0.0):
        raise RuntimeError("non-positive local NAV factor")
    nav = np.cumprod(factors)
    peak = np.maximum.accumulate(np.concatenate(([1.0], nav))) [1:]
    drawdown = nav / peak - 1.0
    positive = realized[realized > 0.0]
    negative = realized[realized < 0.0]
    gross_profit = float(positive.sum())
    gross_loss = float(-negative.sum())
    holding = pd.to_numeric(trades["duration_minutes"], errors="raise").to_numpy(float)
    target_r = pd.to_numeric(trades["target_r"], errors="raise").to_numpy(float)
    return {
        "trades": int(len(trades)),
        "calendar_days": days,
        "trades_per_calendar_day": float(len(trades) / max(days, 1)),
        "positive_trade_rate": float(np.mean(realized > 0.0)),
        "target_first_rate": float(np.mean(trades["outcome"].eq("TARGET_FIRST"))),
        "stop_first_rate": float(np.mean(trades["outcome"].eq("STOP_FIRST"))),
        "timeout_rate": float(np.mean(trades["outcome"].eq("TIMEOUT"))),
        "fast_stop_rate": float(pd.to_numeric(trades["fast_stop"], errors="coerce").fillna(0.0).mean()),
        "mean_realized_r": float(np.mean(realized)),
        "median_realized_r": float(np.median(realized)),
        "mean_planned_target_r": float(np.mean(target_r)),
        "median_planned_target_r": float(np.median(target_r)),
        "profit_factor_r": None if gross_loss <= 0.0 else gross_profit / gross_loss,
        "mean_holding_minutes": float(np.mean(holding)),
        "median_holding_minutes": float(np.median(holding)),
        "p90_holding_minutes": float(np.quantile(holding, 0.90)),
        "ending_nav": float(nav[-1]),
        "total_log_growth": float(np.log(nav[-1])),
        "mean_log_growth_per_trade": float(np.mean(np.log(factors))),
        "maximum_drawdown": float(np.min(drawdown)),
        "longest_nonpositive_streak": int(_longest_losing_streak(realized)),
        "mean_decision_edge": float(pd.to_numeric(trades["decision_edge"], errors="coerce").mean()),
        "mean_model_disagreement": float(pd.to_numeric(trades["outcome_model_disagreement"], errors="coerce").mean()),
        "unique_clusters": int(trades["cluster_id"].nunique()),
    }


def _breakdown(trades: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    if trades.empty:
        return []
    rows: list[dict[str, Any]] = []
    for value, group in trades.groupby(column, dropna=False):
        item = _metrics(group)
        item[column] = str(value)
        rows.append(item)
    return sorted(rows, key=lambda item: (-int(item["trades"]), item[column]))


def _period_daily_log_growth(trades: pd.DataFrame, periods: Iterable[str]) -> pd.Series:
    values: dict[str, float] = {}
    for period in periods:
        group = trades[trades["period"] == period]
        if group.empty:
            values[str(period)] = 0.0
            continue
        log_growth = _realized_log(group)
        days = max(_calendar_days(group), 1)
        values[str(period)] = float(log_growth.sum() / days)
    return pd.Series(values, dtype=float)


def _select_policy(oof_scored: pd.DataFrame) -> tuple[float, float, pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    periods = sorted(oof_scored["period"].unique())
    candidates: list[dict[str, Any]] = []
    best: tuple[float, float, pd.DataFrame, pd.DataFrame] | None = None
    best_utility = -np.inf
    for gamma in GAMMA_GRID:
        for uncertainty_multiplier in UNCERTAINTY_GRID:
            calibrated = _leave_period_calibrate(
                oof_scored,
                gamma=gamma,
                uncertainty_multiplier=uncertainty_multiplier,
            )
            trades = _route(calibrated)
            daily = _period_daily_log_growth(trades, periods)
            q20 = float(daily.quantile(0.20))
            median = float(daily.median())
            spread = float(daily.std(ddof=0))
            utility = q20 + 0.35 * median - 0.10 * spread
            metrics = _metrics(trades, calendar_days=_calendar_days(oof_scored))
            candidate = {
                "gamma": gamma,
                "uncertainty_multiplier": uncertainty_multiplier,
                "environment_daily_log_q20": q20,
                "environment_daily_log_median": median,
                "environment_daily_log_std": spread,
                "selection_utility": utility,
                "continuous_account": metrics,
            }
            candidates.append(candidate)
            if utility > best_utility:
                best_utility = utility
                best = (gamma, uncertainty_multiplier, calibrated, trades)
    if best is None:
        raise RuntimeError("no policy candidate was evaluated")
    return best[0], best[1], best[2], best[3], candidates


def _loss_diagnostics(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    losses = trades[pd.to_numeric(trades["realized_r"], errors="raise") <= 0.0].copy()
    if losses.empty:
        return losses
    return (
        losses.groupby(
            ["period", "family", "source", "symbol", "outcome", "fast_stop"],
            dropna=False,
        )
        .agg(
            trades=("action_id", "count"),
            mean_r=("realized_r", "mean"),
            median_r=("realized_r", "median"),
            mean_planned_r=("target_r", "mean"),
            mean_hold=("duration_minutes", "mean"),
            mean_edge=("decision_edge", "mean"),
            mean_stop_probability=("pred_stop_first_median", "mean"),
        )
        .reset_index()
        .sort_values(["trades", "mean_r"], ascending=[False, True])
    )


def _markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# Mechanism-level causal policy v3",
        "",
        "This file is generated by the untouched-period workflow. It is evidence, not a pass/fail rubric.",
        "",
    ]
    for key, title in (
        ("oof_continuous_account", "Development period-held-out continuous account"),
        ("final_continuous_account", "Untouched final continuous account"),
    ):
        metrics = summary[key]
        lines.extend(
            [
                f"## {title}",
                "",
                f"- Trades: {metrics.get('trades', 0)} over {metrics.get('calendar_days', 0)} calendar days ({metrics.get('trades_per_calendar_day', 0.0):.3f}/day)",
                f"- Positive trade rate: {metrics.get('positive_trade_rate', 0.0):.3%}",
                f"- Target / stop / timeout: {metrics.get('target_first_rate', 0.0):.3%} / {metrics.get('stop_first_rate', 0.0):.3%} / {metrics.get('timeout_rate', 0.0):.3%}",
                f"- Mean / median realized R: {metrics.get('mean_realized_r', 0.0):.4f} / {metrics.get('median_realized_r', 0.0):.4f}",
                f"- Mean / median planned target R: {metrics.get('mean_planned_target_r', 0.0):.4f} / {metrics.get('median_planned_target_r', 0.0):.4f}",
                f"- Mean / median / p90 hold minutes: {metrics.get('mean_holding_minutes', 0.0):.2f} / {metrics.get('median_holding_minutes', 0.0):.2f} / {metrics.get('p90_holding_minutes', 0.0):.2f}",
                f"- Ending NAV / maximum drawdown: {metrics.get('ending_nav', 1.0):.6f} / {metrics.get('maximum_drawdown', 0.0):.3%}",
                f"- Longest non-positive streak: {metrics.get('longest_nonpositive_streak', 0)}",
                "",
            ]
        )
    return "\n".join(lines)


def run(root: Path, output: Path) -> None:
    data = _load(root)
    development = data[data["period"].str.startswith(DEVELOPMENT_PREFIX)].copy()
    final = data[data["period"].str.startswith(FINAL_PREFIX)].copy()
    if development["period"].nunique() < 6:
        raise RuntimeError("at least six separated development environments are required")
    if final.empty:
        raise RuntimeError("the untouched final environment is missing")

    oof_raw, oof_model_counts = _oof_score(development)
    gamma, uncertainty_multiplier, oof_scored, oof_trades, policy_candidates = _select_policy(oof_raw)

    final_models = _fit_ensemble(development, seed=941)
    final_raw = _score(final, final_models)
    final_scored = _final_calibrate(
        final_raw,
        oof_raw,
        gamma=gamma,
        uncertainty_multiplier=uncertainty_multiplier,
    )
    final_trades = _route(final_scored)

    output.mkdir(parents=True, exist_ok=True)
    oof_raw.to_csv(output / "oof_raw_actions.csv", index=False)
    oof_scored.to_csv(output / "oof_scored_actions.csv", index=False)
    oof_trades.to_csv(output / "oof_continuous_trades.csv", index=False)
    final_scored.to_csv(output / "final_scored_actions.csv", index=False)
    final_trades.to_csv(output / "final_continuous_trades.csv", index=False)
    pd.concat(
        [
            _loss_diagnostics(oof_trades).assign(sample="OOF_DEVELOPMENT"),
            _loss_diagnostics(final_trades).assign(sample="UNTOUCHED_FINAL"),
        ],
        ignore_index=True,
        sort=False,
    ).to_csv(output / "loss_diagnostics.csv", index=False)

    oof_days = _calendar_days(development)
    final_days = _calendar_days(final)
    summary: dict[str, Any] = {
        "policy": {
            "name": "MECHANISM_CAUSAL_SELECTIVE_COMPETING_RISK_V3",
            "risk_fraction": RISK_FRACTION,
            "selected_gamma": gamma,
            "selected_uncertainty_multiplier": uncertainty_multiplier,
            "selection_basis": "LOWER_TAIL_AND_MEDIAN_PERIOD_HELD_OUT_DAILY_LOG_GROWTH",
            "account_rule": "ONE_GLOBAL_POSITION_AND_ONE_TRADE_PER_CAUSAL_CLUSTER",
            "identity_policy": "NO_SYMBOL_OR_CALENDAR_FEATURE",
            "feature_columns": list(FEATURE_COLUMNS),
            "final_model_count": len(final_models),
            "final_models": [model.description() for model in final_models],
        },
        "development_actions": int(len(development)),
        "development_episodes": int(development["episode_id"].nunique()),
        "development_clusters": int(development["cluster_id"].nunique()),
        "final_actions": int(len(final)),
        "final_episodes": int(final["episode_id"].nunique()),
        "final_clusters": int(final["cluster_id"].nunique()),
        "oof_model_counts": oof_model_counts,
        "policy_candidates": policy_candidates,
        "oof_continuous_account": _metrics(oof_trades, calendar_days=oof_days),
        "final_continuous_account": _metrics(final_trades, calendar_days=final_days),
        "oof_by_period": _breakdown(oof_trades, "period"),
        "final_by_period": _breakdown(final_trades, "period"),
        "oof_by_family": _breakdown(oof_trades, "family"),
        "final_by_family": _breakdown(final_trades, "family"),
        "oof_by_symbol": _breakdown(oof_trades, "symbol"),
        "final_by_symbol": _breakdown(final_trades, "symbol"),
        "causality": "PERIOD_HELD_OUT_PREDICTIONS__LEAVE_PERIOD_CALIBRATION__FINAL_NEVER_USED_FOR_SELECTION",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output / "RESULT.md").write_text(_markdown_summary(summary), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()
