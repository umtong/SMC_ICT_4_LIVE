#!/usr/bin/env python3
"""Blocked destination/fill/action learning for the coherent v4 action universe.

Every score uses decision-time planned economics.  Actual next-open/limit-fill prices,
fill states, outcomes and resolution data are labels/accounting only.  The router makes
one chronological choice at emission time, then the selected pending order or position
owns the one global account until its causal terminal time.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Sequence
import json
import math

import numpy as np
import pandas as pd

import train_select as base

RISK_FRACTION = 0.03
_ORIGINAL_FEATURE_COLUMNS = base._feature_columns


def feature_columns(frame: pd.DataFrame, *, keep_economics: bool) -> list[str]:
    columns = _ORIGINAL_FEATURE_COLUMNS(frame, keep_economics=keep_economics)
    forbidden_prefixes = (
        "actual_",
        "fill_",
        "resolution_",
        "destination_resolution_",
        "order_terminal_",
    )
    output = [column for column in columns if not str(column).lower().startswith(forbidden_prefixes)]
    if not keep_economics:
        output = [
            column for column in output
            if not str(column).startswith(("planned_account_", "route_obstacle_", "volume_route_target_"))
            and column not in {"entry_geometry", "stop_geometry", "entry_style"}
        ]
    return output


def _read(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    return base._read_universes(root)


def _blocked(frame, target, columns, development, seed):
    return base._blocked_predictions(
        frame,
        np.asarray(target, dtype=int),
        columns,
        development_mask=np.asarray(development, dtype=bool),
        seed=seed,
    )


def attach_destination(actions: pd.DataFrame, states: pd.DataFrame):
    modeling = states[states.destination_label.astype(str).isin(["UPPER_FIRST", "LOWER_FIRST"])].copy().reset_index(drop=True)
    if modeling.empty:
        output = actions.copy()
        output["destination_probability"] = 0.5
        output["destination_disagreement"] = 0.5
        return output, {"resolved_states": 0, "feature_count": 0, "development_base_rate": None, "features": []}
    long_side = modeling.action_side.astype(str).eq("LONG")
    target = ((long_side & modeling.destination_label.eq("UPPER_FIRST")) | (~long_side & modeling.destination_label.eq("LOWER_FIRST"))).astype(int).to_numpy()
    development = modeling.period.astype(str).str.startswith("dev-").to_numpy()
    columns = feature_columns(modeling, keep_economics=False)
    probability, disagreement = _blocked(modeling, target, columns, development, 19817)
    modeling["destination_probability"] = probability
    modeling["destination_disagreement"] = disagreement
    mapping = modeling[["period", "state_id", "destination_probability", "destination_disagreement"]]
    output = actions.merge(mapping, on=["period", "state_id"], how="left")
    base_rate = float(np.mean(target[development])) if development.any() else float(np.mean(target))
    output["destination_probability"] = pd.to_numeric(output.destination_probability, errors="coerce").fillna(base_rate)
    output["destination_disagreement"] = pd.to_numeric(output.destination_disagreement, errors="coerce").fillna(0.35)
    return output, {
        "resolved_states": int(len(modeling)),
        "development_states": int(development.sum()),
        "feature_count": int(len(columns)),
        "development_base_rate": base_rate,
        "features": list(columns),
    }


def attach_fill(actions: pd.DataFrame):
    known = actions.fill_state.astype(str).ne("NO_FUTURE") & actions.order_terminal_time_ns.notna()
    modeling = actions.loc[known].copy().reset_index(drop=True)
    if modeling.empty:
        output = actions.copy(); output["fill_probability"] = 1.0; output["fill_disagreement"] = 0.0
        return output, {"known_actions": 0, "feature_count": 0, "development_base_rate": None, "features": []}
    target = modeling.fill_state.astype(str).str.startswith("FILLED").astype(int).to_numpy()
    development = modeling.period.astype(str).str.startswith("dev-").to_numpy()
    columns = feature_columns(modeling, keep_economics=True)
    probability, disagreement = _blocked(modeling, target, columns, development, 41531)
    modeling["fill_probability"] = probability
    modeling["fill_disagreement"] = disagreement
    output = actions.merge(modeling[["period", "action_id", "fill_probability", "fill_disagreement"]], on=["period", "action_id"], how="left")
    base_rate = float(np.mean(target[development])) if development.any() else float(np.mean(target))
    output["fill_probability"] = pd.to_numeric(output.fill_probability, errors="coerce").fillna(base_rate)
    output["fill_disagreement"] = pd.to_numeric(output.fill_disagreement, errors="coerce").fillna(0.35)
    return output, {
        "known_actions": int(len(modeling)),
        "development_actions": int(development.sum()),
        "feature_count": int(len(columns)),
        "development_base_rate": base_rate,
        "features": list(columns),
    }


def attach_action(actions: pd.DataFrame):
    resolved_names = ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE", "TIME_EXIT"]
    modeling = actions[actions.fill_state.astype(str).str.startswith("FILLED") & actions.outcome.astype(str).isin(resolved_names)].copy().reset_index(drop=True)
    if modeling.empty:
        output = actions.copy(); output["action_probability"] = 0.5; output["action_disagreement"] = 0.5
        return output, {"resolved_actions": 0, "feature_count": 0, "development_base_rate": None, "features": []}
    target = modeling.outcome.astype(str).eq("TARGET_FIRST").astype(int).to_numpy()
    development = modeling.period.astype(str).str.startswith("dev-").to_numpy()
    columns = feature_columns(modeling, keep_economics=True)
    probability, disagreement = _blocked(modeling, target, columns, development, 76213)
    modeling["action_probability"] = probability
    modeling["action_disagreement"] = disagreement
    output = actions.merge(modeling[["period", "action_id", "action_probability", "action_disagreement"]], on=["period", "action_id"], how="left")
    base_rate = float(np.mean(target[development])) if development.any() else float(np.mean(target))
    output["action_probability"] = pd.to_numeric(output.action_probability, errors="coerce").fillna(base_rate)
    output["action_disagreement"] = pd.to_numeric(output.action_disagreement, errors="coerce").fillna(0.35)
    return output, {
        "resolved_actions": int(len(modeling)),
        "development_actions": int(development.sum()),
        "feature_count": int(len(columns)),
        "development_base_rate": base_rate,
        "features": list(columns),
    }


def score(actions: pd.DataFrame) -> pd.DataFrame:
    output = actions.copy()
    p_destination = pd.to_numeric(output.destination_probability, errors="coerce").clip(0.002, 0.998)
    p_action = pd.to_numeric(output.action_probability, errors="coerce").clip(0.002, 0.998)
    p_fill = pd.to_numeric(output.fill_probability, errors="coerce").clip(0.002, 0.998)
    destination_uncertainty = pd.to_numeric(output.destination_disagreement, errors="coerce").fillna(0.35)
    action_uncertainty = pd.to_numeric(output.action_disagreement, errors="coerce").fillna(0.35)
    fill_uncertainty = pd.to_numeric(output.fill_disagreement, errors="coerce").fillna(0.35)
    p_direction_and_action = np.sqrt(p_destination * p_action)
    action_penalty = 0.55 * action_uncertainty + 0.30 * destination_uncertainty + 0.15 * (1.0 - (2.0 * (p_destination - 0.5)).abs())
    conservative_win = (p_direction_and_action - action_penalty).clip(0.002, 0.998)
    conservative_fill = (p_fill - 0.65 * fill_uncertainty).clip(0.002, 0.998)
    target_r = pd.to_numeric(output.planned_account_target_r, errors="coerce")
    stop_r = pd.to_numeric(output.planned_account_stop_r, errors="coerce").fillna(-1.0)
    output["combined_probability"] = p_direction_and_action
    output["conservative_probability"] = conservative_win
    output["conservative_fill_probability"] = conservative_fill
    output["selection_uncertainty"] = action_penalty + 0.35 * fill_uncertainty
    output["robust_expected_r"] = conservative_fill * (conservative_win * target_r + (1.0 - conservative_win) * stop_r)
    safe_target = target_r.clip(lower=-0.999 / RISK_FRACTION)
    safe_stop = stop_r.clip(lower=-0.999 / RISK_FRACTION)
    output["expected_log_growth"] = conservative_fill * (
        conservative_win * np.log1p(RISK_FRACTION * safe_target)
        + (1.0 - conservative_win) * np.log1p(RISK_FRACTION * safe_stop)
    )
    output["planned_break_even_probability"] = (-stop_r / (target_r - stop_r)).replace([np.inf, -np.inf], np.nan)
    return output


def route_account(actions: pd.DataFrame, prefix: str):
    candidates = actions[
        actions.period.astype(str).str.startswith(prefix)
        & pd.to_numeric(actions.robust_expected_r, errors="coerce").gt(0.0)
        & pd.to_numeric(actions.expected_log_growth, errors="coerce").gt(0.0)
        & actions.order_terminal_time_ns.notna()
    ].copy()
    if candidates.empty:
        empty = candidates.copy()
        summary = {"selected_orders": 0, "filled_trades": 0, "wins": 0, "win_rate": None, "mean_net_r": None, "ending_nav": 100000.0, "maximum_drawdown": 0.0}
        return empty, empty, summary
    candidates = candidates.sort_values(["emission_time_ns", "expected_log_growth", "source_timeframe_minutes", "action_id"], ascending=[True, False, False, True])
    selected_rows: list[pd.Series] = []
    occupied_episodes: set[tuple[str, str]] = set()
    busy_until = -1
    for emission_time, group in candidates.groupby("emission_time_ns", sort=True):
        emission_time = int(emission_time)
        if emission_time <= busy_until:
            continue
        mask = [(str(row.period), str(row.episode_id)) not in occupied_episodes for row in group.itertuples()]
        available = group.loc[mask]
        if available.empty:
            continue
        chosen = available.sort_values(["expected_log_growth", "robust_expected_r", "conservative_probability", "source_timeframe_minutes", "action_id"], ascending=[False, False, False, False, True]).iloc[0]
        selected_rows.append(chosen)
        occupied_episodes.add((str(chosen.period), str(chosen.episode_id)))
        busy_until = int(chosen.order_terminal_time_ns)
    orders = pd.DataFrame(selected_rows).reset_index(drop=True) if selected_rows else candidates.iloc[0:0].copy()
    resolved = ["TARGET_FIRST", "STOP_FIRST", "AMBIGUOUS_SAME_MINUTE", "AMBIGUOUS_FILL_BARRIER_SAME_MINUTE", "TIME_EXIT"]
    trades = orders[orders.fill_state.astype(str).str.startswith("FILLED") & orders.outcome.astype(str).isin(resolved)].copy().reset_index(drop=True)
    nav = 100000.0; peak = nav; max_drawdown = 0.0; before=[]; after=[]
    for _, row in trades.iterrows():
        before.append(nav)
        result = _safe_float(row.net_r, 0.0)
        nav *= max(1e-9, 1.0 + RISK_FRACTION * result)
        peak = max(peak, nav); max_drawdown = max(max_drawdown, 1.0 - nav / peak); after.append(nav)
    trades["nav_before"] = before; trades["nav_after"] = after
    wins = int(trades.outcome.astype(str).eq("TARGET_FIRST").sum())
    result = pd.to_numeric(trades.net_r, errors="coerce")
    summary = {
        "selected_orders": int(len(orders)),
        "filled_trades": int(len(trades)),
        "wins": wins,
        "win_rate": wins / len(trades) if len(trades) else None,
        "mean_net_r": float(result.mean()) if len(trades) else None,
        "median_net_r": float(result.median()) if len(trades) else None,
        "ending_nav": float(nav),
        "maximum_drawdown": float(max_drawdown),
        "unfilled_selected_orders": int((~orders.fill_state.astype(str).str.startswith("FILLED")).sum()),
        "by_period": {
            str(period): {"orders": int(len(group)), "filled": int(group.fill_state.astype(str).str.startswith("FILLED").sum()), "wins": int(group.outcome.astype(str).eq("TARGET_FIRST").sum()), "mean_net_r": float(pd.to_numeric(group.loc[group.fill_state.astype(str).str.startswith("FILLED"), "net_r"], errors="coerce").mean()) if group.fill_state.astype(str).str.startswith("FILLED").any() else None}
            for period, group in orders.groupby("period")
        },
        "by_geometry": {
            f"{entry}|{stop}": {"orders": int(len(group)), "filled": int(group.fill_state.astype(str).str.startswith("FILLED").sum()), "win_rate": float(group.loc[group.fill_state.astype(str).str.startswith("FILLED"), "outcome"].astype(str).eq("TARGET_FIRST").mean()) if group.fill_state.astype(str).str.startswith("FILLED").any() else None}
            for (entry, stop), group in orders.groupby(["entry_geometry", "stop_geometry"])
        },
    }
    return orders, trades, summary


def _safe_float(value: Any, default: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def nearest_cases(scored: pd.DataFrame, trades: pd.DataFrame, path: Path):
    columns = feature_columns(scored, keep_economics=True)
    numeric = [column for column in columns if pd.api.types.is_numeric_dtype(scored[column])]
    development = scored[scored.period.astype(str).str.startswith("dev-")].copy()
    if trades.empty or development.empty or not numeric:
        pd.DataFrame().to_csv(path, index=False); return
    matrix = development[numeric].apply(pd.to_numeric, errors="coerce")
    median = matrix.median(); mad = (matrix - median).abs().median(); scale = (1.4826 * mad).replace(0.0, 1.0).fillna(1.0)
    x = ((matrix.fillna(median) - median) / scale).clip(-8, 8).to_numpy(float)
    records=[]
    for _, row in trades.iterrows():
        vector = ((pd.to_numeric(row[numeric], errors="coerce").fillna(median) - median) / scale).clip(-8, 8).to_numpy(float)
        distance = np.sqrt(np.mean((x-vector)**2, axis=1)); rank=0
        for position in np.argsort(distance):
            case=development.iloc[int(position)]
            if str(case.period)==str(row.period): continue
            rank+=1; records.append({"selected_action_id":row.action_id,"selected_period":row.period,"neighbor_rank":rank,"distance":float(distance[position]),"neighbor_action_id":case.action_id,"neighbor_period":case.period,"neighbor_branch":case.narrative_branch,"neighbor_entry":case.entry_geometry,"neighbor_stop":case.stop_geometry,"neighbor_outcome":case.outcome,"neighbor_net_r":case.net_r})
            if rank>=12:break
    pd.DataFrame(records).to_csv(path,index=False)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    actions,states=_read(args.root);actions,destination_summary=attach_destination(actions,states);actions,fill_summary=attach_fill(actions);actions,action_summary=attach_action(actions);scored=score(actions);scored.to_csv(args.output/'scored_action_universe.csv',index=False)
    dev_orders,dev_trades,dev_summary=route_account(scored,'dev-');eval_orders,eval_trades,eval_summary=route_account(scored,'eval-')
    dev_orders.to_csv(args.output/'development_oof_selected_orders.csv',index=False);eval_orders.to_csv(args.output/'evaluation_selected_orders.csv',index=False);dev_trades.to_csv(args.output/'development_oof_account_trades.csv',index=False);eval_trades.to_csv(args.output/'evaluation_account_trades.csv',index=False)
    nearest_cases(scored,pd.concat([dev_trades,eval_trades],ignore_index=True,sort=False),args.output/'selected_trade_neighbors.csv')
    summary={"destination_model":destination_summary,"fill_model":fill_summary,"action_model":action_summary,"development_oof_account":dev_summary,"evaluation_account":eval_summary,"action_universe":len(actions),"state_universe":len(states),"selection":"positive conservative expected log growth using planned decision-time economics; one global pending order or position","symbol_in_model":False,"period_in_model":False,"actual_fill_in_model":False,"risk_fraction":RISK_FRACTION}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=='__main__':main()
