#!/usr/bin/env python3
"""Train a portable period-robust router for integrated auction plans.

Learning chooses among complete causal plans.  It does not create direction,
liquidity, entry, stop or target geometry, and it never receives symbol identity
or a configured target win rate.  Market periods and causal-event groups are
balanced so one volatile week or one repeatedly represented interaction cannot
own the model.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from features_integrated import (
    FEATURE_CLIP_RANGES,
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
)
from integrated_ensemble import ENSEMBLE_SCHEMA, IntegratedPeriodEnsemble


L2_CANDIDATES = (0.03, 0.10, 0.30, 1.0, 3.0, 10.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--members", type=int, default=5)
    parser.add_argument("--risk-fraction", type=float, default=0.03)
    parser.add_argument("--probability-quantile", type=float, default=0.25)
    return parser.parse_args()


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _log_loss(y: np.ndarray, p: np.ndarray, w: np.ndarray) -> float:
    probability = np.clip(p, 1e-9, 1.0 - 1e-9)
    loss = -(y * np.log(probability) + (1.0 - y) * np.log(1.0 - probability))
    return float(np.sum(w * loss) / max(np.sum(w), 1e-12))


def _brier(y: np.ndarray, p: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * np.square(p - y)) / max(np.sum(w), 1e-12))


def _load(paths: Sequence[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        if "development_period" not in frame.columns:
            frame["development_period"] = path.parent.name
        frame["dataset_path"] = str(path)
        frames.append(frame)
    if not frames:
        raise RuntimeError("no integrated datasets were supplied")
    data = pd.concat(frames, ignore_index=True, sort=False)
    if "label" not in data.columns:
        raise RuntimeError("integrated datasets do not contain target-first labels")
    data = data[pd.to_numeric(data["label"], errors="coerce").isin([0, 1])].copy()
    data["label"] = pd.to_numeric(data["label"], errors="raise").astype(int)
    if len(data) < 100:
        raise RuntimeError(f"only {len(data)} resolved plans are available")
    missing = [f"mlf_{name}" for name in FEATURE_NAMES if f"mlf_{name}" not in data]
    if missing:
        raise RuntimeError(f"integrated datasets are missing features {missing[:12]}")
    if data["development_period"].nunique() < 4:
        raise RuntimeError("at least four separated market periods are required")
    return data.sort_values(
        ["event_time_ns", "event_group_id", "plan_id"], kind="mergesort"
    ).reset_index(drop=True)


def _matrix(data: pd.DataFrame) -> np.ndarray:
    columns: list[np.ndarray] = []
    for name in FEATURE_NAMES:
        values = pd.to_numeric(data[f"mlf_{name}"], errors="coerce").fillna(
            FEATURE_DEFAULTS[name]
        )
        lower, upper = FEATURE_CLIP_RANGES[name]
        columns.append(values.clip(lower, upper).to_numpy(dtype=float))
    return np.column_stack(columns)


def _weights(data: pd.DataFrame) -> np.ndarray:
    if "event_group_id" in data.columns:
        group_size = data.groupby(
            ["development_period", "event_group_id"], sort=False
        )["plan_id"].transform("count")
        raw = 1.0 / np.maximum(group_size.to_numpy(dtype=float), 1.0)
    else:
        raw = np.ones(len(data), dtype=float)
    periods = data["development_period"].astype(str).to_numpy()
    result = np.zeros(len(data), dtype=float)
    unique = sorted(set(periods))
    for period in unique:
        mask = periods == period
        total = float(raw[mask].sum())
        if total > 0.0:
            result[mask] = raw[mask] / total / len(unique)
    result *= len(data) / max(result.sum(), 1e-12)
    return result


def _scaling(x: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    del w  # deterministic robust scaling deliberately ignores duplicated row mass
    centers = np.nanmedian(x, axis=0)
    q25 = np.nanquantile(x, 0.25, axis=0)
    q75 = np.nanquantile(x, 0.75, axis=0)
    scales = (q75 - q25) / 1.349
    standard = np.nanstd(x, axis=0)
    scales = np.where(scales > 1e-8, scales, standard)
    scales = np.where(scales > 1e-8, scales, 1.0)
    centers = np.where(np.isfinite(centers), centers, 0.0)
    scales = np.where(np.isfinite(scales), scales, 1.0)
    return centers.astype(float), scales.astype(float)


def _fit_logistic(
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    l2: float,
    centers: np.ndarray | None = None,
    scales: np.ndarray | None = None,
) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    if centers is None or scales is None:
        centers, scales = _scaling(x, w)
    z = (x - centers) / scales
    n_features = z.shape[1]
    initial = np.zeros(n_features + 1, dtype=float)
    prevalence = float(np.sum(w * y) / max(np.sum(w), 1e-12))
    prevalence = min(1.0 - 1e-6, max(1e-6, prevalence))
    initial[0] = math.log(prevalence / (1.0 - prevalence))

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        intercept = theta[0]
        coefficients = theta[1:]
        logits = intercept + z @ coefficients
        probabilities = _sigmoid(logits)
        loss = _log_loss(y, probabilities, w)
        penalty = 0.5 * float(l2) * float(np.mean(np.square(coefficients)))
        residual = probabilities - y
        normalization = max(float(w.sum()), 1e-12)
        grad_intercept = float(np.sum(w * residual) / normalization)
        grad_coefficients = z.T @ (w * residual) / normalization
        grad_coefficients += float(l2) * coefficients / max(n_features, 1)
        gradient = np.concatenate(([grad_intercept], grad_coefficients))
        return loss + penalty, gradient

    try:
        from scipy.optimize import minimize

        result = minimize(
            fun=lambda theta: objective(theta)[0],
            x0=initial,
            jac=lambda theta: objective(theta)[1],
            method="L-BFGS-B",
            options={"maxiter": 500, "ftol": 1e-10, "gtol": 1e-7},
        )
        if not result.success or not np.all(np.isfinite(result.x)):
            raise RuntimeError(str(result.message))
        theta = np.asarray(result.x, dtype=float)
    except Exception:
        theta = initial.copy()
        first = np.zeros_like(theta)
        second = np.zeros_like(theta)
        for iteration in range(1, 2501):
            _, gradient = objective(theta)
            first = 0.9 * first + 0.1 * gradient
            second = 0.999 * second + 0.001 * np.square(gradient)
            corrected_first = first / (1.0 - 0.9**iteration)
            corrected_second = second / (1.0 - 0.999**iteration)
            theta -= 0.03 * corrected_first / (np.sqrt(corrected_second) + 1e-8)
    return theta[1:], float(theta[0]), centers, scales


def _calibrate_intercept(
    logits: np.ndarray, y: np.ndarray, w: np.ndarray
) -> float:
    target = float(np.sum(w * y) / max(np.sum(w), 1e-12))
    target = min(1.0 - 1e-7, max(1e-7, target))
    lower, upper = -20.0, 20.0
    for _ in range(100):
        middle = (lower + upper) / 2.0
        estimate = float(np.sum(w * _sigmoid(logits + middle)) / max(w.sum(), 1e-12))
        if estimate < target:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def _predict(
    x: np.ndarray,
    coefficients: np.ndarray,
    intercept: float,
    centers: np.ndarray,
    scales: np.ndarray,
) -> np.ndarray:
    return _sigmoid(intercept + ((x - centers) / scales) @ coefficients)


def _choose_l2(data: pd.DataFrame, x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, list[dict[str, Any]]]:
    periods = data["development_period"].astype(str).to_numpy()
    records: list[dict[str, Any]] = []
    best: tuple[float, float] | None = None
    for l2 in L2_CANDIDATES:
        fold_losses: list[float] = []
        fold_briers: list[float] = []
        for period in sorted(set(periods)):
            test = periods == period
            train = ~test
            if y[train].min() == y[train].max():
                continue
            coefficients, intercept, centers, scales = _fit_logistic(
                x[train], y[train], w[train], l2=l2
            )
            probability = _predict(x[test], coefficients, intercept, centers, scales)
            fold_losses.append(_log_loss(y[test], probability, w[test]))
            fold_briers.append(_brier(y[test], probability, w[test]))
        if not fold_losses:
            continue
        record = {
            "l2": float(l2),
            "mean_period_log_loss": float(np.mean(fold_losses)),
            "worst_period_log_loss": float(np.max(fold_losses)),
            "mean_period_brier": float(np.mean(fold_briers)),
            "folds": len(fold_losses),
        }
        records.append(record)
        objective = record["mean_period_log_loss"] + 0.10 * record["worst_period_log_loss"]
        candidate = (float(objective), float(l2))
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise RuntimeError("period-separated regularization search produced no model")
    return best[1], records


def _even_periods(periods: Sequence[str], count: int) -> list[str]:
    ordered = list(sorted(periods))
    wanted = min(len(ordered), max(3, int(count)))
    indices = np.linspace(0, len(ordered) - 1, wanted).round().astype(int)
    selected: list[str] = []
    for index in indices:
        value = ordered[int(index)]
        if value not in selected:
            selected.append(value)
    for value in ordered:
        if len(selected) >= wanted:
            break
        if value not in selected:
            selected.append(value)
    return selected


def _duration_priors(data: pd.DataFrame) -> dict[str, Any]:
    duration = pd.to_numeric(
        data.get("counterfactual_minutes_to_resolution"), errors="coerce"
    )
    valid = data[duration.notna() & (duration > 0.0)].copy()
    valid["_duration"] = duration[duration.notna() & (duration > 0.0)].astype(float)
    if valid.empty:
        return {"exact": {}, "state": {}, "family": {}, "global": 60.0}

    def medians(keys: Sequence[str], minimum: int) -> dict[str, float]:
        if any(key not in valid.columns for key in keys):
            return {}
        result: dict[str, float] = {}
        for values, group in valid.groupby(list(keys), dropna=False, sort=True):
            if len(group) < minimum:
                continue
            if not isinstance(values, tuple):
                values = (values,)
            result["|".join(str(value) for value in values)] = float(
                group["_duration"].median()
            )
        return result

    return {
        "exact": medians(("family", "scenario_path", "scale_name"), 5),
        "state": medians(("scenario_path", "scale_name"), 8),
        "family": medians(("family",), 10),
        "global": float(valid["_duration"].median()),
    }


def _duration_for_rows(data: pd.DataFrame, priors: Mapping[str, Any]) -> np.ndarray:
    exact = dict(priors.get("exact", {}))
    state = dict(priors.get("state", {}))
    family = dict(priors.get("family", {}))
    global_value = float(priors.get("global", 60.0))
    output = []
    for row in data.itertuples(index=False):
        family_value = str(getattr(row, "family", ""))
        scenario = str(getattr(row, "scenario_path", ""))
        scale = str(getattr(row, "scale_name", ""))
        output.append(
            float(
                exact.get(
                    f"{family_value}|{scenario}|{scale}",
                    state.get(
                        f"{scenario}|{scale}", family.get(family_value, global_value)
                    ),
                )
            )
        )
    return np.maximum(np.asarray(output, dtype=float), 1.0)


def _member_document(
    *,
    member_id: str,
    calibration_period: str,
    coefficients: np.ndarray,
    intercept: float,
    centers: np.ndarray,
    scales: np.ndarray,
    training_periods: Sequence[str],
) -> dict[str, Any]:
    return {
        "member_id": member_id,
        "calibration_window": calibration_period,
        "training_windows": list(training_periods),
        "feature_names": list(FEATURE_NAMES),
        "centers": [float(value) for value in centers],
        "scales": [float(value) for value in scales],
        "coefficients": [float(value) for value in coefficients],
        "intercept": float(intercept),
    }


def _train_members(
    data: pd.DataFrame,
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    l2: float,
    count: int,
) -> list[dict[str, Any]]:
    periods = data["development_period"].astype(str).to_numpy()
    members: list[dict[str, Any]] = []
    for index, calibration in enumerate(_even_periods(sorted(set(periods)), count), start=1):
        train = periods != calibration
        calibrate = periods == calibration
        coefficients, intercept, centers, scales = _fit_logistic(
            x[train], y[train], w[train], l2=l2
        )
        raw_logits = intercept + ((x[calibrate] - centers) / scales) @ coefficients
        intercept += _calibrate_intercept(raw_logits, y[calibrate], w[calibrate])
        members.append(
            _member_document(
                member_id=f"integrated-period-{index:02d}",
                calibration_period=calibration,
                coefficients=coefficients,
                intercept=intercept,
                centers=centers,
                scales=scales,
                training_periods=sorted(set(periods[train])),
            )
        )
    return members


def _member_probability(member: Mapping[str, Any], x: np.ndarray) -> np.ndarray:
    centers = np.asarray(member["centers"], dtype=float)
    scales = np.asarray(member["scales"], dtype=float)
    coefficients = np.asarray(member["coefficients"], dtype=float)
    return _sigmoid(float(member["intercept"]) + ((x - centers) / scales) @ coefficients)


def _row_economics(data: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    win = pd.to_numeric(data.get("counterfactual_target_net_r"), errors="coerce").to_numpy(dtype=float)
    loss = pd.to_numeric(data.get("counterfactual_stop_net_r"), errors="coerce").to_numpy(dtype=float)
    fallback_win = pd.to_numeric(data.get("ml_win_net_r"), errors="coerce").to_numpy(dtype=float)
    fallback_loss = pd.to_numeric(data.get("ml_loss_net_r"), errors="coerce").to_numpy(dtype=float)
    win = np.where(np.isfinite(win) & (win > 0.0), win, fallback_win)
    loss = np.where(np.isfinite(loss) & (loss < 0.0), loss, fallback_loss)
    if not np.all(np.isfinite(win)) or not np.all(np.isfinite(loss)):
        raise RuntimeError("dataset contains nonfinite trade economics")
    if np.any(win <= 0.0) or np.any(loss >= 0.0):
        raise RuntimeError("dataset trade economics do not contain positive target and negative stop")
    return win, loss


def _score_rows(
    data: pd.DataFrame,
    probabilities: np.ndarray,
    duration: np.ndarray,
    *,
    risk_fraction: float,
) -> pd.DataFrame:
    win, loss = _row_economics(data)
    target_account_r = win / np.abs(loss)
    expected_account_r = probabilities * target_account_r - (1.0 - probabilities)
    growth = probabilities * np.log1p(risk_fraction * target_account_r) + (
        1.0 - probabilities
    ) * math.log(1.0 - risk_fraction)
    output = data.copy()
    output["crossfit_probability"] = probabilities
    output["target_account_r"] = target_account_r
    output["expected_account_r"] = expected_account_r
    output["expected_log_growth"] = growth
    output["expected_duration_minutes"] = duration
    output["expected_log_growth_per_hour"] = growth * 60.0 / np.maximum(duration, 1.0)
    output["accepted"] = (growth > 0.0) & (expected_account_r > 0.0)
    return output


def _simulate(scored: pd.DataFrame, *, risk_fraction: float) -> dict[str, Any]:
    accepted = scored[scored["accepted"]].copy()
    if accepted.empty:
        return {
            "trades": 0,
            "wins": 0,
            "win_rate": None,
            "ending_nav": 1.0,
            "max_drawdown": 0.0,
            "mean_realized_account_r": None,
            "median_hold_minutes": None,
            "trades_per_calendar_day": 0.0,
        }
    accepted["_event"] = pd.to_numeric(accepted["event_time_ns"], errors="raise").astype("int64")
    accepted["_end"] = pd.to_numeric(accepted["label_end_ns"], errors="coerce")
    fallback_end = accepted["_event"] + (
        accepted["expected_duration_minutes"] * 60_000_000_000.0
    ).astype("int64")
    accepted["_end"] = accepted["_end"].fillna(fallback_end).astype("int64")
    selected: list[pd.Series] = []
    active_until = -1
    for event_time, bucket in accepted.groupby("_event", sort=True):
        if int(event_time) < active_until:
            continue
        ranked = bucket.sort_values(
            [
                "expected_log_growth_per_hour",
                "expected_log_growth",
                "crossfit_probability",
                "target_account_r",
                "plan_id",
            ],
            ascending=[False, False, False, False, True],
            kind="mergesort",
        )
        row = ranked.iloc[0]
        selected.append(row)
        active_until = int(row["_end"])
    if not selected:
        return {
            "trades": 0,
            "wins": 0,
            "win_rate": None,
            "ending_nav": 1.0,
            "max_drawdown": 0.0,
            "mean_realized_account_r": None,
            "median_hold_minutes": None,
            "trades_per_calendar_day": 0.0,
        }
    trades = pd.DataFrame(selected).sort_values("_event", kind="mergesort")
    wins = trades["label"].astype(int).to_numpy() == 1
    realized = np.where(wins, trades["target_account_r"].to_numpy(dtype=float), -1.0)
    multipliers = np.where(wins, 1.0 + risk_fraction * realized, 1.0 - risk_fraction)
    nav = np.cumprod(multipliers)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], nav)))
    curve = np.concatenate(([1.0], nav))
    drawdown = curve / peaks - 1.0
    start = pd.to_datetime(int(trades["_event"].min()), unit="ns", utc=True)
    end = pd.to_datetime(int(trades["_event"].max()), unit="ns", utc=True)
    calendar_days = max(1, int((end.normalize() - start.normalize()).days) + 1)
    hold = (trades["_end"] - trades["_event"]) / 60_000_000_000.0
    return {
        "trades": int(len(trades)),
        "wins": int(wins.sum()),
        "win_rate": float(wins.mean()),
        "ending_nav": float(nav[-1]),
        "max_drawdown": float(drawdown.min()),
        "mean_realized_account_r": float(realized.mean()),
        "median_hold_minutes": float(np.median(hold)),
        "trades_per_calendar_day": float(len(trades) / calendar_days),
        "first_event": str(start),
        "last_event": str(end),
    }


def _crossfit(
    data: pd.DataFrame,
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    *,
    l2: float,
    risk_fraction: float,
    probability_quantile: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    periods = data["development_period"].astype(str).to_numpy()
    prediction = np.full(len(data), np.nan, dtype=float)
    details: dict[str, Any] = {}
    for evaluation in sorted(set(periods)):
        test = periods == evaluation
        train_data = data[~test].copy()
        train_x = x[~test]
        train_y = y[~test]
        train_w = w[~test]
        train_periods = train_data["development_period"].astype(str).to_numpy()
        calibration_periods = _even_periods(sorted(set(train_periods)), min(5, len(set(train_periods))))
        member_probabilities: list[np.ndarray] = []
        for calibration in calibration_periods:
            fit_mask = train_periods != calibration
            calibration_mask = train_periods == calibration
            coefficients, intercept, centers, scales = _fit_logistic(
                train_x[fit_mask], train_y[fit_mask], train_w[fit_mask], l2=l2
            )
            calibration_logits = intercept + (
                (train_x[calibration_mask] - centers) / scales
            ) @ coefficients
            intercept += _calibrate_intercept(
                calibration_logits,
                train_y[calibration_mask],
                train_w[calibration_mask],
            )
            member_probabilities.append(
                _predict(x[test], coefficients, intercept, centers, scales)
            )
        matrix = np.column_stack(member_probabilities)
        robust = np.quantile(matrix, probability_quantile, axis=1)
        prediction[test] = robust
        priors = _duration_priors(train_data)
        duration = _duration_for_rows(data[test], priors)
        scored = _score_rows(
            data[test], robust, duration, risk_fraction=risk_fraction
        )
        details[evaluation] = {
            "resolved_plans": int(test.sum()),
            "event_groups": int(scored["event_group_id"].nunique()),
            "raw_target_first_rate": float(scored["label"].mean()),
            "probability_log_loss": _log_loss(
                scored["label"].to_numpy(dtype=float),
                robust,
                _weights(scored),
            ),
            "probability_brier": _brier(
                scored["label"].to_numpy(dtype=float),
                robust,
                _weights(scored),
            ),
            "account": _simulate(scored, risk_fraction=risk_fraction),
        }
    if np.isnan(prediction).any():
        raise RuntimeError("cross-fitted probability is missing for some plans")
    all_priors = _duration_priors(data)
    all_duration = _duration_for_rows(data, all_priors)
    scored_all = _score_rows(data, prediction, all_duration, risk_fraction=risk_fraction)
    combined = {
        "resolved_plans": int(len(scored_all)),
        "event_groups": int(scored_all["event_group_id"].nunique()),
        "raw_target_first_rate": float(scored_all["label"].mean()),
        "probability_log_loss": _log_loss(y, prediction, w),
        "probability_brier": _brier(y, prediction, w),
        "account": _simulate(scored_all, risk_fraction=risk_fraction),
    }
    details["combined_crossfit"] = combined
    return scored_all, details


def train(
    *,
    datasets: Sequence[Path],
    output: Path,
    summary_path: Path,
    member_count: int,
    risk_fraction: float,
    probability_quantile: float,
) -> dict[str, Any]:
    if not 0.0 < risk_fraction < 1.0:
        raise ValueError("risk fraction must be within (0, 1)")
    if not 0.0 <= probability_quantile <= 0.5:
        raise ValueError("probability quantile must be within [0, 0.5]")
    data = _load(datasets)
    x = _matrix(data)
    y = data["label"].to_numpy(dtype=float)
    w = _weights(data)
    l2, search = _choose_l2(data, x, y, w)
    members = _train_members(
        data, x, y, w, l2=l2, count=member_count
    )
    duration_priors = _duration_priors(data)
    document = IntegratedPeriodEnsemble.finalize_document(
        {
            "schema": ENSEMBLE_SCHEMA,
            "status": "trained",
            "feature_names": list(FEATURE_NAMES),
            "feature_defaults": {
                name: float(FEATURE_DEFAULTS[name]) for name in FEATURE_NAMES
            },
            "feature_clip_ranges": {
                name: [
                    float(FEATURE_CLIP_RANGES[name][0]),
                    float(FEATURE_CLIP_RANGES[name][1]),
                ]
                for name in FEATURE_NAMES
            },
            "aggregation": {
                "probability_quantile": float(probability_quantile),
                "duration_floor_minutes": 1.0,
            },
            "members": members,
            "duration_priors": duration_priors,
            "training": {
                "datasets": [str(path) for path in datasets],
                "rows": int(len(data)),
                "event_groups": int(data["event_group_id"].nunique()),
                "periods": sorted(data["development_period"].astype(str).unique()),
                "symbols_observed_but_not_features": sorted(
                    data["symbol"].astype(str).unique()
                ),
                "selected_l2": float(l2),
                "sample_weighting": "equal period mass then inverse candidate count within causal event",
                "label": "target-first versus conservative stop-or-ambiguous first passage after costs",
            },
        }
    )
    model = IntegratedPeriodEnsemble(document)
    model.assert_selectable()
    output.parent.mkdir(parents=True, exist_ok=True)
    model.save(output)

    crossfit_scored, crossfit = _crossfit(
        data,
        x,
        y,
        w,
        l2=l2,
        risk_fraction=risk_fraction,
        probability_quantile=probability_quantile,
    )
    crossfit_path = summary_path.with_name("integrated_crossfit_plans.csv")
    crossfit_scored.to_csv(crossfit_path, index=False)
    summary = {
        "ensemble_id": model.ensemble_id,
        "model_path": str(output),
        "feature_count": len(FEATURE_NAMES),
        "member_count": len(members),
        "periods": sorted(data["development_period"].astype(str).unique()),
        "rows": int(len(data)),
        "event_groups": int(data["event_group_id"].nunique()),
        "target_first_rate": float(y.mean()),
        "selected_l2": float(l2),
        "regularization_search": search,
        "crossfit": crossfit,
        "crossfit_plans": str(crossfit_path),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    args = parse_args()
    summary_path = args.summary or args.output.with_name(
        args.output.stem + "_training_summary.json"
    )
    result = train(
        datasets=args.datasets,
        output=args.output,
        summary_path=summary_path,
        member_count=args.members,
        risk_fraction=args.risk_fraction,
        probability_quantile=args.probability_quantile,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
