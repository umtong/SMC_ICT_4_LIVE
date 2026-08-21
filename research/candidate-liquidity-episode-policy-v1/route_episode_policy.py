#!/usr/bin/env python3
"""Causal model-assisted routing for one-plan liquidity episodes.

This is a trading policy, not an evaluation gate:
- each causal episode supplies at most one plan;
- fill probability and target-before-stop probability are learned only from
  chronologically earlier development windows;
- expected account log-growth decides whether an order is worth occupying the
  single global pending/position slot;
- labels and future diagnostics are never model features.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

from episode_policy_features import FEATURE_COLUMNS

try:
    from sklearn.ensemble import HistGradientBoostingClassifier
except Exception:  # pragma: no cover - workflow records fallback use explicitly
    HistGradientBoostingClassifier = None

RISK_FRACTION = 0.03
EPS = 1e-12
PERIOD_PATTERN = re.compile(r"(dev|fresh|cal|holdout|eval)-\d{4}-[a-z0-9]+", re.IGNORECASE)
RESOLVED_OUTCOMES = {
    "TARGET_FIRST",
    "STOP_FIRST",
    "AMBIGUOUS_SAME_MINUTE",
    "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE",
}


def _bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def _period_from_path(path: Path, summary: dict[str, Any]) -> str:
    if summary.get("period"):
        return str(summary["period"])
    for part in reversed(path.parts):
        match = PERIOD_PATTERN.search(part)
        if match:
            return match.group(0)
    return f"{summary.get('start', 'unknown')}__{summary.get('end', 'unknown')}"


def _role(period: str) -> str:
    return period.split("-", 1)[0] if "-" in period else "unknown"


def load_universe(root: Path) -> tuple[pd.DataFrame, dict[str, int], dict[str, dict[str, Any]]]:
    frames: list[pd.DataFrame] = []
    period_days: dict[str, int] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for action_path in sorted(root.glob("**/departure_actions.csv.gz")):
        summary_path = action_path.parent / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
        period = _period_from_path(action_path, summary)
        frame = pd.read_csv(action_path, low_memory=False)
        if frame.empty:
            summaries[period] = summary
            continue
        frame["period"] = period
        frame["role"] = _role(period)
        frames.append(frame)
        start = pd.Timestamp(summary.get("start")) if summary.get("start") else None
        end = pd.Timestamp(summary.get("end")) if summary.get("end") else None
        if start is not None and end is not None:
            period_days[period] = max(1, int((end - start).days))
        summaries[period] = summary
    if not frames:
        return pd.DataFrame(), period_days, summaries
    return pd.concat(frames, ignore_index=True, sort=False), period_days, summaries


def _numeric_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column in FEATURE_COLUMNS:
        if column in frame:
            output[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            output[column] = 0.0
    output = output.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    for column in output.columns:
        values = output[column].to_numpy(float)
        if len(values):
            low, high = np.quantile(values, [0.005, 0.995])
            if math.isfinite(low) and math.isfinite(high) and high > low:
                output[column] = np.clip(values, low, high)
    return output.astype(float)


def _sigmoid(value: np.ndarray | pd.Series | float) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(array, -30.0, 30.0)))


def _fallback_probabilities(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    coherence = pd.to_numeric(frame.get("mechanism_coherence", 0.0), errors="coerce").fillna(0.0)
    control = pd.to_numeric(frame.get("control_composite", 0.0), errors="coerce").fillna(0.0)
    activity = pd.to_numeric(frame.get("control_activity_ratio", 0.0), errors="coerce").fillna(0.0)
    gross_rr = pd.to_numeric(frame.get("gross_rr", 1.0), errors="coerce").fillna(1.0)
    p_fill = np.clip(
        0.34
        + 0.12 * np.tanh(np.log1p(np.maximum(activity, 0.0)))
        + 0.08 * np.tanh(control),
        0.08,
        0.88,
    )
    p_target = np.clip(
        _sigmoid(
            0.25
            + 1.35 * coherence
            + 0.45 * control
            - 0.10 * np.maximum(gross_rr - 2.0, 0.0)
        ),
        0.08,
        0.92,
    )
    return np.asarray(p_fill), np.asarray(p_target)


def _fit_predict_classifier(
    train_x: pd.DataFrame,
    train_y: pd.Series,
    test_x: pd.DataFrame,
    *,
    random_state: int,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    y = pd.to_numeric(train_y, errors="coerce").dropna().astype(int)
    x = train_x.loc[y.index]
    diagnostics = {
        "train_rows": int(len(y)),
        "positive_rate": float(y.mean()) if len(y) else None,
        "model": "fallback",
    }
    if (
        HistGradientBoostingClassifier is None
        or len(y) < 60
        or y.nunique() < 2
        or int(y.sum()) < 12
        or int((1 - y).sum()) < 12
    ):
        return None, diagnostics
    model = HistGradientBoostingClassifier(
        learning_rate=0.045,
        max_iter=140,
        max_leaf_nodes=7,
        min_samples_leaf=max(12, min(30, len(y) // 8)),
        l2_regularization=1.5,
        random_state=random_state,
    )
    model.fit(x, y)
    raw = model.predict_proba(test_x)[:, 1]
    base = float(y.mean())
    shrink = len(y) / (len(y) + 180.0)
    prediction = base + shrink * (raw - base)
    diagnostics["model"] = "hist_gradient_boosting_shrunk"
    diagnostics["shrink"] = float(shrink)
    return np.clip(prediction, 0.02, 0.98), diagnostics


def _period_start(frame: pd.DataFrame) -> pd.Series:
    order_ns = pd.to_numeric(frame.get("order_time_ns"), errors="coerce")
    return pd.to_datetime(order_ns, unit="ns", utc=True, errors="coerce")


def causal_predictions(orders: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    output = orders.copy()
    output["order_time"] = _period_start(output)
    output["fill_label"] = pd.to_numeric(output.get("fill_time_ns"), errors="coerce").notna().astype(int)
    output["target_label"] = output.get("outcome", "").astype(str).eq("TARGET_FIRST").astype(int)
    output["resolved_label"] = output.get("outcome", "").astype(str).isin(RESOLVED_OUTCOMES)
    x_all = _numeric_features(output)
    fallback_fill, fallback_target = _fallback_probabilities(output)
    output["p_fill"] = fallback_fill
    output["p_target_if_filled"] = fallback_target
    output["prediction_source"] = "mechanism_fallback"

    period_order = output.groupby("period")["order_time"].min().sort_values().index.tolist()
    model_diagnostics: dict[str, Any] = {}
    for sequence, period in enumerate(period_order):
        test_mask = output.period.astype(str).eq(str(period))
        test_index = output.index[test_mask]
        if not len(test_index):
            continue
        test_start = output.loc[test_index, "order_time"].min()
        train_mask = output.role.astype(str).eq("dev") & output.order_time.lt(test_start)
        train_index = output.index[train_mask]
        period_diag: dict[str, Any] = {
            "test_rows": int(len(test_index)),
            "training_periods": sorted(
                output.loc[train_index, "period"].astype(str).unique().tolist()
            ),
        }
        fill_pred, fill_diag = _fit_predict_classifier(
            x_all.loc[train_index],
            output.loc[train_index, "fill_label"],
            x_all.loc[test_index],
            random_state=4100 + sequence,
        )
        resolved_train = train_index[
            output.loc[train_index, "resolved_label"].to_numpy(bool)
        ]
        target_pred, target_diag = _fit_predict_classifier(
            x_all.loc[resolved_train],
            output.loc[resolved_train, "target_label"],
            x_all.loc[test_index],
            random_state=8100 + sequence,
        )
        if fill_pred is not None:
            output.loc[test_index, "p_fill"] = fill_pred
        if target_pred is not None:
            output.loc[test_index, "p_target_if_filled"] = target_pred
        if fill_pred is not None or target_pred is not None:
            output.loc[test_index, "prediction_source"] = (
                f"causal_models(fill={fill_diag['model']},target={target_diag['model']})"
            )
        period_diag["fill_model"] = fill_diag
        period_diag["target_model"] = target_diag
        model_diagnostics[str(period)] = period_diag

    target_r = pd.to_numeric(
        output.get("planned_target_net_r", 0.0), errors="coerce"
    ).fillna(0.0)
    win_log = np.log(np.maximum(EPS, 1.0 + RISK_FRACTION * target_r))
    loss_log = math.log(1.0 - RISK_FRACTION)
    p_target = np.clip(
        pd.to_numeric(output.p_target_if_filled, errors="coerce").fillna(0.0), 0.0, 1.0
    )
    p_fill = np.clip(
        pd.to_numeric(output.p_fill, errors="coerce").fillna(0.0), 0.0, 1.0
    )
    output["expected_log_growth"] = p_fill * (
        p_target * win_log + (1.0 - p_target) * loss_log
    )
    denominator = win_log - loss_log
    output["breakeven_target_probability"] = np.where(
        denominator > EPS, -loss_log / denominator, 1.0
    )
    output["probability_edge"] = p_target - output["breakeven_target_probability"]
    output["policy_eligible"] = (
        output.expected_log_growth.gt(0.0)
        & output.probability_edge.gt(0.0)
        & target_r.gt(0.0)
    )
    return output, model_diagnostics


def _timestamp_ns(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_datetime(
        pd.to_numeric(frame.get(column), errors="coerce"),
        unit="ns",
        utc=True,
        errors="coerce",
    )


def route_account(
    scored: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    eligible = scored[scored.policy_eligible.fillna(False)].copy()
    rejected = scored[~scored.policy_eligible.fillna(False)].copy()
    if eligible.empty:
        return eligible, eligible, rejected, {
            "selected_orders": 0,
            "closed_trades": 0,
            "ending_nav_multiplier": 1.0,
            "maximum_drawdown": 0.0,
        }
    eligible["terminal_time"] = _timestamp_ns(eligible, "order_terminal_time_ns")
    eligible["fill_time"] = _timestamp_ns(eligible, "fill_time_ns")
    eligible["resolution_time"] = _timestamp_ns(eligible, "resolution_time_ns")
    eligible = eligible.sort_values(
        [
            "order_time",
            "expected_log_growth",
            "probability_edge",
            "mechanism_coherence",
            "gross_rr",
            "episode_id",
        ],
        ascending=[True, False, False, False, False, True],
    )

    selected: list[pd.Series] = []
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    used_episodes: set[str] = set()
    for timestamp, group in eligible.groupby("order_time", sort=True):
        timestamp = pd.Timestamp(timestamp)
        if pd.isna(timestamp) or timestamp < busy_until:
            continue
        available = group[~group.episode_id.astype(str).isin(used_episodes)]
        if available.empty:
            continue
        row = available.iloc[0].copy()
        selected.append(row)
        used_episodes.add(str(row.episode_id))
        terminal = pd.Timestamp(row.terminal_time)
        if pd.isna(terminal):
            terminal = timestamp
        busy_until = max(timestamp, terminal)

    selected_orders = (
        pd.DataFrame(selected).reset_index(drop=True)
        if selected
        else eligible.iloc[:0].copy()
    )
    closed = selected_orders[
        pd.to_numeric(selected_orders.get("net_r"), errors="coerce").notna()
        & selected_orders.get("outcome", "").astype(str).isin(RESOLVED_OUTCOMES)
    ].copy().reset_index(drop=True)
    closed["net_r"] = pd.to_numeric(closed.net_r, errors="coerce")

    nav = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    nav_before: list[float] = []
    nav_after: list[float] = []
    for result in closed.net_r.astype(float):
        nav_before.append(nav)
        nav *= max(EPS, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
        nav_after.append(nav)
    closed["nav_before"] = nav_before
    closed["nav_after"] = nav_after
    wins = closed.outcome.astype(str).eq("TARGET_FIRST")
    return selected_orders, closed, rejected, {
        "eligible_orders": int(len(eligible)),
        "selected_orders": int(len(selected_orders)),
        "closed_trades": int(len(closed)),
        "target_first": int(wins.sum()),
        "target_first_rate": float(wins.mean()) if len(closed) else None,
        "mean_net_r": float(closed.net_r.mean()) if len(closed) else None,
        "median_net_r": float(closed.net_r.median()) if len(closed) else None,
        "mean_planned_gross_rr": float(
            pd.to_numeric(closed.get("gross_rr"), errors="coerce").mean()
        ) if len(closed) else None,
        "median_holding_minutes": float(
            pd.to_numeric(closed.get("holding_minutes"), errors="coerce").median()
        ) if len(closed) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "risk_fraction": RISK_FRACTION,
    }


def _group_metrics(frame: pd.DataFrame, key: str) -> dict[str, Any]:
    if frame.empty or key not in frame:
        return {}
    output: dict[str, Any] = {}
    for value, group in frame.groupby(key, dropna=False):
        wins = group.outcome.astype(str).eq("TARGET_FIRST")
        output[str(value)] = {
            "trades": int(len(group)),
            "target_first_rate": float(wins.mean()) if len(group) else None,
            "mean_net_r": float(
                pd.to_numeric(group.net_r, errors="coerce").mean()
            ) if len(group) else None,
            "mean_gross_rr": float(
                pd.to_numeric(group.get("gross_rr"), errors="coerce").mean()
            ) if len(group) else None,
            "median_hold_minutes": float(
                pd.to_numeric(group.get("holding_minutes"), errors="coerce").median()
            ) if len(group) else None,
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    episodes, period_days, source_summaries = load_universe(args.root)
    if episodes.empty:
        raise RuntimeError(f"No episode artifacts found under {args.root}")
    orders = episodes[_bool_series(episodes["order_exists"])].copy()
    scored, model_diagnostics = causal_predictions(orders)
    selected, closed, rejected, account = route_account(scored)

    calendar_days = int(sum(period_days.values()))
    account["diagnostic_calendar_days"] = calendar_days
    account["closed_trades_per_diagnostic_day"] = (
        float(len(closed) / calendar_days) if calendar_days else 0.0
    )
    account["by_period"] = _group_metrics(closed, "period")
    account["by_role"] = _group_metrics(closed, "role")
    account["by_family"] = _group_metrics(closed, "family")
    account["by_symbol"] = _group_metrics(closed, "symbol")

    summary = {
        "policy": (
            "one causal episode -> one destination-first plan -> causal fill/target "
            "models trained only on earlier development windows -> positive expected "
            "account log-growth -> one global pending/position slot -> TP/SL only"
        ),
        "episode_rows": int(len(episodes)),
        "order_rows": int(len(orders)),
        "account": account,
        "model_diagnostics": model_diagnostics,
        "period_days": period_days,
        "source_summaries": source_summaries,
        "feature_columns": FEATURE_COLUMNS,
        "outcome_fields_used_as_features": False,
        "future_diagnostics_used_as_features": False,
        "one_plan_per_episode": True,
        "fixed_rr_target_lattice": False,
        "target_selected_before_rr": True,
        "filled_position_exit_contract": "TAKE_PROFIT_OR_STOP_LOSS_ONLY",
        "single_global_account_slot": True,
        "diagnostic_windows_are_not_a_long_continuous_backtest": True,
    }

    episodes.to_csv(
        args.output / "all_episodes.csv.gz", index=False, compression="gzip"
    )
    scored.to_csv(
        args.output / "scored_orders.csv.gz", index=False, compression="gzip"
    )
    selected.to_csv(args.output / "selected_orders.csv", index=False)
    closed.to_csv(args.output / "closed_trades.csv", index=False)
    rejected.sort_values("expected_log_growth", ascending=False).head(300).to_csv(
        args.output / "near_miss_rejected_orders.csv", index=False
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
