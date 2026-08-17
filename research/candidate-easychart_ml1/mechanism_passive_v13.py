#!/usr/bin/env python3
"""Causal passive first-retest plans for the integrated mechanism grammar.

The skilled-trader advantage is often price selection, not a later prediction.
After a confirmed displacement this module can place one resting order at the
first imbalance/order-block midpoint. The plan is frozen at placement, requires
a one-tick trade-through for a conservative fill, reserves the single account
slot until fill or expiry, and shares the original causal cluster with market
alternatives. An unfilled plan earns zero and still pays its opportunity time.
"""
from __future__ import annotations

import argparse
from datetime import date
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v9 as v9

base = v9.base
v5 = v9.v7.v6.v5
SYMBOLS = base.SYMBOLS
PASSIVE_FEATURE_COLUMNS = (
    "entry_style_market",
    "entry_style_passive",
    "passive_limit_distance_atr",
    "passive_zone_width_atr",
    "passive_ttl_fraction",
)
FEATURE_COLUMNS = tuple(v9.FEATURE_COLUMNS) + PASSIVE_FEATURE_COLUMNS

_previous_features: Any = None
_previous_snapshot_episode: Any = None


def _features(
    frame: pd.DataFrame,
    episode: base.Episode,
    snapshot_index: int,
    target: float,
    objective_rank: int,
    target_r: float,
) -> dict[str, float]:
    values = _previous_features(
        frame,
        episode,
        snapshot_index,
        target,
        objective_rank,
        target_r,
    )
    values.setdefault("entry_style_market", 1.0)
    values.setdefault("entry_style_passive", 0.0)
    values.setdefault("passive_limit_distance_atr", 0.0)
    values.setdefault("passive_zone_width_atr", 0.0)
    values.setdefault("passive_ttl_fraction", 0.0)
    return values


def _passive_ttl(episode: base.Episode) -> int:
    if episode.family == "RANGE_ACCEPTANCE_PULLBACK":
        return 30
    if episode.family == "FAILED_ACCEPTANCE_TRAP":
        return 24
    if episode.family == "SYSTEMIC_FORCED_FLOW_EXHAUSTION":
        return 18
    return 22


def _passive_label(
    frame: pd.DataFrame,
    episode: base.Episode,
    limit: float,
    target: float,
    ttl_minutes: int,
) -> dict[str, Any] | None:
    side = episode.side
    tick = base.TICKS[episode.symbol]
    decision_time = pd.Timestamp(frame.iloc[episode.event_index]["time"])
    ordering_time = decision_time + pd.Timedelta(nanoseconds=1)
    stop_fill = episode.stop - side * base.STOP_SLIP_TICKS * tick
    gross_risk = side * (limit - stop_fill)
    gross_reward = side * (target - limit)
    if gross_risk <= tick or gross_reward <= tick or limit <= 0.0:
        return None
    stop_return = (
        side * (stop_fill - limit) / limit
        - base.MAKER_FEE
        - base.TAKER_FEE
    )
    target_return = (
        side * (target - limit) / limit
        - 2.0 * base.MAKER_FEE
    )
    risk_fraction = -stop_return
    if risk_fraction <= 0.0 or target_return <= 0.0:
        return None
    target_r = target_return / risk_fraction

    first_index = episode.event_index + 1
    expiry_index = min(len(frame) - 1, episode.event_index + ttl_minutes)
    fill_index: int | None = None
    for index in range(first_index, expiry_index + 1):
        row = frame.iloc[index]
        traded_through = (
            float(row["low"]) <= limit - tick
            if side > 0
            else float(row["high"]) >= limit + tick
        )
        if traded_through:
            fill_index = index
            break
    if fill_index is None:
        expiry_time = pd.Timestamp(frame.iloc[expiry_index]["time"])
        return {
            "entry_time": ordering_time.isoformat(),
            "fill_time": None,
            "exit_time": expiry_time.isoformat(),
            "entry": limit,
            "stop": episode.stop,
            "target": target,
            "target_r": target_r,
            "risk_fraction_of_price": risk_fraction,
            "outcome": "TIMEOUT",
            "target_first": 0,
            "stop_first": 0,
            "timeout": 1,
            "fast_stop": 0,
            "filled": 0,
            "unfilled": 1,
            "realized_r": 0.0,
            "duration_minutes": int(expiry_index - episode.event_index),
            "position_duration_minutes": 0,
            "funding_crossings": 0,
        }

    outcome = "TIMEOUT"
    exit_index = min(len(frame) - 1, fill_index + episode.max_hold_minutes)
    for index in range(fill_index, exit_index + 1):
        row = frame.iloc[index]
        stop_hit = (
            float(row["low"]) <= episode.stop
            if side > 0
            else float(row["high"]) >= episode.stop
        )
        target_hit = (
            float(row["high"]) >= target
            if side > 0
            else float(row["low"]) <= target
        )
        if stop_hit:
            outcome = "STOP_FIRST"
            exit_index = index
            break
        if target_hit:
            outcome = "TARGET_FIRST"
            exit_index = index
            break
    fill_time = pd.Timestamp(frame.iloc[fill_index]["time"]) - pd.Timedelta(minutes=1)
    exit_time = pd.Timestamp(frame.iloc[exit_index]["time"])
    crossings = base._funding_crossings(fill_time, exit_time)
    funding_cost = base.FUNDING_CROSSING_COST * crossings
    if outcome == "STOP_FIRST":
        realized_return = stop_return - funding_cost
    elif outcome == "TARGET_FIRST":
        realized_return = target_return - funding_cost
    else:
        exit_price = float(frame.iloc[exit_index]["close"])
        realized_return = (
            side * (exit_price - limit) / limit
            - base.MAKER_FEE
            - base.TAKER_FEE
            - funding_cost
        )
    return {
        "entry_time": ordering_time.isoformat(),
        "fill_time": fill_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry": limit,
        "stop": episode.stop,
        "target": target,
        "target_r": target_r,
        "risk_fraction_of_price": risk_fraction,
        "outcome": outcome,
        "target_first": int(outcome == "TARGET_FIRST"),
        "stop_first": int(outcome == "STOP_FIRST"),
        "timeout": int(outcome == "TIMEOUT"),
        "fast_stop": int(
            outcome == "STOP_FIRST" and exit_index - fill_index + 1 <= 10
        ),
        "filled": 1,
        "unfilled": 0,
        "realized_r": realized_return / risk_fraction,
        "duration_minutes": int(exit_index - episode.event_index),
        "position_duration_minutes": int(exit_index - fill_index + 1),
        "funding_crossings": crossings,
    }


