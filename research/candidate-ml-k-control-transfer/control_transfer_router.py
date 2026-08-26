#!/usr/bin/env python3
"""One-account causal control-transfer router for Candidate ML-k.

The router does not search thresholds at run time.  It consumes explicit plans
already produced by the liquidity-episode generator, recognizes four distinct
control-transfer mechanisms using only public-at-order features, chooses the
nearest usable first-return plan, and routes independent episodes through one
continuous account slot.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

RISK_FRACTION = 0.03
MIN_TARGET_NET_R = 1.0
MAX_TARGET_NET_R = 8.0
GEOMETRY = "ZONE_PROXIMAL_LIMIT"

THRESHOLDS = {
    "efficient_approach_impact": 0.80760719,
    "efficient_approach_path": 0.29738837,
    "meaningful_zone_bps": 6.0039672,
    "basis_compression_bps": -1.351867,
    "departure_low_impact": 0.11387988,
    "source_defenses": 5.0,
    "initial_displacement_bps": 35.48466,
    "late_opposite_delta": -0.13257523,
    "wide_zone_bps": 7.877117,
    "event_absorption_impact": 0.19427535,
    "event_absorption_risk_bps": 7.5579072,
    "late_displacement_bps": 7.7896787,
}

SCENARIO_PRIORITY = {
    "DEFENDED_BASIS_ABSORPTION": 4,
    "PUSH_PULL_ABSORPTION": 3,
    "EVENT_ABSORPTION_DISPLACEMENT": 2,
    "EFFICIENT_APPROACH_SOURCE": 1,
}


def _num(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def scenario_masks(frame: pd.DataFrame) -> dict[str, pd.Series]:
    t = THRESHOLDS
    return {
        "EFFICIENT_APPROACH_SOURCE": (
            _num(frame, "approach_impact_per_activity_12m").ge(t["efficient_approach_impact"])
            & _num(frame, "approach_path_efficiency").ge(t["efficient_approach_path"])
            & _num(frame, "zone_width_bps").ge(t["meaningful_zone_bps"])
        ),
        "DEFENDED_BASIS_ABSORPTION": (
            _num(frame, "departure_basis_change_3m_signed").le(t["basis_compression_bps"])
            & _num(frame, "departure_impact_per_activity").le(t["departure_low_impact"])
            & _num(frame, "source_defense_count").ge(t["source_defenses"])
        ),
        "PUSH_PULL_ABSORPTION": (
            _num(frame, "sequence_block_0_return_bps_signed").ge(t["initial_displacement_bps"])
            & _num(frame, "sequence_block_5_delta_share_signed").le(t["late_opposite_delta"])
            & _num(frame, "zone_width_bps").ge(t["wide_zone_bps"])
        ),
        "EVENT_ABSORPTION_DISPLACEMENT": (
            _num(frame, "event_impact_per_activity").le(t["event_absorption_impact"])
            & _num(frame, "risk_bps").ge(t["event_absorption_risk_bps"])
            & _num(frame, "sequence_block_3_return_bps_signed").ge(t["late_displacement_bps"])
        ),
    }


def label_scenarios(frame: pd.DataFrame) -> pd.DataFrame:
    masks = scenario_masks(frame)
    any_mask = pd.concat(masks, axis=1).any(axis=1)
    selected = frame.loc[any_mask].copy()
    selected["scenario_family"] = ""
    for name, _priority in sorted(SCENARIO_PRIORITY.items(), key=lambda item: item[1]):
        selected.loc[masks[name].reindex(selected.index, fill_value=False), "scenario_family"] = name
    selected["scenario_priority"] = selected["scenario_family"].map(SCENARIO_PRIORITY).astype(int)
    return selected


def choose_public_plans(frame: pd.DataFrame) -> pd.DataFrame:
    selected = label_scenarios(frame)
    target_r = _num(selected, "planned_target_net_r")
    selected = selected[target_r.between(MIN_TARGET_NET_R, MAX_TARGET_NET_R)].copy()
    geometry = selected["entry_geometry"].astype(str) if "entry_geometry" in selected else pd.Series("", index=selected.index)
    selected["preferred_geometry"] = geometry.eq(GEOMETRY).astype(int)
    selected = selected.sort_values(
        ["state_id", "preferred_geometry", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop_duplicates("state_id", keep="first")
    return selected.sort_values(
        ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def first_signal_per_episode(plans: pd.DataFrame) -> pd.DataFrame:
    return plans.sort_values(
        ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).drop_duplicates(["research_period", "episode_id"], keep="first").sort_values(
        ["order_time_ns", "scenario_priority", "planned_target_net_r", "action_id"],
        ascending=[True, False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def route_account(plans: pd.DataFrame, risk_fraction: float = RISK_FRACTION) -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = first_signal_per_episode(plans)
    busy_until = -1
    consumed: set[tuple[str, str]] = set()
    nav = 1.0
    peak = 1.0
    order_rows: list[dict] = []
    trade_rows: list[dict] = []

    for row in signals.itertuples(index=False):
        record = row._asdict()
        episode_key = (str(record["research_period"]), str(record["episode_id"]))
        if episode_key in consumed:
            continue
        order_time = int(record["order_time_ns"])
        if order_time < busy_until:
            continue
        consumed.add(episode_key)

        outcome = str(record.get("outcome", "UNFILLED"))
        if outcome == "UNFILLED":
            terminal = record.get("order_terminal_time_ns")
            if pd.isna(terminal):
                raise ValueError(f"unfilled action lacks terminal time: {record.get('action_id')}")
            busy_until = int(terminal)
            record.update({"account_busy_until_ns": busy_until, "net_r_num": 0.0, "nav_before": nav, "nav_after": nav})
            order_rows.append(record)
            continue

        resolution = record.get("resolution_time_ns")
        if pd.isna(resolution):
            raise ValueError(f"filled action lacks resolution time: {record.get('action_id')}")
        busy_until = int(resolution)
        net_r = record.get("net_r")
        net_r_num = float(net_r) if pd.notna(net_r) else -1.0
        nav_before = nav
        nav = max(0.0, nav * (1.0 + risk_fraction * net_r_num))
        peak = max(peak, nav)
        drawdown = 1.0 - nav / peak if peak > 0 else 1.0
        record.update({"account_busy_until_ns": busy_until, "net_r_num": net_r_num, "nav_before": nav_before, "nav_after": nav, "drawdown": drawdown})
        order_rows.append(record.copy())
        trade_rows.append(record)

    return pd.DataFrame(order_rows), pd.DataFrame(trade_rows)


def _metric_block(orders: pd.DataFrame, trades: pd.DataFrame, risk_fraction: float = RISK_FRACTION) -> dict:
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    if not trades.empty:
        for value in pd.to_numeric(trades["net_r_num"], errors="coerce").fillna(-1.0):
            nav = max(0.0, nav * (1.0 + risk_fraction * float(value)))
            peak = max(peak, nav)
            max_dd = max(max_dd, 1.0 - nav / peak if peak > 0 else 1.0)
    values = pd.to_numeric(trades["net_r_num"], errors="coerce") if not trades.empty else pd.Series(dtype=float)
    wins = values[values > 0]
    losses = values[values < 0]
    outcomes = orders["outcome"].astype(str) if not orders.empty and "outcome" in orders else pd.Series(dtype=str)
    return {
        "orders": int(len(orders)),
        "unfilled_orders": int(outcomes.eq("UNFILLED").sum()),
        "closed_trades": int(len(trades)),
        "wins": int((values > 0).sum()),
        "win_rate": float((values > 0).mean()) if len(values) else 0.0,
        "sum_net_r": float(values.sum()) if len(values) else 0.0,
        "mean_net_r": float(values.mean()) if len(values) else 0.0,
        "average_win_r": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_r": float(losses.mean()) if len(losses) else 0.0,
        "payoff_ratio": float(wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else None,
        "ending_nav": float(nav),
        "max_drawdown": float(max_dd),
    }


def summarize(source: pd.DataFrame, orders: pd.DataFrame, trades: pd.DataFrame) -> dict:
    periods = sorted(source["research_period"].astype(str).unique(), key=lambda p: int(source.loc[source.research_period.astype(str).eq(p), "order_time_ns"].min()))
    by_period = {p: _metric_block(orders[orders.research_period.astype(str).eq(p)] if not orders.empty else orders, trades[trades.research_period.astype(str).eq(p)] if not trades.empty else trades) for p in periods}
    by_family = {f: _metric_block(orders[orders.scenario_family.astype(str).eq(f)] if not orders.empty else orders, trades[trades.scenario_family.astype(str).eq(f)] if not trades.empty else trades) for f in SCENARIO_PRIORITY}
    by_symbol = {s: _metric_block(orders[orders.symbol.astype(str).eq(s)] if not orders.empty else orders, trades[trades.symbol.astype(str).eq(s)] if not trades.empty else trades) for s in sorted(source.symbol.astype(str).unique())}
    return {
        "policy": "ML_K_CAUSAL_CONTROL_TRANSFER_V1",
        "thresholds_fixed_before_new_fresh_windows": True,
        "decision_uses_symbol_identity": False,
        "decision_uses_outcome_fields": False,
        "fixed_account_rules": {
            "one_global_pending_or_position_slot": True,
            "one_plan_per_causal_episode": True,
            "scale_in_or_out": False,
            "risk_fraction_of_current_nav": RISK_FRACTION,
            "entry_geometry_preference": GEOMETRY,
            "minimum_planned_target_net_r": MIN_TARGET_NET_R,
            "maximum_planned_target_net_r": MAX_TARGET_NET_R,
            "ambiguous_same_minute_barrier": "loss",
        },
        "scenario_thresholds": THRESHOLDS,
        "overall_continuous_account": _metric_block(orders, trades),
        "by_period_standalone": by_period,
        "by_scenario_family": by_family,
        "by_symbol": by_symbol,
    }


def load_actions(root: Path) -> pd.DataFrame:
    files = sorted(root.rglob("departure_actions.csv.gz"))
    if not files:
        raise FileNotFoundError(f"no departure_actions.csv.gz below {root}")
    frames = []
    for file in files:
        frame = pd.read_csv(file)
        frame["research_period"] = file.parent.name
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True, sort=False)
    required = {"action_id", "state_id", "episode_id", "symbol", "order_time_ns", "outcome", "order_terminal_time_ns", "planned_target_net_r"}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    return data


def run(root: Path, output: Path) -> dict:
    actions = load_actions(root)
    plans = choose_public_plans(actions)
    orders, trades = route_account(plans)
    summary = summarize(actions, orders, trades)
    output.mkdir(parents=True, exist_ok=True)
    orders.to_csv(output / "selected_orders.csv", index=False)
    trades.to_csv(output / "closed_trades.csv", index=False)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.root, args.output)


if __name__ == "__main__":
    main()
