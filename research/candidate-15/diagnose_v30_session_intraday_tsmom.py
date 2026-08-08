#!/usr/bin/env python3
"""Candidate 15 V30 session-level intraday time-series momentum screen."""
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
BREADTH = "SESSION_BREADTH_TSMOM"
ACTIVITY = "HIGH_ACTIVITY_SESSION_TSMOM"


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


def prior_atr(frame: pd.DataFrame, lookback: int) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.shift(1).rolling(
        lookback,
        min_periods=lookback,
    ).mean()


def session_records(
    symbol: str,
    frame: pd.DataFrame,
    protocol: dict[str, Any],
) -> pd.DataFrame:
    rules = protocol["fixed_rules"]
    start_day = date.fromisoformat(protocol["data"]["start"])
    end_day = date.fromisoformat(protocol["data"]["end_exclusive"])
    atr = prior_atr(frame, int(rules["atr_lookback_bars"]))
    rows: list[dict[str, Any]] = []
    cursor = start_day
    while cursor < end_day:
        for session in rules["sessions"]:
            start = pd.Timestamp(
                datetime(
                    cursor.year,
                    cursor.month,
                    cursor.day,
                    int(session["start_hour_utc"]),
                    tzinfo=UTC,
                )
            )
            end = start + pd.Timedelta(hours=int(session["length_hours"]))
            opening_end = start + pd.Timedelta(
                minutes=int(rules["opening_window_minutes"])
            )
            final_start = end - pd.Timedelta(
                minutes=int(rules["final_window_minutes"])
            )
            confirmation_end = final_start + pd.Timedelta(
                minutes=int(rules["confirmation_minutes"])
            )
            opening = frame[(frame.index > start) & (frame.index <= opening_end)]
            confirmation = frame[
                (frame.index > final_start)
                & (frame.index <= confirmation_end)
            ]
            remaining = frame[
                (frame.index > confirmation_end)
                & (frame.index <= end)
            ]
            if (
                len(opening.index) != int(rules["opening_window_minutes"]) // 5
                or len(confirmation.index) != int(rules["confirmation_minutes"]) // 5
                or len(remaining.index)
                != (
                    int(rules["final_window_minutes"])
                    - int(rules["confirmation_minutes"])
                ) // 5
            ):
                continue
            opening_price = float(opening.iloc[0]["open"])
            opening_close = float(opening.iloc[-1]["close"])
            opening_return = opening_close / opening_price - 1.0
            opening_range = (
                float(opening["high"].max()) - float(opening["low"].min())
            ) / max(opening_price, 1e-12)
            opening_volume = float(opening["quote_volume"].sum())
            confirm = confirmation.iloc[-1]
            confirm_open = float(confirm["open"])
            confirm_close = float(confirm["close"])
            confirm_high = float(confirm["high"])
            confirm_low = float(confirm["low"])
            confirm_return = confirm_close / confirm_open - 1.0
            confirm_body = abs(confirm_close - confirm_open) / max(
                confirm_high - confirm_low,
                1e-12,
            )
            confirm_pressure = (
                2.0
                * float(confirm["taker_buy_quote_volume"])
                / max(float(confirm["quote_volume"]), 1e-12)
                - 1.0
            )
            atr_value = atr.get(confirmation_end, np.nan)
            if pd.isna(atr_value):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "session_name": str(session["name"]),
                    "session_start": start,
                    "session_end": end,
                    "opening_end": opening_end,
                    "opening_return": opening_return,
                    "opening_abs_return": abs(opening_return),
                    "opening_range": opening_range,
                    "opening_quote_volume": opening_volume,
                    "direction_value": float(np.sign(opening_return)),
                    "confirmation_end": confirmation_end,
                    "confirmation_return": confirm_return,
                    "confirmation_body_fraction": confirm_body,
                    "confirmation_taker_pressure": confirm_pressure,
                    "confirmation_high": confirm_high,
                    "confirmation_low": confirm_low,
                    "entry_price": confirm_close,
                    "prior_atr_1h": float(atr_value),
                    "remaining_count": len(remaining.index),
                }
            )
        cursor += timedelta(days=1)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["symbol", "session_name", "session_start"],
        kind="stable",
    )