def _passive_rows(
    episode: base.Episode,
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    zone = v5._imbalance_zone(frame, episode.event_index, episode.side)
    if zone is None:
        return []
    zone_low, zone_high, zone_kind = zone
    if not (
        math.isfinite(zone_low)
        and math.isfinite(zone_high)
        and zone_high > zone_low
    ):
        return []
    tick = base.TICKS[episode.symbol]
    event = frame.iloc[episode.event_index]
    atr = float(event.get("atr", np.nan))
    if not math.isfinite(atr) or atr <= tick:
        return []
    limit = 0.5 * (zone_low + zone_high)
    current = float(event["close"])
    distance = episode.side * (current - limit)
    if distance <= tick or distance > 1.10 * atr:
        return []
    ttl = _passive_ttl(episode)
    snapshot_time = pd.Timestamp(event["time"])
    rows: list[dict[str, Any]] = []
    for objective_rank, (target, target_source) in enumerate(
        zip(episode.targets[:3], episode.target_sources[:3]),
        start=1,
    ):
        label = _passive_label(frame, episode, limit, target, ttl)
        if label is None:
            continue
        feature_values = _previous_features(
            frame,
            episode,
            episode.event_index,
            target,
            objective_rank,
            float(label["target_r"]),
        )
        risk = episode.side * (limit - episode.stop)
        reward = episode.side * (target - limit)
        feature_values.update(
            {
                "planned_target_r": float(label["target_r"]),
                "risk_atr": risk / atr,
                "reward_atr": reward / atr,
                "entry_delay_minutes": 0.0,
                "entry_style_market": 0.0,
                "entry_style_passive": 1.0,
                "passive_limit_distance_atr": distance / atr,
                "passive_zone_width_atr": (zone_high - zone_low) / atr,
                "passive_ttl_fraction": ttl / max(float(episode.max_hold_minutes), 1.0),
            }
        )
        row: dict[str, Any] = {
            "action_id": (
                f"{episode.episode_id}:{snapshot_time.value}:PASSIVE:"
                f"{zone_kind}:{objective_rank}"
            ),
            "episode_id": episode.episode_id,
            "cluster_id": episode.cluster_id,
            "period": episode.period,
            "symbol": episode.symbol,
            "family": episode.family,
            "source": f"{episode.source}|PASSIVE_{zone_kind}",
            "side": episode.side,
            "event_time": episode.event_time.isoformat(),
            "snapshot_time": snapshot_time.isoformat(),
            "target_source": target_source,
            "entry_style": "PASSIVE_FIRST_RETEST",
            "zone_kind": zone_kind,
            "limit_placed": limit,
            "order_ttl_minutes": ttl,
        }
        row.update(label)
        row.update(feature_values)
        rows.append(row)
    return rows


def _snapshot_episode(
    episode: base.Episode,
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    market_rows = _previous_snapshot_episode(episode, frame)
    for row in market_rows:
        row.setdefault("fill_time", row.get("entry_time"))
        row.setdefault("filled", 1)
        row.setdefault("unfilled", 0)
        row.setdefault("position_duration_minutes", row.get("duration_minutes", 0))
        row.setdefault("entry_style", "MARKET_CONFIRMED")
        row.setdefault("zone_kind", None)
        row.setdefault("limit_placed", np.nan)
        row.setdefault("order_ttl_minutes", 0)
        row.setdefault("entry_style_market", 1.0)
        row.setdefault("entry_style_passive", 0.0)
        row.setdefault("passive_limit_distance_atr", 0.0)
        row.setdefault("passive_zone_width_atr", 0.0)
        row.setdefault("passive_ttl_fraction", 0.0)
    return market_rows + _passive_rows(episode, frame)


def _install() -> None:
    global _previous_features, _previous_snapshot_episode
    v9._install()
    _previous_features = base._features
    _previous_snapshot_episode = base._snapshot_episode
    base.FEATURE_COLUMNS = FEATURE_COLUMNS
    base._features = _features
    base._snapshot_episode = _snapshot_episode


def harvest(
    period: str,
    start: date,
    end: date,
    cache: Path,
    output: Path,
) -> None:
    _install()
    base.harvest(period, start, end, cache, output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v13"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
