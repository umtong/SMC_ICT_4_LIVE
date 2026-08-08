#!/usr/bin/env python3
"""Candidate 15 V41 adaptive daily cross-sectional dispersion router."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v28_adaptive_6h_trend as common

SYMBOLS = common.SYMBOLS
MOMENTUM = "DAILY_DISPERSION_MOMENTUM"
REVERSAL = "DAILY_DISPERSION_REVERSAL"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def existing_advance(root: Path) -> list[int]:
    versions: list[int] = []
    for status_name in ("V33_V38_REPLAY_STATUS.json", "V33_V37_ROUND_STATUS.json"):
        status = root / status_name
        if status.is_file():
            versions.extend(int(value) for value in load_object(status).get("advance_versions", []))
    for version in range(33, 41):
        for path in root.glob(f"V{version}_*_RESULT.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ADVANCE_TO_NAUTILUS" in text and "REJECTED_OR_UNDERPOWERED" not in text and "SKIPPED" not in text:
                versions.append(version)
    return sorted(set(versions))


def resample_hourly(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample("1h", closed="right", label="right", origin="start_day")
    output = pd.DataFrame(index=grouped.size().index)
    output["count"] = grouped.size()
    output["open"] = grouped["open"].first()
    output["high"] = grouped["high"].max()
    output["low"] = grouped["low"].min()
    output["close"] = grouped["close"].last()
    output["quote_volume"] = grouped["quote_volume"].sum()
    output["taker_buy_quote_volume"] = grouped["taker_buy_quote_volume"].sum()
    return output[output["count"] == 12].copy()


def hourly_state(symbol: str, five: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    hourly = resample_hourly(five)
    close = hourly["close"].astype(float)
    previous = close.shift(1)
    true_range = pd.concat([
        hourly["high"] - hourly["low"],
        (hourly["high"] - previous).abs(),
        (hourly["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    hourly["atr_hourly"] = true_range.shift(1).rolling(
        int(rules["hourly_atr_lookback"]),
        min_periods=int(rules["hourly_atr_lookback"]),
    ).mean()
    hourly["return_24h"] = close / close.shift(int(rules["return_horizon_hours"])) - 1.0
    hourly["prior_24h_high"] = hourly["high"].shift(1).rolling(24, min_periods=24).max()
    hourly["prior_24h_low"] = hourly["low"].shift(1).rolling(24, min_periods=24).min()
    hourly["body_fraction"] = (hourly["close"] - hourly["open"]).abs() / (hourly["high"] - hourly["low"]).replace(0.0, np.nan)
    hourly["taker_pressure"] = (2.0 * hourly["taker_buy_quote_volume"] / hourly["quote_volume"].replace(0.0, np.nan) - 1.0).clip(-1.0, 1.0)
    hourly["symbol"] = symbol
    return hourly.replace([np.inf, -np.inf], np.nan)


def daily_events(states: dict[str, pd.DataFrame], rules: dict[str, Any]) -> pd.DataFrame:
    returns = pd.DataFrame({symbol: frame["return_24h"] for symbol, frame in states.items()})
    returns = returns[returns.index.hour == int(rules["decision_hour_utc"])]
    median = returns.median(axis=1)
    residual = returns.sub(median, axis=0)
    absolute = residual.abs()
    leader = absolute.idxmax(axis=1)
    top = absolute.max(axis=1)
    second = absolute.apply(lambda row: row.nlargest(2).iloc[-1] if row.notna().sum() >= 2 else np.nan, axis=1)
    dispersion = returns.std(axis=1, ddof=0)
    prior_mean = dispersion.shift(1).rolling(int(rules["dispersion_lookback_days"]), min_periods=int(rules["dispersion_minimum_prior_days"])).mean()
    prior_std = dispersion.shift(1).rolling(int(rules["dispersion_lookback_days"]), min_periods=int(rules["dispersion_minimum_prior_days"])).std(ddof=0).replace(0.0, np.nan)
    dispersion_z = (dispersion - prior_mean) / prior_std
    rows: list[dict[str, Any]] = []
    for timestamp in returns.index:
        symbol = leader.get(timestamp)
        if not isinstance(symbol, str):
            continue
        gap_fraction = float((top.get(timestamp, np.nan) - second.get(timestamp, np.nan)) / max(float(top.get(timestamp, np.nan)), 1e-12))
        if pd.isna(dispersion_z.get(timestamp)) or float(dispersion_z[timestamp]) < float(rules["dispersion_z_min"]):
            continue
        if gap_fraction < float(rules["residual_leader_gap_fraction"]):
            continue
        event_return = float(returns.at[timestamp, symbol])
        residual_return = float(residual.at[timestamp, symbol])
        if event_return == 0.0 or np.sign(event_return) != np.sign(residual_return):
            continue
        state = states[symbol]
        if timestamp not in state.index:
            continue
        row = state.loc[timestamp]
        if pd.isna(row["atr_hourly"]) or pd.isna(row["prior_24h_high"]) or pd.isna(row["prior_24h_low"]):
            continue
        rows.append({
            "decision_ts": pd.Timestamp(timestamp),
            "symbol": symbol,
            "return_24h": event_return,
            "residual_return_24h": residual_return,
            "residual_abs": abs(residual_return),
            "residual_leader_gap_fraction": gap_fraction,
            "dispersion": float(dispersion[timestamp]),
            "dispersion_z": float(dispersion_z[timestamp]),
            "event_direction": float(np.sign(event_return)),
            "prior_24h_high": float(row["prior_24h_high"]),
            "prior_24h_low": float(row["prior_24h_low"]),
            "prior_atr_hourly": float(row["atr_hourly"]),
        })
    return pd.DataFrame(rows)


def simulate_route(event: pd.Series, hourly: pd.DataFrame, rules: dict[str, Any], route: str) -> dict[str, Any] | None:
    decision = pd.Timestamp(event["decision_ts"])
    direction = float(event["event_direction"]) if route == MOMENTUM else -float(event["event_direction"])
    entry_ts = decision + pd.Timedelta(hours=int(rules["confirmation_hours"]))
    if entry_ts not in hourly.index:
        return None
    confirmation = hourly.loc[entry_ts]
    confirm_return = float(confirmation["close"]) / float(confirmation["open"]) - 1.0
    if direction * confirm_return <= 0.0:
        return None
    if float(confirmation["body_fraction"]) < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if direction * float(confirmation["taker_pressure"]) < float(rules["minimum_directional_taker_pressure"]):
        return None
    entry = float(confirmation["close"])
    atr = float(event["prior_atr_hourly"])
    buffer = float(rules["initial_stop_atr_buffer"]) * atr
    stop = float(event["prior_24h_low"]) - buffer if direction > 0.0 else float(event["prior_24h_high"]) + buffer
    if direction * (entry - stop) <= 0.0:
        return None
    cap = entry_ts + pd.Timedelta(hours=int(rules["maximum_hold_hours"]))
    path = hourly[(hourly.index > entry_ts) & (hourly.index <= cap)]
    if path.empty:
        return None
    stop_current, best_close = stop, entry
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    reason = ""
    updates = 0
    for timestamp, bar in path.iterrows():
        if direction > 0.0 and float(bar["low"]) <= stop_current:
            exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop_current, "TRAILING_STOP"
            break
        if direction < 0.0 and float(bar["high"]) >= stop_current:
            exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop_current, "TRAILING_STOP"
            break
        c = float(bar["close"])
        best_close = max(best_close, c) if direction > 0.0 else min(best_close, c)
        current_atr = float(bar["atr_hourly"]) if pd.notna(bar["atr_hourly"]) else atr
        new_stop = max(stop_current, best_close - float(rules["trailing_stop_atr"]) * current_atr) if direction > 0.0 else min(stop_current, best_close + float(rules["trailing_stop_atr"]) * current_atr)
        if abs(new_stop - stop_current) > 1e-12:
            stop_current = new_stop
            updates += 1
    if exit_ts is None:
        exit_ts, exit_price, reason = pd.Timestamp(path.index[-1]), float(path.iloc[-1]["close"]), "NEXT_DAILY_BOUNDARY"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (float(rules["execution_round_trip_cost_bps"]) + float(rules["funding_and_unmodeled_impact_reserve_bps"])) / 10_000.0
    return {**event.to_dict(), "route": route, "direction": "LONG" if direction > 0.0 else "SHORT", "direction_value": direction, "entry_ts": entry_ts, "exit_ts": exit_ts, "confirmation_return": confirm_return, "confirmation_body_fraction": float(confirmation["body_fraction"]), "confirmation_taker_pressure": float(confirmation["taker_pressure"]), "entry_price": entry, "initial_stop": stop, "final_stop": stop_current, "exit_price": float(exit_price), "exit_reason": reason, "trailing_updates": updates, "holding_minutes": (exit_ts - entry_ts).total_seconds() / 60.0, "gross_return": gross, "net_return": gross - cost}


def build_shadow(events: pd.DataFrame, states: dict[str, pd.DataFrame], rules: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        hourly = states[str(event["symbol"])]
        for route in (MOMENTUM, REVERSAL):
            result = simulate_route(event, hourly, rules, route)
            if result is not None:
                rows.append(result)
    return pd.DataFrame(rows).sort_values(["entry_ts", "route"], kind="stable") if rows else pd.DataFrame()


def matured_stats(shadow: pd.DataFrame, route: str, entry: pd.Timestamp, rules: dict[str, Any]) -> dict[str, Any]:
    lower = entry - pd.Timedelta(days=int(rules["shadow_activation_lookback_days"]))
    sample = shadow[(shadow["route"] == route) & (shadow["exit_ts"] < entry) & (shadow["entry_ts"] >= lower)]
    if sample.empty:
        return {"events": 0, "mean_net_bps": None, "accuracy": None, "std_bps": None}
    return {"events": len(sample.index), "mean_net_bps": float(sample["net_return"].mean() * 10_000.0), "accuracy": float((sample["net_return"] > 0.0).mean()), "std_bps": float(sample["net_return"].std(ddof=0) * 10_000.0)}


def route_shadow(shadow: pd.DataFrame, rules: dict[str, Any]) -> tuple[pd.DataFrame, Counter[str]]:
    accepted: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    if shadow.empty:
        return shadow.copy(), skips
    for decision_ts, group in shadow.groupby("decision_ts", sort=True):
        eligible: list[dict[str, Any]] = []
        for _, row in group.iterrows():
            entry = pd.Timestamp(row["entry_ts"])
            stats = matured_stats(shadow, str(row["route"]), entry, rules)
            if stats["events"] < int(rules["minimum_matured_shadow_events"]):
                skips["INSUFFICIENT_MATURED_HISTORY"] += 1
                continue
            if stats["mean_net_bps"] is None or stats["mean_net_bps"] < float(rules["minimum_shadow_mean_net_bps"]):
                skips["NONPOSITIVE_SHADOW_MEAN"] += 1
                continue
            if stats["accuracy"] is None or stats["accuracy"] < float(rules["minimum_shadow_directional_accuracy"]):
                skips["LOW_SHADOW_ACCURACY"] += 1
                continue
            record = row.to_dict()
            record.update({"shadow_events": stats["events"], "shadow_mean_net_bps": stats["mean_net_bps"], "shadow_accuracy": stats["accuracy"], "shadow_score": float(stats["mean_net_bps"]) / max(float(stats["std_bps"] or 1.0), 1.0)})
            eligible.append(record)
        if eligible:
            eligible.sort(key=lambda item: (-float(item["shadow_score"]), str(item["route"])))
            accepted.append(eligible[0])
            skips["SAME_DECISION_ROUTE_LOSER"] += max(0, len(eligible) - 1)
    return pd.DataFrame(accepted).sort_values("entry_ts", kind="stable").reset_index(drop=True) if accepted else shadow.iloc[0:0].copy(), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    std = float(values.std(ddof=1))
    return None if not math.isfinite(std) or std <= 0.0 else float(values.mean() / (std / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins, losses = values[values > 0.0], values[values < 0.0]
    return None if wins.empty or losses.empty else float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sample = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": 0, "trades_per_day": 0.0, "mean_gross_bps": None, "mean_net_bps": None, "win_rate": None, "payoff_ratio": None, "net_t_stat": None, "positive_months": 0, "active_months": 0, "positive_month_share": 0.0, "route_counts": {}, "symbol_counts": {}, "route_stats": {}}
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    route_stats: dict[str, Any] = {}
    for route, current in sample.groupby("route", sort=True):
        route_stats[str(route)] = {"trades": len(current.index), "mean_net_bps": float(current["net_return"].mean() * 10_000.0), "win_rate": float((current["net_return"] > 0.0).mean()), "payoff_ratio": payoff(current["net_return"]), "net_t_stat": t_stat(current["net_return"])}
    return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": len(sample.index), "trades_per_day": len(sample.index) / max(days, 1), "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0), "mean_net_bps": float(sample["net_return"].mean() * 10_000.0), "win_rate": float((sample["net_return"] > 0.0).mean()), "payoff_ratio": payoff(sample["net_return"]), "net_t_stat": t_stat(sample["net_return"]), "positive_months": int((monthly > 0.0).sum()), "active_months": len(monthly.index), "positive_month_share": float((monthly > 0.0).mean()), "route_counts": {str(k): int(v) for k, v in sample["route"].value_counts().items()}, "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()}, "route_stats": route_stats}


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v41-adaptive-daily-dispersion-v1":
        raise RuntimeError("unexpected V41 protocol")
    output.mkdir(parents=True, exist_ok=True)
    prior_advances = existing_advance(protocol_path.parent)
    if prior_advances:
        summary = {"schema": "candidate-15-v41-summary-v1", "classification": "V41_SKIPPED_EXISTING_NAUTILUS_ADVANCE", "advance_to_nautilus": False, "existing_advance_versions": prior_advances, "decision": "V41 was not evaluated because an earlier route already requires Nautilus promotion."}
        write_json(output / "summary.json", summary)
        (output / "RESULT.md").write_text(f"# Candidate 15 V41\n\n**{summary['classification']}**\n\n- existing_advance_versions: `{prior_advances}`\n", encoding="utf-8")
        return summary
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v41-data-manifest-v1", "files": manifest})
    start, end = date.fromisoformat(protocol["data"]["start"]), date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    states = {symbol: hourly_state(symbol, frame, protocol["fixed_rules"]) for symbol, frame in five_frames.items()}
    events = daily_events(states, protocol["fixed_rules"])
    shadow = build_shadow(events, states, protocol["fixed_rules"])
    selected, skips = route_shadow(shadow, protocol["fixed_rules"])
    events.to_csv(output / "daily_dispersion_events.csv", index=False)
    shadow.to_csv(output / "all_shadow_trades.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    evaluation = protocol["evaluation"]
    summaries = {name: summarize(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"]) for name in ("development", "stability", "july_confirmation", "latest_pulse")}
    development, stability, july, pulse = summaries["development"], summaries["stability"], summaries["july_confirmation"], summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    total = sum(stability["symbol_counts"].values())
    max_symbol_share = max(stability["symbol_counts"].values(), default=0) / max(total, 1)
    checks = {"positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0, "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]), "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]), "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]), "stability_frequency": stability["trades_per_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]), "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0, "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]), "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]), "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]), "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"])}
    advance = all(checks.values())
    classification = "V41_ADAPTIVE_DAILY_DISPERSION_ADVANCE_TO_NAUTILUS" if advance else "V41_ADAPTIVE_DAILY_DISPERSION_REJECTED_OR_UNDERPOWERED"
    decision = "Freeze the prior-only expert activation and implement the daily route in NautilusTrader." if advance else "The fixed adaptive dispersion router did not pass stability, recent confirmation and frequency together. Do not tune it."
    summary = {"schema": "candidate-15-v41-summary-v1", "classification": classification, "advance_to_nautilus": advance, "daily_events": len(events.index), "shadow_trades": len(shadow.index), "selected_trades": len(selected.index), "activation_skips": dict(skips), **summaries, "advance_checks": checks, "maximum_stability_symbol_share": max_symbol_share, "decision": decision}
    write_json(output / "summary.json", summary)
    lines = ["# Candidate 15 V41 — Adaptive daily dispersion", "", f"**{classification}**", "", "At each completed UTC daily boundary, momentum and reversal shadow policies are evaluated. Only a policy with positive fully matured prior 90-day outcomes may trade after a separate one-hour confirmation.", ""]
    for title, name in (("Development", "development"), ("Year-long stability", "stability"), ("July 2026 confirmation", "july_confirmation"), ("Latest August 1-7 pulse", "latest_pulse")):
        r = summaries[name]
        lines.extend([f"## {title}", f"- interval: `{r['start']} -> {r['end_exclusive']}`", f"- trades / day: `{r['trades']} / {r['trades_per_day']}`", f"- gross / net mean: `{r['mean_gross_bps']} / {r['mean_net_bps']}` bp", f"- win rate / payoff: `{r['win_rate']} / {r['payoff_ratio']}`", f"- net t-stat: `{r['net_t_stat']}`", f"- positive months: `{r['positive_months']} / {r['active_months']}`", f"- route counts: `{r['route_counts']}`", f"- route stats: `{r['route_stats']}`", f"- symbol counts: `{r['symbol_counts']}`", ""])
    lines.extend(["## Advance checks", *[f"- {k}: `{v}`" for k, v in checks.items()], "", "## Decision", decision, "", "This is an economic mechanism screen. A pass still requires online shadow accounting and exact NautilusTrader continuous-account execution."])
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
