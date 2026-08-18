#!/usr/bin/env python3
"""Blocked causal plan learning and one-account quality-first routing.

The model never invents orders and never sees symbol, period, actual fills, future path,
or outcome fields.  It estimates fill and target-before-stop probabilities for immutable
causal plans.  A plan must have positive cost-after expected log growth and its calibrated
target probability must exceed its stop probability; large RR cannot justify a plan more
likely to lose.  Simultaneous plans are ranked by target probability first, then expected
log growth per expected account-occupation hour.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence
import json
import math
import re

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score

RISK_FRACTION = 0.03
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
    "TIME_EXIT",
}
ALLOWED_CATEGORICAL = {
    "narrative_branch",
    "setup_kind",
    "location_kind",
    "response_kind",
    "source_pool_kind",
    "target_pool_kind",
    "objective_kind",
    "entry_geometry",
    "stop_geometry",
    "entry_style",
    "source_kind",
}
FORBIDDEN_EXACT = {
    "action_id",
    "episode_id",
    "symbol",
    "period",
    "state_id",
    "fill_state",
    "outcome",
    "fill_index",
    "fill_time_ns",
    "resolution_index",
    "resolution_time_ns",
    "entry_wait_minutes",
    "holding_minutes",
    "order_terminal_time_ns",
    "actual_entry",
    "actual_target_net_r",
    "actual_stop_net_r",
    "actual_gross_rr",
    "net_r",
    "mfe_r",
    "mae_r",
    "filled_label",
    "target_label",
    "terminal_minutes",
    "episode_weight",
    "emission_time_ns",
    "interaction_time_ns",
    "emission_index",
    "source_level_id",
    "objective_id",
    "diagnostic_target_level_id",
    "entry",
    "stop",
    "target",
    "source_price",
    "source_lower",
    "source_upper",
}
FORBIDDEN_PREFIXES = (
    "diagnostic_",
    "actual_",
    "fill_",
    "resolution_",
    "order_terminal_",
)
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _period_from_path(path: Path) -> str:
    for part in reversed(path.parts):
        match = re.search(r"((?:dev|eval)-\d{4}-[a-z]{3})", part.lower())
        if match:
            return match.group(1)
    raise ValueError(f"cannot infer period from {path}")


def _period_key(value: str) -> tuple[int, int]:
    match = re.search(r"(\d{4})-([a-z]{3})$", value.lower())
    if not match:
        raise ValueError(value)
    return int(match.group(1)), MONTHS[match.group(2)]


def read_actions(root: Path) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for path in sorted(root.glob("**/coherent_actions.csv")):
        # Ignore per-symbol files and consume only each period's unified universe.
        if path.name != "coherent_actions.csv":
            continue
        frame = pd.read_csv(path)
        if frame.empty or "action_id" not in frame:
            continue
        frame["period"] = _period_from_path(path)
        rows.append(frame)
    if not rows:
        raise RuntimeError(f"no coherent action universes under {root}")
    output = pd.concat(rows, ignore_index=True, sort=False)
    if output.duplicated(["period", "action_id"]).any():
        raise RuntimeError("duplicate period/action identity")
    output["filled_label"] = output.fill_state.astype(str).str.startswith("FILLED").astype(int)
    output["target_label"] = output.outcome.astype(str).eq("TARGET_FIRST").astype(int)
    output["terminal_minutes"] = (
        pd.to_numeric(output.order_terminal_time_ns, errors="coerce")
        - pd.to_numeric(output.emission_time_ns, errors="coerce")
    ) / 60_000_000_000
    output["terminal_minutes"] = output.terminal_minutes.clip(lower=1.0)
    output["episode_weight"] = 1.0 / output.groupby(
        ["period", "episode_id"]
    ).action_id.transform("size")
    return output.reset_index(drop=True)


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    columns: list[str] = []
    for column in frame.columns:
        if column in FORBIDDEN_EXACT:
            continue
        if column.startswith(FORBIDDEN_PREFIXES):
            continue
        if column.endswith("_id") or column.endswith("_time_ns") or column.endswith("_index"):
            continue
        if frame[column].dtype == object and column not in ALLOWED_CATEGORICAL:
            continue
        columns.append(column)
    categorical = [column for column in columns if frame[column].dtype == object]
    return columns, categorical


def prepare_features(
    frame: pd.DataFrame,
    columns: Sequence[str],
    categorical: Sequence[str],
) -> pd.DataFrame:
    output = frame.loc[:, columns].copy()
    for column in categorical:
        output[column] = output[column].astype("string").fillna("__NA__").astype("category")
    for column in columns:
        if column not in categorical:
            output[column] = pd.to_numeric(output[column], errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )
    return output


def classifier(seed: int) -> LGBMClassifier:
    return LGBMClassifier(
        objective="binary",
        n_estimators=220,
        learning_rate=0.03,
        num_leaves=15,
        max_depth=5,
        min_child_samples=80,
        min_split_gain=0.002,
        subsample=0.82,
        subsample_freq=1,
        colsample_bytree=0.70,
        reg_alpha=1.5,
        reg_lambda=12.0,
        random_state=seed,
        n_jobs=-1,
        verbosity=-1,
    )


def duration_prior(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    hierarchy = (
        ("entry_geometry", "narrative_branch", "stop_geometry", "objective_kind"),
        ("entry_geometry", "narrative_branch", "stop_geometry"),
        ("entry_geometry", "narrative_branch"),
        ("entry_geometry",),
        ("entry_style",),
    )
    output = pd.Series(np.nan, index=test.index, dtype=float)
    for keys in hierarchy:
        median = train.groupby(list(keys), observed=True).terminal_minutes.median()
        if len(keys) == 1:
            values = test[keys[0]].map(median).to_numpy(float)
        else:
            lookup = pd.MultiIndex.from_frame(test.loc[:, list(keys)])
            values = median.reindex(lookup).to_numpy(float)
        missing = output.isna().to_numpy() & np.isfinite(values)
        output.iloc[np.flatnonzero(missing)] = values[missing]
    return output.fillna(float(train.terminal_minutes.median())).clip(lower=1.0).to_numpy()


def platt_calibrate(
    raw: np.ndarray,
    labels: np.ndarray,
    mask: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, dict[str, float]]:
    epsilon = 1e-6
    clipped = np.clip(raw, epsilon, 1.0 - epsilon)
    logits = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
    model = LogisticRegression(C=0.5, solver="lbfgs")
    model.fit(logits[mask], labels[mask], sample_weight=weights[mask])
    probability = model.predict_proba(logits)[:, 1]
    return probability, {
        "coefficient": float(model.coef_[0, 0]),
        "intercept": float(model.intercept_[0]),
    }


def fit_blocked(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    columns, categorical = feature_columns(frame)
    features = prepare_features(frame, columns, categorical)
    development_periods = sorted(
        frame.loc[frame.period.astype(str).str.startswith("dev-"), "period"].unique(),
        key=_period_key,
    )
    evaluation_periods = sorted(
        frame.loc[frame.period.astype(str).str.startswith("eval-"), "period"].unique(),
        key=_period_key,
    )
    if len(development_periods) < 4 or not evaluation_periods:
        raise RuntimeError(
            f"need at least four development periods and evaluation data: "
            f"dev={development_periods}, eval={evaluation_periods}"
        )
    raw_target = np.full(len(frame), np.nan)
    raw_fill = np.full(len(frame), np.nan)
    expected_duration = np.full(len(frame), np.nan)
    period_metrics: dict[str, Any] = {}

    for ordinal, period in enumerate(development_periods):
        test = frame.period.astype(str).eq(period).to_numpy()
        train = frame.period.astype(str).str.startswith("dev-").to_numpy() & ~test
        action_train = train & frame.filled_label.astype(bool).to_numpy()
        target_model = classifier(17_000 + ordinal)
        target_model.fit(
            features.loc[action_train],
            frame.loc[action_train, "target_label"],
            sample_weight=frame.loc[action_train, "episode_weight"],
            categorical_feature=categorical,
        )
        raw_target[test] = target_model.predict_proba(features.loc[test])[:, 1]
        fill_model = classifier(29_000 + ordinal)
        fill_model.fit(
            features.loc[train],
            frame.loc[train, "filled_label"],
            sample_weight=frame.loc[train, "episode_weight"],
            categorical_feature=categorical,
        )
        raw_fill[test] = fill_model.predict_proba(features.loc[test])[:, 1]
        expected_duration[test] = duration_prior(frame.loc[train], frame.loc[test])
        filled_test = test & frame.filled_label.astype(bool).to_numpy()
        period_metrics[period] = {
            "actions": int(test.sum()),
            "filled_actions": int(filled_test.sum()),
            "target_rate": float(frame.loc[filled_test, "target_label"].mean()),
            "target_auc": float(
                roc_auc_score(
                    frame.loc[filled_test, "target_label"], raw_target[filled_test]
                )
            ),
            "fill_auc": float(
                roc_auc_score(frame.loc[test, "filled_label"], raw_fill[test])
            ),
        }

    development = frame.period.astype(str).str.startswith("dev-").to_numpy()
    evaluation = frame.period.astype(str).str.startswith("eval-").to_numpy()
    filled_development = development & frame.filled_label.astype(bool).to_numpy()
    target_model = classifier(91_001)
    target_model.fit(
        features.loc[filled_development],
        frame.loc[filled_development, "target_label"],
        sample_weight=frame.loc[filled_development, "episode_weight"],
        categorical_feature=categorical,
    )
    raw_target[evaluation] = target_model.predict_proba(features.loc[evaluation])[:, 1]
    fill_model = classifier(91_002)
    fill_model.fit(
        features.loc[development],
        frame.loc[development, "filled_label"],
        sample_weight=frame.loc[development, "episode_weight"],
        categorical_feature=categorical,
    )
    raw_fill[evaluation] = fill_model.predict_proba(features.loc[evaluation])[:, 1]
    expected_duration[evaluation] = duration_prior(
        frame.loc[development], frame.loc[evaluation]
    )

    target_probability, target_calibration = platt_calibrate(
        raw_target,
        frame.target_label.to_numpy(int),
        filled_development,
        frame.episode_weight.to_numpy(float),
    )
    fill_probability, fill_calibration = platt_calibrate(
        raw_fill,
        frame.filled_label.to_numpy(int),
        development,
        frame.episode_weight.to_numpy(float),
    )
    output = frame.copy()
    output["target_probability"] = target_probability
    output["fill_probability"] = fill_probability
    output["expected_occupation_minutes"] = np.maximum(expected_duration, 1.0)
    target_r = pd.to_numeric(output.planned_account_target_r, errors="coerce").to_numpy(float)
    stop_r = pd.to_numeric(output.planned_account_stop_r, errors="coerce").fillna(-1.0).to_numpy(float)
    output["planned_break_even_probability"] = -stop_r / np.maximum(target_r - stop_r, 1e-12)
    win_log = np.log1p(RISK_FRACTION * np.clip(target_r, -0.999 / RISK_FRACTION, None))
    loss_log = np.log1p(RISK_FRACTION * np.clip(stop_r, -0.999 / RISK_FRACTION, None))
    output["expected_log_growth"] = fill_probability * (
        target_probability * win_log + (1.0 - target_probability) * loss_log
    )
    output["expected_log_growth_per_hour"] = (
        output.expected_log_growth
        * 60.0
        / output.expected_occupation_minutes.clip(lower=1.0)
    )
    output["target_more_likely_than_stop"] = target_probability > 0.5

    filled_evaluation = evaluation & output.filled_label.astype(bool).to_numpy()
    for period in evaluation_periods:
        mask = output.period.astype(str).eq(period).to_numpy()
        filled_mask = mask & output.filled_label.astype(bool).to_numpy()
        period_metrics[period] = {
            "actions": int(mask.sum()),
            "filled_actions": int(filled_mask.sum()),
            "target_rate": float(output.loc[filled_mask, "target_label"].mean()),
            "target_auc": float(
                roc_auc_score(
                    output.loc[filled_mask, "target_label"],
                    output.loc[filled_mask, "target_probability"],
                )
            ),
            "target_brier": float(
                brier_score_loss(
                    output.loc[filled_mask, "target_label"],
                    output.loc[filled_mask, "target_probability"],
                    sample_weight=output.loc[filled_mask, "episode_weight"],
                )
            ),
            "fill_auc": float(
                roc_auc_score(
                    output.loc[mask, "filled_label"],
                    output.loc[mask, "fill_probability"],
                )
            ),
            "fill_brier": float(
                brier_score_loss(
                    output.loc[mask, "filled_label"],
                    output.loc[mask, "fill_probability"],
                    sample_weight=output.loc[mask, "episode_weight"],
                )
            ),
        }
    diagnostics = {
        "feature_count": len(columns),
        "categorical_features": categorical,
        "development_periods": development_periods,
        "evaluation_periods": evaluation_periods,
        "target_calibration": target_calibration,
        "fill_calibration": fill_calibration,
        "period_metrics": period_metrics,
        "symbol_in_model": False,
        "period_in_model": False,
        "actual_fill_or_outcome_in_model": False,
    }
    return output, diagnostics


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def route_account(
    scored: pd.DataFrame,
    prefix: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    candidates = scored[
        scored.period.astype(str).str.startswith(prefix)
        & scored.expected_log_growth.gt(0.0)
        & scored.target_more_likely_than_stop
        & scored.order_terminal_time_ns.notna()
    ].copy()
    candidates = candidates.sort_values(
        [
            "emission_time_ns",
            "target_probability",
            "expected_log_growth_per_hour",
            "expected_log_growth",
            "action_id",
        ],
        ascending=[True, False, False, False, True],
    )
    selected: list[pd.Series] = []
    occupied_episodes: set[tuple[str, str]] = set()
    busy_until = -1
    for emission_time, group in candidates.groupby("emission_time_ns", sort=True):
        emission_time = int(emission_time)
        if emission_time <= busy_until:
            continue
        available = group[
            [
                (str(row.period), str(row.episode_id)) not in occupied_episodes
                for row in group.itertuples()
            ]
        ]
        if available.empty:
            continue
        chosen = available.iloc[0]
        selected.append(chosen)
        occupied_episodes.add((str(chosen.period), str(chosen.episode_id)))
        busy_until = int(chosen.order_terminal_time_ns)
    orders = (
        pd.DataFrame(selected).reset_index(drop=True)
        if selected
        else candidates.iloc[0:0].copy()
    )
    trades = orders[
        orders.fill_state.astype(str).str.startswith("FILLED")
        & orders.outcome.astype(str).isin(RESOLVED_OUTCOMES)
    ].copy().reset_index(drop=True)
    nav = 100_000.0
    peak = nav
    maximum_drawdown = 0.0
    before: list[float] = []
    after: list[float] = []
    for _, trade in trades.iterrows():
        before.append(nav)
        nav *= max(1e-9, 1.0 + RISK_FRACTION * _safe_float(trade.net_r))
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        after.append(nav)
    trades["nav_before"] = before
    trades["nav_after"] = after
    calendar_days = 0
    for period in orders.period.astype(str).unique():
        calendar_days += 7
    summary: dict[str, Any] = {
        "selected_orders": int(len(orders)),
        "filled_trades": int(len(trades)),
        "wins": int(trades.outcome.astype(str).eq("TARGET_FIRST").sum()),
        "win_rate": float(trades.outcome.astype(str).eq("TARGET_FIRST").mean())
        if len(trades)
        else None,
        "mean_net_r": float(pd.to_numeric(trades.net_r, errors="coerce").mean())
        if len(trades)
        else None,
        "median_net_r": float(pd.to_numeric(trades.net_r, errors="coerce").median())
        if len(trades)
        else None,
        "mean_planned_target_r": float(
            pd.to_numeric(trades.planned_account_target_r, errors="coerce").mean()
        )
        if len(trades)
        else None,
        "mean_holding_minutes": float(
            pd.to_numeric(trades.holding_minutes, errors="coerce").mean()
        )
        if len(trades)
        else None,
        "ending_nav": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "unfilled_selected_orders": int(
            (~orders.fill_state.astype(str).str.startswith("FILLED")).sum()
        ),
        "calendar_days": int(calendar_days),
        "filled_trades_per_day": len(trades) / calendar_days if calendar_days else 0.0,
        "by_period": {},
    }
    for period, group in orders.groupby("period"):
        period_trades = group[group.fill_state.astype(str).str.startswith("FILLED")]
        summary["by_period"][str(period)] = {
            "orders": int(len(group)),
            "trades": int(len(period_trades)),
            "win_rate": float(
                period_trades.outcome.astype(str).eq("TARGET_FIRST").mean()
            )
            if len(period_trades)
            else None,
            "mean_net_r": float(
                pd.to_numeric(period_trades.net_r, errors="coerce").mean()
            )
            if len(period_trades)
            else None,
        }
    return orders, trades, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    actions = read_actions(args.root)
    scored, model_diagnostics = fit_blocked(actions)
    development_orders, development_trades, development_summary = route_account(
        scored, "dev-"
    )
    evaluation_orders, evaluation_trades, evaluation_summary = route_account(
        scored, "eval-"
    )
    scored.to_csv(args.output / "scored_action_universe.csv", index=False)
    development_orders.to_csv(
        args.output / "development_oof_selected_orders.csv", index=False
    )
    development_trades.to_csv(
        args.output / "development_oof_account_trades.csv", index=False
    )
    evaluation_orders.to_csv(
        args.output / "evaluation_selected_orders.csv", index=False
    )
    evaluation_trades.to_csv(
        args.output / "evaluation_account_trades.csv", index=False
    )
    summary = {
        "policy": (
            "immutable episode-conditioned plan -> calibrated fill and target-first "
            "probability -> target more likely than stop and positive expected log "
            "growth -> probability-first one-global-account arbitration"
        ),
        "risk_fraction": RISK_FRACTION,
        "action_universe": int(len(scored)),
        "model": model_diagnostics,
        "development_oof_account": development_summary,
        "evaluation_account": evaluation_summary,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
