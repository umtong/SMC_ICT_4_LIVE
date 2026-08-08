#!/usr/bin/env python3
"""Candidate 15 V20 directional-change intrinsic-time diagnostic.

Directional changes are confirmed only at actual completed five-minute closes.
The threshold is frozen at each intrinsic event from prior-only volatility and
is not moved inside the leg. Route confirmation uses a separate completed bar.
The event-based exit is the next opposite directional change or a four-hour
cap. This is an economic mechanism screen, not an account/NAV engine.
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
import diagnose_v18_spot_perp as spot_common
import run_v17_open_interest as metrics_adapter
import run_v18_spot_perp as market_adapter

ACCEPTED = "DC_ACCEPTED_OVERSHOOT"
FAILED = "DC_FAILED_TRANSITION_RECLAIM"


def safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    return numerator / denominator.replace(0.0, np.nan)


def total_screening_cost(rules: dict[str, Any]) -> float:
    return (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0


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
    futures_return = futures_close.pct_change()
    futures_return_1h = futures_close / futures_close.shift(12) - 1.0
    spot_return_1h = spot_close / spot_close.shift(12) - 1.0
    index_return_1h = index_close / index_close.shift(12) - 1.0
    perp_spot_gap_1h = futures_return_1h - spot_return_1h
    futures_pressure = (
        2.0
        * safe_div(
            joined["futures_taker_buy_quote"].astype(float),
            joined["futures_quote_volume"].astype(float),
        )
        - 1.0
    ).clip(-1.0, 1.0)
    log_open_interest = np.log(
        joined["sum_open_interest"].astype(float).replace(0.0, np.nan)
    )
    open_interest_1h_change = log_open_interest.diff(12)
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
    perp_spot_gap_1h_z, _, _ = common.rolling_z(
        perp_spot_gap_1h,
        perp_spot_gap_1h.shift(1),
        state_window,
        state_minimum,
    )
    futures_return_z, _, _ = common.rolling_z(
        futures_return,
        futures_return.shift(1),
        state_window,
        state_minimum,
    )

    threshold_window = int(rules["threshold_lookback_bars"])
    threshold_minimum = int(rules["threshold_minimum_prior_bars"])
    horizon_bars = int(rules["threshold_physical_horizon_bars"])
    prior_one_hour_sigma = (
        futures_return.shift(1)
        .rolling(threshold_window, min_periods=threshold_minimum)
        .std(ddof=0)
        * math.sqrt(horizon_bars)
    )
    threshold_floor = (
        total_screening_cost(rules)
        * float(rules["minimum_threshold_total_cost_multiple"])
    )
    threshold_candidate = prior_one_hour_sigma.clip(lower=threshold_floor)

    output = joined.loc[:, [
        "futures_close",
        "spot_close",
        "index_close",
    ]].copy()
    output["futures_return"] = futures_return
    output["futures_return_z"] = futures_return_z
    output["futures_return_1h"] = futures_return_1h
    output["spot_return_1h"] = spot_return_1h
    output["index_return_1h"] = index_return_1h
    output["perp_spot_gap_1h_z"] = perp_spot_gap_1h_z
    output["futures_pressure"] = futures_pressure
    output["open_interest_1h_z"] = open_interest_1h_z
    output["open_interest_5m_z"] = open_interest_5m_z
    output["threshold_candidate"] = threshold_candidate
    return output.replace([np.inf, -np.inf], np.nan)


def detect_directional_changes(
    symbol: str,
    frame: pd.DataFrame,
) -> pd.DataFrame:
    required = frame.dropna(subset=["futures_close", "threshold_candidate"])
    if required.empty:
        return pd.DataFrame()
    first_timestamp = required.index[0]
    first_price = float(required.iloc[0]["futures_close"])
    threshold = float(required.iloc[0]["threshold_candidate"])
    mode = 0
    high = first_price
    low = first_price
    extreme = first_price
    events: list[dict[str, Any]] = []
    sequence = 0

    for timestamp, row in required.iterrows():
        price = float(row["futures_close"])
        if not math.isfinite(price) or price <= 0.0:
            continue
        candidate = float(row["threshold_candidate"])
        if not math.isfinite(candidate) or candidate <= 0.0:
            continue
        direction = 0
        prior_extreme = float("nan")
        used_threshold = threshold

        if mode == 0:
            high = max(high, price)
            low = min(low, price)
            if price >= low * (1.0 + threshold):
                direction = 1
                prior_extreme = low
            elif price <= high * (1.0 - threshold):
                direction = -1
                prior_extreme = high
        elif mode > 0:
            if price > extreme:
                extreme = price
            elif price <= extreme * (1.0 - threshold):
                direction = -1
                prior_extreme = extreme
        else:
            if price < extreme:
                extreme = price
            elif price >= extreme * (1.0 + threshold):
                direction = 1
                prior_extreme = extreme

        if direction == 0:
            continue
        sequence += 1
        intrinsic_distance = direction * (price / prior_extreme - 1.0)
        events.append(
            {
                "symbol": symbol,
                "event_sequence": sequence,
                "event_ts": timestamp,
                "direction": float(direction),
                "confirmation_price": price,
                "prior_extreme": prior_extreme,
                "confirmation_threshold": used_threshold,
                "actual_confirmation_distance": intrinsic_distance,
                "actual_confirmation_distance_ratio": (
                    intrinsic_distance / used_threshold
                    if used_threshold > 0.0
                    else float("nan")
                ),
                "next_leg_threshold": candidate,
            }
        )
        mode = direction
        extreme = price
        high = price
        low = price
        threshold = candidate

    if not events:
        return pd.DataFrame()
    output = pd.DataFrame(events).sort_values("event_ts", kind="stable")
    output["next_dc_ts"] = output["event_ts"].shift(-1)
    output["next_dc_price"] = output["confirmation_price"].shift(-1)
    return output


def enrich_events(
    events: pd.DataFrame,
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> pd.DataFrame:
    if events.empty:
        return events
    maximum_holding = pd.Timedelta(minutes=int(rules["maximum_holding_minutes"]))
    rows: list[dict[str, Any]] = []
    for event in events.itertuples(index=False):
        event_ts = pd.Timestamp(event.event_ts)
        confirmation_ts = event_ts + pd.Timedelta(minutes=5)
        next_dc_ts = pd.Timestamp(event.next_dc_ts)
        if pd.isna(next_dc_ts) or next_dc_ts <= confirmation_ts:
            continue
        capped_exit = confirmation_ts + maximum_holding
        if next_dc_ts <= capped_exit:
            exit_ts = next_dc_ts
            exit_reason = "NEXT_DIRECTIONAL_CHANGE"
        else:
            exit_ts = capped_exit
            exit_reason = "FOUR_HOUR_CAP"
        if event_ts not in frame.index or confirmation_ts not in frame.index:
            continue
        if exit_ts not in frame.index:
            continue
        event_row = frame.loc[event_ts]
        confirmation_row = frame.loc[confirmation_ts]
        if isinstance(event_row, pd.DataFrame) or isinstance(
            confirmation_row,
            pd.DataFrame,
        ):
            raise RuntimeError(f"duplicate rows at directional event {event_ts}")
        required_values = (
            event_row["futures_close"],
            event_row["spot_close"],
            event_row["index_close"],
            event_row["spot_return_1h"],
            event_row["perp_spot_gap_1h_z"],
            event_row["open_interest_1h_z"],
            confirmation_row["futures_close"],
            confirmation_row["spot_close"],
            confirmation_row["index_close"],
            confirmation_row["futures_pressure"],
            confirmation_row["open_interest_5m_z"],
            confirmation_row["futures_return_z"],
            frame.loc[exit_ts, "futures_close"],
        )
        if not all(math.isfinite(float(value)) for value in required_values):
            continue
        direction = float(event.direction)
        entry_price = float(confirmation_row["futures_close"])
        exit_price = float(frame.loc[exit_ts, "futures_close"])
        confirmation_price_extension = direction * (
            entry_price / float(event_row["futures_close"]) - 1.0
        )
        confirmation_spot_follow = direction * (
            float(confirmation_row["spot_close"])
            / float(event_row["spot_close"])
            - 1.0
        )
        confirmation_index_follow = direction * (
            float(confirmation_row["index_close"])
            / float(event_row["index_close"])
            - 1.0
        )
        rows.append(
            {
                **event._asdict(),
                "confirmation_ts": confirmation_ts,
                "entry_ts": confirmation_ts,
                "entry_price": entry_price,
                "exit_ts": exit_ts,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "holding_minutes": int(
                    (exit_ts - confirmation_ts).total_seconds() // 60
                ),
                "event_open_interest_1h_z": float(
                    event_row["open_interest_1h_z"]
                ),
                "event_directional_spot_1h": direction
                * float(event_row["spot_return_1h"]),
                "event_directional_perp_spot_gap_1h_z": direction
                * float(event_row["perp_spot_gap_1h_z"]),
                "confirmation_price_extension": confirmation_price_extension,
                "confirmation_open_interest_5m_z": float(
                    confirmation_row["open_interest_5m_z"]
                ),
                "confirmation_directional_futures_taker_pressure": direction
                * float(confirmation_row["futures_pressure"]),
                "confirmation_directional_return_z": direction
                * float(confirmation_row["futures_return_z"]),
                "confirmation_directional_spot_follow": confirmation_spot_follow,
                "confirmation_directional_index_follow": confirmation_index_follow,
                "continuation_gross_return": direction
                * (exit_price / entry_price - 1.0),
                "reclaim_gross_return": -direction
                * (exit_price / entry_price - 1.0),
            }
        )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("event_ts", kind="stable")


def add_peer_spot_breadth(
    events: pd.DataFrame,
    spot_frames: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    if events.empty:
        return events
    one_bar_returns = pd.DataFrame(
        {
            symbol: frame["close"].astype(float).pct_change()
            for symbol, frame in spot_frames.items()
        }
    )
    breadth: list[float] = []
    for event in events.itertuples(index=False):
        timestamp = pd.Timestamp(event.confirmation_ts)
        if timestamp not in one_bar_returns.index:
            breadth.append(float("nan"))
            continue
        values = one_bar_returns.loc[timestamp].dropna().to_numpy(dtype=float)
        breadth.append(
            float(np.mean(np.sign(values) == float(event.direction)))
            if len(values)
            else float("nan")
        )
    output = events.copy()
    output["confirmation_peer_spot_breadth"] = breadth
    return output.dropna(subset=["confirmation_peer_spot_breadth"])


def classify(events: pd.DataFrame, rules: dict[str, Any]) -> pd.DataFrame:
    if events.empty:
        return events
    accepted = (
        (
            events["event_open_interest_1h_z"]
            >= float(rules["accepted_event_open_interest_1h_z_min"])
        )
        & (
            events["event_directional_spot_1h"]
            > float(rules["accepted_event_directional_spot_1h_min"])
        )
        & (
            events["event_directional_perp_spot_gap_1h_z"]
            <= float(rules["accepted_event_directional_perp_spot_gap_z_max"])
        )
        & (
            events["confirmation_price_extension"]
            > float(rules["accepted_confirmation_price_extension_min"])
        )
        & (
            events["confirmation_open_interest_5m_z"]
            >= float(rules["accepted_confirmation_open_interest_5m_z_min"])
        )
        & (
            events["confirmation_directional_futures_taker_pressure"]
            >= float(
                rules[
                    "accepted_confirmation_directional_futures_taker_pressure_min"
                ]
            )
        )
        & (
            events["confirmation_directional_spot_follow"]
            > float(rules["accepted_confirmation_directional_spot_follow_min"])
        )
        & (
            events["confirmation_directional_index_follow"]
            > float(rules["accepted_confirmation_directional_index_follow_min"])
        )
        & (
            events["confirmation_peer_spot_breadth"]
            >= float(rules["accepted_confirmation_peer_spot_breadth_min"])
        )
    )
    failed = (
        (
            events["event_open_interest_1h_z"]
            <= float(rules["failed_event_open_interest_1h_z_max"])
        )
        & (
            events["event_directional_perp_spot_gap_1h_z"]
            >= float(rules["failed_event_directional_perp_spot_gap_z_min"])
        )
        & (
            events["confirmation_price_extension"]
            <= float(rules["failed_confirmation_price_extension_max"])
        )
        & (
            events["confirmation_open_interest_5m_z"]
            >= float(rules["failed_confirmation_open_interest_5m_z_min"])
        )
        & (
            events["confirmation_directional_futures_taker_pressure"]
            <= float(
                rules[
                    "failed_confirmation_directional_futures_taker_pressure_max"
                ]
            )
        )
        & (
            events["confirmation_directional_spot_follow"]
            <= float(rules["failed_confirmation_directional_spot_follow_max"])
        )
    )
    routed = events[accepted | failed].copy()
    if routed.empty:
        return routed
    routed_accepted = accepted.loc[routed.index]
    routed["route"] = np.where(routed_accepted, ACCEPTED, FAILED)
    routed["trade_direction"] = np.where(
        routed["route"] == ACCEPTED,
        routed["direction"],
        -routed["direction"],
    )
    routed["gross_return"] = np.where(
        routed["route"] == ACCEPTED,
        routed["continuation_gross_return"],
        routed["reclaim_gross_return"],
    )
    cost = total_screening_cost(rules)
    routed["cost_return"] = cost
    routed["net_return"] = routed["gross_return"] - cost

    accepted_context = (
        routed["confirmation_threshold"] / max(cost, 1e-12)
        * (routed["event_open_interest_1h_z"] + 1.0).clip(lower=0.01)
        * (
            routed["event_directional_spot_1h"]
            / routed["confirmation_threshold"].replace(0.0, np.nan)
        ).clip(lower=0.0)
    )
    failed_context = (
        routed["confirmation_threshold"] / max(cost, 1e-12)
        * (-routed["event_open_interest_1h_z"]).clip(lower=0.0)
        * routed["event_directional_perp_spot_gap_1h_z"].clip(lower=0.0)
    )
    accepted_quality = (
        routed["confirmation_directional_return_z"].clip(lower=0.0)
        * (routed["confirmation_open_interest_5m_z"] + 0.25).clip(lower=0.01)
        * routed["confirmation_peer_spot_breadth"]
    )
    failed_quality = (
        (-routed["confirmation_directional_return_z"]).clip(lower=0.0)
        * (-routed["confirmation_directional_futures_taker_pressure"]).clip(
            lower=0.0
        )
        * (routed["confirmation_open_interest_5m_z"] + 0.25).clip(lower=0.01)
    )
    routed["context_score"] = np.where(
        routed["route"] == ACCEPTED,
        accepted_context,
        failed_context,
    )
    routed["state_quality"] = np.where(
        routed["route"] == ACCEPTED,
        accepted_quality,
        failed_quality,
    )
    routed["rank_score"] = routed["context_score"] * routed["state_quality"]
    return routed.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["rank_score", "gross_return", "net_return", "exit_ts"],
    )


def arbitrate(events: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if events.empty:
        return events, Counter()
    ordered = events.sort_values(
        ["event_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for _, episode in ordered.groupby("event_ts", sort=True):
        winner = episode.iloc[0]
        skips["SAME_INTRINSIC_EVENT_LOSER"] += max(0, len(episode.index) - 1)
        entry_ts = pd.Timestamp(winner["entry_ts"])
        if entry_ts < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    if not selected:
        return ordered.iloc[0:0], skips
    return pd.DataFrame(selected).reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    entry_times = pd.to_datetime(frame["entry_ts"], utc=True)
    sample = frame[(entry_times >= lower) & (entry_times < upper)].copy()
    calendar_days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end,
            "calendar_days": calendar_days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "net_t_stat": None,
            "win_rate": None,
            "payoff_ratio": None,
            "positive_month_share": 0.0,
            "positive_months": 0,
            "active_months": 0,
            "mean_holding_minutes": None,
            "route_stats": {},
            "symbol_counts": {},
            "exit_reason_counts": {},
        }
    sample["entry_ts"] = pd.to_datetime(sample["entry_ts"], utc=True)
    monthly = (
        sample.set_index("entry_ts")["net_return"].resample("MS").mean().dropna()
    )
    wins = sample[sample["net_return"] > 0.0]["net_return"]
    losses = sample[sample["net_return"] < 0.0]["net_return"]
    payoff = None
    if len(wins.index) and len(losses.index):
        payoff = float(wins.mean() / abs(losses.mean()))
    route_stats: dict[str, Any] = {}
    for route, routed in sample.groupby("route"):
        route_stats[str(route)] = {
            "trades": len(routed.index),
            "mean_net_bps": float(routed["net_return"].mean() * 10_000.0),
            "win_rate": float((routed["net_return"] > 0.0).mean()),
            "net_t_stat": t_stat(routed["net_return"]),
            "mean_holding_minutes": float(routed["holding_minutes"].mean()),
        }
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": calendar_days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(calendar_days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "net_t_stat": t_stat(sample["net_return"]),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff,
        "positive_month_share": float((monthly > 0.0).mean()),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "mean_holding_minutes": float(sample["holding_minutes"].mean()),
        "route_stats": route_stats,
        "symbol_counts": dict(Counter(sample["symbol"].astype(str))),
        "exit_reason_counts": dict(Counter(sample["exit_reason"].astype(str))),
    }


def cross_split_routes(
    development: dict[str, Any],
    stability: dict[str, Any],
    latest: dict[str, Any],
) -> dict[str, Any]:
    routes = set(development["route_stats"]) | set(stability["route_stats"]) | set(
        latest["route_stats"]
    )
    output: dict[str, Any] = {}
    for route in sorted(routes):
        splits: dict[str, Any] = {}
        positive_all = True
        for name, summary in (
            ("development", development),
            ("stability", stability),
            ("latest_pulse", latest),
        ):
            stats = summary["route_stats"].get(route)
            splits[name] = stats
            if stats is None or stats["trades"] == 0 or stats["mean_net_bps"] <= 0.0:
                positive_all = False
        output[route] = {
            "positive_across_all_declared_splits": positive_all,
            "splits": splits,
        }
    return output


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Candidate 15 V20 — Directional-change intrinsic-time diagnostic",
        "",
        f"**{payload['classification']}**",
        "",
        "Actual completed closes confirm every directional change; theoretical threshold-touch prices are never used.",
    ]
    for title, key in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("Latest July 2026 pulse", "latest_pulse"),
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
                f"- mean holding minutes: `{summary['mean_holding_minutes']}`",
                f"- route stats: `{summary['route_stats']}`",
                f"- symbol counts: `{summary['symbol_counts']}`",
                f"- exit reasons: `{summary['exit_reason_counts']}`",
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
            "This mechanism screen does not synthesize account NAV. A surviving route still requires frozen NautilusTrader orders, 3% current-NAV risk sizing, one global slot, actual fees/funding and continuous-account validation.",
        )
    )
    return "\n".join(lines) + "\n"


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = common.load_json(protocol_path)
    data = protocol["data"]
    rules = protocol["fixed_rules"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)

    futures_tasks: list[tuple[str, str, str, str, Path]] = []
    spot_tasks: list[tuple[str, str, str, Path]] = []
    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in data["symbols"]:
        for month in common.months(start, end):
            token = f"{month.year:04d}-{month.month:02d}"
            for dataset in ("klines", "indexPriceKlines"):
                futures_tasks.append(
                    (
                        dataset,
                        symbol,
                        data["interval"],
                        token,
                        output
                        / "data"
                        / dataset
                        / symbol
                        / f"{symbol}-{data['interval']}-{token}.zip",
                    )
                )
            spot_tasks.append(
                (
                    symbol,
                    data["interval"],
                    token,
                    output
                    / "data"
                    / "spot_klines"
                    / symbol
                    / f"{symbol}-{data['interval']}-{token}.zip",
                )
            )
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
        jobs = [pool.submit(common.download_one, task) for task in futures_tasks]
        jobs.extend(pool.submit(spot_common.download_spot, task) for task in spot_tasks)
        jobs.extend(pool.submit(oi_common.download_metric, task) for task in metric_tasks)
        for future in as_completed(jobs):
            records.append(future.result())
    common.write_json(output / "data_manifest.json", oi_common.aggregate_manifest(records))

    spot_frames: dict[str, pd.DataFrame] = {}
    symbol_frames: dict[str, pd.DataFrame] = {}
    raw_dc_parts: list[pd.DataFrame] = []
    enriched_parts: list[pd.DataFrame] = []
    for symbol in data["symbols"]:
        futures = market_adapter.load_market_series(
            sorted((output / "data" / "klines" / symbol).glob("*.zip")),
            start,
            end,
        )
        spot = market_adapter.load_market_series(
            sorted((output / "data" / "spot_klines" / symbol).glob("*.zip")),
            start,
            end,
        )
        index_price = common.load_series(
            sorted(
                (output / "data" / "indexPriceKlines" / symbol).glob("*.zip")
            ),
            start,
            end,
            False,
        )
        metrics = metrics_adapter.load_metrics(
            sorted((output / "data" / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
        frame = build_symbol_frame(futures, spot, index_price, metrics, rules)
        dc_events = detect_directional_changes(symbol, frame)
        enriched = enrich_events(dc_events, frame, rules)
        spot_frames[symbol] = spot
        symbol_frames[symbol] = frame
        if not dc_events.empty:
            raw_dc_parts.append(dc_events)
        if not enriched.empty:
            enriched_parts.append(enriched)

    raw_dc = (
        pd.concat(raw_dc_parts, ignore_index=True).sort_values("event_ts")
        if raw_dc_parts
        else pd.DataFrame()
    )
    enriched_events = (
        pd.concat(enriched_parts, ignore_index=True).sort_values("event_ts")
        if enriched_parts
        else pd.DataFrame()
    )
    with_breadth = add_peer_spot_breadth(enriched_events, spot_frames)
    routed = classify(with_breadth, rules)
    routed.to_csv(output / "routed_events.csv", index=False)
    selected, skips = arbitrate(routed)
    selected.to_csv(output / "selected_episodes.csv", index=False)

    development = summarize(
        selected,
        evaluation["development_start"],
        evaluation["development_end_exclusive"],
    )
    stability = summarize(
        selected,
        evaluation["stability_start"],
        evaluation["stability_end_exclusive"],
    )
    latest = summarize(
        selected,
        evaluation["latest_pulse_start"],
        evaluation["latest_pulse_end_exclusive"],
    )
    routes = cross_split_routes(development, stability, latest)
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
        "stability_independent_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_stability_trades_per_calendar_day"])
        ),
        "positive_latest_pulse_mean_net": (
            latest["mean_net_bps"] is not None
            and latest["mean_net_bps"]
            > float(gate["minimum_latest_pulse_mean_net_bps"])
        ),
        "latest_pulse_trade_count": (
            latest["trades"] >= int(gate["minimum_latest_pulse_trades"])
        ),
        "symbol_concentration": (
            concentration <= float(gate["maximum_single_symbol_share"])
        ),
    }
    passed = all(checks.values())
    if passed:
        classification = "V20_MECHANISM_ADVANCES_TO_FROZEN_NAUTILUS"
        decision = (
            "The fixed actual-close directional-change router survived development, "
            "year-long stability and the latest pulse. Freeze the intrinsic threshold, "
            "state routes and event exit for implementation in the existing "
            "NautilusTrader global portfolio runner."
        )
    else:
        classification = "V20_DIRECTIONAL_CHANGE_ROUTER_REJECTED_OR_UNDERPOWERED"
        survivors = [
            route
            for route, evidence in routes.items()
            if evidence["positive_across_all_declared_splits"]
        ]
        decision = (
            "The integrated directional-change router failed at least one predeclared "
            f"gate. Cross-split-positive routes: {survivors}. Do not tune thresholds; "
            "retain only those routes and move to another independent causal family."
        )
    payload = {
        "schema": "candidate-15-v20-summary-v1",
        "classification": classification,
        "advance_to_nautilus": passed,
        "actual_close_confirmations_only": True,
        "total_screening_cost_bps": total_screening_cost(rules) * 10_000.0,
        "raw_directional_change_events": len(raw_dc.index),
        "enriched_events": len(enriched_events.index),
        "routed_events": len(routed.index),
        "selected_events": len(selected.index),
        "arbitration_skips": dict(skips),
        "development": development,
        "stability": stability,
        "latest_pulse": latest,
        "cross_split_routes": routes,
        "advance_checks": checks,
        "latest_pulse_status": evaluation["latest_pulse_status"],
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