def add_prior_activity(
    records: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    outputs: list[pd.DataFrame] = []
    window = int(rules["activity_rolling_sessions"])
    minimum = int(rules["activity_minimum_prior_sessions"])
    return_q = float(rules["opening_absolute_return_quantile"])
    activity_q = float(rules["opening_activity_quantile"])
    for (_, _), group in records.groupby(
        ["symbol", "session_name"],
        sort=False,
    ):
        current = group.sort_values("session_start", kind="stable").copy()
        current["prior_abs_return_threshold"] = (
            current["opening_abs_return"]
            .shift(1)
            .rolling(window, min_periods=minimum)
            .quantile(return_q)
        )
        current["prior_volume_threshold"] = (
            current["opening_quote_volume"]
            .shift(1)
            .rolling(window, min_periods=minimum)
            .quantile(activity_q)
        )
        current["prior_range_threshold"] = (
            current["opening_range"]
            .shift(1)
            .rolling(window, min_periods=minimum)
            .quantile(activity_q)
        )
        outputs.append(current)
    return pd.concat(outputs, ignore_index=True)


def add_breadth(records: pd.DataFrame) -> pd.DataFrame:
    frame = records.copy()
    directions = frame.pivot(
        index="session_start",
        columns="symbol",
        values="direction_value",
    )
    positive = (directions > 0.0).mean(axis=1)
    negative = (directions < 0.0).mean(axis=1)
    frame["directional_breadth"] = np.where(
        frame["direction_value"] > 0.0,
        frame["session_start"].map(positive),
        frame["session_start"].map(negative),
    )
    return frame


def executable_candidates(
    records: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
    protocol: dict[str, Any],
) -> pd.DataFrame:
    rules = protocol["fixed_rules"]
    rows: list[dict[str, Any]] = []
    for item in records.to_dict("records"):
        required = (
            "prior_abs_return_threshold",
            "prior_volume_threshold",
            "prior_range_threshold",
            "directional_breadth",
        )
        if any(pd.isna(item[name]) for name in required):
            continue
        direction = float(item["direction_value"])
        if direction == 0.0:
            continue
        magnitude = float(item["opening_abs_return"]) >= float(
            item["prior_abs_return_threshold"]
        )
        high_volume = float(item["opening_quote_volume"]) >= float(
            item["prior_volume_threshold"]
        )
        high_range = float(item["opening_range"]) >= float(
            item["prior_range_threshold"]
        )
        if not magnitude or not (high_volume or high_range):
            continue
        if direction * float(item["confirmation_return"]) <= 0.0:
            continue
        if (
            float(item["confirmation_body_fraction"])
            < float(rules["minimum_confirmation_body_fraction"])
        ):
            continue
        if (
            direction * float(item["confirmation_taker_pressure"])
            < float(rules["minimum_directional_taker_pressure"])
        ):
            continue
        breadth = (
            float(item["directional_breadth"])
            >= float(rules["breadth_fraction_min"])
        )
        route = BREADTH if breadth else ACTIVITY
        entry = float(item["entry_price"])
        buffer = float(rules["stop_atr_buffer"]) * float(item["prior_atr_1h"])
        stop = (
            float(item["confirmation_low"]) - buffer
            if direction > 0.0
            else float(item["confirmation_high"]) + buffer
        )
        if direction * (entry - stop) <= 0.0:
            continue
        quality = (
            float(item["opening_abs_return"])
            / max(float(item["prior_abs_return_threshold"]), 1e-12)
        )
        quality *= max(
            float(item["opening_quote_volume"])
            / max(float(item["prior_volume_threshold"]), 1e-12),
            float(item["opening_range"])
            / max(float(item["prior_range_threshold"]), 1e-12),
        )
        quality *= 1.0 + float(item["directional_breadth"])
        frame = frames[str(item["symbol"])]
        path = frame[
            (frame.index > pd.Timestamp(item["confirmation_end"]))
            & (frame.index <= pd.Timestamp(item["session_end"]))
        ]
        if len(path.index) != int(item["remaining_count"]):
            continue
        exit_ts: pd.Timestamp | None = None
        exit_price: float | None = None
        exit_reason = ""
        for timestamp, bar in path.iterrows():
            if direction > 0.0 and float(bar["low"]) <= stop:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = stop
                exit_reason = "CONFIRMATION_LEG_INVALIDATED"
                break
            if direction < 0.0 and float(bar["high"]) >= stop:
                exit_ts = pd.Timestamp(timestamp)
                exit_price = stop
                exit_reason = "CONFIRMATION_LEG_INVALIDATED"
                break
        if exit_ts is None:
            exit_ts = pd.Timestamp(path.index[-1])
            exit_price = float(path.iloc[-1]["close"])
            exit_reason = "SESSION_END"
        gross = direction * (float(exit_price) / entry - 1.0)
        cost = (
            float(rules["execution_round_trip_cost_bps"])
            + float(rules["funding_and_unmodeled_impact_reserve_bps"])
        ) / 10_000.0
        rows.append(
            {
                **item,
                "route": route,
                "route_priority": 2 if route == BREADTH else 1,
                "entry_ts": pd.Timestamp(item["confirmation_end"]),
                "initial_stop": stop,
                "rank_score": quality,
                "exit_ts": exit_ts,
                "exit_price": float(exit_price),
                "exit_reason": exit_reason,
                "gross_return": gross,
                "net_return": gross - cost,
                "holding_minutes": (
                    exit_ts - pd.Timestamp(item["confirmation_end"])
                ).total_seconds() / 60.0,
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["entry_ts", "route_priority", "rank_score", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )


def arbitrate(candidates: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if candidates.empty:
        return candidates.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in candidates.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_SESSION_LOSER"] += max(0, len(group.index) - 1)
        if pd.Timestamp(entry_ts) < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    if not selected:
        return candidates.iloc[0:0].copy(), skips
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
            "session_counts": {},
            "symbol_counts": {},
            "exit_reasons": {},
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
        "session_counts": {
            str(key): int(value)
            for key, value in sample["session_name"].value_counts().items()
        },
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
    if protocol["schema"] != "candidate-15-v30-session-intraday-tsmom-v1":
        raise RuntimeError("unexpected V30 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(
        output / "data_manifest.json",
        {"schema": "candidate-15-v30-data-manifest-v1", "files": manifest},
    )
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    frames = {
        symbol: common.load_symbol(paths[symbol], start, end)
        for symbol in SYMBOLS
    }
    records = pd.concat(
        [
            session_records(symbol, frame, protocol)
            for symbol, frame in frames.items()
        ],
        ignore_index=True,
    )
    records = add_prior_activity(records, protocol["fixed_rules"])
    records = add_breadth(records)
    candidates = executable_candidates(records, frames, protocol)
    selected, skips = arbitrate(candidates)
    records.to_csv(output / "all_session_states.csv", index=False)
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    summaries = {
        name: summarize(
            selected,
            evaluation[f"{name}_start"],
            evaluation[f"{name}_end_exclusive"],
        )
        for name in ("development", "stability", "july_confirmation", "latest_pulse")
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    total_stability = sum(stability["symbol_counts"].values())
    max_symbol_share = (
        max(stability["symbol_counts"].values(), default=0)
        / max(total_stability, 1)
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
            july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0
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
    for route in (BREADTH, ACTIVITY):
        route_splits = {
            name: summary["route_stats"].get(route)
            for name, summary in summaries.items()
        }
        cross_split[route] = {
            "positive_across_all_declared_splits": all(
                record is not None and record["mean_net_bps"] > 0.0
                for record in route_splits.values()
            ),
            "splits": route_splits,
        }
    survivors = [
        route for route, record in cross_split.items()
        if record["positive_across_all_declared_splits"]
    ]
    advance = all(checks.values()) and bool(survivors)
    classification = (
        "V30_SESSION_INTRADAY_TSMOM_ADVANCE_TO_NAUTILUS"
        if advance
        else "V30_SESSION_INTRADAY_TSMOM_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        f"Cross-split-positive fixed routes: {survivors}. "
        + (
            "Freeze and implement the survivor in NautilusTrader."
            if advance
            else "Do not tune session clocks, activity thresholds, confirmation "
            "or stop geometry; move to another independent mechanism."
        )
    )
    summary = {
        "schema": "candidate-15-v30-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "session_states": len(records.index),
        "executable_candidates": len(candidates.index),
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
        "# Candidate 15 V30 — Session intraday time-series momentum",
        "",
        f"**{classification}**",
        "",
        "The first completed half-hour of each Asia, Europe and America session "
        "sets direction only when activity is high. Entry requires a separate "
        "first five-minute confirmation in the final half-hour.",
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
                f"- trades / day: `{record['trades']} / {record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / "
                f"{record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / "
                f"{record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / "
                f"{record['active_months']}`",
                f"- route stats: `{record['route_stats']}`",
                f"- session counts: `{record['session_counts']}`",
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
            "This is an economic mechanism screen. A pass still requires exact "
            "NautilusTrader orders, current-NAV 3% sizing, one global slot and "
            "continuous-account validation.",
        ]
    )
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
