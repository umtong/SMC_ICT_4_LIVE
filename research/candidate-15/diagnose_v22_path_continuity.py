#!/usr/bin/env python3
"""Candidate 15 V22 gradual-path versus discrete-shock diagnostic.

A one-hour move is decomposed into path continuity, sign dominance and maximum
single-bar contribution.  Gradual multi-market information diffusion is routed
to continuation only after a separate confirmation bar.  A discrete
perpetual-led move with OI destruction is routed to reclaim only after failure.
This is an economic mechanism screen and does not replace NautilusTrader.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v16_index_basis as common
import diagnose_v17_open_interest as oi_common
import diagnose_v21_cojump as v21
import run_v17_open_interest as metrics_adapter
import run_v18_spot_perp as market_adapter

GRADUAL = "GRADUAL_INFORMATION_DIFFUSION"
DISCRETE = "DISCRETE_LEVERAGE_SHOCK_RECLAIM"


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def path_shape(log_return: pd.Series, window: int) -> pd.DataFrame:
    absolute = log_return.abs()
    path_return = log_return.rolling(window, min_periods=window).sum()
    path_length = absolute.rolling(window, min_periods=window).sum()
    sign_sum = np.sign(log_return).rolling(window, min_periods=window).sum()
    sign_count = log_return.notna().astype(float).rolling(
        window,
        min_periods=window,
    ).sum()
    output = pd.DataFrame(index=log_return.index)
    output["path_return"] = path_return
    output["path_continuity"] = safe_div(path_return.abs(), path_length)
    output["dominant_sign_fraction"] = 0.5 * (
        1.0 + safe_div(sign_sum.abs(), sign_count)
    )
    output["max_single_bar_share"] = safe_div(
        absolute.rolling(window, min_periods=window).max(),
        path_length,
    )
    return output


def build_symbol_frame(
    futures: pd.DataFrame,
    spot: pd.DataFrame,
    index_price: pd.DataFrame,
    metrics: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    joined = (
        futures.rename(
            columns={
                "close": "futures_close",
                "quote_volume": "futures_quote_volume",
                "taker_buy_quote_volume": "futures_taker_buy_quote",
            }
        )
        .join(
            spot.rename(
                columns={
                    "close": "spot_close",
                    "quote_volume": "spot_quote_volume",
                    "taker_buy_quote_volume": "spot_taker_buy_quote",
                }
            ),
            how="inner",
        )
        .join(index_price.rename(columns={"close": "index_close"}), how="inner")
        .join(metrics, how="inner")
    )
    futures_close = joined["futures_close"].astype(float)
    spot_close = joined["spot_close"].astype(float)
    index_close = joined["index_close"].astype(float)
    futures_return = np.log(futures_close).diff()
    spot_return = np.log(spot_close).diff()
    index_return = np.log(index_close).diff()
    window = int(rules["path_window_bars"])
    futures_path = path_shape(futures_return, window)
    spot_path = path_shape(spot_return, window)
    index_path = path_shape(index_return, window)
    continuous_sigma = v21.bipower_sigma(
        futures_return,
        int(rules["bipower_lookback_bars"]),
        int(rules["bipower_minimum_prior_bars"]),
    ) * np.sqrt(window)
    path_z = safe_div(futures_path["path_return"], continuous_sigma)

    futures_pressure = (
        2.0
        * safe_div(
            joined["futures_taker_buy_quote"].astype(float),
            joined["futures_quote_volume"].astype(float),
        )
        - 1.0
    ).clip(-1.0, 1.0)
    pressure_mean = futures_pressure.rolling(window, min_periods=window).mean()
    log_open_interest = np.log(
        joined["sum_open_interest"].astype(float).replace(0.0, np.nan)
    )
    open_interest_1h_change = log_open_interest.diff(window)
    open_interest_5m_change = log_open_interest.diff()
    state_window = int(rules["state_rolling_prior_bars"])
    state_minimum = int(rules["state_minimum_prior_bars"])
    open_interest_1h_z, _, _ = common.rolling_z(
        open_interest_1h_change,
        open_interest_1h_change.shift(1),
        state_window,
        state_minimum,
    )
    open_interest_5m_z, _, _ = common.rolling_z(
        open_interest_5m_change,
        open_interest_5m_change.shift(1),
        state_window,
        state_minimum,
    )
    return_z, _, _ = common.rolling_z(
        futures_return,
        futures_return.shift(1),
        state_window,
        state_minimum,
    )

    output = joined.loc[:, ["futures_close", "spot_close", "index_close"]].copy()
    output["futures_return"] = futures_return
    output["spot_return"] = spot_return
    output["index_return"] = index_return
    output["path_return"] = futures_path["path_return"]
    output["path_z"] = path_z
    output["path_continuity"] = futures_path["path_continuity"]
    output["dominant_sign_fraction"] = futures_path["dominant_sign_fraction"]
    output["max_single_bar_share"] = futures_path["max_single_bar_share"]
    output["spot_path_return"] = spot_path["path_return"]
    output["spot_path_continuity"] = spot_path["path_continuity"]
    output["index_path_return"] = index_path["path_return"]
    output["index_path_continuity"] = index_path["path_continuity"]
    output["futures_pressure"] = futures_pressure
    output["flow_persistence"] = pressure_mean
    output["open_interest_1h_z"] = open_interest_1h_z
    output["open_interest_5m_z"] = open_interest_5m_z
    output["futures_return_z"] = return_z
    return output.replace([np.inf, -np.inf], np.nan)


def add_cross_market_features(
    frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    spot_paths = pd.DataFrame(
        {symbol: frame["spot_path_return"] for symbol, frame in frames.items()}
    )
    spot_returns = pd.DataFrame(
        {symbol: frame["spot_return"] for symbol, frame in frames.items()}
    )
    output: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        enriched = frame.copy()
        direction = np.sign(enriched["path_return"])
        enriched["event_peer_spot_path_breadth"] = (
            spot_paths.mul(direction, axis=0) > 0.0
        ).mean(axis=1)
        enriched["confirmation_peer_spot_breadth"] = (
            spot_returns.shift(-1).mul(direction, axis=0) > 0.0
        ).mean(axis=1)
        output[symbol] = enriched
    return output


def candidate_events(
    symbol: str,
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    direction = np.sign(frame["path_return"])
    path_magnitude = frame["path_return"].abs().replace(0.0, np.nan)
    spot_delivery_ratio = safe_div(
        direction * frame["spot_path_return"],
        path_magnitude,
    )
    index_delivery_ratio = safe_div(
        direction * frame["index_path_return"],
        path_magnitude,
    )
    gradual = (
        (frame["path_z"].abs() >= float(rules["gradual_absolute_path_z_min"]))
        & (
            frame["path_continuity"]
            >= float(rules["gradual_path_continuity_min"])
        )
        & (
            frame["dominant_sign_fraction"]
            >= float(rules["gradual_dominant_sign_fraction_min"])
        )
        & (
            frame["max_single_bar_share"]
            <= float(rules["gradual_max_single_bar_share_max"])
        )
        & (
            frame["spot_path_continuity"]
            >= float(rules["gradual_spot_path_continuity_min"])
        )
        & (direction * frame["spot_path_return"] > 0.0)
        & (direction * frame["index_path_return"] > 0.0)
        & (
            frame["event_peer_spot_path_breadth"]
            >= float(rules["gradual_peer_spot_path_breadth_min"])
        )
        & (
            frame["open_interest_1h_z"]
            >= float(rules["gradual_event_open_interest_1h_z_min"])
        )
        & (
            direction * frame["flow_persistence"]
            >= float(rules["gradual_directional_flow_persistence_min"])
        )
    )
    discrete = (
        (frame["path_z"].abs() >= float(rules["discrete_absolute_path_z_min"]))
        & (
            frame["max_single_bar_share"]
            >= float(rules["discrete_max_single_bar_share_min"])
        )
        & (
            spot_delivery_ratio
            <= float(rules["discrete_spot_delivery_ratio_max"])
        )
        & (
            index_delivery_ratio
            <= float(rules["discrete_index_delivery_ratio_max"])
        )
        & (
            frame["open_interest_1h_z"]
            <= float(rules["discrete_event_open_interest_1h_z_max"])
        )
        & (
            direction * frame["futures_pressure"]
            >= float(
                rules["discrete_directional_current_futures_taker_pressure_min"]
            )
        )
    )
    candidates = frame[gradual | discrete].copy()
    if candidates.empty:
        return pd.DataFrame()
    candidates["symbol"] = symbol
    candidates["event_ts"] = candidates.index
    candidates["event_direction"] = direction.loc[candidates.index]
    candidates["gradual_state"] = gradual.loc[candidates.index]
    candidates["discrete_state"] = discrete.loc[candidates.index]
    candidates["spot_delivery_ratio"] = spot_delivery_ratio.loc[candidates.index]
    candidates["index_delivery_ratio"] = index_delivery_ratio.loc[candidates.index]

    confirmation = frame.shift(-1).loc[candidates.index]
    candidates["entry_ts"] = candidates.index + pd.Timedelta(minutes=5)
    candidates["entry_price"] = confirmation["futures_close"].to_numpy()
    candidates["confirmation_price_extension"] = (
        candidates["event_direction"].to_numpy()
        * (
            confirmation["futures_close"].to_numpy()
            / candidates["futures_close"].to_numpy()
            - 1.0
        )
    )
    candidates["confirmation_open_interest_5m_z"] = confirmation[
        "open_interest_5m_z"
    ].to_numpy()
    candidates["confirmation_directional_futures_taker_pressure"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["futures_pressure"].to_numpy()
    )
    candidates["confirmation_directional_spot_follow"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["spot_return"].to_numpy()
    )
    candidates["confirmation_directional_index_follow"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["index_return"].to_numpy()
    )
    candidates["confirmation_directional_return_z"] = (
        candidates["event_direction"].to_numpy()
        * confirmation["futures_return_z"].to_numpy()
    )
    candidates["confirmation_peer_spot_breadth"] = frame.loc[
        candidates.index,
        "confirmation_peer_spot_breadth",
    ].to_numpy()

    continuation_steps = 1 + int(rules["continuation_horizon_minutes"]) // 5
    reclaim_steps = 1 + int(rules["reclaim_horizon_minutes"]) // 5
    continuation_target = frame["futures_close"].shift(-continuation_steps).loc[
        candidates.index
    ]
    reclaim_target = frame["futures_close"].shift(-reclaim_steps).loc[
        candidates.index
    ]
    candidates["continuation_gross_return"] = (
        candidates["event_direction"]
        * (continuation_target.to_numpy() / candidates["entry_price"] - 1.0)
    )
    candidates["reclaim_gross_return"] = (
        -candidates["event_direction"]
        * (reclaim_target.to_numpy() / candidates["entry_price"] - 1.0)
    )
    required = [
        "entry_price",
        "confirmation_price_extension",
        "confirmation_open_interest_5m_z",
        "confirmation_directional_futures_taker_pressure",
        "confirmation_directional_spot_follow",
        "confirmation_directional_index_follow",
        "confirmation_directional_return_z",
        "confirmation_peer_spot_breadth",
        "continuation_gross_return",
        "reclaim_gross_return",
    ]
    return candidates.replace([np.inf, -np.inf], np.nan).dropna(subset=required)


def classify_and_cooldown(
    events: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if events.empty:
        return events
    gradual_confirmation = (
        events["gradual_state"].astype(bool)
        & (
            events["confirmation_price_extension"]
            > float(rules["gradual_confirmation_price_extension_min"])
        )
        & (
            events["confirmation_open_interest_5m_z"]
            >= float(rules["gradual_confirmation_open_interest_5m_z_min"])
        )
        & (
            events["confirmation_directional_futures_taker_pressure"]
            >= float(
                rules[
                    "gradual_confirmation_directional_futures_taker_pressure_min"
                ]
            )
        )
        & (
            events["confirmation_directional_spot_follow"]
            > float(rules["gradual_confirmation_directional_spot_follow_min"])
        )
        & (
            events["confirmation_directional_index_follow"]
            > float(rules["gradual_confirmation_directional_index_follow_min"])
        )
        & (
            events["confirmation_peer_spot_breadth"]
            >= float(rules["gradual_confirmation_peer_spot_breadth_min"])
        )
    )
    discrete_confirmation = (
        events["discrete_state"].astype(bool)
        & (
            events["confirmation_price_extension"]
            <= float(rules["discrete_confirmation_price_extension_max"])
        )
        & (
            events["confirmation_open_interest_5m_z"]
            >= float(rules["discrete_confirmation_open_interest_5m_z_min"])
        )
        & (
            events["confirmation_directional_futures_taker_pressure"]
            <= float(
                rules[
                    "discrete_confirmation_directional_futures_taker_pressure_max"
                ]
            )
        )
        & (
            events["confirmation_directional_spot_follow"]
            <= float(rules["discrete_confirmation_directional_spot_follow_max"])
        )
    )
    routed = events[gradual_confirmation | discrete_confirmation].copy()
    if routed.empty:
        return routed
    gradual_mask = gradual_confirmation.loc[routed.index]
    routed["route"] = np.where(gradual_mask, GRADUAL, DISCRETE)
    routed["trade_direction"] = np.where(
        routed["route"] == GRADUAL,
        routed["event_direction"],
        -routed["event_direction"],
    )
    routed["horizon_minutes"] = np.where(
        routed["route"] == GRADUAL,
        int(rules["continuation_horizon_minutes"]),
        int(rules["reclaim_horizon_minutes"]),
    )
    routed["gross_return"] = np.where(
        routed["route"] == GRADUAL,
        routed["continuation_gross_return"],
        routed["reclaim_gross_return"],
    )
    routed["cost_return"] = v21.total_cost(rules)
    routed["net_return"] = routed["gross_return"] - routed["cost_return"]

    gradual_context = (
        routed["path_z"].abs()
        * routed["path_continuity"]
        * routed["dominant_sign_fraction"]
        * (routed["open_interest_1h_z"] + 1.0).clip(lower=0.01)
        * routed["event_peer_spot_path_breadth"]
    )
    discrete_context = (
        routed["path_z"].abs()
        * routed["max_single_bar_share"]
        * (-routed["open_interest_1h_z"]).clip(lower=0.0)
        * (1.0 - routed["spot_delivery_ratio"]).clip(lower=0.0)
    )
    gradual_quality = (
        routed["confirmation_directional_return_z"].clip(lower=0.0)
        * (routed["confirmation_open_interest_5m_z"] + 0.25).clip(lower=0.01)
        * routed["confirmation_peer_spot_breadth"]
    )
    discrete_quality = (
        (-routed["confirmation_directional_return_z"]).clip(lower=0.0)
        * (
            -routed["confirmation_directional_futures_taker_pressure"]
        ).clip(lower=0.0)
        * (routed["confirmation_open_interest_5m_z"] + 0.25).clip(lower=0.01)
    )
    routed["context_score"] = np.where(
        routed["route"] == GRADUAL,
        gradual_context,
        discrete_context,
    )
    routed["state_quality"] = np.where(
        routed["route"] == GRADUAL,
        gradual_quality,
        discrete_quality,
    )
    routed["rank_score"] = routed["context_score"] * routed["state_quality"]
    routed = routed.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["rank_score", "gross_return", "net_return"],
    )

    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_event_cooldown_minutes"]),
    )
    accepted: list[pd.Series] = []
    for _, symbol_events in routed.groupby("symbol"):
        next_allowed = pd.Timestamp.min.tz_localize("UTC")
        for _, row in symbol_events.sort_values("event_ts").iterrows():
            event_ts = pd.Timestamp(row["event_ts"])
            if event_ts < next_allowed:
                continue
            accepted.append(row)
            next_allowed = event_ts + cooldown
    if not accepted:
        return routed.iloc[0:0]
    return pd.DataFrame(accepted).sort_values("event_ts", kind="stable")


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate 15 V22 — Price-path continuity diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "The state router distinguishes a distributed one-hour path from a single-bar-dominated shock before the independent confirmation bar.",
    ]
    for title, key in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("July 2026 confirmation", "july_confirmation"),
        ("Latest August 1-7 pulse", "latest_august_pulse"),
    ):
        summary = payload[key]
        lines.extend(
            (
                "",
                f"## {title}",
                f"- interval: `{summary['start']} -> {summary['end_exclusive']}`",
                f"- selected independent episodes / day: `{summary['trades']} / {summary['trades_per_day']}`",
                f"- gross / net mean: `{summary['mean_gross_bps']} / {summary['mean_net_bps']}` bp",
                f"- win rate / payoff: `{summary['win_rate']} / {summary['payoff_ratio']}`",
                f"- net t-stat: `{summary['net_t_stat']}`",
                f"- positive months: `{summary['positive_months']} / {summary['active_months']}`",
                f"- route stats: `{summary['route_stats']}`",
                f"- symbol counts: `{summary['symbol_counts']}`",
            )
        )
    lines.extend(("", "## Advance checks"))
    lines.extend(
        f"- {name}: `{value}`" for name, value in payload["advance_checks"].items()
    )
    lines.extend(
        (
            "",
            "## Cross-split route evidence",
            f"`{payload['cross_split_routes']}`",
            "",
            "## Decision",
            payload["decision"],
            "",
            "A family-level pass is not final-system success. Any survivor still requires frozen NautilusTrader orders, same-leg invalidation, current-NAV 3% risk sizing, actual costs, one global slot and final continuous-account frequency of at least one independent completed trade per calendar day.",
        )
    )
    return "\n".join(lines) + "\n"


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = common.load_json(protocol_path)
    data = protocol["data"]
    rules = protocol["fixed_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    monthly_end = date.fromisoformat(data["monthly_end_exclusive"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)

    archive_tasks: list[tuple[str, str, str, str, str, str, Path]] = []
    for symbol in data["symbols"]:
        for token in v21.month_tokens(start, monthly_end):
            for market, dataset, root in (
                ("futures", "klines", "futures_monthly"),
                ("futures", "indexPriceKlines", "futures_monthly"),
                ("spot", "klines", "spot_monthly"),
            ):
                archive_tasks.append(
                    (
                        market,
                        "monthly",
                        dataset,
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / root
                        / dataset
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    )
                )
        for token in data["latest_daily_dates"]:
            for market, dataset, root in (
                ("futures", "klines", "futures_daily"),
                ("futures", "indexPriceKlines", "futures_daily"),
                ("spot", "klines", "spot_daily"),
            ):
                archive_tasks.append(
                    (
                        market,
                        "daily",
                        dataset,
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / root
                        / dataset
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    )
                )
    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        cursor = start
        while cursor < end:
            token = cursor.isoformat()
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
            cursor += timedelta(days=1)

    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        jobs = [pool.submit(v21.download_archive, task) for task in archive_tasks]
        jobs.extend(pool.submit(oi_common.download_metric, task) for task in metric_tasks)
        for future in as_completed(jobs):
            records.append(future.result())
    common.write_json(output / "data_manifest.json", oi_common.aggregate_manifest(records))

    frames: dict[str, pd.DataFrame] = {}
    for symbol in data["symbols"]:
        futures_paths = sorted(
            (output / "data" / "futures_monthly" / "klines" / symbol).glob("*.zip")
        ) + sorted(
            (output / "data" / "futures_daily" / "klines" / symbol).glob("*.zip")
        )
        index_paths = sorted(
            (
                output
                / "data"
                / "futures_monthly"
                / "indexPriceKlines"
                / symbol
            ).glob("*.zip")
        ) + sorted(
            (
                output
                / "data"
                / "futures_daily"
                / "indexPriceKlines"
                / symbol
            ).glob("*.zip")
        )
        spot_paths = sorted(
            (output / "data" / "spot_monthly" / "klines" / symbol).glob("*.zip")
        ) + sorted(
            (output / "data" / "spot_daily" / "klines" / symbol).glob("*.zip")
        )
        futures = market_adapter.load_market_series(futures_paths, start, end)
        spot = market_adapter.load_market_series(spot_paths, start, end)
        index_price = v21.load_index_series(index_paths, start, end)
        metrics = metrics_adapter.load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
        frames[symbol] = build_symbol_frame(
            futures,
            spot,
            index_price,
            metrics,
            rules,
        )
    frames = add_cross_market_features(frames)

    candidate_parts = [
        candidate_events(symbol, frame, rules)
        for symbol, frame in frames.items()
    ]
    candidate_parts = [part for part in candidate_parts if not part.empty]
    candidates = (
        pd.concat(candidate_parts, ignore_index=True).sort_values("event_ts")
        if candidate_parts
        else pd.DataFrame()
    )
    routed = classify_and_cooldown(candidates, rules)
    routed.to_csv(output / "routed_events.csv", index=False)
    selected, skips = v21.arbitrate(routed)
    selected.to_csv(output / "selected_episodes.csv", index=False)

    splits = {
        "development": v21.summarize(
            selected,
            evaluation["development_start"],
            evaluation["development_end_exclusive"],
        ),
        "stability": v21.summarize(
            selected,
            evaluation["stability_start"],
            evaluation["stability_end_exclusive"],
        ),
        "july_confirmation": v21.summarize(
            selected,
            evaluation["july_confirmation_start"],
            evaluation["july_confirmation_end_exclusive"],
        ),
        "latest_august_pulse": v21.summarize(
            selected,
            evaluation["latest_august_pulse_start"],
            evaluation["latest_august_pulse_end_exclusive"],
        ),
    }
    routes = v21.cross_split_routes(splits)
    development = splits["development"]
    stability = splits["stability"]
    july = splits["july_confirmation"]
    august = splits["latest_august_pulse"]
    gate = protocol["advance_gate"]
    concentration = (
        max(stability["symbol_counts"].values()) / stability["trades"]
        if stability["trades"]
        else 1.0
    )
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_stability_mean_net": (
            stability["mean_net_bps"] is not None
            and stability["mean_net_bps"]
            > float(gate["minimum_stability_mean_net_bps"])
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
        "family_stability_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_family_stability_trades_per_calendar_day"])
        ),
        "positive_july_confirmation_mean_net": (
            july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0
        ),
        "july_confirmation_trade_count": (
            july["trades"] >= int(gate["minimum_july_confirmation_trades"])
        ),
        "positive_latest_august_pulse_mean_net": (
            august["mean_net_bps"] is not None
            and august["mean_net_bps"]
            > float(gate["minimum_latest_august_pulse_mean_net_bps"])
        ),
        "latest_august_pulse_trade_count": (
            august["trades"] >= int(gate["minimum_latest_august_pulse_trades"])
        ),
        "symbol_concentration": (
            concentration <= float(gate["maximum_single_symbol_share"])
        ),
    }
    passed = all(checks.values())
    if passed:
        classification = "V22_FAMILY_ADVANCES_TO_FROZEN_NAUTILUS"
        decision = (
            "The fixed path-continuity family survived development, year-long "
            "stability, July confirmation and the August pulse. Freeze both path "
            "states for NautilusTrader implementation; final integrated frequency and "
            "continuous NAV remain mandatory."
        )
    else:
        classification = "V22_PATH_CONTINUITY_ROUTER_REJECTED_OR_UNDERPOWERED"
        survivors = [
            route
            for route, evidence in routes.items()
            if evidence["positive_across_all_declared_splits"]
        ]
        decision = (
            "The path-continuity router failed at least one predeclared family gate. "
            f"Cross-split-positive routes: {survivors}. Do not tune path/state "
            "thresholds; retain only those routes and move to another independent "
            "mechanism."
        )
    payload = {
        "schema": "candidate-15-v22-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "total_screening_cost_bps": v21.total_cost(rules) * 10_000.0,
        "candidate_events": len(candidates.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        **splits,
        "cross_split_routes": routes,
        "advance_checks": checks,
        "latest_august_pulse_status": evaluation[
            "latest_august_pulse_status"
        ],
        "decision": decision,
    }
    common.write_json(output / "summary.json", payload)
    (output / "RESULT.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
