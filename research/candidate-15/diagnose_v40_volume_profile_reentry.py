#!/usr/bin/env python3
"""Candidate 15 V40 prior-auction volume-profile reentry screen."""
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
HIGH_ROUTE = "HIGH_VALUE_REJECTION_TO_POC"
LOW_ROUTE = "LOW_VALUE_REJECTION_TO_POC"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def existing_advance(root: Path) -> list[int]:
    versions: list[int] = []
    for status_name in ("V33_V38_REPLAY_STATUS.json", "V33_V37_ROUND_STATUS.json"):
        status = root / status_name
        if status.is_file():
            payload = load_object(status)
            versions.extend(int(value) for value in payload.get("advance_versions", []))
    for version in range(33, 40):
        for path in root.glob(f"V{version}_*_RESULT.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ADVANCE_TO_NAUTILUS" in text and "REJECTED_OR_UNDERPOWERED" not in text and "SKIPPED" not in text:
                versions.append(version)
    return sorted(set(versions))


def resample_thirty(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample("30min", closed="right", label="right", origin="start_day")
    output = pd.DataFrame(index=grouped.size().index)
    output["count"] = grouped.size()
    output["open"] = grouped["open"].first()
    output["high"] = grouped["high"].max()
    output["low"] = grouped["low"].min()
    output["close"] = grouped["close"].last()
    output["quote_volume"] = grouped["quote_volume"].sum()
    output["taker_buy_quote_volume"] = grouped["taker_buy_quote_volume"].sum()
    return output[output["count"] == 6].copy()


def value_area(window: pd.DataFrame, bins: int, fraction: float) -> tuple[float, float, float] | None:
    low = float(window["low"].min())
    high = float(window["high"].max())
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return None
    edges = np.linspace(low, high, bins + 1)
    typical = ((window["high"] + window["low"] + window["close"]) / 3.0).to_numpy(dtype=float)
    volume = window["quote_volume"].to_numpy(dtype=float)
    indexes = np.clip(np.searchsorted(edges, typical, side="right") - 1, 0, bins - 1)
    histogram = np.zeros(bins, dtype=float)
    np.add.at(histogram, indexes, volume)
    total = float(histogram.sum())
    if total <= 0.0:
        return None
    poc = int(np.argmax(histogram))
    selected = {poc}
    accumulated = float(histogram[poc])
    left, right = poc - 1, poc + 1
    while accumulated / total < fraction and (left >= 0 or right < bins):
        left_volume = float(histogram[left]) if left >= 0 else -1.0
        right_volume = float(histogram[right]) if right < bins else -1.0
        if right_volume > left_volume:
            selected.add(right)
            accumulated += max(right_volume, 0.0)
            right += 1
        else:
            selected.add(left)
            accumulated += max(left_volume, 0.0)
            left -= 1
    low_bin, high_bin = min(selected), max(selected)
    val = float(edges[low_bin])
    vah = float(edges[high_bin + 1])
    poc_price = float((edges[poc] + edges[poc + 1]) / 2.0)
    return val, vah, poc_price


def build_events(symbol: str, five: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    bars = resample_thirty(five)
    previous_close = bars["close"].shift(1)
    true_range = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - previous_close).abs(),
        (bars["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.shift(1).rolling(int(rules["atr_lookback_bars"]), min_periods=int(rules["atr_lookback_bars"])).mean()
    volume_median = bars["quote_volume"].shift(1).rolling(
        int(rules["volume_median_lookback_bars"]),
        min_periods=max(int(rules["profile_lookback_bars"]), int(rules["volume_median_lookback_bars"]) // 2),
    ).median()
    pressure = (2.0 * bars["taker_buy_quote_volume"] / bars["quote_volume"].replace(0.0, np.nan) - 1.0).clip(-1.0, 1.0)
    body_fraction = (bars["close"] - bars["open"]).abs() / (bars["high"] - bars["low"]).replace(0.0, np.nan)
    rows: list[dict[str, Any]] = []
    lookback = int(rules["profile_lookback_bars"])
    for index in range(lookback, len(bars.index) - 1):
        outside = bars.iloc[index]
        reentry = bars.iloc[index + 1]
        timestamp = pd.Timestamp(bars.index[index])
        reentry_ts = pd.Timestamp(bars.index[index + 1])
        atr_value = atr.iloc[index]
        median_volume = volume_median.iloc[index]
        if pd.isna(atr_value) or pd.isna(median_volume) or float(median_volume) <= 0.0:
            continue
        profile = value_area(bars.iloc[index - lookback:index], int(rules["profile_price_bins"]), float(rules["value_area_fraction"]))
        if profile is None:
            continue
        val, vah, poc = profile
        volume_ratio = float(outside["quote_volume"]) / float(median_volume)
        if volume_ratio < float(rules["minimum_outside_volume_ratio"]):
            continue
        high_excursion = (float(outside["close"]) - vah) / max(float(atr_value), 1e-12)
        low_excursion = (val - float(outside["close"])) / max(float(atr_value), 1e-12)
        route: str | None = None
        direction = 0.0
        if high_excursion >= float(rules["minimum_outside_excursion_atr"]):
            if (
                float(reentry["close"]) < vah
                and float(reentry["close"]) > poc
                and float(reentry["close"]) < float(reentry["open"])
                and float(body_fraction.iloc[index + 1]) >= float(rules["minimum_reentry_body_fraction"])
                and float(pressure.iloc[index + 1]) <= -float(rules["minimum_reentry_directional_taker_pressure"])
            ):
                route, direction = HIGH_ROUTE, -1.0
        elif low_excursion >= float(rules["minimum_outside_excursion_atr"]):
            if (
                float(reentry["close"]) > val
                and float(reentry["close"]) < poc
                and float(reentry["close"]) > float(reentry["open"])
                and float(body_fraction.iloc[index + 1]) >= float(rules["minimum_reentry_body_fraction"])
                and float(pressure.iloc[index + 1]) >= float(rules["minimum_reentry_directional_taker_pressure"])
            ):
                route, direction = LOW_ROUTE, 1.0
        if route is None:
            continue
        rows.append({
            "symbol": symbol,
            "outside_ts": timestamp,
            "reentry_ts": reentry_ts,
            "route": route,
            "direction_value": direction,
            "frozen_val": val,
            "frozen_vah": vah,
            "frozen_poc": poc,
            "outside_open": float(outside["open"]),
            "outside_high": float(outside["high"]),
            "outside_low": float(outside["low"]),
            "outside_close": float(outside["close"]),
            "outside_volume_ratio": volume_ratio,
            "outside_excursion_atr": high_excursion if route == HIGH_ROUTE else low_excursion,
            "reentry_open": float(reentry["open"]),
            "reentry_high": float(reentry["high"]),
            "reentry_low": float(reentry["low"]),
            "reentry_close": float(reentry["close"]),
            "reentry_pressure": float(pressure.iloc[index + 1]),
            "prior_atr_30m": float(atr_value),
        })
    return pd.DataFrame(rows)


def confirm_and_simulate(item: pd.Series, five: pd.DataFrame, rules: dict[str, Any]) -> dict[str, Any] | None:
    direction = float(item["direction_value"])
    reentry_ts = pd.Timestamp(item["reentry_ts"])
    confirmation_end = reentry_ts + pd.Timedelta(minutes=int(rules["confirmation_minutes"]))
    confirmation = five[(five.index > reentry_ts) & (five.index <= confirmation_end)]
    if len(confirmation.index) != int(rules["confirmation_minutes"]) // 5:
        return None
    opening = float(confirmation.iloc[0]["open"])
    closing = float(confirmation.iloc[-1]["close"])
    high = float(confirmation["high"].max())
    low = float(confirmation["low"].min())
    body = abs(closing - opening) / max(high - low, 1e-12)
    pressure = 2.0 * float(confirmation["taker_buy_quote_volume"].sum()) / max(float(confirmation["quote_volume"].sum()), 1e-12) - 1.0
    confirmation_return = closing / opening - 1.0
    if direction * confirmation_return <= 0.0:
        return None
    if body < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if direction * pressure < float(rules["minimum_confirmation_taker_pressure"]):
        return None
    target = float(item["frozen_poc"])
    entry = closing
    if direction * (target - entry) <= 0.0:
        return None
    buffer = float(rules["stop_atr_buffer"]) * float(item["prior_atr_30m"])
    stop = float(item["outside_high"]) + buffer if direction < 0.0 else float(item["outside_low"]) - buffer
    if direction * (entry - stop) <= 0.0:
        return None
    cap = confirmation_end + pd.Timedelta(minutes=int(rules["maximum_hold_minutes"]))
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    reason = ""
    for timestamp, bar in path.iterrows():
        if direction > 0.0:
            if float(bar["low"]) <= stop:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop, "OUTSIDE_EXTREME_INVALIDATED"
                break
            if float(bar["high"]) >= target:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), target, "FROZEN_POC_REACHED"
                break
        else:
            if float(bar["high"]) >= stop:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), stop, "OUTSIDE_EXTREME_INVALIDATED"
                break
            if float(bar["low"]) <= target:
                exit_ts, exit_price, reason = pd.Timestamp(timestamp), target, "FROZEN_POC_REACHED"
                break
    if exit_ts is None:
        exit_ts, exit_price, reason = pd.Timestamp(path.index[-1]), float(path.iloc[-1]["close"]), "SIX_HOUR_CAP"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (float(rules["execution_round_trip_cost_bps"]) + float(rules["funding_and_unmodeled_impact_reserve_bps"])) / 10_000.0
    quality = float(item["outside_excursion_atr"]) * float(item["outside_volume_ratio"])
    return {**item.to_dict(), "entry_ts": confirmation_end, "exit_ts": exit_ts, "direction": "LONG" if direction > 0.0 else "SHORT", "confirmation_return": confirmation_return, "confirmation_body_fraction": body, "confirmation_taker_pressure": pressure, "entry_price": entry, "initial_stop": stop, "target_price": target, "exit_price": float(exit_price), "exit_reason": reason, "holding_minutes": (exit_ts - confirmation_end).total_seconds() / 60.0, "rank_score": quality, "gross_return": gross, "net_return": gross - cost}


def build_candidates(events: dict[str, pd.DataFrame], five_frames: dict[str, pd.DataFrame], rules: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in events.items():
        for _, item in frame.iterrows():
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
    standard = float(values.std(ddof=1))
    return None if not math.isfinite(standard) or standard <= 0.0 else float(values.mean() / (standard / math.sqrt(len(values.index))))


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
    if protocol["schema"] != "candidate-15-v40-volume-profile-reentry-v1":
        raise RuntimeError("unexpected V40 protocol")
    output.mkdir(parents=True, exist_ok=True)
    prior_advances = existing_advance(protocol_path.parent)
    if prior_advances:
        summary = {"schema": "candidate-15-v40-summary-v1", "classification": "V40_SKIPPED_EXISTING_NAUTILUS_ADVANCE", "advance_to_nautilus": False, "existing_advance_versions": prior_advances, "decision": "V40 was not evaluated because an earlier frozen route already requires Nautilus promotion."}
        write_json(output / "summary.json", summary)
        (output / "RESULT.md").write_text(f"# Candidate 15 V40\n\n**{summary['classification']}**\n\n- existing_advance_versions: `{prior_advances}`\n", encoding="utf-8")
        return summary
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v40-data-manifest-v1", "files": manifest})
    start, end = date.fromisoformat(protocol["data"]["start"]), date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    events = {symbol: build_events(symbol, frame, protocol["fixed_rules"]) for symbol, frame in five_frames.items()}
    candidates = build_candidates(events, five_frames, protocol["fixed_rules"])
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
    for route in (HIGH_ROUTE, LOW_ROUTE):
        split = {name: summary["route_stats"].get(route) for name, summary in summaries.items()}
        cross_split[route] = {"positive_across_all_declared_splits": all(record is not None and record["mean_net_bps"] > 0.0 for record in split.values()), "splits": split}
    survivors = [route for route, record in cross_split.items() if record["positive_across_all_declared_splits"]]
    advance = all(checks.values()) and bool(survivors)
    classification = "V40_VOLUME_PROFILE_REENTRY_ADVANCE_TO_NAUTILUS" if advance else "V40_VOLUME_PROFILE_REENTRY_REJECTED_OR_UNDERPOWERED"
    decision = f"Cross-split-positive fixed routes: {survivors}. " + ("Freeze and implement the survivor in NautilusTrader." if advance else "Do not tune profile bins, value area, excursion, reentry, confirmation, stop or POC target rules.")
    summary = {"schema": "candidate-15-v40-summary-v1", "classification": classification, "advance_to_nautilus": advance, "raw_reentries": int(sum(len(frame.index) for frame in events.values())), "executable_candidates": len(candidates.index), "selected_trades": len(selected.index), "arbitration_skips": dict(skips), **summaries, "advance_checks": checks, "cross_split_routes": cross_split, "surviving_routes": survivors, "maximum_stability_symbol_share": max_symbol_share, "decision": decision}
    write_json(output / "summary.json", summary)
    lines = ["# Candidate 15 V40 — Prior-auction volume-profile reentry", "", f"**{classification}**", "", "A prior 24-hour profile is frozen before an outside event. Entry requires a later completed value-area reentry and a still later fifteen-minute continuation toward the frozen POC.", ""]
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
