#!/usr/bin/env python3
"""Candidate 15 V31 volatility-compression release screen."""
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
BREADTH = "BREADTH_SQUEEZE_RELEASE"
RELATIVE = "RELATIVE_SQUEEZE_RELEASE"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def resample(frame: pd.DataFrame, rule: str, expected: int) -> pd.DataFrame:
    grouped = frame.resample(
        rule,
        closed="right",
        label="right",
        origin="start_day",
    )
    output = pd.DataFrame(index=grouped.size().index)
    output["count"] = grouped.size()
    output["open"] = grouped["open"].first()
    output["high"] = grouped["high"].max()
    output["low"] = grouped["low"].min()
    output["close"] = grouped["close"].last()
    output["quote_volume"] = grouped["quote_volume"].sum()
    output["taker_buy_quote_volume"] = grouped[
        "taker_buy_quote_volume"
    ].sum()
    return output[output["count"] == expected].copy()


def squeeze_streak(value: pd.Series) -> pd.Series:
    groups = (~value.fillna(False)).cumsum()
    return value.fillna(False).astype(int).groupby(groups).cumsum()


def hourly_state(
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    hourly = resample(five, "1h", 12)
    close = hourly["close"].astype(float)
    previous = close.shift(1)
    true_range = pd.concat(
        [
            hourly["high"] - hourly["low"],
            (hourly["high"] - previous).abs(),
            (hourly["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bb_lookback = int(rules["bollinger_lookback_hours"])
    kc_lookback = int(rules["keltner_lookback_hours"])
    middle = close.rolling(bb_lookback, min_periods=bb_lookback).mean()
    std = close.rolling(bb_lookback, min_periods=bb_lookback).std(ddof=0)
    atr = true_range.rolling(kc_lookback, min_periods=kc_lookback).mean()
    bb_upper = middle + float(rules["bollinger_std_multiple"]) * std
    bb_lower = middle - float(rules["bollinger_std_multiple"]) * std
    kc_middle = close.rolling(kc_lookback, min_periods=kc_lookback).mean()
    kc_upper = kc_middle + float(rules["keltner_atr_multiple"]) * atr
    kc_lower = kc_middle - float(rules["keltner_atr_multiple"]) * atr
    squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    output = hourly.copy()
    output["atr_1h"] = atr
    output["squeeze"] = squeeze
    output["squeeze_streak"] = squeeze_streak(squeeze)
    box = int(rules["compression_box_hours"])
    output["box_high"] = hourly["high"].rolling(box, min_periods=box).max()
    output["box_low"] = hourly["low"].rolling(box, min_periods=box).min()
    output["box_width_atr"] = (
        output["box_high"] - output["box_low"]
    ) / atr.replace(0.0, np.nan)
    output["state_ts"] = output.index
    return output.replace([np.inf, -np.inf], np.nan)


def fifteen_minute_state(
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    bars = resample(five, "15min", 3)
    bars["body_fraction"] = (
        (bars["close"] - bars["open"]).abs()
        / (bars["high"] - bars["low"]).replace(0.0, np.nan)
    )
    bars["taker_pressure"] = (
        2.0
        * bars["taker_buy_quote_volume"]
        / bars["quote_volume"].replace(0.0, np.nan)
        - 1.0
    ).clip(-1.0, 1.0)
    bars["prior_volume_threshold"] = (
        bars["quote_volume"]
        .shift(1)
        .rolling(
            int(rules["breakout_volume_prior_window_bars"]),
            min_periods=int(rules["breakout_volume_prior_minimum_bars"]),
        )
        .quantile(float(rules["breakout_volume_quantile"]))
    )
    return bars.replace([np.inf, -np.inf], np.nan)


def symbol_breakouts(
    symbol: str,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    hourly = hourly_state(five, rules)
    quarter = fifteen_minute_state(five, rules)
    available = hourly.copy()
    available.index = available.index + pd.Timedelta(microseconds=1)
    state = available.reindex(quarter.index, method="ffill")
    joined = quarter.join(
        state[
            [
                "atr_1h", "squeeze_streak", "box_high", "box_low",
                "box_width_atr", "state_ts",
            ]
        ],
        how="left",
    )
    direction = pd.Series(0.0, index=joined.index)
    direction[joined["close"] > joined["box_high"]] = 1.0
    direction[joined["close"] < joined["box_low"]] = -1.0
    joined["direction_value"] = direction
    joined["range_atr"] = (
        joined["high"] - joined["low"]
    ) / joined["atr_1h"].replace(0.0, np.nan)
    joined["breakout_distance_atr"] = np.where(
        direction > 0.0,
        (joined["close"] - joined["box_high"])
        / joined["atr_1h"].replace(0.0, np.nan),
        (joined["box_low"] - joined["close"])
        / joined["atr_1h"].replace(0.0, np.nan),
    )
    eligible = joined[
        (joined["squeeze_streak"] >= int(rules["minimum_consecutive_squeeze_hours"]))
        & (direction != 0.0)
        & (joined["body_fraction"] >= float(rules["minimum_breakout_body_fraction"]))
        & (joined["quote_volume"] >= joined["prior_volume_threshold"])
        & (joined["range_atr"] >= float(rules["breakout_range_atr_min"]))
        & (direction * joined["taker_pressure"] >= float(rules["minimum_directional_taker_pressure"]))
        & (
            (joined.index - pd.to_datetime(joined["state_ts"], utc=True))
            <= pd.Timedelta(hours=1)
        )
    ].copy()
    eligible["symbol"] = symbol
    eligible["breakout_ts"] = eligible.index
    return eligible.reset_index(drop=True)


def add_cross_market_state(
    candidates: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if candidates.empty:
        return candidates
    frame = candidates.copy()
    direction_table = frame.pivot_table(
        index="breakout_ts",
        columns="symbol",
        values="direction_value",
        aggfunc="first",
    )
    positive = (direction_table > 0.0).mean(axis=1)
    negative = (direction_table < 0.0).mean(axis=1)
    frame["directional_breadth"] = np.where(
        frame["direction_value"] > 0.0,
        frame["breakout_ts"].map(positive),
        frame["breakout_ts"].map(negative),
    )
    frame["absolute_expansion"] = frame["breakout_distance_atr"].abs()
    max_expansion = frame.groupby("breakout_ts")["absolute_expansion"].transform("max")
    second_expansion = frame.groupby("breakout_ts")["absolute_expansion"].transform(
        lambda values: values.nlargest(2).iloc[-1] if len(values.index) >= 2 else 0.0
    )
    frame["is_relative_leader"] = frame["absolute_expansion"] >= max_expansion - 1e-12
    frame["relative_leader_gap_atr"] = max_expansion - second_expansion
    breadth = frame["directional_breadth"] >= float(rules["breadth_fraction_min"])
    relative = (
        ~breadth
        & frame["is_relative_leader"]
        & (frame["absolute_expansion"] >= float(rules["relative_expansion_atr_min"]))
        & (frame["relative_leader_gap_atr"] >= float(rules["relative_leader_gap_atr_min"]))
    )
    frame["route"] = "UNRESOLVED"
    frame.loc[relative, "route"] = RELATIVE
    frame.loc[breadth, "route"] = BREADTH
    return frame[frame["route"] != "UNRESOLVED"].copy()


def confirmation_and_path(
    item: dict[str, Any],
    five: pd.DataFrame,
    hourly: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    breakout_ts = pd.Timestamp(item["breakout_ts"])
    direction = float(item["direction_value"])
    confirmation_end = breakout_ts + pd.Timedelta(minutes=int(rules["confirmation_minutes"]))
    confirmation = five[(five.index > breakout_ts) & (five.index <= confirmation_end)]
    if len(confirmation.index) != int(rules["confirmation_minutes"]) // 5:
        return None
    bar = confirmation.iloc[-1]
    opening = float(bar["open"])
    closing = float(bar["close"])
    high = float(bar["high"])
    low = float(bar["low"])
    body = abs(closing - opening) / max(high - low, 1e-12)
    pressure = 2.0 * float(bar["taker_buy_quote_volume"]) / max(
        float(bar["quote_volume"]), 1e-12
    ) - 1.0
    boundary = float(item["box_high"] if direction > 0.0 else item["box_low"])
    if direction * (closing - boundary) <= 0.0:
        return None
    if direction * (closing / opening - 1.0) <= 0.0:
        return None
    if body < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if direction * pressure < float(rules["minimum_directional_taker_pressure"]):
        return None
    entry = closing
    atr = float(item["atr_1h"])
    buffer = float(rules["initial_stop_atr_buffer"]) * atr
    if direction > 0.0:
        stop = min(float(item["low"]), float(item["box_high"]) - buffer)
    else:
        stop = max(float(item["high"]), float(item["box_low"]) + buffer)
    if direction * (entry - stop) <= 0.0:
        return None
    cap = confirmation_end + pd.Timedelta(minutes=int(rules["maximum_hold_minutes"]))
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None
    available_hourly = hourly.copy()
    available_hourly.index = available_hourly.index + pd.Timedelta(microseconds=1)
    mapped_atr = available_hourly["atr_1h"].reindex(path.index, method="ffill")
    best_close = entry
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason = ""
    updates = 0
    for timestamp, path_bar in path.iterrows():
        if direction > 0.0 and float(path_bar["low"]) <= stop:
            exit_ts = pd.Timestamp(timestamp)
            exit_price = stop
            exit_reason = "TRAILING_STOP"
            break
        if direction < 0.0 and float(path_bar["high"]) >= stop:
            exit_ts = pd.Timestamp(timestamp)
            exit_price = stop
            exit_reason = "TRAILING_STOP"
            break
        close = float(path_bar["close"])
        best_close = max(best_close, close) if direction > 0.0 else min(best_close, close)
        current_atr = mapped_atr.get(timestamp, np.nan)
        if pd.isna(current_atr):
            current_atr = atr
        if direction > 0.0:
            new_stop = max(stop, best_close - float(rules["trailing_stop_atr"]) * float(current_atr))
        else:
            new_stop = min(stop, best_close + float(rules["trailing_stop_atr"]) * float(current_atr))
        if abs(new_stop - stop) > 1e-12:
            stop = new_stop
            updates += 1
    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        exit_reason = "TWELVE_HOUR_CAP"
    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    quality = (
        float(item["range_atr"])
        * max(float(item["quote_volume"]) / max(float(item["prior_volume_threshold"]), 1e-12), 0.0)
        * (1.0 + float(item["directional_breadth"]))
    )
    return {
        **item,
        "entry_ts": confirmation_end,
        "entry_price": entry,
        "initial_stop": float(
            min(float(item["low"]), float(item["box_high"]) - buffer)
            if direction > 0.0
            else max(float(item["high"]), float(item["box_low"]) + buffer)
        ),
        "confirmation_return": closing / opening - 1.0,
        "confirmation_body_fraction": body,
        "confirmation_taker_pressure": pressure,
        "rank_score": quality,
        "route_priority": 2 if item["route"] == BREADTH else 1,
        "exit_ts": exit_ts,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "gross_return": gross,
        "net_return": gross - cost,
        "holding_minutes": (exit_ts - confirmation_end).total_seconds() / 60.0,
        "trailing_updates": updates,
    }


def build_executable(
    candidates: pd.DataFrame,
    five_frames: dict[str, pd.DataFrame],
    hourly_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for item in candidates.to_dict("records"):
        result = confirmation_and_path(
            item,
            five_frames[str(item["symbol"])],
            hourly_frames[str(item["symbol"])],
            rules,
        )
        if result is not None:
            rows.append(result)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["entry_ts", "route_priority", "rank_score", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )


def arbitrate(frame: pd.DataFrame, rules: dict[str, Any]) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    last_symbol_entry: dict[str, pd.Timestamp] = {}
    cooldown = pd.Timedelta(minutes=int(rules["same_symbol_cooldown_minutes"]))
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        symbol = str(winner["symbol"])
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        if symbol in last_symbol_entry and timestamp < last_symbol_entry[symbol] + cooldown:
            skips["SAME_SYMBOL_COOLDOWN"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
        last_symbol_entry[symbol] = timestamp
    if not selected:
        return frame.iloc[0:0].copy(), skips
    return pd.DataFrame(selected).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end, tz="UTC")
    sample = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start, "end_exclusive": end, "calendar_days": days,
            "trades": 0, "trades_per_day": 0.0, "mean_gross_bps": None,
            "mean_net_bps": None, "win_rate": None, "payoff_ratio": None,
            "net_t_stat": None, "positive_months": 0, "active_months": 0,
            "positive_month_share": 0.0, "route_stats": {},
            "symbol_counts": {}, "exit_reasons": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    route_stats: dict[str, Any] = {}
    for route, current in sample.groupby("route", sort=True):
        route_stats[str(route)] = {
            "trades": len(current.index),
            "mean_net_bps": float(current["net_return"].mean() * 10_000.0),
            "win_rate": float((current["net_return"] > 0.0).mean()),
            "payoff_ratio": payoff(current["net_return"]),
            "net_t_stat": t_stat(current["net_return"]),
            "mean_holding_minutes": float(current["holding_minutes"].mean()),
        }
    return {
        "start": start, "end_exclusive": end, "calendar_days": days,
        "trades": len(sample.index), "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff(sample["net_return"]),
        "net_t_stat": t_stat(sample["net_return"]),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "route_stats": route_stats,
        "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()},
        "exit_reasons": {str(k): int(v) for k, v in sample["exit_reason"].value_counts().items()},
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v31-squeeze-expansion-v1":
        raise RuntimeError("unexpected V31 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v31-data-manifest-v1", "files": manifest})
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    hourly_frames = {symbol: hourly_state(frame, protocol["fixed_rules"]) for symbol, frame in five_frames.items()}
    breakouts = pd.concat(
        [symbol_breakouts(symbol, frame, protocol["fixed_rules"]) for symbol, frame in five_frames.items()],
        ignore_index=True,
    )
    routed = add_cross_market_state(breakouts, protocol["fixed_rules"])
    executable = build_executable(routed, five_frames, hourly_frames, protocol["fixed_rules"])
    selected, skips = arbitrate(executable, protocol["fixed_rules"])
    breakouts.to_csv(output / "all_breakouts.csv", index=False)
    routed.to_csv(output / "all_routed_breakouts.csv", index=False)
    executable.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    summaries = {
        name: summarize(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"])
        for name in ("development", "stability", "july_confirmation", "latest_pulse")
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
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
        cross_split[route] = {
            "positive_across_all_declared_splits": all(record is not None and record["mean_net_bps"] > 0.0 for record in splits.values()),
            "splits": splits,
        }
    survivors = [route for route, record in cross_split.items() if record["positive_across_all_declared_splits"]]
    advance = all(checks.values()) and bool(survivors)
    classification = "V31_SQUEEZE_EXPANSION_ADVANCE_TO_NAUTILUS" if advance else "V31_SQUEEZE_EXPANSION_REJECTED_OR_UNDERPOWERED"
    decision = (
        f"Cross-split-positive fixed routes: {survivors}. Freeze and implement in NautilusTrader."
        if advance
        else "No fixed squeeze-release route survived all splits. Do not tune channel, breakout, confirmation or stop thresholds."
    )
    summary = {
        "schema": "candidate-15-v31-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "raw_breakouts": len(breakouts.index),
        "routed_breakouts": len(routed.index),
        "executable_candidates": len(executable.index),
        "selected_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        **summaries,
        "advance_checks": checks,
        "cross_split_routes": cross_split,
        "surviving_routes": survivors,
        "maximum_stability_symbol_share": max_symbol_share,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)
    lines = [
        "# Candidate 15 V31 — Squeeze expansion diagnostic", "", f"**{classification}**", "",
        "A completed one-hour BB/Keltner squeeze creates only context; a later completed fifteen-minute breakout chooses direction and a separate five-minute bar confirms entry.", "",
    ]
    for title, name in (("Development", "development"), ("Year-long stability", "stability"), ("July 2026 confirmation", "july_confirmation"), ("Latest August 1-7 pulse", "latest_pulse")):
        record = summaries[name]
        lines.extend([
            f"## {title}",
            f"- interval: `{record['start']} -> {record['end_exclusive']}`",
            f"- trades / day: `{record['trades']} / {record['trades_per_day']}`",
            f"- gross / net mean: `{record['mean_gross_bps']} / {record['mean_net_bps']}` bp",
            f"- win rate / payoff: `{record['win_rate']} / {record['payoff_ratio']}`",
            f"- net t-stat: `{record['net_t_stat']}`",
            f"- positive months: `{record['positive_months']} / {record['active_months']}`",
            f"- route stats: `{record['route_stats']}`",
            f"- symbol counts: `{record['symbol_counts']}`",
            f"- exit reasons: `{record['exit_reasons']}`", "",
        ])
    lines.extend([
        "## Advance checks", *[f"- {key}: `{value}`" for key, value in checks.items()], "",
        "## Cross-split route evidence", f"`{cross_split}`", "", "## Decision", decision, "",
        "This is an economic mechanism screen. A pass still requires frozen NautilusTrader execution, current-NAV 3% sizing, one global slot and continuous-account validation.",
    ])
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
