#!/usr/bin/env python3
"""Candidate 15 V36 24-hour price/CVD boundary-divergence screen."""
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
HIGH_ROUTE = "FAILED_HIGH_CVD_DIVERGENCE"
LOW_ROUTE = "FAILED_LOW_CVD_DIVERGENCE"


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


def resample_thirty(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample(
        "30min",
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
    return output[output["count"] == 6].copy()


def build_state(
    symbol: str,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    frame = resample_thirty(five)
    lookback = int(rules["auction_lookback_bars"])
    close = frame["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(
        int(rules["atr_lookback_bars"]),
        min_periods=int(rules["atr_lookback_bars"]),
    ).mean()

    signed_flow = (
        2.0 * frame["taker_buy_quote_volume"]
        - frame["quote_volume"]
    )
    rolling_cvd = signed_flow.rolling(
        lookback,
        min_periods=lookback,
    ).sum()
    prior_price_high = frame["high"].shift(1).rolling(
        lookback,
        min_periods=lookback,
    ).max()
    prior_price_low = frame["low"].shift(1).rolling(
        lookback,
        min_periods=lookback,
    ).min()
    prior_cvd_high = rolling_cvd.shift(1).rolling(
        lookback,
        min_periods=lookback,
    ).max()
    prior_cvd_low = rolling_cvd.shift(1).rolling(
        lookback,
        min_periods=lookback,
    ).min()
    cvd_range = (prior_cvd_high - prior_cvd_low).abs().replace(
        0.0,
        np.nan,
    )

    typical = (
        frame["high"] + frame["low"] + frame["close"]
    ) / 3.0
    prior_value = (
        (typical * frame["quote_volume"])
        .shift(1)
        .rolling(lookback, min_periods=lookback)
        .sum()
        / frame["quote_volume"]
        .shift(1)
        .rolling(lookback, min_periods=lookback)
        .sum()
        .replace(0.0, np.nan)
    )
    volume_median = frame["quote_volume"].shift(1).rolling(
        int(rules["volume_median_lookback_bars"]),
        min_periods=lookback,
    ).median()
    volume_ratio = (
        frame["quote_volume"] / volume_median.replace(0.0, np.nan)
    )
    pressure = (
        2.0
        * frame["taker_buy_quote_volume"]
        / frame["quote_volume"].replace(0.0, np.nan)
        - 1.0
    ).clip(-1.0, 1.0)

    high_breach_atr = (
        frame["high"] - prior_price_high
    ) / atr.replace(0.0, np.nan)
    low_breach_atr = (
        prior_price_low - frame["low"]
    ) / atr.replace(0.0, np.nan)
    high_divergence = (
        prior_cvd_high - rolling_cvd
    ) / cvd_range
    low_divergence = (
        rolling_cvd - prior_cvd_low
    ) / cvd_range

    output = frame.copy()
    output["symbol"] = symbol
    output["event_ts"] = output.index
    output["atr_30m"] = atr
    output["signed_flow"] = signed_flow
    output["rolling_cvd_24h"] = rolling_cvd
    output["prior_cvd_high"] = prior_cvd_high
    output["prior_cvd_low"] = prior_cvd_low
    output["prior_price_high"] = prior_price_high
    output["prior_price_low"] = prior_price_low
    output["prior_value"] = prior_value
    output["volume_ratio"] = volume_ratio
    output["taker_pressure"] = pressure
    output["high_breach_atr"] = high_breach_atr
    output["low_breach_atr"] = low_breach_atr
    output["high_cvd_divergence_fraction"] = high_divergence
    output["low_cvd_divergence_fraction"] = low_divergence
    return output.replace([np.inf, -np.inf], np.nan)


def confirm_and_simulate(
    item: pd.Series,
    five: pd.DataFrame,
    rules: dict[str, Any],
    route: str,
) -> dict[str, Any] | None:
    event_ts = pd.Timestamp(item["event_ts"])
    direction = -1.0 if route == HIGH_ROUTE else 1.0
    old_boundary = float(
        item["prior_price_high"]
        if route == HIGH_ROUTE
        else item["prior_price_low"]
    )
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
    if direction * confirmation_return <= 0.0:
        return None
    if direction < 0.0 and closing >= old_boundary:
        return None
    if direction > 0.0 and closing <= old_boundary:
        return None
    if body_fraction < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if (
        direction * pressure
        < float(rules["minimum_opposite_taker_pressure"])
    ):
        return None

    entry = closing
    target = float(item["prior_value"])
    if direction * (target - entry) <= 0.0:
        return None
    buffer = float(rules["stop_atr_buffer"]) * float(item["atr_30m"])
    stop = (
        float(item["high"]) + buffer
        if direction < 0.0
        else float(item["low"]) - buffer
    )
    if direction * (entry - stop) <= 0.0:
        return None

    cap = confirmation_end + pd.Timedelta(
        minutes=int(rules["maximum_hold_minutes"])
    )
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason = ""
    for timestamp, bar in path.iterrows():
        if direction > 0.0:
            if float(bar["low"]) <= stop:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = stop
                exit_reason = "BOUNDARY_EXTREME_INVALIDATED"
                break
            if float(bar["high"]) >= target:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = target
                exit_reason = "PRIOR_VALUE_REACHED"
                break
        else:
            if float(bar["high"]) >= stop:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = stop
                exit_reason = "BOUNDARY_EXTREME_INVALIDATED"
                break
            if float(bar["low"]) <= target:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = target
                exit_reason = "PRIOR_VALUE_REACHED"
                break
    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        exit_reason = "SIX_HOUR_CAP"

    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    divergence = float(
        item["high_cvd_divergence_fraction"]
        if route == HIGH_ROUTE
        else item["low_cvd_divergence_fraction"]
    )
    breach = float(
        item["high_breach_atr"]
        if route == HIGH_ROUTE
        else item["low_breach_atr"]
    )
    quality = (
        max(divergence, 0.0)
        * max(breach, 0.0)
        * max(float(item["volume_ratio"]), 0.0)
    )
    return {
        "event_ts": event_ts,
        "entry_ts": confirmation_end,
        "exit_ts": exit_ts,
        "symbol": str(item["symbol"]),
        "route": route,
        "direction": "LONG" if direction > 0.0 else "SHORT",
        "direction_value": direction,
        "old_boundary": old_boundary,
        "prior_value": target,
        "event_high": float(item["high"]),
        "event_low": float(item["low"]),
        "entry_price": entry,
        "initial_stop": stop,
        "target_price": target,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "volume_ratio": float(item["volume_ratio"]),
        "event_taker_pressure": float(item["taker_pressure"]),
        "cvd_divergence_fraction": divergence,
        "boundary_breach_atr": breach,
        "confirmation_return": confirmation_return,
        "confirmation_body_fraction": body_fraction,
        "confirmation_taker_pressure": pressure,
        "rank_score": quality,
        "gross_return": gross,
        "net_return": gross - cost,
        "holding_minutes": (
            exit_ts - confirmation_end
        ).total_seconds() / 60.0,
    }


def candidates(
    states: dict[str, pd.DataFrame],
    five_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in states.items():
        common_mask = (
            frame["volume_ratio"] >= float(rules["minimum_volume_ratio"])
        )
        high = frame[
            common_mask
            & (
                frame["high_breach_atr"]
                >= float(rules["minimum_boundary_breach_atr"])
            )
            & (
                frame["high_cvd_divergence_fraction"]
                >= float(rules["minimum_cvd_divergence_fraction"])
            )
        ]
        low = frame[
            common_mask
            & (
                frame["low_breach_atr"]
                >= float(rules["minimum_boundary_breach_atr"])
            )
            & (
                frame["low_cvd_divergence_fraction"]
                >= float(rules["minimum_cvd_divergence_fraction"])
            )
        ]
        for _, item in high.iterrows():
            result = confirm_and_simulate(
                item,
                five_frames[symbol],
                rules,
                HIGH_ROUTE,
            )
            if result is not None:
                rows.append(result)
        for _, item in low.iterrows():
            result = confirm_and_simulate(
                item,
                five_frames[symbol],
                rules,
                LOW_ROUTE,
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
    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_cooldown_minutes"])
    )
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        symbol = str(winner["symbol"])
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        if (
            symbol in last_symbol_entry
            and timestamp < last_symbol_entry[symbol] + cooldown
        ):
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


def summarize(
    frame: pd.DataFrame,
    start: str,
    end: str,
) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end, tz="UTC")
    sample = frame[
        (frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)
    ].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end,
            "calendar_days": days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "win_rate": None,
            "payoff_ratio": None,
            "net_t_stat": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "route_stats": {},
            "symbol_counts": {},
            "exit_reasons": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    route_stats: dict[str, Any] = {}
    for route, current in sample.groupby("route", sort=True):
        route_stats[str(route)] = {
            "trades": len(current.index),
            "mean_net_bps": float(
                current["net_return"].mean() * 10_000.0
            ),
            "win_rate": float((current["net_return"] > 0.0).mean()),
            "payoff_ratio": payoff(current["net_return"]),
            "net_t_stat": t_stat(current["net_return"]),
        }
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff(sample["net_return"]),
        "net_t_stat": t_stat(sample["net_return"]),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "route_stats": route_stats,
        "symbol_counts": {
            str(key): int(value)
            for key, value in sample["symbol"].value_counts().items()
        },
        "exit_reasons": {
            str(key): int(value)
            for key, value in sample["exit_reason"].value_counts().items()
        },
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v36-cvd-boundary-divergence-v1":
        raise RuntimeError("unexpected V36 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v36-data-manifest-v1",
            "files": manifest,
        },
    )
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {
        symbol: common.load_symbol(paths[symbol], start, end)
        for symbol in SYMBOLS
    }
    states = {
        symbol: build_state(symbol, frame, protocol["fixed_rules"])
        for symbol, frame in five_frames.items()
    }
    all_candidates = candidates(
        states,
        five_frames,
        protocol["fixed_rules"],
    )
    selected, skips = arbitrate(
        all_candidates,
        protocol["fixed_rules"],
    )
    all_candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    summaries = {
        name: summarize(
            selected,
            evaluation[f"{name}_start"],
            evaluation[f"{name}_end_exclusive"],
        )
        for name in (
            "development",
            "stability",
            "july_confirmation",
            "latest_pulse",
        )
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    total = sum(stability["symbol_counts"].values())
    max_symbol_share = (
        max(stability["symbol_counts"].values(), default=0)
        / max(total, 1)
    )
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_stability_mean_net": (
            stability["mean_net_bps"] is not None
            and stability["mean_net_bps"]
            >= float(gate["minimum_stability_mean_net_bps"])
        ),
        "stability_net_t_stat": (
            stability["net_t_stat"] is not None
            and stability["net_t_stat"]
            >= float(gate["minimum_stability_net_t_stat"])
        ),
        "stability_positive_month_share": (
            stability["positive_month_share"]
            >= float(gate["minimum_stability_positive_month_share"])
        ),
        "stability_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_stability_trades_per_calendar_day"])
        ),
        "positive_july_confirmation_mean_net": (
            july["mean_net_bps"] is not None
            and july["mean_net_bps"] > 0.0
        ),
        "july_confirmation_trade_count": (
            july["trades"] >= int(gate["minimum_july_confirmation_trades"])
        ),
        "positive_latest_pulse_mean_net": (
            pulse["mean_net_bps"] is not None
            and pulse["mean_net_bps"]
            >= float(gate["minimum_latest_pulse_mean_net_bps"])
        ),
        "latest_pulse_trade_count": (
            pulse["trades"] >= int(gate["minimum_latest_pulse_trades"])
        ),
        "symbol_concentration": (
            max_symbol_share <= float(gate["maximum_single_symbol_share"])
        ),
    }
    cross_split: dict[str, Any] = {}
    for route in (HIGH_ROUTE, LOW_ROUTE):
        splits = {
            name: summary["route_stats"].get(route)
            for name, summary in summaries.items()
        }
        cross_split[route] = {
            "positive_across_all_declared_splits": all(
                record is not None and record["mean_net_bps"] > 0.0
                for record in splits.values()
            ),
            "splits": splits,
        }
    survivors = [
        route
        for route, record in cross_split.items()
        if record["positive_across_all_declared_splits"]
    ]
    advance = all(checks.values()) and bool(survivors)
    classification = (
        "V36_CVD_BOUNDARY_DIVERGENCE_ADVANCE_TO_NAUTILUS"
        if advance
        else "V36_CVD_BOUNDARY_DIVERGENCE_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        f"Cross-split-positive fixed routes: {survivors}. "
        + (
            "Freeze and implement the survivor in NautilusTrader."
            if advance
            else "Do not tune boundary, flow divergence, confirmation, "
            "stop or value-target rules."
        )
    )
    summary = {
        "schema": "candidate-15-v36-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "executable_candidates": len(all_candidates.index),
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
        "# Candidate 15 V36 — CVD boundary divergence",
        "",
        f"**{classification}**",
        "",
        "A new completed 24-hour price boundary is treated as failed only when "
        "rolling signed taker flow does not confirm it and a separate fifteen-"
        "minute bar reclaims the old boundary.",
        "",
    ]
    for title, name in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("July 2026 confirmation", "july_confirmation"),
        ("Latest August 1-7 pulse", "latest_pulse"),
    ):
        record = summaries[name]
        lines.extend(
            [
                f"## {title}",
                f"- interval: `{record['start']} -> {record['end_exclusive']}`",
                f"- trades / day: `{record['trades']} / "
                f"{record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / "
                f"{record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / "
                f"{record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / "
                f"{record['active_months']}`",
                f"- route stats: `{record['route_stats']}`",
                f"- symbol counts: `{record['symbol_counts']}`",
                f"- exit reasons: `{record['exit_reasons']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Cross-split route evidence",
            f"`{cross_split}`",
            "",
            "## Decision",
            decision,
            "",
            "This is an economic mechanism screen. A pass still requires "
            "frozen NautilusTrader execution, current-NAV 3% sizing, one "
            "global slot and continuous-account validation.",
        ]
    )
    (output / "RESULT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
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
