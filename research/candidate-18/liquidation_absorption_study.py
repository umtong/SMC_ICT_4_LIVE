#!/usr/bin/env python3
"""Causal delayed-entry study for forced-basis liquidation absorption."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import v9_tardis_liquidation_study as upstream
from liquidation_absorption_logic import LiquidationEvent, MinuteObservation, evaluate_confirmation

POLICIES = ("OPEN_RECLAIM", "TWO_MINUTE_ABSORPTION", "VWAP_RECLAIM")
CONFIRMATION_WINDOW_MINUTES = 10
MAX_HOLD_MINUTES = 60
COST_RATES = (0.0020, 0.0030)


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _true_range(group: pd.DataFrame) -> pd.Series:
    previous = group["perp_close"].shift(1)
    return pd.concat([
        group["perp_high"] - group["perp_low"],
        (group["perp_high"] - previous).abs(),
        (group["perp_low"] - previous).abs(),
    ], axis=1).max(axis=1)


def _directional_basis(row: pd.Series, direction: int) -> tuple[float, float]:
    perp_scale = max(float(row["perp_spot_basis_scale"]), 1e-6)
    mark_scale = max(float(row["mark_index_basis_scale"]), 1e-6)
    return (
        direction * (float(row["perp_spot_basis"]) - float(row["perp_spot_basis_center"])) / perp_scale,
        direction * (float(row["mark_index_basis"]) - float(row["mark_index_basis_center"])) / mark_scale,
    )


def _observation(row: pd.Series, direction: int) -> MinuteObservation | None:
    quote = _finite(row.get("perp_quote_volume"))
    taker = _finite(row.get("perp_taker_buy_quote"))
    if quote is None or taker is None or quote <= 0.0:
        return None
    perp_z, mark_z = _directional_basis(row, direction)
    values = [row["perp_high"], row["perp_low"], row["perp_close"], perp_z, mark_z]
    if any(_finite(value) is None for value in values):
        return None
    return MinuteObservation(
        high=float(row["perp_high"]), low=float(row["perp_low"]),
        close=float(row["perp_close"]), aggressor_imbalance=2.0 * taker / quote - 1.0,
        perp_basis_z_directional=perp_z, mark_basis_z_directional=mark_z,
    )


def _simulate(*, side: int, entry: float, stop: float, target: float,
              future: pd.DataFrame, cost_rate: float) -> dict[str, Any]:
    risk_rate = abs(entry - stop) / entry + cost_rate
    if risk_rate <= 0.0:
        raise ValueError("risk rate must be positive")
    exit_price = float(future.iloc[-1]["perp_close"]) if not future.empty else entry
    exit_reason = "MAX_HOLD"
    exit_minute = future.index[-1] if not future.empty else None
    for minute, row in future.iterrows():
        high, low = float(row["perp_high"]), float(row["perp_low"])
        stop_hit = low <= stop if side > 0 else high >= stop
        target_hit = high >= target if side > 0 else low <= target
        if stop_hit:
            exit_price, exit_reason, exit_minute = stop, "STOP_FIRST_CONSERVATIVE", minute
            break
        if target_hit:
            exit_price, exit_reason, exit_minute = target, "TARGET", minute
            break
    gross = side * (exit_price / entry - 1.0)
    net = gross - cost_rate
    return {
        "exit_price": exit_price, "exit_reason": exit_reason, "exit_minute": exit_minute,
        "gross_return": gross, "net_return": net, "net_r": net / risk_rate,
        "planned_loss_rate": risk_rate, "win": net > 0.0,
    }


def _event_record(event_row: pd.Series, group: pd.DataFrame, policy: str) -> dict[str, Any] | None:
    moment = pd.Timestamp(event_row["minute"])
    if moment not in group.index:
        return None
    position = group.index.get_loc(moment)
    if not isinstance(position, (int, np.integer)) or position < 31:
        return None
    event = group.iloc[position]
    prior = group.iloc[position - 30:position]
    atr = float(_true_range(group.iloc[position - 31:position + 1]).iloc[:-1].mean())
    if not math.isfinite(atr) or atr <= 0.0:
        atr = float(event["perp_high"] - event["perp_low"])
    direction = int(event_row["event_direction"])
    objective = float(prior["perp_low"].min() if direction > 0 else prior["perp_high"].max())
    state = LiquidationEvent(
        event_direction=direction, event_open=float(event["perp_open"]),
        event_high=float(event["perp_high"]), event_low=float(event["perp_low"]),
        event_close=float(event["perp_close"]), rolling_vwap=float(event["rolling_vwap_4h"]),
        pre_event_objective=objective, atr=atr,
        perp_basis_z_directional=float(event_row["perp_basis_z_directional"]),
        mark_basis_z_directional=float(event_row["mark_basis_z_directional"]),
    )
    later = group.iloc[position + 1:position + 1 + CONFIRMATION_WINDOW_MINUTES]
    observations: list[MinuteObservation] = []
    confirmation = None
    confirmation_minute = None
    confirmation_position = None
    for offset, (minute, row) in enumerate(later.iterrows(), start=1):
        observation = _observation(row, direction)
        if observation is None:
            continue
        observations.append(observation)
        decision = evaluate_confirmation(policy, state, observations)
        if decision.confirmed:
            confirmation, confirmation_minute, confirmation_position = decision, minute, position + offset
            break
    if confirmation is None or confirmation_position is None:
        return None
    entry, stop, target = float(confirmation.entry), float(confirmation.stop), float(confirmation.target)
    side = -direction
    future = group.iloc[confirmation_position + 1:confirmation_position + 1 + MAX_HOLD_MINUTES]
    if future.empty:
        return None
    base = {
        "policy": policy, "symbol": str(event_row["symbol"]), "event_minute": moment,
        "confirmation_minute": confirmation_minute,
        "confirmation_delay_minutes": int((pd.Timestamp(confirmation_minute) - moment) / pd.Timedelta(minutes=1)),
        "event_direction": direction, "side": side, "regime": str(event_row["regime"]),
        "entry": entry, "stop": stop, "target": target, "atr": atr,
        "risk_price_fraction": abs(entry - stop) / entry,
        "target_r_gross": abs(target - entry) / abs(entry - stop),
        "cluster_symbol_count": int(event_row["cluster_symbol_count"]),
        "liq_share_of_perp_volume": float(event_row["liq_share_of_perp_volume"]),
        "oi_change_15m": float(event_row["oi_change_15m"]),
        "event_perp_basis_z": float(event_row["perp_basis_z_directional"]),
        "event_mark_basis_z": float(event_row["mark_basis_z_directional"]),
        "confirmation_reason": confirmation.reason,
    }
    for cost_rate in COST_RATES:
        result = _simulate(side=side, entry=entry, stop=stop, target=target,
                           future=future, cost_rate=cost_rate)
        suffix = f"{int(round(cost_rate * 10_000))}bps"
        for key, value in result.items():
            base[f"{key}_{suffix}"] = value
    return base


def _summary(frame: pd.DataFrame, cost_rate: float) -> dict[str, Any]:
    suffix = f"{int(round(cost_rate * 10_000))}bps"
    if frame.empty:
        return {"trades": 0, "wins": 0, "win_rate": None, "profit_factor": None,
                "mean_net_r": None, "median_net_r": None, "total_net_r": 0.0,
                "symbols": {}, "active_days": 0}
    returns = frame[f"net_return_{suffix}"].astype(float)
    net_r = frame[f"net_r_{suffix}"].astype(float)
    wins, losses = returns[returns > 0.0], returns[returns < 0.0]
    gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
    return {
        "trades": int(len(frame)), "wins": int((returns > 0.0).sum()),
        "win_rate": float((returns > 0.0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
        "mean_net_r": float(net_r.mean()), "median_net_r": float(net_r.median()),
        "total_net_r": float(net_r.sum()), "mean_net_return": float(returns.mean()),
        "median_planned_loss_rate": float(frame[f"planned_loss_rate_{suffix}"].astype(float).median()),
        "target_rate": float((frame[f"exit_reason_{suffix}"] == "TARGET").mean()),
        "stop_rate": float((frame[f"exit_reason_{suffix}"] == "STOP_FIRST_CONSERVATIVE").mean()),
        "symbols": {str(k): int(v) for k, v in frame["symbol"].value_counts().sort_index().items()},
        "active_days": int(pd.to_datetime(frame["event_minute"]).dt.date.nunique()),
        "largest_winner_share": float(wins.max() / gross_profit) if gross_profit > 0.0 else None,
    }


def _year(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    return frame if frame.empty else frame[pd.to_datetime(frame["event_minute"]).dt.year == year]


def _robust_pass(a: dict[str, Any], b: dict[str, Any]) -> bool:
    def passes(s: dict[str, Any]) -> bool:
        return (int(s.get("trades", 0)) >= 3 and s.get("profit_factor") is not None
                and float(s["profit_factor"]) > 1.0 and s.get("mean_net_r") is not None
                and float(s["mean_net_r"]) > 0.0 and s.get("win_rate") is not None
                and float(s["win_rate"]) >= 0.50)
    return passes(a) and passes(b)


def _json_safe(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def run(cache: Path, output: Path) -> dict[str, Any]:
    days = upstream._months(upstream.START_MONTH, upstream.END_MONTH)
    requests = [(symbol, day) for day in days for symbol in upstream.SYMBOLS]
    obtained: dict[tuple[str, date], dict[str, Path]] = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(upstream.obtain_day, symbol, day, cache): (symbol, day)
                   for symbol, day in requests}
        for future in as_completed(futures):
            obtained[futures[future]] = future.result()
    panels = [upstream.build_day(symbol, day, obtained[(symbol, day)])
              for day in days for symbol in upstream.SYMBOLS]
    panel = upstream.apply_causal_event_thresholds(pd.concat(panels, ignore_index=True))
    events = upstream.classify_and_score(panel)
    forced = events[events["regime"] == "FORCED_BASIS_DISLOCATION"].copy()
    lookups = {(str(symbol), str(day)): group.sort_values("minute", kind="stable").set_index("minute")
               for (symbol, day), group in panel.groupby(["symbol", "sample_day"], sort=False)}
    records: list[dict[str, Any]] = []
    for record in forced.to_dict(orient="records"):
        event = pd.Series(record)
        group = lookups.get((str(event["symbol"]), str(event["sample_day"])))
        if group is None:
            continue
        for policy in POLICIES:
            trade = _event_record(event, group, policy)
            if trade is not None:
                records.append(trade)
    trades = pd.DataFrame(records)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    forced.to_csv(output / "forced_basis_events.csv", index=False)
    policies: dict[str, Any] = {}
    promoted: list[str] = []
    for policy in POLICIES:
        current = trades[trades["policy"] == policy] if not trades.empty else trades
        summaries: dict[str, Any] = {}
        for cost_rate in COST_RATES:
            label = f"{int(round(cost_rate * 10_000))}bps"
            y22, y23 = _summary(_year(current, 2022), cost_rate), _summary(_year(current, 2023), cost_rate)
            summaries[label] = {"all": _summary(current, cost_rate), "2022": y22, "2023": y23,
                                "robust_pass": _robust_pass(y22, y23)}
        promote = bool(summaries["20bps"]["robust_pass"])
        if promote:
            promoted.append(policy)
        policies[policy] = {"confirmation_count": int(len(current)), "summaries": summaries,
                            "promote": promote}
    decision = ("PROMOTE_FIXED_LIQUIDATION_ABSORPTION_POLICY_TO_NAUTILUS" if len(promoted) == 1
                else "PROMOTE_MULTIPLE_POLICIES_ONLY_AS_DISTINCT_STATE_FAMILIES" if promoted
                else "DISCARD_DELAYED_FORCED_BASIS_REVERSAL; NO_POLICY_SURVIVED_BOTH_YEARS")
    result = {
        "schema": "candidate-18-v11-liquidation-absorption-study-v1",
        "role": "causal mechanism screen only; no account or NAV claim",
        "source_reuse": {"branch": "research/candidate-16-v9-liquidation-vwap-basis",
                         "module": "research/candidate-16/v9_tardis_liquidation_study.py",
                         "source_sha": "e33e7edf478cfcc70ed2761fb33522c50c766667"},
        "event_family": "FORCED_BASIS_DISLOCATION", "event_count": int(len(forced)),
        "confirmation_window_minutes": CONFIRMATION_WINDOW_MINUTES,
        "max_hold_minutes": MAX_HOLD_MINUTES, "cost_rates": list(COST_RATES),
        "policies": policies, "promoted_policies": promoted, "promote": bool(promoted),
        "decision": decision,
        "validity": {"event_minute_is_context_not_entry": True,
                     "confirmation_is_strictly_later": True,
                     "basis_compression_is_relative_to_event_state": True,
                     "stop_is_event_or_intervening_extreme_plus_atr_buffer": True,
                     "target_is_first_favorable_pre_event_vwap_or_range_objective_capped_at_1p5r": True,
                     "same_bar_stop_target_collision_resolves_to_stop": True,
                     "events_remain_globally_declustered": True, "no_parameter_grid": True},
    }
    safe = _json_safe(result)
    (output / "study.json").write_text(json.dumps(safe, indent=2, sort_keys=True, allow_nan=False) + "\n",
                                       encoding="utf-8")
    return safe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.cache.resolve(), args.output.resolve())
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
