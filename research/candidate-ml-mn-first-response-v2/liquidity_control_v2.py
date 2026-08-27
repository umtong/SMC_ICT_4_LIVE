#!/usr/bin/env python3
"""Frozen first-response causal-control router for Candidate ML-MN V2.

The generator defines an immutable structural entry/stop/target plan.  This router
uses only the earliest available state of each causal episode.  It estimates
fill and target-before-stop probabilities from causal, symbol-agnostic state
features and occupies the one global account slot only when expected net R is
positive.  No later return can replace a rejected first response.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK = 0.03
MIN_R = 1.0
MAX_SOURCE_R = 8.0
CAP_R = 1.5
GEOMETRY = "ZONE_PROXIMAL_LIMIT"
POLICY = "ML_MN_FIRST_RESPONSE_CAUSAL_TRANSFER_V2"
HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = HERE / "first_response_model_v2.json"


def n(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def s(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series("", index=frame.index, dtype=str)
    return frame[column].fillna("").astype(str)


def choose_geometry(frame: pd.DataFrame) -> pd.DataFrame:
    selected = frame[n(frame, "planned_target_net_r").between(MIN_R, MAX_SOURCE_R)].copy()
    selected["preferred_geometry"] = s(selected, "entry_geometry").eq(GEOMETRY).astype(int)
    selected = (
        selected.sort_values(
            ["state_id", "preferred_geometry", "planned_target_net_r", "action_id"],
            ascending=[True, False, True, True],
            kind="mergesort",
        )
        .drop_duplicates("state_id", keep="first")
        .reset_index(drop=True)
    )
    return selected


def first_episode_state(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the first immutable decision state; never substitute a later retest."""
    return (
        frame.sort_values(
            ["research_period", "episode_id", "order_time_ns", "planned_target_net_r", "action_id"],
            ascending=[True, True, True, True, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"], keep="first")
        .reset_index(drop=True)
    )


def engineer(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    side_sign = np.where(s(out, "side").eq("LONG"), 1.0, -1.0)
    family_sign = np.where(
        s(out, "family").eq("ACCEPTED_AUCTION_CONTINUATION"), 1.0, -1.0
    )
    for timeframe in (15, 60, 240):
        trend = n(out, f"structure_{timeframe}m_trend_state")
        out[f"role_align_{timeframe}"] = family_sign * side_sign * trend
    out["source_log_scale"] = np.log1p(n(out, "source_scale_minutes").clip(lower=0.0))
    out["target_log_scale"] = np.log1p(n(out, "target_scale_minutes").clip(lower=0.0))
    out["clock_hour_sin2"] = n(out, "clock_hour_sin")
    out["clock_hour_cos2"] = n(out, "clock_hour_cos")
    return out


def _sigmoid(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-values))


def frozen_probability(frame: pd.DataFrame, spec: dict[str, Any]) -> np.ndarray:
    numeric_parts: list[np.ndarray] = []
    for column, median, mean, scale in zip(
        spec["numeric_features"],
        spec["numeric_medians"],
        spec["numeric_means"],
        spec["numeric_scales"],
        strict=True,
    ):
        values = n(frame, column).to_numpy(dtype=float)
        values[~np.isfinite(values)] = float(median)
        divisor = float(scale) if abs(float(scale)) > 1e-15 else 1.0
        numeric_parts.append((values - float(mean)) / divisor)

    categorical_parts: list[np.ndarray] = []
    for column, fill_value, categories in zip(
        spec["categorical_features"],
        spec["categorical_fill_values"],
        spec["categorical_categories"],
        strict=True,
    ):
        raw = frame[column] if column in frame else pd.Series(np.nan, index=frame.index)
        values = raw.astype(object).to_numpy(copy=True)
        missing = pd.isna(values)
        values[missing] = str(fill_value)
        values = np.asarray([str(value) for value in values], dtype=object)
        for category in categories:
            categorical_parts.append((values == str(category)).astype(float))

    pieces = numeric_parts + categorical_parts
    matrix = np.column_stack(pieces) if pieces else np.empty((len(frame), 0), dtype=float)
    coefficients = np.asarray(spec["coefficients"], dtype=float)
    if matrix.shape[1] != coefficients.size:
        raise ValueError(
            f"model dimension mismatch: transformed={matrix.shape[1]} coefficients={coefficients.size}"
        )
    return _sigmoid(matrix @ coefficients + float(spec["intercept"]))


def score_first_response(
    frame: pd.DataFrame, model: dict[str, Any]
) -> pd.DataFrame:
    scored = engineer(first_episode_state(choose_geometry(frame)))
    scored["predicted_fill_probability"] = frozen_probability(scored, model["fill_model"])
    scored["predicted_target_probability_given_fill"] = frozen_probability(
        scored, model["win_model"]
    )
    target_r = n(scored, "planned_target_net_r")
    scored["expected_net_r"] = scored["predicted_fill_probability"] * (
        scored["predicted_target_probability_given_fill"] * target_r
        - (1.0 - scored["predicted_target_probability_given_fill"])
    )
    threshold = float(model["selection_expected_net_r_threshold"])
    selected = scored[n(scored, "expected_net_r").gt(threshold)].copy()
    selected["scenario_family"] = "FIRST_RESPONSE_CAUSAL_TRANSFER"
    selected["scenario_priority"] = 1
    return selected.sort_values(
        ["order_time_ns", "expected_net_r", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def route_account(plans: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    busy_until = -1
    nav = peak = 1.0
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    for record in plans.to_dict("records"):
        order_time = int(record["order_time_ns"])
        if order_time < busy_until:
            continue
        outcome = str(record.get("outcome", "UNFILLED"))
        if outcome == "UNFILLED":
            terminal = record.get("order_terminal_time_ns")
            if pd.isna(terminal):
                continue
            busy_until = int(terminal)
            record.update(
                account_busy_until_ns=busy_until,
                net_r_num=0.0,
                nav_before=nav,
                nav_after=nav,
            )
            orders.append(record)
            continue

        resolution = record.get("resolution_time_ns")
        if pd.isna(resolution):
            continue
        busy_until = int(resolution)
        net_r = float(record["net_r"]) if pd.notna(record.get("net_r")) else -1.0
        nav_before = nav
        nav = max(0.0, nav * (1.0 + RISK * net_r))
        peak = max(peak, nav)
        record.update(
            account_busy_until_ns=busy_until,
            net_r_num=net_r,
            nav_before=nav_before,
            nav_after=nav,
            drawdown=1.0 - nav / peak,
        )
        orders.append(record.copy())
        trades.append(record)
    return pd.DataFrame(orders), pd.DataFrame(trades)


def metric_block(orders: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    values = (
        pd.to_numeric(trades["net_r_num"], errors="coerce").dropna()
        if len(trades)
        else pd.Series(dtype=float)
    )
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    nav = peak = 1.0
    drawdown = 0.0
    for value in values:
        nav = max(0.0, nav * (1.0 + RISK * float(value)))
        peak = max(peak, nav)
        drawdown = max(drawdown, 1.0 - nav / peak)
    outcomes = s(orders, "outcome") if len(orders) else pd.Series(dtype=str)
    return {
        "orders": int(len(orders)),
        "unfilled_orders": int(outcomes.eq("UNFILLED").sum()),
        "closed_trades": int(len(values)),
        "wins": int((values > 0.0).sum()),
        "win_rate": float((values > 0.0).mean()) if len(values) else 0.0,
        "sum_net_r": float(values.sum()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "average_win_r": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": (
            float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None
        ),
        "ending_nav": float(nav),
        "max_drawdown": float(drawdown),
    }


def summarize(
    source: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    model: dict[str, Any],
) -> dict[str, Any]:
    periods = sorted(
        source["research_period"].astype(str).unique(),
        key=lambda period: int(
            source.loc[source["research_period"].astype(str).eq(period), "order_time_ns"].min()
        ),
    )
    symbols = sorted(source["symbol"].astype(str).unique())
    return {
        "policy": POLICY,
        "model_training_periods": model["training_periods"],
        "decision_uses_symbol_identity": False,
        "decision_uses_outcome_fields": False,
        "first_state_only": True,
        "selection_expected_net_r_threshold": float(
            model["selection_expected_net_r_threshold"]
        ),
        "account": {
            "one_global_pending_or_position_slot": True,
            "one_plan_per_causal_episode": True,
            "risk_fraction_of_current_nav": RISK,
            "scale_in_or_out": False,
            "minimum_planned_target_net_r": MIN_R,
            "maximum_realized_target_net_r": CAP_R,
        },
        "overall_continuous_account": metric_block(orders, trades),
        "by_period": {
            period: metric_block(
                orders[orders["research_period"].astype(str).eq(period)],
                trades[trades["research_period"].astype(str).eq(period)],
            )
            for period in periods
        },
        "by_symbol": {
            symbol: metric_block(
                orders[orders["symbol"].astype(str).eq(symbol)],
                trades[trades["symbol"].astype(str).eq(symbol)],
            )
            for symbol in symbols
        },
    }


def load_actions(root: Path) -> pd.DataFrame:
    files = sorted(root.rglob("departure_actions.csv.gz"))
    if not files:
        raise FileNotFoundError(root)
    frames: list[pd.DataFrame] = []
    for path in files:
        frame = pd.read_csv(path, low_memory=False)
        period = path.parent.name
        if period.endswith("USDT"):
            period = path.parent.parent.name
        frame["research_period"] = period
        frames.append(frame)
    return pd.concat(frames, ignore_index=True, sort=False)


def apply_period_bounds(frame: pd.DataFrame, path: Path | None) -> pd.DataFrame:
    if path is None:
        return frame
    config = json.loads(path.read_text(encoding="utf-8"))
    timestamps = n(frame, "order_time_ns")
    keep = pd.Series(False, index=frame.index)
    for period, window in config.items():
        keep |= (
            frame["research_period"].astype(str).eq(period)
            & timestamps.ge(pd.Timestamp(window["start"], tz="UTC").value)
            & timestamps.lt(pd.Timestamp(window["end"], tz="UTC").value)
        )
    return frame.loc[keep].copy()


def run(
    root: Path,
    output: Path,
    model_path: Path = DEFAULT_MODEL,
    period_bounds: Path | None = None,
) -> dict[str, Any]:
    model = json.loads(model_path.read_text(encoding="utf-8"))
    source = apply_period_bounds(load_actions(root), period_bounds)
    plans = score_first_response(source, model)
    orders, trades = route_account(plans)
    result = summarize(source, orders, trades, model)
    output.mkdir(parents=True, exist_ok=True)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--period-bounds", type=Path)
    args = parser.parse_args()
    run(args.root, args.output, args.model, args.period_bounds)


if __name__ == "__main__":
    main()
