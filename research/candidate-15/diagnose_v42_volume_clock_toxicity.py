#!/usr/bin/env python3
"""Candidate 15 V42 prior-only volume-clock toxicity screen."""
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
DISCOVERY = "TOXIC_FLOW_PRICE_DISCOVERY"
ABSORPTION = "ABSORBED_TOXIC_FLOW_REVERSAL"


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
    for version in range(33, 42):
        for path in root.glob(f"V{version}_*_RESULT.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ADVANCE_TO_NAUTILUS" in text and "REJECTED_OR_UNDERPOWERED" not in text and "SKIPPED" not in text:
                versions.append(version)
    return sorted(set(versions))


def prior_z(value: pd.Series, lookback: int, minimum: int) -> pd.Series:
    prior = value.shift(1)
    mean = prior.rolling(lookback, min_periods=minimum).mean()
    std = prior.rolling(lookback, min_periods=minimum).std(ddof=0).replace(0.0, np.nan)
    return (value - mean) / std


def build_buckets(symbol: str, frame: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    prior_median = frame["quote_volume"].shift(1).rolling(
        int(rules["target_volume_lookback_bars"]),
        min_periods=int(rules["target_volume_minimum_prior_bars"]),
    ).median()
    target_series = prior_median * float(rules["target_bucket_multiple_of_prior_median_5m_volume"])
    rows: list[dict[str, Any]] = []
    current: list[tuple[pd.Timestamp, pd.Series]] = []
    target = math.nan
    accumulated = 0.0
    for timestamp, bar in frame.iterrows():
        if not current:
            target = float(target_series.get(timestamp, np.nan))
            if not math.isfinite(target) or target <= 0.0:
                continue
            accumulated = 0.0
        current.append((pd.Timestamp(timestamp), bar))
        accumulated += float(bar["quote_volume"])
        count = len(current)
        close_bucket = (
            count >= int(rules["minimum_bucket_bars"])
            and accumulated >= target
        ) or count >= int(rules["maximum_bucket_bars"])
        if not close_bucket:
            continue
        first_ts, first = current[0]
        last_ts, last = current[-1]
        high = max(float(item[1]["high"]) for item in current)
        low = min(float(item[1]["low"]) for item in current)
        quote_volume = sum(float(item[1]["quote_volume"]) for item in current)
        taker_buy = sum(float(item[1]["taker_buy_quote_volume"]) for item in current)
        rows.append({
            "symbol": symbol,
            "bucket_start_ts": first_ts,
            "bucket_end_ts": last_ts,
            "bucket_bars": count,
            "target_quote_volume": target,
            "quote_volume": quote_volume,
            "open": float(first["open"]),
            "high": high,
            "low": low,
            "close": float(last["close"]),
            "taker_buy_quote_volume": taker_buy,
        })
        current = []
        accumulated = 0.0
    if not rows:
        return pd.DataFrame()
    buckets = pd.DataFrame(rows).set_index("bucket_end_ts").sort_index()
    buckets["bucket_return"] = buckets["close"] / buckets["open"] - 1.0
    buckets["signed_imbalance"] = (
        2.0 * buckets["taker_buy_quote_volume"]
        - buckets["quote_volume"]
    ) / buckets["quote_volume"].replace(0.0, np.nan)
    buckets["absolute_imbalance"] = buckets["signed_imbalance"].abs()
    previous_close = buckets["close"].shift(1)
    true_range = pd.concat([
        buckets["high"] - buckets["low"],
        (buckets["high"] - previous_close).abs(),
        (buckets["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    buckets["bucket_atr"] = true_range.shift(1).rolling(
        int(rules["bucket_atr_lookback"]),
        min_periods=int(rules["bucket_atr_lookback"]),
    ).mean()
    buckets["return_atr"] = (
        buckets["bucket_return"] * buckets["close"]
        / buckets["bucket_atr"].replace(0.0, np.nan)
    )
    buckets["vpin"] = buckets["absolute_imbalance"].rolling(
        int(rules["vpin_lookback_buckets"]),
        min_periods=int(rules["vpin_lookback_buckets"]),
    ).mean()
    buckets["vpin_z"] = prior_z(
        buckets["vpin"],
        int(rules["vpin_z_lookback_buckets"]),
        int(rules["vpin_z_minimum_prior_buckets"]),
    )
    impact = buckets["bucket_return"].abs() * (
        buckets["target_quote_volume"]
        / buckets["quote_volume"].replace(0.0, np.nan)
    )
    buckets["log_impact"] = np.log(impact.replace(0.0, np.nan))
    buckets["impact_z"] = prior_z(
        buckets["log_impact"],
        int(rules["impact_z_lookback_buckets"]),
        int(rules["impact_z_minimum_prior_buckets"]),
    )
    buckets["bucket_midpoint"] = (buckets["open"] + buckets["close"]) / 2.0
    return buckets.replace([np.inf, -np.inf], np.nan)


def classify(buckets: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    if buckets.empty:
        return buckets.copy()
    common_mask = (
        (buckets["vpin_z"] >= float(rules["vpin_z_min"]))
        & (buckets["absolute_imbalance"] >= float(rules["minimum_absolute_signed_imbalance"]))
    )
    discovery = (
        common_mask
        & (np.sign(buckets["bucket_return"]) == np.sign(buckets["signed_imbalance"]))
        & (buckets["impact_z"] >= float(rules["discovery_impact_z_min"]))
    )
    absorption = (
        common_mask
        & ~discovery
        & (
            (np.sign(buckets["bucket_return"]) != np.sign(buckets["signed_imbalance"]))
            | (buckets["return_atr"].abs() <= float(rules["absorption_max_return_atr"]))
        )
    )
    output = buckets.copy()
    output["route"] = "UNRESOLVED"
    output["direction_value"] = 0.0
    output.loc[discovery, "route"] = DISCOVERY
    output.loc[discovery, "direction_value"] = np.sign(output.loc[discovery, "signed_imbalance"])
    output.loc[absorption, "route"] = ABSORPTION
    output.loc[absorption, "direction_value"] = -np.sign(output.loc[absorption, "signed_imbalance"])
    return output


def confirm_and_simulate(item: pd.Series, five: pd.DataFrame, rules: dict[str, Any]) -> dict[str, Any] | None:
    bucket_end = pd.Timestamp(item.name)
    direction = float(item["direction_value"])
    confirmation_end = bucket_end + pd.Timedelta(minutes=int(rules["confirmation_minutes"]))
    confirmation = five[(five.index > bucket_end) & (five.index <= confirmation_end)]
    if len(confirmation.index) != int(rules["confirmation_minutes"]) // 5:
        return None
    opening, closing = float(confirmation.iloc[0]["open"]), float(confirmation.iloc[-1]["close"])
    high, low = float(confirmation["high"].max()), float(confirmation["low"].min())
    body = abs(closing - opening) / max(high - low, 1e-12)
    pressure = 2.0 * float(confirmation["taker_buy_quote_volume"].sum()) / max(float(confirmation["quote_volume"].sum()), 1e-12) - 1.0
    confirm_return = closing / opening - 1.0
    if direction * confirm_return <= 0.0:
        return None
    if body < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if direction * pressure < float(rules["minimum_confirmation_taker_pressure"]):
        return None
    entry = closing
    atr = float(item["bucket_atr"])
    buffer = float(rules["stop_atr_buffer"]) * atr
    stop = float(item["low"]) - buffer if direction > 0.0 else float(item["high"]) + buffer
    if direction * (entry - stop) <= 0.0:
        return None
    target = float(item["bucket_midpoint"]) if item["route"] == ABSORPTION else math.nan
    if item["route"] == ABSORPTION and direction * (target - entry) <= 0.0:
        return None
    cap = confirmation_end + pd.Timedelta(minutes=int(rules["maximum_hold_minutes"]))
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None
    stop_current, best_close = stop, entry
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    reason = ""
    updates = 0
    for timestamp, bar in path.iterrows():
        if direction > 0.0:
            if float(bar["low"]) <= stop_current:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop_current, "BUCKET_EXTREME_INVALIDATED"
                break
            if item["route"] == ABSORPTION and float(bar["high"]) >= target:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), target, "BUCKET_MIDPOINT_REACHED"
                break
        else:
            if float(bar["high"]) >= stop_current:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop_current, "BUCKET_EXTREME_INVALIDATED"
                break
            if item["route"] == ABSORPTION and float(bar["low"]) <= target:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), target, "BUCKET_MIDPOINT_REACHED"
                break
        if item["route"] == DISCOVERY and timestamp.minute in (0, 30):
            close = float(bar["close"])
            best_close = max(best_close, close) if direction > 0.0 else min(best_close, close)
            new_stop = max(stop_current, best_close - float(rules["continuation_trailing_stop_atr"]) * atr) if direction > 0.0 else min(stop_current, best_close + float(rules["continuation_trailing_stop_atr"]) * atr)
            if abs(new_stop - stop_current) > 1e-12:
                stop_current = new_stop
                updates += 1
    if exit_ts is None:
        exit_ts, exit_price, reason = pd.Timestamp(path.index[-1]), float(path.iloc[-1]["close"]), "FOUR_HOUR_CAP"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (float(rules["execution_round_trip_cost_bps"]) + float(rules["funding_and_unmodeled_impact_reserve_bps"])) / 10_000.0
    quality = float(item["vpin_z"]) * float(item["absolute_imbalance"]) * (1.0 + abs(float(item["impact_z"])))
    return {
        "bucket_start_ts": pd.Timestamp(item["bucket_start_ts"]),
        "bucket_end_ts": bucket_end,
        "entry_ts": confirmation_end,
        "exit_ts": exit_ts,
        "symbol": str(item["symbol"]),
        "route": str(item["route"]),
        "direction": "LONG" if direction > 0.0 else "SHORT",
        "direction_value": direction,
        "bucket_bars": int(item["bucket_bars"]),
        "vpin": float(item["vpin"]),
        "vpin_z": float(item["vpin_z"]),
        "signed_imbalance": float(item["signed_imbalance"]),
        "impact_z": float(item["impact_z"]),
        "return_atr": float(item["return_atr"]),
        "confirmation_return": confirm_return,
        "confirmation_body_fraction": body,
        "confirmation_taker_pressure": pressure,
        "entry_price": entry,
        "initial_stop": stop,
        "final_stop": stop_current,
        "target_price": None if math.isnan(target) else target,
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "trailing_updates": updates,
        "holding_minutes": (exit_ts - confirmation_end).total_seconds() / 60.0,
        "rank_score": quality,
        "gross_return": gross,
        "net_return": gross - cost,
    }


def build_candidates(states: dict[str, pd.DataFrame], five_frames: dict[str, pd.DataFrame], rules: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in states.items():
        eligible = frame[frame["route"] != "UNRESOLVED"].copy()
        eligible["symbol"] = symbol
        for _, item in eligible.iterrows():
            result = confirm_and_simulate(item, five_frames[symbol], rules)
            if result is not None:
                rows.append(result)
    return pd.DataFrame(rows).sort_values(["entry_ts", "rank_score", "symbol"], ascending=[True, False, True], kind="stable") if rows else pd.DataFrame()


def arbitrate(frame: pd.DataFrame, rules: dict[str, Any]) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    last_symbol: dict[str, pd.Timestamp] = {}
    cooldown = pd.Timedelta(minutes=int(rules["same_symbol_cooldown_minutes"]))
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_ENTRY_LOSER"] += max(0, len(group.index) - 1)
        timestamp, symbol = pd.Timestamp(entry_ts), str(winner["symbol"])
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        if symbol in last_symbol and timestamp < last_symbol[symbol] + cooldown:
            skips["SAME_SYMBOL_COOLDOWN"] += 1
            continue
        selected.append(winner)
        free_at, last_symbol[symbol] = pd.Timestamp(winner["exit_ts"]), timestamp
    return (pd.DataFrame(selected).reset_index(drop=True) if selected else frame.iloc[0:0].copy()), skips


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
        return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": 0, "trades_per_day": 0.0, "mean_gross_bps": None, "mean_net_bps": None, "win_rate": None, "payoff_ratio": None, "net_t_stat": None, "positive_months": 0, "active_months": 0, "positive_month_share": 0.0, "route_stats": {}, "symbol_counts": {}, "exit_reasons": {}}
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    route_stats: dict[str, Any] = {}
    for route, current in sample.groupby("route", sort=True):
        route_stats[str(route)] = {"trades": len(current.index), "mean_net_bps": float(current["net_return"].mean() * 10_000.0), "win_rate": float((current["net_return"] > 0.0).mean()), "payoff_ratio": payoff(current["net_return"]), "net_t_stat": t_stat(current["net_return"])}
    return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": len(sample.index), "trades_per_day": len(sample.index) / max(days, 1), "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0), "mean_net_bps": float(sample["net_return"].mean() * 10_000.0), "win_rate": float((sample["net_return"] > 0.0).mean()), "payoff_ratio": payoff(sample["net_return"]), "net_t_stat": t_stat(sample["net_return"]), "positive_months": int((monthly > 0.0).sum()), "active_months": len(monthly.index), "positive_month_share": float((monthly > 0.0).mean()), "route_stats": route_stats, "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()}, "exit_reasons": {str(k): int(v) for k, v in sample["exit_reason"].value_counts().items()}}


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v42-volume-clock-toxicity-v1":
        raise RuntimeError("unexpected V42 protocol")
    output.mkdir(parents=True, exist_ok=True)
    prior_advances = existing_advance(protocol_path.parent)
    if prior_advances:
        summary = {"schema": "candidate-15-v42-summary-v1", "classification": "V42_SKIPPED_EXISTING_NAUTILUS_ADVANCE", "advance_to_nautilus": False, "existing_advance_versions": prior_advances, "decision": "V42 was not evaluated because an earlier route already requires Nautilus promotion."}
        write_json(output / "summary.json", summary)
        (output / "RESULT.md").write_text(f"# Candidate 15 V42\n\n**{summary['classification']}**\n\n- existing_advance_versions: `{prior_advances}`\n", encoding="utf-8")
        return summary
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v42-data-manifest-v1", "files": manifest})
    start, end = date.fromisoformat(protocol["data"]["start"]), date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    buckets = {symbol: build_buckets(symbol, frame, protocol["fixed_rules"]) for symbol, frame in five_frames.items()}
    states = {symbol: classify(frame, protocol["fixed_rules"]) for symbol, frame in buckets.items()}
    candidates = build_candidates(states, five_frames, protocol["fixed_rules"])
    selected, skips = arbitrate(candidates, protocol["fixed_rules"])
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    evaluation = protocol["evaluation"]
    summaries = {name: summarize(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"]) for name in ("development", "stability", "july_confirmation", "latest_pulse")}
    development, stability, july, pulse = summaries["development"], summaries["stability"], summaries["july_confirmation"], summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    total = sum(stability["symbol_counts"].values())
    max_symbol_share = max(stability["symbol_counts"].values(), default=0) / max(total, 1)
    checks = {"positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0, "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]), "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]), "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]), "stability_frequency": stability["trades_per_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]), "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0, "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]), "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]), "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]), "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"])}
    cross_split: dict[str, Any] = {}
    for route in (DISCOVERY, ABSORPTION):
        split = {name: summary["route_stats"].get(route) for name, summary in summaries.items()}
        cross_split[route] = {"positive_across_all_declared_splits": all(record is not None and record["mean_net_bps"] > 0.0 for record in split.values()), "splits": split}
    survivors = [route for route, record in cross_split.items() if record["positive_across_all_declared_splits"]]
    advance = all(checks.values()) and bool(survivors)
    classification = "V42_VOLUME_CLOCK_TOXICITY_ADVANCE_TO_NAUTILUS" if advance else "V42_VOLUME_CLOCK_TOXICITY_REJECTED_OR_UNDERPOWERED"
    decision = f"Cross-split-positive fixed routes: {survivors}. " + ("Freeze and implement the survivor in NautilusTrader." if advance else "Do not tune volume targets, VPIN, impact, absorption, confirmation or stops.")
    summary = {"schema": "candidate-15-v42-summary-v1", "classification": classification, "advance_to_nautilus": advance, "bucket_counts": {symbol: len(frame.index) for symbol, frame in buckets.items()}, "executable_candidates": len(candidates.index), "selected_trades": len(selected.index), "arbitration_skips": dict(skips), **summaries, "advance_checks": checks, "cross_split_routes": cross_split, "surviving_routes": survivors, "maximum_stability_symbol_share": max_symbol_share, "decision": decision}
    write_json(output / "summary.json", summary)
    lines = ["# Candidate 15 V42 — Volume-clock toxicity", "", f"**{classification}**", "", "Prior-only expected quote volume creates nonuniform volume buckets. VPIN and impact split toxic price discovery from absorbed toxic flow before a separate fifteen-minute confirmation.", ""]
    for title, name in (("Development", "development"), ("Year-long stability", "stability"), ("July 2026 confirmation", "july_confirmation"), ("Latest August 1-7 pulse", "latest_pulse")):
        r = summaries[name]
        lines.extend([f"## {title}", f"- interval: `{r['start']} -> {r['end_exclusive']}`", f"- trades / day: `{r['trades']} / {r['trades_per_day']}`", f"- gross / net mean: `{r['mean_gross_bps']} / {r['mean_net_bps']}` bp", f"- win rate / payoff: `{r['win_rate']} / {r['payoff_ratio']}`", f"- net t-stat: `{r['net_t_stat']}`", f"- positive months: `{r['positive_months']} / {r['active_months']}`", f"- route stats: `{r['route_stats']}`", f"- symbol counts: `{r['symbol_counts']}`", f"- exit reasons: `{r['exit_reasons']}`", ""])
    lines.extend(["## Advance checks", *[f"- {k}: `{v}`" for k, v in checks.items()], "", "## Cross-split route evidence", f"`{cross_split}`", "", "## Decision", decision, "", "This is an economic mechanism screen. A pass still requires online volume-bucket construction and exact NautilusTrader continuous-account execution."])
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
