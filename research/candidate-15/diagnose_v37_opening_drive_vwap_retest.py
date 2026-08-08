#!/usr/bin/env python3
"""Candidate 15 V37 opening-drive first VWAP-retest screen."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v28_adaptive_6h_trend as common

UTC = timezone.utc
SYMBOLS = common.SYMBOLS
BREADTH = "BREADTH_OPENING_DRIVE"
RELATIVE = "RELATIVE_OPENING_DRIVE"


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


def hourly_atr(frame: pd.DataFrame, lookback: int) -> pd.Series:
    grouped = frame.resample("1h", closed="right", label="right", origin="start_day")
    hourly = pd.DataFrame(index=grouped.size().index)
    hourly["count"] = grouped.size()
    hourly["high"] = grouped["high"].max()
    hourly["low"] = grouped["low"].min()
    hourly["close"] = grouped["close"].last()
    hourly = hourly[hourly["count"] == 12]
    previous = hourly["close"].shift(1)
    tr = pd.concat([
        hourly["high"] - hourly["low"],
        (hourly["high"] - previous).abs(),
        (hourly["low"] - previous).abs(),
    ], axis=1).max(axis=1)
    return tr.shift(1).rolling(lookback, min_periods=lookback).mean()


def daily_opening_records(symbol: str, frame: pd.DataFrame, protocol: dict[str, Any]) -> pd.DataFrame:
    rules = protocol["fixed_rules"]
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    atr = hourly_atr(frame, int(rules["atr_lookback_hours"]))
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor < end:
        day_start = pd.Timestamp(datetime(cursor.year, cursor.month, cursor.day, tzinfo=UTC))
        opening_end = day_start + pd.Timedelta(hours=int(rules["opening_drive_hours"]))
        opening = frame[(frame.index > day_start) & (frame.index <= opening_end)]
        if len(opening.index) != int(rules["opening_drive_hours"]) * 12:
            cursor += timedelta(days=1)
            continue
        open_px = float(opening.iloc[0]["open"])
        close_px = float(opening.iloc[-1]["close"])
        high = float(opening["high"].max())
        low = float(opening["low"].min())
        atr_value = atr.get(day_start, np.nan)
        if pd.isna(atr_value):
            cursor += timedelta(days=1)
            continue
        move = close_px - open_px
        direction = float(np.sign(move))
        rows.append({
            "symbol": symbol,
            "day_start": day_start,
            "opening_end": opening_end,
            "session_open": open_px,
            "opening_close": close_px,
            "opening_high": high,
            "opening_low": low,
            "opening_direction": direction,
            "opening_move_atr": abs(move) / max(float(atr_value), 1e-12),
            "opening_quote_volume": float(opening["quote_volume"].sum()),
            "prior_atr_24h": float(atr_value),
        })
        cursor += timedelta(days=1)
    if not rows:
        return pd.DataFrame()
    output = pd.DataFrame(rows).sort_values("day_start", kind="stable")
    lookback = int(rules["opening_volume_lookback_days"])
    output["prior_opening_volume_median"] = output["opening_quote_volume"].shift(1).rolling(
        lookback, min_periods=max(10, lookback // 2)
    ).median()
    output["opening_volume_ratio"] = output["opening_quote_volume"] / output[
        "prior_opening_volume_median"
    ].replace(0.0, np.nan)
    return output.replace([np.inf, -np.inf], np.nan)


def add_cross_market_state(records: dict[str, pd.DataFrame], rules: dict[str, Any]) -> dict[str, pd.DataFrame]:
    moves = pd.DataFrame({symbol: frame.set_index("day_start")["opening_move_atr"] * frame.set_index("day_start")["opening_direction"] for symbol, frame in records.items()})
    directions = np.sign(moves)
    positive = (directions > 0.0).mean(axis=1)
    negative = (directions < 0.0).mean(axis=1)
    median = moves.median(axis=1)
    residual = moves.sub(median, axis=0)
    absolute = residual.abs()
    top = absolute.max(axis=1)
    second = absolute.apply(lambda row: row.nlargest(2).iloc[-1] if row.notna().sum() >= 2 else np.nan, axis=1)
    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in records.items():
        current = frame.set_index("day_start").copy()
        current["directional_breadth"] = np.where(
            current["opening_direction"] > 0.0, positive, negative
        )
        current["residual_drive_atr"] = residual[symbol]
        current["residual_leader_gap_atr"] = top - second
        current["is_absolute_residual_leader"] = absolute[symbol] >= top - 1e-12
        breadth = current["directional_breadth"] >= float(rules["breadth_fraction_min"])
        relative = (
            ~breadth
            & current["is_absolute_residual_leader"].fillna(False)
            & (current["opening_move_atr"] >= float(rules["relative_drive_atr_min"]))
            & (current["residual_leader_gap_atr"] >= float(rules["relative_leader_gap_atr_min"]))
            & (np.sign(current["residual_drive_atr"]) == current["opening_direction"])
        )
        current["route"] = "UNRESOLVED"
        current.loc[relative, "route"] = RELATIVE
        current.loc[breadth, "route"] = BREADTH
        output[symbol] = current.reset_index()
    return output


def anchored_vwap(frame: pd.DataFrame) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    notional = frame["quote_volume"].astype(float)
    return (typical * notional).cumsum() / notional.cumsum().replace(0.0, np.nan)


def fifteen_bars(day: pd.DataFrame) -> pd.DataFrame:
    grouped = day.resample("15min", closed="right", label="right", origin="start_day")
    out = pd.DataFrame(index=grouped.size().index)
    out["count"] = grouped.size()
    out["open"] = grouped["open"].first()
    out["high"] = grouped["high"].max()
    out["low"] = grouped["low"].min()
    out["close"] = grouped["close"].last()
    out["quote_volume"] = grouped["quote_volume"].sum()
    out["taker_buy_quote_volume"] = grouped["taker_buy_quote_volume"].sum()
    return out[out["count"] == 3].copy()


def candidate_for_day(item: pd.Series, five: pd.DataFrame, rules: dict[str, Any]) -> dict[str, Any] | None:
    direction = float(item["opening_direction"])
    if direction == 0.0:
        return None
    day_start = pd.Timestamp(item["day_start"])
    day_end = day_start + pd.Timedelta(days=1)
    day = five[(five.index > day_start) & (five.index <= day_end)].copy()
    if len(day.index) < 280:
        return None
    day["session_vwap"] = anchored_vwap(day)
    bars15 = fifteen_bars(day)
    vwap_at_15 = day["session_vwap"].reindex(bars15.index, method="ffill")
    bars15["session_vwap"] = vwap_at_15
    bars15["body_fraction"] = (bars15["close"] - bars15["open"]).abs() / (
        bars15["high"] - bars15["low"]
    ).replace(0.0, np.nan)
    bars15["taker_pressure"] = (
        2.0 * bars15["taker_buy_quote_volume"] / bars15["quote_volume"].replace(0.0, np.nan) - 1.0
    ).clip(-1.0, 1.0)
    interaction_start = day_start + pd.Timedelta(hours=int(rules["interaction_start_hour_utc"]))
    last_entry = day_start + pd.Timedelta(hours=int(rules["last_entry_hour_utc"]))
    scan = bars15[(bars15.index > interaction_start) & (bars15.index < last_entry)]
    interaction_ts: pd.Timestamp | None = None
    interaction: pd.Series | None = None
    for timestamp, bar in scan.iterrows():
        vwap = float(bar["session_vwap"])
        touch = float(bar["low"]) <= vwap <= float(bar["high"])
        close_distance = abs(float(bar["close"]) - vwap) / max(float(item["prior_atr_24h"]), 1e-12)
        if not touch and close_distance > float(rules["maximum_vwap_distance_atr"]):
            continue
        if direction * (float(bar["close"]) - vwap) <= 0.0:
            continue
        if direction * (float(bar["close"]) - float(item["session_open"])) <= 0.0:
            continue
        if direction * float(bar["taker_pressure"]) > -float(rules["minimum_opposite_interaction_pressure"]):
            continue
        interaction_ts = pd.Timestamp(timestamp)
        interaction = bar
        break
    if interaction_ts is None or interaction is None:
        return None
    confirmation_ts = interaction_ts + pd.Timedelta(minutes=int(rules["confirmation_minutes"]))
    if confirmation_ts not in bars15.index:
        return None
    confirmation = bars15.loc[confirmation_ts]
    confirm_return = float(confirmation["close"]) / float(confirmation["open"]) - 1.0
    if direction * confirm_return <= 0.0:
        return None
    if direction * (float(confirmation["close"]) - float(confirmation["session_vwap"])) <= 0.0:
        return None
    if float(confirmation["body_fraction"]) < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if direction * float(confirmation["taker_pressure"]) < float(rules["minimum_directional_taker_pressure"]):
        return None
    entry = float(confirmation["close"])
    target = float(item["opening_high"] if direction > 0.0 else item["opening_low"])
    target_space_bps = direction * (target / entry - 1.0) * 10_000.0
    if target_space_bps < float(rules["minimum_net_target_space_bps"]):
        return None
    buffer = float(rules["stop_atr_buffer"]) * float(item["prior_atr_24h"])
    stop = float(interaction["low"]) - buffer if direction > 0.0 else float(interaction["high"]) + buffer
    if direction * (entry - stop) <= 0.0:
        return None
    path = five[(five.index > confirmation_ts) & (five.index <= day_end)]
    if path.empty:
        return None
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason = ""
    for timestamp, bar in path.iterrows():
        if direction > 0.0:
            if float(bar["low"]) <= stop:
                exit_ts, exit_price, exit_reason = pd.Timestamp(timestamp), stop, "INTERACTION_INVALIDATED"
                break
            if float(bar["high"]) >= target:
                exit_ts, exit_price, exit_reason = pd.Timestamp(timestamp), target, "OPENING_EXTREME_REACHED"
                break
        else:
            if float(bar["high"]) >= stop:
                exit_ts, exit_price, exit_reason = pd.Timestamp(timestamp), stop, "INTERACTION_INVALIDATED"
                break
            if float(bar["low"]) <= target:
                exit_ts, exit_price, exit_reason = pd.Timestamp(timestamp), target, "OPENING_EXTREME_REACHED"
                break
    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        exit_reason = "UTC_DAY_END"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (float(rules["execution_round_trip_cost_bps"]) + float(rules["funding_and_unmodeled_impact_reserve_bps"])) / 10_000.0
    quality = float(item["opening_move_atr"]) * float(item["opening_volume_ratio"])
    quality *= 1.0 + float(item["directional_breadth"]) if item["route"] == BREADTH else 1.0 + abs(float(item["residual_drive_atr"]))
    return {
        "day_start": day_start,
        "interaction_ts": interaction_ts,
        "entry_ts": confirmation_ts,
        "exit_ts": exit_ts,
        "symbol": str(item["symbol"]),
        "route": str(item["route"]),
        "route_priority": 2 if item["route"] == BREADTH else 1,
        "direction": "LONG" if direction > 0.0 else "SHORT",
        "direction_value": direction,
        "opening_move_atr": float(item["opening_move_atr"]),
        "opening_volume_ratio": float(item["opening_volume_ratio"]),
        "directional_breadth": float(item["directional_breadth"]),
        "residual_drive_atr": float(item["residual_drive_atr"]),
        "interaction_taker_pressure": float(interaction["taker_pressure"]),
        "confirmation_return": confirm_return,
        "confirmation_body_fraction": float(confirmation["body_fraction"]),
        "confirmation_taker_pressure": float(confirmation["taker_pressure"]),
        "entry_price": entry,
        "initial_stop": stop,
        "target_price": target,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "holding_minutes": (exit_ts - confirmation_ts).total_seconds() / 60.0,
        "rank_score": quality,
        "gross_return": gross,
        "net_return": gross - cost,
    }


def build_candidates(records: dict[str, pd.DataFrame], five_frames: dict[str, pd.DataFrame], rules: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in records.items():
        eligible = frame[
            (frame["route"] != "UNRESOLVED")
            & (frame["opening_move_atr"] >= float(rules["opening_drive_atr_min"]))
            & (frame["opening_volume_ratio"] >= float(rules["minimum_opening_volume_ratio"]))
        ]
        for _, item in eligible.iterrows():
            result = candidate_for_day(item, five_frames[symbol], rules)
            if result is not None:
                rows.append(result)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["entry_ts", "route_priority", "rank_score", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )


def arbitrate(frame: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_TIME_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    return (pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[0:0].copy()), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    std = float(values.std(ddof=1))
    if not math.isfinite(std) or std <= 0.0:
        return None
    return float(values.mean() / (std / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins, losses = values[values > 0.0], values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sample = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": 0, "trades_per_day": 0.0, "mean_gross_bps": None, "mean_net_bps": None, "win_rate": None, "payoff_ratio": None, "net_t_stat": None, "positive_months": 0, "active_months": 0, "positive_month_share": 0.0, "route_stats": {}, "symbol_counts": {}, "exit_reasons": {}}
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    route_stats: dict[str, Any] = {}
    for route, current in sample.groupby("route", sort=True):
        route_stats[str(route)] = {"trades": len(current.index), "mean_net_bps": float(current["net_return"].mean() * 10_000.0), "win_rate": float((current["net_return"] > 0.0).mean()), "payoff_ratio": payoff(current["net_return"]), "net_t_stat": t_stat(current["net_return"])}
    return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": len(sample.index), "trades_per_day": len(sample.index) / max(days, 1), "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0), "mean_net_bps": float(sample["net_return"].mean() * 10_000.0), "win_rate": float((sample["net_return"] > 0.0).mean()), "payoff_ratio": payoff(sample["net_return"]), "net_t_stat": t_stat(sample["net_return"]), "positive_months": int((monthly > 0.0).sum()), "active_months": len(monthly.index), "positive_month_share": float((monthly > 0.0).mean()), "route_stats": route_stats, "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()}, "exit_reasons": {str(k): int(v) for k, v in sample["exit_reason"].value_counts().items()}}


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v37-opening-drive-vwap-retest-v1":
        raise RuntimeError("unexpected V37 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v37-data-manifest-v1", "files": manifest})
    start, end = date.fromisoformat(protocol["data"]["start"]), date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    records = {symbol: daily_opening_records(symbol, frame, protocol) for symbol, frame in five_frames.items()}
    records = add_cross_market_state(records, protocol["fixed_rules"])
    candidates = build_candidates(records, five_frames, protocol["fixed_rules"])
    selected, skips = arbitrate(candidates)
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    evaluation = protocol["evaluation"]
    summaries = {name: summarize(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"]) for name in ("development", "stability", "july_confirmation", "latest_pulse")}
    development, stability, july, pulse = summaries["development"], summaries["stability"], summaries["july_confirmation"], summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    total = sum(stability["symbol_counts"].values())
    max_symbol_share = max(stability["symbol_counts"].values(), default=0) / max(total, 1)
    checks = {
        "positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0,
        "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]),
        "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]),
        "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]),
        "stability_frequency": stability["trades_per_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]),
        "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0,
        "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]),
        "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]),
        "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]),
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
    }
    cross_split: dict[str, Any] = {}
    for route in (BREADTH, RELATIVE):
        splits = {name: summary["route_stats"].get(route) for name, summary in summaries.items()}
        cross_split[route] = {"positive_across_all_declared_splits": all(record is not None and record["mean_net_bps"] > 0.0 for record in splits.values()), "splits": splits}
    survivors = [route for route, record in cross_split.items() if record["positive_across_all_declared_splits"]]
    advance = all(checks.values()) and bool(survivors)
    classification = "V37_OPENING_DRIVE_VWAP_RETEST_ADVANCE_TO_NAUTILUS" if advance else "V37_OPENING_DRIVE_VWAP_RETEST_REJECTED_OR_UNDERPOWERED"
    decision = f"Cross-split-positive fixed routes: {survivors}. " + ("Freeze and implement the survivor in NautilusTrader." if advance else "Do not tune opening hours, VWAP interaction, flow, confirmation, stop or target rules.")
    summary = {"schema": "candidate-15-v37-summary-v1", "classification": classification, "advance_to_nautilus": advance, "executable_candidates": len(candidates.index), "selected_trades": len(selected.index), "arbitration_skips": dict(skips), **summaries, "advance_checks": checks, "cross_split_routes": cross_split, "surviving_routes": survivors, "maximum_stability_symbol_share": max_symbol_share, "decision": decision}
    write_json(output / "summary.json", summary)
    lines = ["# Candidate 15 V37 — Opening-drive first VWAP retest", "", f"**{classification}**", "", "A completed high-activity four-hour opening drive creates context. The first later VWAP interaction must show opposite flow without price acceptance, followed by a separate fifteen-minute reacceptance.", ""]
    for title, name in (("Development", "development"), ("Year-long stability", "stability"), ("July 2026 confirmation", "july_confirmation"), ("Latest August 1-7 pulse", "latest_pulse")):
        r = summaries[name]
        lines.extend([f"## {title}", f"- interval: `{r['start']} -> {r['end_exclusive']}`", f"- trades / day: `{r['trades']} / {r['trades_per_day']}`", f"- gross / net mean: `{r['mean_gross_bps']} / {r['mean_net_bps']}` bp", f"- win rate / payoff: `{r['win_rate']} / {r['payoff_ratio']}`", f"- net t-stat: `{r['net_t_stat']}`", f"- positive months: `{r['positive_months']} / {r['active_months']}`", f"- route stats: `{r['route_stats']}`", f"- symbol counts: `{r['symbol_counts']}`", f"- exit reasons: `{r['exit_reasons']}`", ""])
    lines.extend(["## Advance checks", *[f"- {k}: `{v}`" for k, v in checks.items()], "", "## Cross-split route evidence", f"`{cross_split}`", "", "## Decision", decision, "", "This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation."])
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
