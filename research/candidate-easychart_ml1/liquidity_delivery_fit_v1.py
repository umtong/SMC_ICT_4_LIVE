#!/usr/bin/env python3
"""Grouped-OOF direction and execution policy for liquidity-delivery actions.

Direction is learned first from the competition between still-live upper and
lower external liquidity.  Execution quality is learned separately from the
chosen event/entry geometry.  Their probabilities are combined through the
actual cost-adjusted log-growth equation, then one alternative per causal event
and one global account slot are enforced.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor, HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RISK_FRACTION = 0.03
RANDOM_STATE = 20260818


IDENTITY_COLUMNS = {
    "period", "plan_id", "episode_id", "symbol", "state", "side",
    "action_family", "action_id", "source_pool_id", "source_pool_side",
    "source_alias_ids", "target_liquidity_id", "target_kind",
    "zone_formed_ts", "adverse_gap_id", "aligned_gap_id", "mss_ts",
    "interaction_ts", "confirm_ts", "decision_ts", "entry_ts",
    "destination_upper_id", "destination_lower_id", "destination_upper_kind",
    "destination_lower_kind", "destination_resolution_ts", "fixed_r_definition",
    "evaluation_start", "evaluation_end",
    "delivery_state_version", "entry_role",
}
LEAKAGE_TOKENS = (
    "outcome", "target_first", "stopped", "timed_out", "realized_r",
    "net_return", "gross_return", "fee_return", "funding_return",
    "resolution_ts", "minutes_to_resolution", "exit_ts", "exit_price",
    "duration_minutes", "mfe_r_", "mae_r_", "destination_up_label",
    "destination_side", "destination_aligned_label", "destination_minutes",
)
RAW_PRICE_COLUMNS = {
    "entry", "stop", "structural_target", "zone_lower", "zone_upper",
    "mss_level", "destination_upper", "destination_lower",
}


def _load_actions(root: Path) -> pd.DataFrame:
    paths = sorted(root.rglob("actions.csv"))
    if not paths:
        raise FileNotFoundError(f"no actions.csv below {root}")
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        if "period" not in frame.columns:
            frame["period"] = path.parent.name
        frame["source_path"] = str(path)
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    actions = pd.concat(frames, ignore_index=True, sort=False)
    actions = actions.drop_duplicates("plan_id", keep="last")
    return actions.sort_values(["entry_ts", "episode_id", "plan_id"]).reset_index(drop=True)


def _numeric_feature_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        if column in IDENTITY_COLUMNS or column in RAW_PRICE_COLUMNS or column == "source_path":
            continue
        lower = column.lower()
        if any(token in lower for token in LEAKAGE_TOKENS):
            continue
        # Counterfactual fixed-R outcome blocks are future labels.  The known
        # structural planned geometry is retained.
        if lower.startswith("r_"):
            continue
        if lower.startswith("structural_") and not lower.endswith(
            ("planned_target_r", "planned_loss_return", "planned_target_return")
        ) and lower not in {"structural_gross_rr"}:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().sum() < max(20, int(0.02 * len(frame))):
            continue
        if values.nunique(dropna=True) <= 1:
            continue
        columns.append(column)
    return sorted(columns)


def _direction_feature_columns(columns: Iterable[str]) -> list[str]:
    prefixes = (
        "state_", "source_", "interaction_", "confirm_", "event_",
        "approach_", "reclaim_", "displacement_", "reclaim_minus_approach_",
        "displacement_minus_approach_", "live_", "nearest_", "ahead_",
        "behind_", "liquidity_", "comparable_", "source_more_",
    )
    excluded = (
        "risk_", "reward_", "target_distance_", "zone_", "mitigation_",
        "structural_", "bpr_", "ifvg_", "entry_",
    )
    return [
        column
        for column in columns
        if column.startswith(prefixes) and not column.startswith(excluded)
    ]


def _matrix(frame: pd.DataFrame, columns: list[str]) -> np.ndarray:
    if not columns:
        return np.zeros((len(frame), 1), dtype=float)
    return frame[columns].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def _constant_probability(y: np.ndarray, weights: np.ndarray) -> float:
    finite = np.isfinite(y)
    if not finite.any():
        return 0.5
    w = weights[finite]
    return float(np.average(y[finite], weights=w if w.sum() > 0 else None))


def _classification_oof(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    prediction = np.full(len(y), np.nan, dtype=float)
    disagreement = np.full(len(y), np.nan, dtype=float)
    fold_info: dict[str, Any] = {}
    unique_groups = sorted(pd.unique(groups).tolist())
    for holdout in unique_groups:
        test = groups == holdout
        train = (~test) & np.isfinite(y)
        if train.sum() < 40 or len(np.unique(y[train])) < 2:
            base = _constant_probability(y[train], weights[train]) if train.any() else 0.5
            prediction[test] = base
            disagreement[test] = 0.5
            fold_info[str(holdout)] = {"train": int(train.sum()), "fallback": True, "base": base}
            continue
        train_x, train_y, train_w = x[train], y[train].astype(int), weights[train]
        test_x = x[test]
        models = [
            make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True),
                StandardScaler(),
                LogisticRegression(
                    C=0.20,
                    max_iter=1200,
                    class_weight="balanced",
                    random_state=RANDOM_STATE,
                ),
            ),
            make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True),
                ExtraTreesClassifier(
                    n_estimators=320,
                    min_samples_leaf=max(4, int(math.sqrt(train.sum()) / 5)),
                    max_features=0.65,
                    class_weight="balanced_subsample",
                    n_jobs=-1,
                    random_state=RANDOM_STATE + 1,
                ),
            ),
            make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True),
                HistGradientBoostingClassifier(
                    max_iter=180,
                    learning_rate=0.045,
                    max_leaf_nodes=15,
                    min_samples_leaf=max(12, int(math.sqrt(train.sum()))),
                    l2_regularization=2.0,
                    random_state=RANDOM_STATE + 2,
                ),
            ),
        ]
        fold_predictions: list[np.ndarray] = []
        for model in models:
            try:
                model.fit(train_x, train_y, **({"logisticregression__sample_weight": train_w} if isinstance(model[-1], LogisticRegression) else {"extratreesclassifier__sample_weight": train_w} if isinstance(model[-1], ExtraTreesClassifier) else {"histgradientboostingclassifier__sample_weight": train_w}))
                fold_predictions.append(model.predict_proba(test_x)[:, 1])
            except Exception:
                continue
        if not fold_predictions:
            base = _constant_probability(train_y.astype(float), train_w)
            prediction[test] = base
            disagreement[test] = 0.5
            fold_info[str(holdout)] = {"train": int(train.sum()), "fallback": True, "base": base}
            continue
        stack = np.vstack(fold_predictions)
        base = _constant_probability(train_y.astype(float), train_w)
        raw = stack.mean(axis=0)
        # OOF probabilities are gently shrunk rather than thresholded.
        prediction[test] = 0.90 * raw + 0.10 * base
        disagreement[test] = stack.std(axis=0) if len(stack) > 1 else 0.0
        fold_info[str(holdout)] = {
            "train": int(train.sum()),
            "test": int(test.sum()),
            "models": len(stack),
            "train_positive_rate": base,
        }
    missing = ~np.isfinite(prediction)
    if missing.any():
        base = _constant_probability(y, weights)
        prediction[missing] = base
        disagreement[missing] = 0.5
    return np.clip(prediction, 0.005, 0.995), np.nan_to_num(disagreement, nan=0.5), fold_info


def _regression_oof(
    x: np.ndarray,
    y: np.ndarray,
    groups: np.ndarray,
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    prediction = np.full(len(y), np.nan, dtype=float)
    disagreement = np.full(len(y), np.nan, dtype=float)
    fold_info: dict[str, Any] = {}
    unique_groups = sorted(pd.unique(groups).tolist())
    clipped_y = np.clip(y, -1.5, 6.0)
    for holdout in unique_groups:
        test = groups == holdout
        train = (~test) & np.isfinite(clipped_y)
        if train.sum() < 40:
            base = float(np.average(clipped_y[train], weights=weights[train])) if train.any() else 0.0
            prediction[test] = base
            disagreement[test] = 1.0
            fold_info[str(holdout)] = {"train": int(train.sum()), "fallback": True, "base": base}
            continue
        train_x, train_y, train_w = x[train], clipped_y[train], weights[train]
        test_x = x[test]
        models = [
            make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True),
                StandardScaler(),
                Ridge(alpha=8.0),
            ),
            make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True),
                ExtraTreesRegressor(
                    n_estimators=320,
                    min_samples_leaf=max(4, int(math.sqrt(train.sum()) / 5)),
                    max_features=0.70,
                    n_jobs=-1,
                    random_state=RANDOM_STATE + 11,
                ),
            ),
            make_pipeline(
                SimpleImputer(strategy="median", add_indicator=True),
                HistGradientBoostingRegressor(
                    max_iter=180,
                    learning_rate=0.045,
                    max_leaf_nodes=15,
                    min_samples_leaf=max(12, int(math.sqrt(train.sum()))),
                    l2_regularization=3.0,
                    loss="squared_error",
                    random_state=RANDOM_STATE + 12,
                ),
            ),
        ]
        fold_predictions: list[np.ndarray] = []
        for model in models:
            try:
                kwargs = (
                    {"ridge__sample_weight": train_w}
                    if isinstance(model[-1], Ridge)
                    else {"extratreesregressor__sample_weight": train_w}
                    if isinstance(model[-1], ExtraTreesRegressor)
                    else {"histgradientboostingregressor__sample_weight": train_w}
                )
                model.fit(train_x, train_y, **kwargs)
                fold_predictions.append(model.predict(test_x))
            except Exception:
                continue
        if not fold_predictions:
            base = float(np.average(train_y, weights=train_w))
            prediction[test] = base
            disagreement[test] = 1.0
            fold_info[str(holdout)] = {"train": int(train.sum()), "fallback": True, "base": base}
            continue
        stack = np.vstack(fold_predictions)
        prediction[test] = stack.mean(axis=0)
        disagreement[test] = stack.std(axis=0) if len(stack) > 1 else 0.0
        fold_info[str(holdout)] = {"train": int(train.sum()), "test": int(test.sum()), "models": len(stack)}
    missing = ~np.isfinite(prediction)
    if missing.any():
        base = float(np.nanmean(clipped_y)) if np.isfinite(clipped_y).any() else 0.0
        prediction[missing] = base
        disagreement[missing] = 1.0
    return np.clip(prediction, -1.5, 6.0), np.nan_to_num(disagreement, nan=1.0), fold_info


def _auc(y: np.ndarray, p: np.ndarray) -> float | None:
    mask = np.isfinite(y) & np.isfinite(p)
    if mask.sum() < 2 or len(np.unique(y[mask])) < 2:
        return None
    order = np.argsort(p[mask])
    ranked = np.empty(mask.sum(), dtype=float)
    ranked[order] = np.arange(mask.sum(), dtype=float) + 1.0
    positives = y[mask] > 0.5
    n_pos = int(positives.sum())
    n_neg = int((~positives).sum())
    return float((ranked[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def _score_actions(actions: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    numeric = _numeric_feature_columns(actions)
    direction_columns = _direction_feature_columns(numeric)
    if len(direction_columns) < 5:
        direction_columns = numeric
    execution_columns = numeric
    groups = actions.period.astype(str).to_numpy()
    weights = pd.to_numeric(actions.get("episode_weight", 1.0), errors="coerce").fillna(1.0).to_numpy(float)

    direction_y = pd.to_numeric(actions.destination_up_label, errors="coerce").to_numpy(float)
    direction_x = _matrix(actions, direction_columns)
    p_up, direction_uncertainty, direction_folds = _classification_oof(
        direction_x, direction_y, groups, weights
    )

    execution_y = pd.to_numeric(actions.structural_target_first, errors="coerce").to_numpy(float)
    execution_x = _matrix(actions, execution_columns)
    p_execution, execution_uncertainty, execution_folds = _classification_oof(
        execution_x, execution_y, groups, weights
    )
    realized_y = pd.to_numeric(actions.structural_realized_r, errors="coerce").to_numpy(float)
    predicted_r, r_uncertainty, r_folds = _regression_oof(
        execution_x, realized_y, groups, weights
    )

    scored = actions.copy()
    side = pd.to_numeric(scored.side_sign, errors="raise").to_numpy(float)
    p_aligned = np.where(side > 0.0, p_up, 1.0 - p_up)
    p_win = 0.62 * p_execution + 0.38 * p_aligned
    win_r = pd.to_numeric(scored.structural_planned_target_r, errors="coerce").fillna(0.0).clip(lower=0.0, upper=12.0).to_numpy(float)
    binary_ev = p_win * win_r - (1.0 - p_win)
    expected_r = 0.68 * binary_ev + 0.32 * predicted_r
    log_win = np.log1p(RISK_FRACTION * win_r)
    log_loss = math.log1p(-RISK_FRACTION)
    expected_log = p_win * log_win + (1.0 - p_win) * log_loss
    combined_uncertainty = (
        0.45 * execution_uncertainty
        + 0.25 * direction_uncertainty
        + 0.30 * np.minimum(r_uncertainty / (1.0 + np.abs(predicted_r)), 1.0)
        + 0.20 * np.abs(p_execution - p_aligned)
    )
    # Uncertainty is a smooth opportunity cost, not an arbitrary confidence gate.
    decision_score = expected_log - 0.20 * RISK_FRACTION * combined_uncertainty

    scored["oof_p_destination_up"] = p_up
    scored["oof_p_direction_aligned"] = p_aligned
    scored["oof_p_execution_target"] = p_execution
    scored["oof_p_win_blend"] = p_win
    scored["oof_predicted_realized_r"] = predicted_r
    scored["oof_expected_r"] = expected_r
    scored["oof_expected_log_growth"] = expected_log
    scored["oof_model_uncertainty"] = combined_uncertainty
    scored["decision_score"] = decision_score
    scored["positive_cost_adjusted_opportunity"] = decision_score > 0.0

    diagnostics = {
        "actions": int(len(scored)),
        "numeric_features": len(numeric),
        "direction_features": len(direction_columns),
        "direction_auc": _auc(direction_y, p_up),
        "execution_auc": _auc(execution_y, p_execution),
        "direction_resolved": int(np.isfinite(direction_y).sum()),
        "direction_folds": direction_folds,
        "execution_folds": execution_folds,
        "regression_folds": r_folds,
        "feature_columns": execution_columns,
        "direction_feature_columns": direction_columns,
    }
    return scored, diagnostics


def _one_action_per_episode(scored: pd.DataFrame) -> pd.DataFrame:
    eligible = scored[scored.positive_cost_adjusted_opportunity].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        ["episode_id", "decision_score", "oof_p_win_blend", "entry_ts", "plan_id"],
        ascending=[True, False, False, True, True],
    )
    return eligible.drop_duplicates("episode_id", keep="first")


def _global_single_slot(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    work = candidates.copy()
    work["entry_time"] = pd.to_datetime(work.entry_ts, utc=True)
    work["exit_time"] = pd.to_datetime(work.structural_exit_ts, utc=True, errors="coerce")
    work = work.sort_values(
        ["entry_time", "decision_score", "oof_p_win_blend", "plan_id"],
        ascending=[True, False, False, True],
    )
    selected: list[int] = []
    available = pd.Timestamp("1970-01-01", tz="UTC")
    for _, simultaneous in work.groupby("entry_time", sort=True):
        entry_time = simultaneous.entry_time.iloc[0]
        if entry_time < available:
            continue
        choice = simultaneous.sort_values(
            ["decision_score", "oof_p_win_blend", "plan_id"],
            ascending=[False, False, True],
        ).iloc[0]
        selected.append(int(choice.name))
        exit_time = choice.exit_time
        available = entry_time + pd.Timedelta(minutes=1) if pd.isna(exit_time) else max(exit_time, entry_time + pd.Timedelta(minutes=1))
    return work.loc[selected].sort_values("entry_time").reset_index(drop=True)


def _continuous_nav(trades: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        return trades.copy(), {
            "trades": 0, "ending_nav": 1.0, "maximum_drawdown": 0.0,
            "positive_trade_rate": 0.0, "target_first_rate": 0.0,
        }
    out = trades.copy().sort_values("entry_time").reset_index(drop=True)
    realized = pd.to_numeric(out.structural_realized_r, errors="raise").to_numpy(float)
    factors = 1.0 + RISK_FRACTION * realized
    if np.any(factors <= 0.0):
        raise RuntimeError("non-positive NAV factor")
    nav_before = np.r_[1.0, np.cumprod(factors)[:-1]]
    nav_after = np.cumprod(factors)
    peak = np.maximum.accumulate(np.r_[1.0, nav_after])[1:]
    drawdown = nav_after / peak - 1.0
    out["nav_before"] = nav_before
    out["nav_after"] = nav_after
    out["nav_factor"] = factors
    out["drawdown"] = drawdown
    positive = realized[realized > 0.0]
    negative = realized[realized < 0.0]
    if "evaluation_calendar_days" in out.columns:
        days = int(
            out.groupby("period")["evaluation_calendar_days"]
            .max()
            .fillna(0)
            .sum()
        )
    else:
        days = int(
            sum(
                (pd.Timestamp(group.interaction_ts.max()) - pd.Timestamp(group.interaction_ts.min())).days + 1
                for _, group in out.groupby("period")
            )
        )
    holding = pd.to_numeric(out.structural_duration_minutes, errors="coerce")
    planned = pd.to_numeric(out.structural_planned_target_r, errors="coerce")
    metrics = {
        "trades": int(len(out)),
        "independent_episodes": int(out.episode_id.nunique()),
        "sampled_calendar_days": days,
        "trades_per_sampled_day": float(len(out) / max(days, 1)),
        "positive_trade_rate": float(np.mean(realized > 0.0)),
        "target_first_rate": float(pd.to_numeric(out.structural_target_first, errors="coerce").mean()),
        "stop_rate": float(pd.to_numeric(out.structural_stopped, errors="coerce").mean()),
        "timeout_rate": float(pd.to_numeric(out.structural_timed_out, errors="coerce").mean()),
        "mean_realized_r": float(np.mean(realized)),
        "median_realized_r": float(np.median(realized)),
        "mean_planned_target_r": float(planned.mean()),
        "median_planned_target_r": float(planned.median()),
        "profit_factor_r": None if not len(negative) else float(positive.sum() / -negative.sum()),
        "mean_holding_minutes": float(holding.mean()),
        "median_holding_minutes": float(holding.median()),
        "p90_holding_minutes": float(holding.quantile(0.90)),
        "ending_nav": float(nav_after[-1]),
        "total_log_growth": float(np.log(factors).sum()),
        "maximum_drawdown": float(drawdown.min()),
        "mean_oof_expected_r": float(pd.to_numeric(out.oof_expected_r).mean()),
        "mean_oof_p_win": float(pd.to_numeric(out.oof_p_win_blend).mean()),
    }
    return out, metrics


def _group_metrics(trades: pd.DataFrame, column: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    if trades.empty or column not in trades.columns:
        return output
    for name, group in trades.groupby(column):
        r = pd.to_numeric(group.structural_realized_r, errors="coerce")
        output[str(name)] = {
            "trades": int(len(group)),
            "positive_trade_rate": float((r > 0.0).mean()),
            "mean_r": float(r.mean()),
            "target_first_rate": float(pd.to_numeric(group.structural_target_first, errors="coerce").mean()),
            "mean_planned_r": float(pd.to_numeric(group.structural_planned_target_r, errors="coerce").mean()),
            "mean_holding_minutes": float(pd.to_numeric(group.structural_duration_minutes, errors="coerce").mean()),
        }
    return output


def run(root: Path, output: Path) -> None:
    actions = _load_actions(root)
    output.mkdir(parents=True, exist_ok=True)
    if actions.empty:
        (output / "summary.json").write_text(json.dumps({"actions": 0}, indent=2), encoding="utf-8")
        pd.DataFrame().to_csv(output / "scored_actions.csv", index=False)
        pd.DataFrame().to_csv(output / "selected_trades.csv", index=False)
        return
    scored, model_diagnostics = _score_actions(actions)
    episode_candidates = _one_action_per_episode(scored)
    selected = _global_single_slot(episode_candidates)
    selected, metrics = _continuous_nav(selected)

    scored.to_csv(output / "scored_actions.csv", index=False)
    episode_candidates.to_csv(output / "episode_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    losses = selected[pd.to_numeric(selected.structural_realized_r, errors="coerce") <= 0.0].copy()
    losses.to_csv(output / "selected_losses.csv", index=False)

    summary = {
        "policy": {
            "name": "DIRECTION_FIRST_LIQUIDITY_DELIVERY_V1",
            "risk_fraction": RISK_FRACTION,
            "decision": "UP_DOWN_LIQUIDITY_DESTINATION_THEN_EVENT_EXECUTION_THEN_COST_ADJUSTED_LOG_GROWTH",
            "routing": "ONE_ACTION_PER_CAUSAL_EPISODE_AND_ONE_GLOBAL_ACCOUNT_SLOT",
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        },
        "actions": int(len(scored)),
        "positive_opportunities": int(scored.positive_cost_adjusted_opportunity.sum()),
        "episode_candidates": int(len(episode_candidates)),
        "metrics": metrics,
        "by_period": _group_metrics(selected, "period"),
        "by_symbol": _group_metrics(selected, "symbol"),
        "by_action": _group_metrics(selected, "action_family"),
        "by_state": _group_metrics(selected, "state"),
        "model": model_diagnostics,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Direction-first liquidity-delivery result",
        "",
        f"- Actions scored: {len(scored)}",
        f"- Independent selected trades: {metrics.get('trades', 0)}",
        f"- Positive trade rate: {metrics.get('positive_trade_rate', 0.0):.3f}",
        f"- Mean realized R: {metrics.get('mean_realized_r', 0.0):.3f}",
        f"- Mean planned target R: {metrics.get('mean_planned_target_r', 0.0):.3f}",
        f"- Trades per sampled day: {metrics.get('trades_per_sampled_day', 0.0):.3f}",
        f"- Mean holding minutes: {metrics.get('mean_holding_minutes', 0.0):.1f}",
        f"- Ending NAV from 1.0: {metrics.get('ending_nav', 1.0):.4f}",
        f"- Maximum drawdown: {metrics.get('maximum_drawdown', 0.0):.3%}",
        f"- Direction OOF AUC: {model_diagnostics.get('direction_auc')}",
        f"- Execution OOF AUC: {model_diagnostics.get('execution_auc')}",
    ]
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
