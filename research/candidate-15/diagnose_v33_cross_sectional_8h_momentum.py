#!/usr/bin/env python3
"""Candidate 15 V33 cross-sectional eight-hour momentum screen.

The state is observed only at completed 00:00, 08:00 and 16:00 UTC decision
boundaries.  A separately completed one-hour bar confirms the selected market;
entry occurs at that bar's close.  Stops are tested before any same-bar trail
update and a single chronological slot is enforced across all four markets.
"""
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
BREADTH = "BREADTH_TREND_LEADER"
RELATIVE = "CROSS_SECTIONAL_TREND_LEADER"


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


def resample_hourly(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample(
        "1h",
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
    return output[output["count"] == 12].copy()


def build_decision_frame(
    symbol: str,
    five: pd.DataFrame,
    rules: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    hourly = resample_hourly(five)
    close = hourly["close"].astype(float)
    previous_close = close.shift(1)
    hourly_return = close / previous_close - 1.0

    volatility = (
        hourly_return.shift(1)
        .rolling(
            int(rules["volatility_lookback_hours"]),
            min_periods=int(rules["volatility_minimum_prior_hours"]),
        )
        .std(ddof=0)
        .replace(0.0, np.nan)
    )
    short_hours = int(rules["short_horizon_hours"])
    medium_hours = int(rules["medium_horizon_hours"])
    short_return = close / close.shift(short_hours) - 1.0
    medium_return = close / close.shift(medium_hours) - 1.0
    short_score = short_return / (
        volatility * math.sqrt(short_hours)
    ).replace(0.0, np.nan)
    medium_score = medium_return / (
        volatility * math.sqrt(medium_hours)
    ).replace(0.0, np.nan)
    horizon_agreement = (
        np.sign(short_return) == np.sign(medium_return)
    ) & (np.sign(short_return) != 0.0)
    direction = np.where(horizon_agreement, np.sign(short_return), 0.0)
    score = 0.5 * (short_score + medium_score)

    true_range = pd.concat(
        [
            hourly["high"] - hourly["low"],
            (hourly["high"] - previous_close).abs(),
            (hourly["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.rolling(
        int(rules["atr_lookback_hours"]),
        min_periods=int(rules["atr_lookback_hours"]),
    ).mean()

    eight_hour_volume = hourly["quote_volume"].rolling(
        int(rules["decision_interval_hours"]),
        min_periods=int(rules["decision_interval_hours"]),
    ).sum()
    decision_mask = hourly.index.hour.isin((0, 8, 16))
    decision = pd.DataFrame(index=hourly.index[decision_mask])
    decision["symbol"] = symbol
    decision["close"] = close[decision_mask]
    decision["short_return"] = short_return[decision_mask]
    decision["medium_return"] = medium_return[decision_mask]
    decision["short_score"] = short_score[decision_mask]
    decision["medium_score"] = medium_score[decision_mask]
    decision["trend_score"] = score[decision_mask]
    decision["horizon_agreement"] = horizon_agreement[decision_mask]
    decision["direction_value"] = pd.Series(
        direction,
        index=hourly.index,
    )[decision_mask]
    decision["atr_24h"] = atr[decision_mask]
    decision["decision_volume"] = eight_hour_volume[decision_mask]
    decision["volume_threshold"] = (
        decision["decision_volume"]
        .shift(1)
        .rolling(
            int(rules["volume_median_lookback_decisions"]),
            min_periods=max(
                20,
                int(rules["volume_median_lookback_decisions"]) // 3,
            ),
        )
        .median()
    )
    decision["volume_ratio"] = (
        decision["decision_volume"]
        / decision["volume_threshold"].replace(0.0, np.nan)
    )
    decision["decision_ts"] = decision.index
    return decision.replace([np.inf, -np.inf], np.nan), hourly


def add_cross_market_state(
    frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    scores = pd.DataFrame(
        {symbol: frame["trend_score"] for symbol, frame in frames.items()}
    )
    directions = pd.DataFrame(
        {symbol: frame["direction_value"] for symbol, frame in frames.items()}
    )
    median_score = scores.median(axis=1)
    residual = scores.sub(median_score, axis=0)
    absolute_residual = residual.abs()
    top = absolute_residual.max(axis=1)
    second = absolute_residual.apply(
        lambda row: row.nlargest(2).iloc[-1]
        if row.notna().sum() >= 2
        else np.nan,
        axis=1,
    )
    positive_breadth = (directions > 0.0).mean(axis=1)
    negative_breadth = (directions < 0.0).mean(axis=1)

    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        enriched = frame.copy()
        direction = enriched["direction_value"]
        enriched["directional_breadth"] = np.where(
            direction > 0.0,
            positive_breadth,
            negative_breadth,
        )
        enriched["market_median_trend_score"] = median_score
        enriched["residual_trend_score"] = residual[symbol]
        enriched["is_absolute_residual_leader"] = (
            absolute_residual[symbol] >= top - 1e-12
        )
        enriched["residual_leader_gap"] = top - second

        breadth = (
            enriched["horizon_agreement"].fillna(False)
            & (
                enriched["directional_breadth"]
                >= float(rules["breadth_fraction_min"])
            )
        )
        relative = (
            ~breadth
            & enriched["horizon_agreement"].fillna(False)
            & enriched["is_absolute_residual_leader"].fillna(False)
            & (
                enriched["residual_trend_score"].abs()
                >= float(rules["relative_score_min"])
            )
            & (
                enriched["residual_leader_gap"]
                >= float(rules["relative_leader_gap_min"])
            )
            & (
                np.sign(enriched["residual_trend_score"])
                == enriched["direction_value"]
            )
        )
        enriched["route"] = "UNRESOLVED"
        enriched.loc[relative, "route"] = RELATIVE
        enriched.loc[breadth, "route"] = BREADTH
        output[symbol] = enriched
    return output


def confirmation_and_path(
    item: pd.Series,
    hourly: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    decision_ts = pd.Timestamp(item["decision_ts"])
    direction = float(item["direction_value"])
    confirmation_end = decision_ts + pd.Timedelta(
        hours=int(rules["confirmation_hours"])
    )
    if confirmation_end not in hourly.index:
        return None
    confirmation = hourly.loc[confirmation_end]
    opening = float(confirmation["open"])
    closing = float(confirmation["close"])
    high = float(confirmation["high"])
    low = float(confirmation["low"])
    body_fraction = abs(closing - opening) / max(high - low, 1e-12)
    pressure = (
        2.0
        * float(confirmation["taker_buy_quote_volume"])
        / max(float(confirmation["quote_volume"]), 1e-12)
        - 1.0
    )
    confirmation_return = closing / opening - 1.0
    if direction * confirmation_return <= 0.0:
        return None
    if body_fraction < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if (
        direction * pressure
        < float(rules["minimum_directional_taker_pressure"])
    ):
        return None

    atr = float(item["atr_24h"])
    entry = closing
    if direction > 0.0:
        initial_stop = min(
            low,
            entry - float(rules["initial_stop_atr"]) * atr,
        )
    else:
        initial_stop = max(
            high,
            entry + float(rules["initial_stop_atr"]) * atr,
        )
    if direction * (entry - initial_stop) <= 0.0:
        return None

    cap = confirmation_end + pd.Timedelta(
        hours=int(rules["maximum_hold_hours"])
    )
    path = hourly[
        (hourly.index > confirmation_end) & (hourly.index <= cap)
    ]
    if path.empty:
        return None

    close_series = hourly["close"].astype(float)
    previous_close = close_series.shift(1)
    true_range = pd.concat(
        [
            hourly["high"] - hourly["low"],
            (hourly["high"] - previous_close).abs(),
            (hourly["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_series = true_range.rolling(
        int(rules["atr_lookback_hours"]),
        min_periods=int(rules["atr_lookback_hours"]),
    ).mean()

    stop = initial_stop
    best_close = entry
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason = ""
    updates = 0
    for timestamp, bar in path.iterrows():
        if direction > 0.0 and float(bar["low"]) <= stop:
            exit_ts = pd.Timestamp(timestamp)
            exit_price = stop
            exit_reason = "TRAILING_STOP"
            break
        if direction < 0.0 and float(bar["high"]) >= stop:
            exit_ts = pd.Timestamp(timestamp)
            exit_price = stop
            exit_reason = "TRAILING_STOP"
            break

        bar_close = float(bar["close"])
        best_close = (
            max(best_close, bar_close)
            if direction > 0.0
            else min(best_close, bar_close)
        )
        current_atr = atr_series.get(timestamp, np.nan)
        if pd.isna(current_atr):
            current_atr = atr
        if direction > 0.0:
            new_stop = max(
                stop,
                best_close
                - float(rules["trailing_stop_atr"]) * float(current_atr),
            )
        else:
            new_stop = min(
                stop,
                best_close
                + float(rules["trailing_stop_atr"]) * float(current_atr),
            )
        if abs(new_stop - stop) > 1e-12:
            stop = new_stop
            updates += 1

    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        exit_reason = "NEXT_DECISION_CAP"

    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    quality = (
        abs(float(item["trend_score"]))
        * max(float(item["volume_ratio"]), 0.0)
    )
    if item["route"] == BREADTH:
        quality *= 1.0 + float(item["directional_breadth"])
    else:
        quality *= 1.0 + abs(float(item["residual_trend_score"]))

    return {
        "decision_ts": decision_ts,
        "entry_ts": confirmation_end,
        "exit_ts": exit_ts,
        "symbol": str(item["symbol"]),
        "route": str(item["route"]),
        "route_priority": 2 if item["route"] == BREADTH else 1,
        "direction": "LONG" if direction > 0.0 else "SHORT",
        "direction_value": direction,
        "short_return": float(item["short_return"]),
        "medium_return": float(item["medium_return"]),
        "short_score": float(item["short_score"]),
        "medium_score": float(item["medium_score"]),
        "trend_score": float(item["trend_score"]),
        "directional_breadth": float(item["directional_breadth"]),
        "residual_trend_score": float(item["residual_trend_score"]),
        "residual_leader_gap": float(item["residual_leader_gap"]),
        "volume_ratio": float(item["volume_ratio"]),
        "atr_24h": atr,
        "confirmation_return": confirmation_return,
        "confirmation_body_fraction": body_fraction,
        "confirmation_taker_pressure": pressure,
        "entry_price": entry,
        "initial_stop": initial_stop,
        "final_stop": stop,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "trailing_updates": updates,
        "holding_minutes": (
            exit_ts - confirmation_end
        ).total_seconds() / 60.0,
        "rank_score": quality,
        "gross_return": gross,
        "net_return": gross - cost,
    }


def executable_candidates(
    decision_frames: dict[str, pd.DataFrame],
    hourly_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in decision_frames.items():
        eligible = frame[
            (frame["route"] != "UNRESOLVED")
            & frame["horizon_agreement"].fillna(False)
            & (
                frame["volume_ratio"]
                >= float(rules["minimum_decision_volume_ratio"])
            )
        ]
        for _, item in eligible.iterrows():
            result = confirmation_and_path(
                item,
                hourly_frames[symbol],
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


def arbitrate(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_DECISION_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    if not selected:
        return frame.iloc[0:0].copy(), skips
    output = pd.DataFrame(selected).reset_index(drop=True)
    if len(output.index) > 1:
        opened = pd.to_datetime(output["entry_ts"], utc=True)
        closed = pd.to_datetime(output["exit_ts"], utc=True)
        if not (
            opened.iloc[1:].reset_index(drop=True)
            >= closed.iloc[:-1].reset_index(drop=True)
        ).all():
            raise RuntimeError("global overlap survived V33 arbitration")
    return output, skips


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
    end_exclusive: str,
) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end_exclusive, tz="UTC")
    sample = frame[
        (frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)
    ].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end_exclusive,
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
            "mean_holding_minutes": None,
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
            "mean_holding_minutes": float(
                current["holding_minutes"].mean()
            ),
        }
    return {
        "start": start,
        "end_exclusive": end_exclusive,
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
        "mean_holding_minutes": float(sample["holding_minutes"].mean()),
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
    if protocol["schema"] != "candidate-15-v33-cross-sectional-8h-momentum-v1":
        raise RuntimeError("unexpected V33 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v33-data-manifest-v1",
            "files": manifest,
        },
    )
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {
        symbol: common.load_symbol(paths[symbol], start, end)
        for symbol in SYMBOLS
    }
    hourly_frames: dict[str, pd.DataFrame] = {}
    decision_frames: dict[str, pd.DataFrame] = {}
    for symbol, frame in five_frames.items():
        decisions, hourly = build_decision_frame(
            symbol,
            frame,
            protocol["fixed_rules"],
        )
        decision_frames[symbol] = decisions
        hourly_frames[symbol] = hourly
    decision_frames = add_cross_market_state(
        decision_frames,
        protocol["fixed_rules"],
    )
    candidates = executable_candidates(
        decision_frames,
        hourly_frames,
        protocol["fixed_rules"],
    )
    selected, skips = arbitrate(candidates)
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
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
    for route in (BREADTH, RELATIVE):
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
        "V33_CROSS_SECTIONAL_8H_MOMENTUM_ADVANCE_TO_NAUTILUS"
        if advance
        else "V33_CROSS_SECTIONAL_8H_MOMENTUM_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        f"Cross-split-positive fixed routes: {survivors}. "
        + (
            "Freeze and implement the survivor in NautilusTrader."
            if advance
            else "Do not change horizons, decision clocks, ranking, "
            "confirmation or stop rules; move to another independent mechanism."
        )
    )
    summary = {
        "schema": "candidate-15-v33-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
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
        "# Candidate 15 V33 — Cross-sectional eight-hour momentum",
        "",
        f"**{classification}**",
        "",
        "Completed 24-hour and seven-day trends are ranked only at fixed "
        "eight-hour boundaries. Entry follows a wholly subsequent completed "
        "one-hour confirmation and one global slot arbitrates all symbols.",
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
                f"- mean holding minutes: `{record['mean_holding_minutes']}`",
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
