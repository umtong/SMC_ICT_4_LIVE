#!/usr/bin/env python3
"""Train only on strict departure-time plans and route one global account.

Candidate existence, plan geometry and all model inputs are fixed at the completed
departure bar. Future first-return/response fields are excluded. Pending orders own
the single account slot until causal cancellation or fill; after fill only TP/SL
releases the slot.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

RISK = 0.03
EPS = 1e-12
CATEGORICAL = [
    "family", "side", "entry_geometry", "setup_kind", "location_kind",
    "source_pool_kind", "route_kind",
]
DENY = (
    "outcome", "fill_state", "fill_index", "fill_time", "resolution",
    "order_terminal", "entry_wait", "holding", "net_r", "mfe_r", "mae_r",
    "actual_", "diagnostic_response", "diagnostic_first_return",
    "diagnostic_retest", "response_",
)


def period_name(directory: Path) -> str:
    for token in ("fresh-", "dev-", "eval-", "train-", "cal-", "holdout-"):
        at = directory.name.find(token)
        if at >= 0:
            return directory.name[at:]
    return directory.name


def load_actions(root: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        frame["period"] = period_name(path.parent)
        frames.append(frame)
    if not frames:
        raise RuntimeError(f"no strict departure actions below {root}")
    frame = pd.concat(frames, ignore_index=True, sort=False)
    for column in (
        "order_time_ns", "order_terminal_time_ns", "fill_time_ns",
        "resolution_time_ns", "gross_rr", "planned_target_net_r",
        "actual_target_net_r", "net_r", "holding_minutes",
    ):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["filled"] = frame.fill_state.astype(str).str.startswith("FILLED")
    frame["resolved"] = frame.outcome.astype(str).isin(
        ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE",
         "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE"]
    )
    frame["win"] = frame.outcome.astype(str).eq("TARGET_FIRST")
    ambiguous = frame.outcome.astype(str).str.startswith("AMBIGUOUS")
    frame.loc[ambiguous & frame.net_r.isna(), "net_r"] = -1.0
    frame["target_net_r"] = frame.actual_target_net_r.where(
        frame.actual_target_net_r.notna(), frame.planned_target_net_r
    )
    frame["terminal_ns"] = frame.order_terminal_time_ns
    for period, group in frame.groupby("period"):
        end = pd.to_numeric(group.order_terminal_time_ns, errors="coerce").max()
        frame.loc[(frame.period == period) & frame.terminal_ns.isna(), "terminal_ns"] = end
    return frame


def economic_lattice(frame: pd.DataFrame) -> pd.DataFrame:
    family = frame.family.astype(str)
    failed = family.str.contains("FAILED")
    accepted = family.str.contains("ACCEPTED")
    proximal = frame.entry_geometry.astype(str).str.contains("PROXIMAL")
    middle = frame.entry_geometry.astype(str).str.contains("MID")
    rr = pd.to_numeric(frame.gross_rr, errors="coerce")
    target = pd.to_numeric(frame.target_net_r, errors="coerce")
    keep = (
        (failed & (proximal | middle) & np.isclose(rr, 1.25))
        | (accepted & proximal & np.isclose(rr, 2.0))
    ) & target.ge(0.40)
    return frame[keep].copy()


def feature_columns(frame: pd.DataFrame) -> tuple[list[str], list[str]]:
    categorical = [column for column in CATEGORICAL if column in frame]
    numeric = []
    identifiers = {
        "period", "symbol", "action_id", "state_id", "episode_id", "entry",
        "stop", "target", "order_time_ns", "order_terminal_time_ns",
        "fill_time_ns", "resolution_time_ns", "terminal_ns",
    }
    for column in frame.columns:
        low = column.lower()
        if column in identifiers or column in categorical:
            continue
        if any(token in low for token in DENY):
            continue
        if low.startswith("diagnostic_") or low.endswith("_time_ns"):
            continue
        if not pd.api.types.is_numeric_dtype(frame[column]):
            continue
        if frame[column].notna().sum() < max(30, int(0.08 * len(frame))):
            continue
        if frame[column].nunique(dropna=True) <= 1:
            continue
        numeric.append(column)
    return numeric, categorical


def prior(train: pd.DataFrame, label: str, test: pd.DataFrame) -> np.ndarray:
    global_p = (float(train[label].sum()) + 8.0) / (len(train) + 16.0)
    result = np.full(len(test), global_p)
    weight = np.full(len(test), 16.0)
    for keys in (["family"], ["family", "entry_geometry", "gross_rr"],
                 ["family", "entry_geometry", "gross_rr", "setup_kind"]):
        stats = train.groupby(keys, dropna=False)[label].agg(["sum", "count"]).reset_index()
        merged = test[keys].merge(stats, on=keys, how="left")
        count = merged["count"].fillna(0).to_numpy(float)
        success = merged["sum"].fillna(0).to_numpy(float)
        probability = (success + 24.0 * global_p) / (count + 24.0)
        added = np.minimum(count, 80.0)
        result = (result * weight + probability * added) / np.maximum(weight + added, EPS)
        weight += added
    return np.clip(result, 0.01, 0.99)


def predict_classifier(
    train: pd.DataFrame,
    test: pd.DataFrame,
    label: str,
    numeric: list[str],
    categorical: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    if len(train) < 80 or train[label].nunique() < 2:
        probability = (float(train[label].sum()) + 5.0) / (len(train) + 10.0)
        return np.full(len(test), probability), np.zeros(len(test))
    columns = numeric + categorical
    x_train = train[columns].copy()
    x_test = test[columns].copy()
    for column in categorical:
        x_train[column] = x_train[column].fillna("__NA__").astype(str)
        x_test[column] = x_test[column].fillna("__NA__").astype(str)
    cat_index = [x_train.columns.get_loc(column) for column in categorical]
    predictions = []
    for seed in (17, 71, 173):
        model = CatBoostClassifier(
            iterations=260,
            depth=5,
            learning_rate=0.045,
            l2_leaf_reg=8.0,
            random_strength=1.2,
            loss_function="Logloss",
            auto_class_weights="Balanced",
            verbose=False,
            allow_writing_files=False,
            random_seed=seed,
            thread_count=-1,
        )
        model.fit(x_train, train[label].astype(int), cat_features=cat_index)
        predictions.append(model.predict_proba(x_test)[:, 1])
    matrix = np.vstack(predictions)
    return matrix.mean(axis=0), matrix.std(axis=0)


def score_fold(train: pd.DataFrame, test: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    fill_mean, fill_std = predict_classifier(train, test, "filled", numeric, categorical)
    resolved = train[train.filled & train.resolved & train.net_r.notna()].copy()
    win_mean, win_std = predict_classifier(resolved, test, "win", numeric, categorical)
    fill = 0.65 * fill_mean + 0.35 * prior(train, "filled", test)
    win = 0.65 * win_mean + 0.35 * prior(resolved, "win", test)
    family_count = resolved.groupby("family").size().reindex(test.family).fillna(1).to_numpy(float)
    output = test.copy()
    output["p_fill"] = np.clip(fill, 0.01, 0.99)
    output["p_win"] = np.clip(win, 0.01, 0.99)
    output["p_fill_low"] = np.clip(fill - 0.30 * fill_std, 0.01, 0.99)
    output["p_win_low"] = np.clip(win - 0.45 * win_std - 0.12 / np.sqrt(family_count), 0.01, 0.99)
    target = output.target_net_r.clip(lower=0.0).to_numpy(float)
    log_win = np.log1p(RISK * target)
    log_loss = math.log(1.0 - RISK)
    output["break_even_p"] = -log_loss / np.maximum(log_win - log_loss, EPS)
    output["conservative_log_growth"] = output.p_fill_low * (
        output.p_win_low * log_win + (1.0 - output.p_win_low) * log_loss
    )
    return output


def route(frame: pd.DataFrame, require_positive: bool = True) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    candidates = frame.copy()
    if require_positive:
        candidates = candidates[
            (candidates.conservative_log_growth > 0.0)
            & (candidates.p_win_low > candidates.break_even_p)
        ]
    candidates = candidates.sort_values(
        ["period", "state_id", "conservative_log_growth", "target_net_r"],
        ascending=[True, True, False, False],
    ).groupby(["period", "state_id"], as_index=False).first()
    candidates = candidates.sort_values(
        ["period", "order_time_ns", "conservative_log_growth", "target_net_r", "state_id"],
        ascending=[True, True, False, False, True],
    )
    selected = []
    for period, group in candidates.groupby("period", sort=True):
        busy_until = -np.inf
        for timestamp, simultaneous in group.groupby("order_time_ns", sort=True):
            if not np.isfinite(timestamp) or timestamp < busy_until:
                continue
            row = simultaneous.iloc[0]
            selected.append(row)
            busy_until = max(float(timestamp), float(row.terminal_ns))
    orders = pd.DataFrame(selected).reset_index(drop=True) if selected else candidates.iloc[:0].copy()
    trades = orders[orders.resolved & orders.net_r.notna()].copy().reset_index(drop=True)
    nav = peak = 1.0
    maximum_drawdown = 0.0
    for result in trades.net_r.astype(float):
        nav *= max(EPS, 1.0 + RISK * result)
        peak = max(peak, nav)
        maximum_drawdown = max(maximum_drawdown, 1.0 - nav / peak)
    by_period = trades.groupby("period").agg(
        trades=("net_r", "size"),
        target_first_rate=("win", "mean"),
        mean_net_r=("net_r", "mean"),
    ).reset_index() if len(trades) else pd.DataFrame()
    summary = {
        "selected_orders": int(len(orders)),
        "closed_trades": int(len(trades)),
        "periods": int(frame.period.nunique()),
        "calendar_days": int(7 * frame.period.nunique()),
        "trades_per_day": float(len(trades) / max(7 * frame.period.nunique(), 1)),
        "target_first_rate": float(trades.win.mean()) if len(trades) else None,
        "mean_net_r": float(trades.net_r.mean()) if len(trades) else None,
        "median_hold_minutes": float(trades.holding_minutes.median()) if len(trades) else None,
        "mean_hold_minutes": float(trades.holding_minutes.mean()) if len(trades) else None,
        "ending_nav_multiplier": float(nav),
        "maximum_drawdown": float(maximum_drawdown),
        "by_period": by_period.to_dict("records"),
    }
    return orders, trades, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--fresh-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    development = economic_lattice(load_actions(args.development_root))
    numeric, categorical = feature_columns(development)
    oof = []
    for period in sorted(development.period.unique()):
        oof.append(score_fold(development[development.period != period], development[development.period == period], numeric, categorical))
    oof_frame = pd.concat(oof, ignore_index=True, sort=False)
    oof_orders, oof_trades, oof_summary = route(oof_frame)
    oof_frame.to_csv(args.output / "development_oof_plans.csv.gz", index=False, compression="gzip")
    oof_orders.to_csv(args.output / "development_oof_orders.csv", index=False)
    oof_trades.to_csv(args.output / "development_oof_trades.csv", index=False)
    result: dict[str, Any] = {
        "policy": "STRICT_CAUSAL_FIRST_RETURN_EVENT_THEN_GEOMETRY_THEN_GLOBAL_SLOT",
        "causal_features": {"numeric": numeric, "categorical": categorical},
        "development_oof": oof_summary,
    }
    if args.fresh_root is not None:
        fresh = economic_lattice(load_actions(args.fresh_root))
        scored = score_fold(development, fresh, numeric, categorical)
        fresh_orders, fresh_trades, fresh_summary = route(scored)
        scored.to_csv(args.output / "fresh_scored_plans.csv.gz", index=False, compression="gzip")
        fresh_orders.to_csv(args.output / "fresh_orders.csv", index=False)
        fresh_trades.to_csv(args.output / "fresh_trades.csv", index=False)
        result["fresh"] = fresh_summary
    (args.output / "summary.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
    lines = [
        "# V5 strict-causal first-return result", "",
        "Candidate existence and every model input are fixed at the departure bar.",
        "Pending-order lifetime is causal; filled positions exit only at TP or SL.", "",
        "## Development leave-one-period-out", "", json.dumps(oof_summary, indent=2, default=str),
    ]
    if "fresh" in result:
        lines += ["", "## Fresh periods", "", json.dumps(result["fresh"], indent=2, default=str)]
    (args.output / "RESULT.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
