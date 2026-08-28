#!/usr/bin/env python3
"""EasyChart-B V3: causal sweep-release / first-return structural policy.

The target is inherited from market structure by ``harvest_structural.py``.  This router
only decides whether the causal event has revealed enough control to arm the first return
while a material part of the declared structural completion remains.  It never uses a
symbol ID, calendar field, outcome, MFE/MAE or a fixed-R target cap.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
PREFERRED_GEOMETRY = "ZONE_PROXIMAL_LIMIT"

# Causal meanings shared by all symbols.  These are deliberately broad mechanism
# boundaries rather than an indicator vote or a symbol-specific parameter set.
MIN_NET_COMPLETION_R = 0.45
MIN_ACCEPTED_CLOSE_RATIO = 0.45
MIN_ACCEPTED_VOLUME_RATIO = 0.50
MIN_PATH_EFFICIENCY = 0.08
MIN_REVEALED_FRACTION = 0.18
MAX_CONSUMED_FRACTION = 0.78
MAX_CURRENT_RETRACE = 0.55
MIN_ACTIVITY = 0.55
MAX_ACTIVITY = 3.60
MIN_SOURCE_DEFENSES = 3.0

SCENARIO_PRIORITY = {
    "FAILED_AUCTION_LOCAL_RECLAIM": 2,
    "ACCEPTED_AUCTION_FIRST_RETURN": 1,
}

DECISION_COLUMNS = {
    "family",
    "auction_phase",
    "structural_target_provenance",
    "planned_target_net_r",
    "arm_structural_target_consumed_fraction",
    "arm_structural_target_headroom_r",
    "arm_outside_close_ratio",
    "arm_outside_volume_share",
    "arm_path_efficiency",
    "arm_current_retrace_fraction",
    "arm_activity_ratio",
    "arm_futures_index_residual_signed",
    "departure_residual_return_3m_signed",
    "arm_index_return_signed",
    "source_defense_count",
    "entry_geometry",
    "state_id",
    "episode_id",
    "order_time_ns",
    "action_id",
}


def numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in frame:
        return pd.Series(default, index=frame.index, dtype=str)
    return frame[column].fillna(default).astype(str)


def _period_from_path(path: Path, period_names: set[str]) -> str:
    joined = "/".join(path.parts)
    matches = [name for name in period_names if name in joined]
    if matches:
        return max(matches, key=len)
    return path.parent.name


def load_actions(root: Path, period_bounds: dict[str, dict[str, str]]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    period_names = set(period_bounds)
    for path in sorted(root.rglob("departure_actions.csv.gz")):
        frame = pd.read_csv(path, low_memory=False)
        if frame.empty:
            continue
        frame = frame.copy()
        frame["research_period"] = _period_from_path(path, period_names)
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"No departure_actions.csv.gz found below {root}")
    out = pd.concat(frames, ignore_index=True, sort=False)
    out = out.drop_duplicates("action_id", keep="last").reset_index(drop=True)
    out["order_time_ns"] = pd.to_numeric(out.order_time_ns, errors="coerce")
    return out[out.order_time_ns.notna()].copy()


def scenario_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    family = text(frame, "family")
    phase = text(frame, "auction_phase")
    consumed = numeric(frame, "arm_structural_target_consumed_fraction")
    accepted = (
        phase.isin({"ACCEPTED_EXPANSION", "FIRST_RETEST_FORMING"})
        & numeric(frame, "arm_outside_close_ratio").ge(MIN_ACCEPTED_CLOSE_RATIO)
        & numeric(frame, "arm_outside_volume_share").ge(MIN_ACCEPTED_VOLUME_RATIO)
        & numeric(frame, "arm_path_efficiency").ge(MIN_PATH_EFFICIENCY)
        & numeric(frame, "arm_current_retrace_fraction").le(MAX_CURRENT_RETRACE)
        & numeric(frame, "arm_activity_ratio").between(MIN_ACTIVITY, MAX_ACTIVITY)
        & consumed.between(MIN_REVEALED_FRACTION, MAX_CONSUMED_FRACTION)
    )
    local_control = (
        numeric(frame, "arm_futures_index_residual_signed").ge(0.0)
        | numeric(frame, "departure_residual_return_3m_signed").ge(0.0)
    )
    broad_or_local_support = (
        numeric(frame, "arm_index_return_signed").ge(0.0)
        | local_control
    )
    failed = (
        family.eq("FAILED_AUCTION_REVERSAL")
        & accepted
        & local_control
    )
    continuation = (
        family.eq("ACCEPTED_AUCTION_CONTINUATION")
        & accepted
        & broad_or_local_support
        & numeric(frame, "source_defense_count").ge(MIN_SOURCE_DEFENSES)
    )
    return {
        "FAILED_AUCTION_LOCAL_RECLAIM": failed,
        "ACCEPTED_AUCTION_FIRST_RETURN": continuation,
    }


def select_plans(frame: pd.DataFrame) -> pd.DataFrame:
    masks = scenario_masks(frame)
    eligible = pd.concat(masks, axis=1).any(axis=1)
    eligible &= numeric(frame, "gross_rr").ge(1.0)
    eligible &= numeric(frame, "planned_target_net_r").ge(MIN_NET_COMPLETION_R)
    selected = frame.loc[eligible].copy()
    if selected.empty:
        return selected
    selected["scenario_family"] = ""
    for name, priority in sorted(SCENARIO_PRIORITY.items(), key=lambda item: item[1]):
        selected.loc[
            masks[name].reindex(selected.index, fill_value=False), "scenario_family"
        ] = name
    selected["scenario_priority"] = selected.scenario_family.map(SCENARIO_PRIORITY).astype(int)
    selected["preferred_geometry"] = (
        text(selected, "entry_geometry").eq(PREFERRED_GEOMETRY).astype(int)
    )
    # One immutable plan per causal state.  Favor the source-proximal first return;
    # target distance is never used to manufacture a destination.
    selected = (
        selected.sort_values(
            ["state_id", "preferred_geometry", "planned_target_net_r", "action_id"],
            ascending=[True, False, False, True],
            kind="mergesort",
        )
        .drop_duplicates("state_id")
        .sort_values(
            ["order_time_ns", "scenario_priority", "action_id"],
            ascending=[True, False, True],
            kind="mergesort",
        )
        .reset_index(drop=True)
    )
    return selected


def first_plan_per_episode(plans: pd.DataFrame) -> pd.DataFrame:
    if plans.empty:
        return plans.copy()
    return (
        plans.sort_values(
            ["order_time_ns", "scenario_priority", "action_id"],
            ascending=[True, False, True],
            kind="mergesort",
        )
        .drop_duplicates(["research_period", "episode_id"])
        .sort_values(["order_time_ns", "scenario_priority", "action_id"], kind="mergesort")
        .reset_index(drop=True)
    )


def route_continuous_account(plans: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    busy_until = -1
    nav = peak = 1.0
    orders: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    for record in first_plan_per_episode(plans).to_dict("records"):
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
                drawdown=1.0 - nav / peak,
            )
            orders.append(record)
            continue
        resolution = record.get("resolution_time_ns")
        net_r = pd.to_numeric(pd.Series([record.get("net_r")]), errors="coerce").iloc[0]
        if pd.isna(resolution) or pd.isna(net_r):
            continue
        busy_until = int(resolution)
        before = nav
        nav = max(0.0, nav * (1.0 + RISK_FRACTION * float(net_r)))
        peak = max(peak, nav)
        record.update(
            account_busy_until_ns=busy_until,
            net_r_num=float(net_r),
            nav_before=before,
            nav_after=nav,
            drawdown=1.0 - nav / peak,
        )
        orders.append(record.copy())
        trades.append(record)
    return pd.DataFrame(orders), pd.DataFrame(trades)


def metric_block(orders: pd.DataFrame, trades: pd.DataFrame) -> dict[str, Any]:
    values = (
        pd.to_numeric(trades.get("net_r_num"), errors="coerce").dropna()
        if len(trades)
        else pd.Series(dtype=float)
    )
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    nav = peak = 1.0
    max_drawdown = 0.0
    for value in values:
        nav = max(0.0, nav * (1.0 + RISK_FRACTION * float(value)))
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1.0 - nav / peak)
    outcomes = text(orders, "outcome") if len(orders) else pd.Series(dtype=str)
    return {
        "orders": int(len(orders)),
        "unfilled_orders": int(outcomes.eq("UNFILLED").sum()),
        "closed_trades": int(len(values)),
        "wins": int((values > 0.0).sum()),
        "losses": int((values < 0.0).sum()),
        "win_rate": float((values > 0.0).mean()) if len(values) else 0.0,
        "sum_net_r": float(values.sum()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "average_win_r": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": (
            float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None
        ),
        "mean_planned_gross_rr": (
            float(numeric(trades, "gross_rr").mean()) if len(trades) else 0.0
        ),
        "mean_planned_target_net_r": (
            float(numeric(trades, "planned_target_net_r").mean()) if len(trades) else 0.0
        ),
        "median_hold_minutes": (
            float(numeric(trades, "holding_minutes").median()) if len(trades) else None
        ),
        "ending_nav": float(nav),
        "max_drawdown": float(max_drawdown),
    }


def build_summary(
    source: pd.DataFrame,
    selected: pd.DataFrame,
    orders: pd.DataFrame,
    trades: pd.DataFrame,
    period_bounds: dict[str, dict[str, str]],
) -> dict[str, Any]:
    periods = sorted(
        period_bounds,
        key=lambda period: period_bounds[period]["start"],
    )
    calendar_days = sum(
        (date.fromisoformat(window["end"]) - date.fromisoformat(window["start"])).days
        for window in period_bounds.values()
    )
    overall = metric_block(orders, trades)
    overall["calendar_days"] = int(calendar_days)
    overall["closed_trades_per_calendar_day"] = overall["closed_trades"] / max(calendar_days, 1)
    def subset_metric(column: str, value: str) -> dict[str, Any]:
        oo = orders[text(orders, column).eq(value)] if len(orders) else orders
        tt = trades[text(trades, column).eq(value)] if len(trades) else trades
        return metric_block(oo, tt)
    return {
        "policy": "ML_EASYCHART_B_V3_STRUCTURAL_COMPLETION_CONTROL",
        "decision_uses_symbol_identity": False,
        "decision_uses_calendar_fields": False,
        "decision_uses_outcome_fields": False,
        "fixed_r_target_cap": False,
        "target_contract": (
            "nearest causal impulse-reclaim or still-live opposing structure; gross RR is only a 1R admissibility floor"
        ),
        "decision_columns": sorted(DECISION_COLUMNS),
        "eligible_state_plans": int(len(selected)),
        "eligible_episodes": int(selected.episode_id.nunique()) if len(selected) else 0,
        "overall_continuous_account": overall,
        "by_period": {
            period: metric_block(
                orders[text(orders, "research_period").eq(period)] if len(orders) else orders,
                trades[text(trades, "research_period").eq(period)] if len(trades) else trades,
            )
            for period in periods
        },
        "by_scenario_family": {
            name: subset_metric("scenario_family", name) for name in SCENARIO_PRIORITY
        },
        "by_target_provenance": {
            name: subset_metric("structural_target_provenance", name)
            for name in sorted(text(selected, "structural_target_provenance").unique())
            if name
        },
        "by_symbol": {
            symbol: subset_metric("symbol", symbol)
            for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
        },
    }


def run(root: Path, period_bounds_path: Path, output: Path) -> dict[str, Any]:
    period_bounds = json.loads(period_bounds_path.read_text(encoding="utf-8"))
    actions = load_actions(root, period_bounds)
    selected = select_plans(actions)
    orders, trades = route_continuous_account(selected)
    summary = build_summary(actions, selected, orders, trades, period_bounds)
    output.mkdir(parents=True, exist_ok=True)
    selected.to_csv(output / "eligible_plans.csv", index=False)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--period-bounds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.period_bounds, args.output)


if __name__ == "__main__":
    main()
