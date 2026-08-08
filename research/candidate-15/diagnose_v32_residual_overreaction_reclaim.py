#!/usr/bin/env python3
"""Candidate 15 V32 residual four-hour overreaction reclaim screen."""
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
ROUTE = "RESIDUAL_4H_OVERREACTION_RECLAIM"


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


def resample_four_hour(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample(
        "4h",
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
    return output[output["count"] == 48].copy()


def build_event_frame(
    symbol: str,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    event = resample_four_hour(five)
    close = event["close"].astype(float)
    previous_close = close.shift(1)
    close_return = close / previous_close - 1.0
    body_return = event["close"] / event["open"] - 1.0
    true_range = pd.concat(
        [
            event["high"] - event["low"],
            (event["high"] - previous_close).abs(),
            (event["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(
        int(rules["atr_lookback_bars"]),
        min_periods=int(rules["atr_lookback_bars"]),
    ).mean()
    prior_mean = close_return.shift(1).rolling(
        int(rules["return_z_lookback_bars"]),
        min_periods=int(rules["return_z_minimum_prior_bars"]),
    ).mean()
    prior_std = close_return.shift(1).rolling(
        int(rules["return_z_lookback_bars"]),
        min_periods=int(rules["return_z_minimum_prior_bars"]),
    ).std(ddof=0).replace(0.0, np.nan)
    volume_median = event["quote_volume"].shift(1).rolling(
        int(rules["volume_median_lookback_bars"]),
        min_periods=int(rules["volume_median_lookback_bars"]),
    ).median()
    output = event.copy()
    output["symbol"] = symbol
    output["close_return"] = close_return
    output["body_return"] = body_return
    output["direction_value"] = np.sign(body_return)
    output["return_z"] = (close_return - prior_mean) / prior_std
    output["atr_4h"] = atr
    output["body_displacement_atr"] = (
        (event["close"] - event["open"]).abs()
        / atr.replace(0.0, np.nan)
    )
    output["signed_body_atr"] = (
        (event["close"] - event["open"])
        / atr.replace(0.0, np.nan)
    )
    output["volume_ratio"] = (
        event["quote_volume"] / volume_median.replace(0.0, np.nan)
    )
    output["event_midpoint"] = (event["open"] + event["close"]) / 2.0
    output["event_ts"] = output.index
    return output.replace([np.inf, -np.inf], np.nan)


def add_cross_market_state(
    frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    signed = pd.DataFrame(
        {symbol: frame["signed_body_atr"] for symbol, frame in frames.items()}
    )
    median = signed.median(axis=1)
    residual = signed.sub(median, axis=0)
    absolute = residual.abs()
    top = absolute.max(axis=1)
    second = absolute.apply(
        lambda row: row.nlargest(2).iloc[-1]
        if row.notna().sum() >= 2 else np.nan,
        axis=1,
    )
    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        enriched = frame.copy()
        enriched["market_median_body_atr"] = median
        enriched["residual_body_atr"] = residual[symbol]
        enriched["is_absolute_residual_leader"] = (
            absolute[symbol] >= top - 1e-12
        )
        enriched["residual_leader_gap_atr"] = top - second
        output[symbol] = enriched
    return output


def confirm_and_simulate(
    item: pd.Series,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    event_ts = pd.Timestamp(item["event_ts"])
    event_direction = float(item["direction_value"])
    reclaim_direction = -event_direction
    confirmation_end = event_ts + pd.Timedelta(
        minutes=int(rules["confirmation_minutes"])
    )
    confirmation = five[
        (five.index > event_ts) & (five.index <= confirmation_end)
    ]
    if len(confirmation.index) != int(rules["confirmation_minutes"]) // 5:
        return None
    opening = float(confirmation.iloc[0]["open"])
    closing = float(confirmation.iloc[-1]["close"])
    high = float(confirmation["high"].max())
    low = float(confirmation["low"].min())
    body_fraction = abs(closing - opening) / max(high - low, 1e-12)
    pressure = (
        2.0
        * float(confirmation["taker_buy_quote_volume"].sum())
        / max(float(confirmation["quote_volume"].sum()), 1e-12)
        - 1.0
    )
    confirmation_return = closing / opening - 1.0
    event_body = abs(float(item["close"]) - float(item["open"]))
    reclaimed = event_direction * (float(item["close"]) - closing)
    if reclaim_direction * confirmation_return <= 0.0:
        return None
    if reclaimed < float(rules["minimum_reclaim_fraction_of_event_body"]) * event_body:
        return None
    if body_fraction < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if reclaim_direction * pressure < float(rules["minimum_opposite_taker_pressure"]):
        return None
    entry = closing
    target = float(item["event_midpoint"])
    if reclaim_direction * (target - entry) <= 0.0:
        return None
    buffer = float(rules["stop_atr_buffer"]) * float(item["atr_4h"])
    stop = (
        float(item["high"]) + buffer
        if reclaim_direction < 0.0
        else float(item["low"]) - buffer
    )
    if reclaim_direction * (entry - stop) <= 0.0:
        return None
    cap = confirmation_end + pd.Timedelta(
        minutes=int(rules["maximum_hold_minutes"])
    )
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    reason = ""
    for timestamp, bar in path.iterrows():
        if reclaim_direction > 0.0:
            if float(bar["low"]) <= stop:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = stop
                reason = "EVENT_EXTREME_INVALIDATED"
                break
            if float(bar["high"]) >= target:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = target
                reason = "EVENT_MIDPOINT_REACHED"
                break
        else:
            if float(bar["high"]) >= stop:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = stop
                reason = "EVENT_EXTREME_INVALIDATED"
                break
            if float(bar["low"]) <= target:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = target
                reason = "EVENT_MIDPOINT_REACHED"
                break
    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        reason = "EIGHT_HOUR_CAP"
    gross = reclaim_direction * (float(exit_price) / entry - 1.0)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    quality = (
        abs(float(item["return_z"]))
        * abs(float(item["residual_body_atr"]))
        * max(float(item["volume_ratio"]), 0.0)
    )
    return {
        "event_ts": event_ts,
        "entry_ts": confirmation_end,
        "exit_ts": exit_ts,
        "symbol": str(item["symbol"]),
        "route": ROUTE,
        "direction": "LONG" if reclaim_direction > 0.0 else "SHORT",
        "direction_value": reclaim_direction,
        "event_direction": "UP" if event_direction > 0.0 else "DOWN",
        "event_open": float(item["open"]),
        "event_high": float(item["high"]),
        "event_low": float(item["low"]),
        "event_close": float(item["close"]),
        "event_midpoint": target,
        "entry_price": entry,
        "initial_stop": stop,
        "target_price": target,
        "exit_price": float(exit_price),
        "exit_reason": reason,
        "return_z": float(item["return_z"]),
        "body_displacement_atr": float(item["body_displacement_atr"]),
        "residual_body_atr": float(item["residual_body_atr"]),
        "residual_leader_gap_atr": float(item["residual_leader_gap_atr"]),
        "volume_ratio": float(item["volume_ratio"]),
        "confirmation_return": confirmation_return,
        "confirmation_body_fraction": body_fraction,
        "confirmation_taker_pressure": pressure,
        "rank_score": quality,
        "gross_return": gross,
        "net_return": gross - cost,
        "holding_minutes": (exit_ts - confirmation_end).total_seconds() / 60.0,
    }


def candidates(
    frames: dict[str, pd.DataFrame],
    five_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in frames.items():
        eligible = frame[
            (frame["return_z"].abs() >= float(rules["absolute_return_z_min"]))
            & (
                frame["body_displacement_atr"]
                >= float(rules["event_displacement_atr_min"])
            )
            & (frame["volume_ratio"] >= float(rules["minimum_volume_ratio"]))
            & (
                frame["residual_body_atr"].abs()
                >= float(rules["residual_overreaction_atr_min"])
            )
            & (
                frame["residual_leader_gap_atr"]
                >= float(rules["residual_leader_gap_atr_min"])
            )
            & frame["is_absolute_residual_leader"].fillna(False)
            & (
                np.sign(frame["return_z"])
                == frame["direction_value"]
            )
        ]
        for _, item in eligible.iterrows():
            result = confirm_and_simulate(
                item,
                five_frames[symbol],
                rules,
            )
            if result is not None:
                rows.append(result)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["entry_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )


def arbitrate(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> tuple[pd.DataFrame, Counter[str]]:
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
            "positive_month_share": 0.0, "symbol_counts": {},
            "exit_reasons": {}, "mean_holding_minutes": None,
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
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
        "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()},
        "exit_reasons": {str(k): int(v) for k, v in sample["exit_reason"].value_counts().items()},
        "mean_holding_minutes": float(sample["holding_minutes"].mean()),
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v32-residual-overreaction-reclaim-v1":
        raise RuntimeError("unexpected V32 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(output / "data_manifest.json", {"schema": "candidate-15-v32-data-manifest-v1", "files": manifest})
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {symbol: common.load_symbol(paths[symbol], start, end) for symbol in SYMBOLS}
    event_frames = {
        symbol: build_event_frame(symbol, frame, protocol["fixed_rules"])
        for symbol, frame in five_frames.items()
    }
    event_frames = add_cross_market_state(event_frames)
    all_candidates = candidates(event_frames, five_frames, protocol["fixed_rules"])
    selected, skips = arbitrate(all_candidates, protocol["fixed_rules"])
    all_candidates.to_csv(output / "all_executable_candidates.csv", index=False)
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
    positive_all = all(
        summaries[name]["mean_net_bps"] is not None
        and summaries[name]["mean_net_bps"] > 0.0
        for name in summaries
    )
    advance = all(checks.values()) and positive_all
    classification = "V32_RESIDUAL_OVERREACTION_RECLAIM_ADVANCE_TO_NAUTILUS" if advance else "V32_RESIDUAL_OVERREACTION_RECLAIM_REJECTED_OR_UNDERPOWERED"
    decision = (
        "The fixed reclaim scenario survived every split; freeze and implement in NautilusTrader."
        if advance
        else "The fixed residual overreaction reclaim did not survive all splits. Do not tune overreaction, reclaim, stop or midpoint target thresholds."
    )
    summary = {
        "schema": "candidate-15-v32-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "executable_candidates": len(all_candidates.index),
        "selected_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        **summaries,
        "advance_checks": checks,
        "positive_across_all_declared_splits": positive_all,
        "maximum_stability_symbol_share": max_symbol_share,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)
    lines = [
        "# Candidate 15 V32 — Residual four-hour overreaction reclaim", "", f"**{classification}**", "",
        "A completed four-hour cross-sectional residual overreaction is traded only after a separately completed opposite reclaim bar; the target is the event auction midpoint.", "",
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
            f"- mean holding minutes: `{record['mean_holding_minutes']}`",
            f"- symbol counts: `{record['symbol_counts']}`",
            f"- exit reasons: `{record['exit_reasons']}`", "",
        ])
    lines.extend([
        "## Advance checks", *[f"- {key}: `{value}`" for key, value in checks.items()], "",
        "## Decision", decision, "",
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
