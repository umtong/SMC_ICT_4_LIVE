#!/usr/bin/env python3
"""Candidate 15 V26 cross-sectional residual/OI state-router screen.

Every five-minute return is decomposed into a prior-only rolling beta times the
four-market median factor plus an asset-specific residual.  Thirty-minute
residual shocks are classified with open interest and a distinct subsequent
15-minute transition:

- OI destruction + idiosyncratic shock + residual/flow rejection -> reversal.
- OI buildup + broad shock + residual/flow continuation -> continuation.

Entry is after the confirmation interval.  This is a mechanism screen, not a
portfolio/account simulator.  A survivor must still be materialized in
NautilusTrader with exact current-NAV risk and one global position.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v16_index_basis as common
import diagnose_v17_open_interest as oi_common

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
REVERSAL = "RESIDUAL_LIQUIDATION_EXHAUSTION_2H"
CONTINUATION = "RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def rolling_prior_mean_std(
    series: pd.Series,
    window: int,
    minimum: int,
) -> tuple[pd.Series, pd.Series]:
    prior = series.shift(1)
    mean = prior.rolling(window, min_periods=minimum).mean()
    standard = prior.rolling(window, min_periods=minimum).std(ddof=0)
    return mean, standard.replace(0.0, np.nan)


def rolling_prior_z(
    series: pd.Series,
    window: int,
    minimum: int,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    mean, standard = rolling_prior_mean_std(series, window, minimum)
    return (series - mean) / standard, mean, standard


def weighted_rolling_pressure(
    pressure: pd.Series,
    quote_volume: pd.Series,
    bars: int,
) -> pd.Series:
    numerator = (pressure * quote_volume).rolling(bars, min_periods=bars).sum()
    denominator = quote_volume.rolling(bars, min_periods=bars).sum().replace(0.0, np.nan)
    return numerator / denominator


def future_weighted_pressure(
    pressure: pd.Series,
    quote_volume: pd.Series,
    bars: int,
) -> pd.Series:
    numerator = sum(
        pressure.shift(-offset) * quote_volume.shift(-offset)
        for offset in range(1, bars + 1)
    )
    denominator = sum(
        quote_volume.shift(-offset)
        for offset in range(1, bars + 1)
    ).replace(0.0, np.nan)
    return numerator / denominator


def factor_beta(
    symbol_return: pd.Series,
    factor_return: pd.Series,
    window: int,
    minimum: int,
) -> pd.Series:
    symbol_prior = symbol_return.shift(1)
    factor_prior = factor_return.shift(1)
    covariance = symbol_prior.rolling(window, min_periods=minimum).cov(factor_prior)
    variance = factor_prior.rolling(window, min_periods=minimum).var(ddof=0)
    return covariance / variance.replace(0.0, np.nan)


def common_index(
    futures_frames: dict[str, pd.DataFrame],
    metrics_frames: dict[str, pd.DataFrame],
) -> pd.DatetimeIndex:
    index: pd.DatetimeIndex | None = None
    for symbol in SYMBOLS:
        candidate = futures_frames[symbol].index.intersection(metrics_frames[symbol].index)
        index = candidate if index is None else index.intersection(candidate)
    if index is None or index.empty:
        raise RuntimeError("no synchronized price/OI observations")
    return index.sort_values()


def build_market_matrices(
    futures_frames: dict[str, pd.DataFrame],
    metrics_frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    index = common_index(futures_frames, metrics_frames)
    close = pd.DataFrame(
        {symbol: futures_frames[symbol].reindex(index)["close"].astype(float) for symbol in SYMBOLS},
        index=index,
    )
    quote_volume = pd.DataFrame(
        {symbol: futures_frames[symbol].reindex(index)["quote_volume"].astype(float) for symbol in SYMBOLS},
        index=index,
    )
    open_interest = pd.DataFrame(
        {
            symbol: metrics_frames[symbol]
            .reindex(index)["sum_open_interest"]
            .astype(float)
            .replace(0.0, np.nan)
            for symbol in SYMBOLS
        },
        index=index,
    )
    taker_ratio = pd.DataFrame(
        {
            symbol: metrics_frames[symbol]
            .reindex(index)["sum_taker_long_short_vol_ratio"]
            .astype(float)
            .clip(lower=1e-8)
            for symbol in SYMBOLS
        },
        index=index,
    )
    taker_pressure = np.tanh(0.5 * np.log(taker_ratio))
    log_return = np.log(close).diff()
    factor_return = log_return.median(axis=1, skipna=True)
    return {
        "close": close,
        "quote_volume": quote_volume,
        "open_interest": open_interest,
        "taker_pressure": taker_pressure,
        "log_return": log_return,
        "factor_return": factor_return.to_frame("factor"),
    }


def event_frame_for_symbol(
    symbol: str,
    matrices: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    close = matrices["close"][symbol]
    quote_volume = matrices["quote_volume"][symbol]
    open_interest = matrices["open_interest"][symbol]
    taker_pressure = matrices["taker_pressure"][symbol]
    symbol_return = matrices["log_return"][symbol]
    factor_return = matrices["factor_return"]["factor"]

    beta = factor_beta(
        symbol_return,
        factor_return,
        int(rules["factor_beta_prior_bars"]),
        int(rules["factor_beta_minimum_prior_bars"]),
    )
    residual_5m = symbol_return - beta * factor_return
    bars = int(rules["event_bars"])
    symbol_move = symbol_return.rolling(bars, min_periods=bars).sum()
    factor_move = factor_return.rolling(bars, min_periods=bars).sum()
    residual_move = residual_5m.rolling(bars, min_periods=bars).sum()
    event_quote_volume = quote_volume.rolling(bars, min_periods=bars).sum()
    event_pressure = weighted_rolling_pressure(taker_pressure, quote_volume, bars)
    log_oi = np.log(open_interest)
    oi_move = log_oi - log_oi.shift(bars)

    clock_mask = pd.DatetimeIndex(close.index).minute.isin(
        [int(value) for value in rules["event_clock_minutes"]]
    )
    base = pd.DataFrame(index=close.index)
    base["symbol"] = symbol
    base["close"] = close
    base["beta"] = beta
    base["symbol_move"] = symbol_move
    base["factor_move"] = factor_move
    base["residual_move"] = residual_move
    base["event_quote_volume"] = event_quote_volume
    base["event_taker_pressure"] = event_pressure
    base["oi_move"] = oi_move
    base = base.loc[clock_mask].copy()

    prior_events = int(rules["residual_prior_events"])
    minimum_events = int(rules["residual_minimum_prior_events"])
    base["residual_z"], _, _ = rolling_prior_z(
        base["residual_move"], prior_events, minimum_events
    )
    base["factor_z"], _, _ = rolling_prior_z(
        base["factor_move"], prior_events, minimum_events
    )
    volume_log = np.log1p(base["event_quote_volume"])
    base["event_volume_z"], _, _ = rolling_prior_z(
        volume_log, prior_events, minimum_events
    )
    base["oi_move_z"], _, _ = rolling_prior_z(
        base["oi_move"], prior_events, minimum_events
    )
    base["event_direction"] = np.sign(base["residual_move"])
    base["directional_event_taker_pressure"] = (
        base["event_direction"] * base["event_taker_pressure"]
    )
    base["residual_share"] = (
        base["residual_move"].abs()
        / base["symbol_move"].abs().replace(0.0, np.nan)
    )

    all_symbol_moves = matrices["log_return"].rolling(
        bars, min_periods=bars
    ).sum().reindex(base.index)
    direction = base["event_direction"]
    base["cross_market_breadth"] = all_symbol_moves.apply(
        lambda column: np.sign(column).eq(direction)
    ).mean(axis=1)

    confirmation_bars = int(rules["confirmation_bars"])
    confirmation_residual = sum(
        residual_5m.shift(-offset)
        for offset in range(1, confirmation_bars + 1)
    ).reindex(base.index)
    confirmation_symbol_return = (
        close.shift(-confirmation_bars) / close - 1.0
    ).reindex(base.index)
    confirmation_factor = sum(
        factor_return.shift(-offset)
        for offset in range(1, confirmation_bars + 1)
    ).reindex(base.index)
    confirmation_pressure = future_weighted_pressure(
        taker_pressure,
        quote_volume,
        confirmation_bars,
    ).reindex(base.index)
    confirmation_oi_change = (
        log_oi.shift(-confirmation_bars) - log_oi
    ).reindex(base.index)
    oi_15m = log_oi - log_oi.shift(confirmation_bars)
    oi_15m_mean, oi_15m_std = rolling_prior_mean_std(
        oi_15m,
        int(rules["factor_beta_prior_bars"]),
        int(rules["factor_beta_minimum_prior_bars"]),
    )
    base["confirmation_residual_move"] = confirmation_residual
    base["directional_confirmation_residual"] = (
        base["event_direction"] * confirmation_residual
    )
    base["confirmation_symbol_return"] = confirmation_symbol_return
    base["directional_confirmation_symbol_return"] = (
        base["event_direction"] * confirmation_symbol_return
    )
    base["confirmation_factor_move"] = confirmation_factor
    base["confirmation_taker_pressure"] = (
        base["event_direction"] * confirmation_pressure
    )
    base["confirmation_oi_change_z"] = (
        confirmation_oi_change - oi_15m_mean.reindex(base.index)
    ) / oi_15m_std.reindex(base.index)
    base["entry_ts"] = base.index + pd.Timedelta(
        minutes=5 * confirmation_bars
    )
    base["entry_price"] = close.shift(-confirmation_bars).reindex(base.index)

    outcome_bars = int(int(rules["reversal_horizon_minutes"]) // 5)
    exit_offset = confirmation_bars + outcome_bars
    base["future_return"] = (
        close.shift(-exit_offset) / close.shift(-confirmation_bars) - 1.0
    ).reindex(base.index)
    base["exit_ts"] = base["entry_ts"] + pd.Timedelta(
        minutes=int(rules["reversal_horizon_minutes"])
    )
    return base.replace([np.inf, -np.inf], np.nan)


def classify_symbol_events(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    required = [
        "residual_z",
        "factor_z",
        "event_volume_z",
        "oi_move_z",
        "event_direction",
        "directional_event_taker_pressure",
        "residual_share",
        "cross_market_breadth",
        "directional_confirmation_residual",
        "confirmation_taker_pressure",
        "confirmation_oi_change_z",
        "entry_price",
        "future_return",
    ]
    events = frame.dropna(subset=required).copy()
    context = (
        (events["residual_z"].abs() >= float(rules["absolute_residual_z_min"]))
        & (events["event_volume_z"] >= float(rules["event_volume_z_min"]))
        & (
            events["directional_event_taker_pressure"]
            >= float(rules["directional_event_taker_pressure_min"])
        )
        & (
            events["residual_share"]
            >= float(rules["minimum_residual_share_of_symbol_move"])
        )
    )
    reversal_state = (
        context
        & (events["factor_z"].abs() <= float(rules["idiosyncratic_factor_z_max"]))
        & (
            events["oi_move_z"]
            <= float(rules["open_interest_contraction_z_max"])
        )
    )
    continuation_state = (
        context
        & (
            events["oi_move_z"]
            >= float(rules["open_interest_buildup_z_min"])
        )
        & (
            events["cross_market_breadth"]
            >= float(rules["continuation_factor_breadth_min"])
        )
    )
    reversal_confirmation = (
        events["directional_confirmation_residual"]
        <= float(rules["confirmation_reversal_residual_max"])
    ) & (
        events["confirmation_taker_pressure"]
        <= float(rules["confirmation_reversal_taker_pressure_max"])
    ) & (
        events["confirmation_oi_change_z"]
        >= float(rules["confirmation_reversal_oi_change_z_min"])
    )
    continuation_confirmation = (
        events["directional_confirmation_residual"]
        > float(rules["confirmation_continuation_residual_min"])
    ) & (
        events["confirmation_taker_pressure"]
        >= float(rules["confirmation_continuation_taker_pressure_min"])
    ) & (
        events["confirmation_oi_change_z"]
        >= float(rules["confirmation_continuation_oi_change_z_min"])
    )
    reversal = reversal_state & reversal_confirmation
    continuation = continuation_state & continuation_confirmation
    output = events[reversal | continuation].copy()
    if output.empty:
        return output
    output["route"] = np.where(reversal[reversal | continuation], REVERSAL, CONTINUATION)
    output["trade_direction_sign"] = np.where(
        output["route"] == REVERSAL,
        -output["event_direction"],
        output["event_direction"],
    )
    output["direction"] = np.where(
        output["trade_direction_sign"] > 0.0,
        "LONG",
        "SHORT",
    )
    output["horizon_minutes"] = int(rules["reversal_horizon_minutes"])
    output["gross_return"] = output["trade_direction_sign"] * output["future_return"]
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    output["net_return"] = output["gross_return"] - cost
    reversal_quality = (
        output["residual_z"].abs()
        * (-output["oi_move_z"]).clip(lower=0.0)
        * (-output["directional_confirmation_residual"]).clip(lower=0.0)
    )
    continuation_quality = (
        output["residual_z"].abs()
        * output["oi_move_z"].clip(lower=0.0)
        * output["directional_confirmation_residual"].clip(lower=0.0)
        * output["cross_market_breadth"]
    )
    output["rank_score"] = np.where(
        output["route"] == REVERSAL,
        reversal_quality,
        continuation_quality,
    )
    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_event_cooldown_minutes"])
    )
    accepted: list[pd.Series] = []
    next_allowed = pd.Timestamp.min.tz_localize("UTC")
    for timestamp, row in output.sort_index(kind="stable").iterrows():
        if timestamp < next_allowed:
            continue
        accepted.append(row)
        next_allowed = timestamp + cooldown
    if not accepted:
        return output.iloc[0:0].copy()
    return pd.DataFrame(accepted).sort_values("entry_ts", kind="stable")


def arbitrate(events: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if events.empty:
        return events.copy(), Counter()
    ordered = events.sort_values(
        ["entry_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    chosen: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for stamp, group in ordered.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        if pd.Timestamp(stamp) < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        chosen.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    if not chosen:
        return ordered.iloc[0:0].copy(), skips
    return pd.DataFrame(chosen).reset_index(drop=True), skips


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
            "net_t_stat": None,
            "win_rate": None,
            "payoff_ratio": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "route_stats": {},
            "symbol_counts": {},
        }
    monthly = (
        sample.set_index("entry_ts")["net_return"].resample("MS").sum().dropna()
    )
    route_stats: dict[str, Any] = {}
    for route, routed in sample.groupby("route"):
        route_stats[str(route)] = {
            "trades": len(routed.index),
            "mean_net_bps": float(routed["net_return"].mean() * 10_000.0),
            "win_rate": float((routed["net_return"] > 0.0).mean()),
            "net_t_stat": t_stat(routed["net_return"]),
        }
    net = sample["net_return"]
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(net.mean() * 10_000.0),
        "net_t_stat": t_stat(net),
        "win_rate": float((net > 0.0).mean()),
        "payoff_ratio": payoff(net),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "route_stats": route_stats,
        "symbol_counts": dict(Counter(sample["symbol"].astype(str))),
    }


def cross_split_routes(summaries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for route in (REVERSAL, CONTINUATION):
        records: dict[str, Any] = {}
        positive = True
        for split, summary in summaries.items():
            record = summary["route_stats"].get(route)
            records[split] = record
            if record is None or float(record["mean_net_bps"]) <= 0.0:
                positive = False
        output[route] = {
            "positive_across_all_declared_splits": positive,
            "splits": records,
        }
    return output


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v26-residual-oi-state-router-v1":
        raise RuntimeError("unexpected Candidate 15 V26 protocol")
    data = protocol["data"]
    rules = protocol["fixed_state_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)

    price_tasks: list[tuple[str, str, str, str, Path]] = []
    for symbol in data["symbols"]:
        for month in common.months(start, end):
            token = f"{month.year:04d}-{month.month:02d}"
            price_tasks.append(
                (
                    "klines",
                    symbol,
                    data["interval"],
                    token,
                    output
                    / "data"
                    / "klines"
                    / symbol
                    / f"{symbol}-{data['interval']}-{token}.zip",
                )
            )
    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        for day in oi_common.days(start, end):
            token = day.isoformat()
            metric_tasks.append(
                (
                    symbol,
                    token,
                    output
                    / "data"
                    / "metrics"
                    / symbol
                    / f"{symbol}-metrics-{token}.zip",
                )
            )
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = [pool.submit(common.download_one, task) for task in price_tasks]
        futures.extend(pool.submit(oi_common.download_metric, task) for task in metric_tasks)
        for future in as_completed(futures):
            records.append(future.result())
    write_json(output / "data_manifest.json", oi_common.aggregate_manifest(records))

    futures_frames: dict[str, pd.DataFrame] = {}
    metrics_frames: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures_frames[symbol] = common.load_series(
            sorted((output / "data" / "klines" / symbol).glob("*.zip")),
            start,
            end,
            True,
        )
        metrics_frames[symbol] = oi_common.load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
    matrices = build_market_matrices(futures_frames, metrics_frames)
    raw_parts = [
        event_frame_for_symbol(symbol, matrices, rules)
        for symbol in data["symbols"]
    ]
    raw = pd.concat(raw_parts, ignore_index=False).sort_index(kind="stable")
    raw.reset_index(names="event_ts").to_csv(output / "all_clock_events.csv", index=False)
    routed_parts = [
        classify_symbol_events(raw[raw["symbol"] == symbol], rules)
        for symbol in data["symbols"]
    ]
    routed = pd.concat(
        [part for part in routed_parts if not part.empty],
        ignore_index=False,
    ).sort_values("entry_ts", kind="stable") if any(not part.empty for part in routed_parts) else raw.iloc[0:0].copy()
    routed.reset_index(names="event_ts").to_csv(output / "routed_events.csv", index=False)
    selected, skips = arbitrate(routed)
    selected.to_csv(output / "selected_episodes.csv", index=False)

    summaries = {
        name: summarize(
            selected,
            evaluation[f"{name}_start"],
            evaluation[f"{name}_end_exclusive"],
        )
        for name in ("development", "stability", "july_confirmation")
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    gate = protocol["advance_gate"]
    route_evidence = cross_split_routes(summaries)
    surviving_routes = [
        route
        for route, record in route_evidence.items()
        if record["positive_across_all_declared_splits"]
    ]
    max_symbol_share = (
        max(stability["symbol_counts"].values()) / max(stability["trades"], 1)
        if stability["symbol_counts"]
        else 1.0
    )
    checks = {
        "positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0,
        "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]),
        "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]),
        "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]),
        "stability_frequency": stability["trades_per_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]),
        "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0,
        "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]),
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
        "cross_split_positive_route": bool(surviving_routes),
    }
    advance = all(checks.values())
    classification = (
        "V26_RESIDUAL_OI_ROUTE_ADVANCE_TO_NAUTILUS"
        if advance
        else "V26_RESIDUAL_OI_ROUTER_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        f"Freeze surviving routes {surviving_routes} and implement same-leg NautilusTrader execution."
        if advance
        else "Do not tune the residual/OI family after these declared results; preserve only any cross-split-positive route and move to another independent mechanism."
    )
    summary = {
        "schema": "candidate-15-v26-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "clock_events": len(raw.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "stability": stability,
        "july_confirmation": july,
        "cross_split_routes": route_evidence,
        "surviving_routes": surviving_routes,
        "maximum_stability_symbol_share": max_symbol_share,
        "advance_checks": checks,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V26 — Cross-sectional residual/OI state-router diagnostic",
        "",
        f"**{classification}**",
        "",
        "Every thirty-minute move is decomposed with a prior-only rolling beta against the four-market median factor. OI determines liquidation versus buildup state; a separate following fifteen-minute residual/flow transition determines entry.",
        "",
    ]
    for title, record in (
        ("Development", development),
        ("Year-long stability", stability),
        ("July 2026 confirmation", july),
    ):
        lines.extend(
            [
                f"## {title}",
                f"- interval: `{record['start']} -> {record['end_exclusive']}`",
                f"- trades / day: `{record['trades']} / {record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / {record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / {record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / {record['active_months']}`",
                f"- route stats: `{record['route_stats']}`",
                f"- symbol counts: `{record['symbol_counts']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Cross-split route evidence",
            f"`{route_evidence}`",
            "",
            "## Decision",
            decision,
            "",
            "This mechanism screen does not synthesize NAV. Any survivor still requires frozen NautilusTrader orders, event/same-leg invalidation, exact current-NAV 3% risk sizing, realistic costs, one global slot and continuous-account validation.",
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
