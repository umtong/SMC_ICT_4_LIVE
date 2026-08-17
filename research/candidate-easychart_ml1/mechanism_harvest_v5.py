#!/usr/bin/env python3
"""First-retest execution layer for the sequential mechanism grammar.

A confirmed transition is often directionally correct but already too far from
its structural invalidation. V5 keeps the v4 causal event, then creates a later
alternative only when the first revisit of the displacement imbalance or the
last adverse candle holds. The alternative belongs to the original cluster, so
it cannot inflate trade count or be traded after an earlier action from the
same cause.
"""
from __future__ import annotations

import argparse
from datetime import date
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v4 as v4

base = v4.base
EXTRA_FEATURES = ("setup_immediate", "setup_first_retest")
FEATURE_COLUMNS = tuple(base.FEATURE_COLUMNS) + EXTRA_FEATURES
SYMBOLS = base.SYMBOLS

_original_features = base._features
_original_range = v4._range_episodes


def _source_root(source: str) -> str:
    return source.split("|", 1)[0]


def _features(
    frame: pd.DataFrame,
    episode: base.Episode,
    snapshot_index: int,
    target: float,
    objective_rank: int,
    target_r: float,
) -> dict[str, float]:
    values = _original_features(
        frame,
        episode,
        snapshot_index,
        target,
        objective_rank,
        target_r,
    )
    root = _source_root(str(episode.source))
    values["source_previous_8h"] = float(root == "PREVIOUS_8H")
    values["source_previous_day"] = float(root == "PREVIOUS_DAY")
    values["source_rolling_4h"] = float(root == "ROLLING_4H")
    values["source_systemic"] = float(root == "SYSTEMIC")
    values["source_common_factor"] = float(root == "COMMON_FACTOR")
    is_retest = "FIRST_RETEST" in str(episode.source)
    values["setup_immediate"] = float(not is_retest)
    values["setup_first_retest"] = float(is_retest)
    return values


def _imbalance_zone(
    frame: pd.DataFrame,
    event_index: int,
    side: int,
) -> tuple[float, float, str] | None:
    for j in range(event_index, max(1, event_index - 4), -1):
        if j < 2:
            break
        if side > 0:
            older_high = float(frame.iloc[j - 2]["high"])
            newer_low = float(frame.iloc[j]["low"])
            if newer_low > older_high:
                return older_high, newer_low, "FVG"
        else:
            newer_high = float(frame.iloc[j]["high"])
            older_low = float(frame.iloc[j - 2]["low"])
            if newer_high < older_low:
                return newer_high, older_low, "FVG"

    for j in range(event_index - 1, max(-1, event_index - 7), -1):
        if j < 0:
            break
        candle = frame.iloc[j]
        bearish = float(candle["close"]) < float(candle["open"])
        bullish = float(candle["close"]) > float(candle["open"])
        if side > 0 and bearish:
            return float(candle["low"]), float(candle["open"]), "ORDER_BLOCK"
        if side < 0 and bullish:
            return float(candle["open"]), float(candle["high"]), "ORDER_BLOCK"
    return None


def _first_retest(
    episode: base.Episode,
    frame: pd.DataFrame,
    end_time: pd.Timestamp,
) -> base.Episode | None:
    zone = _imbalance_zone(frame, episode.event_index, episode.side)
    if zone is None:
        return None
    zone_low, zone_high, zone_kind = zone
    if not (
        math.isfinite(zone_low)
        and math.isfinite(zone_high)
        and zone_high > zone_low
    ):
        return None
    side = episode.side
    tick = base.TICKS[episode.symbol]
    nearest_target = float(episode.targets[0])
    decision_end = min(len(frame) - 2, episode.event_index + 18)
    for i in range(episode.event_index + 1, decision_end + 1):
        row = frame.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if timestamp > end_time:
            return None
        stop_hit = (
            float(row["low"]) <= episode.stop
            if side > 0
            else float(row["high"]) >= episode.stop
        )
        target_hit = (
            float(row["high"]) >= nearest_target
            if side > 0
            else float(row["low"]) <= nearest_target
        )
        if stop_hit or target_hit:
            return None
        touched = float(row["low"]) <= zone_high and float(row["high"]) >= zone_low
        if not touched:
            continue
        midpoint = 0.5 * (zone_low + zone_high)
        held = (
            float(row["close"]) >= midpoint
            if side > 0
            else float(row["close"]) <= midpoint
        )
        aligned_flow = side * float(row.get("flow_3", np.nan))
        aligned_return = side * float(row.get("ret_1", np.nan))
        if not (
            held
            and math.isfinite(aligned_flow)
            and aligned_flow > -0.03
            and math.isfinite(aligned_return)
            and aligned_return > -0.10
        ):
            return None
        atr = float(row.get("atr", np.nan))
        if not math.isfinite(atr) or atr <= tick:
            return None
        entry_hint = float(row["close"])
        root_source = _source_root(str(episode.source))
        targets, target_sources = base._structural_targets(
            row,
            side,
            entry_hint,
            episode.family,
            episode.level,
            tick,
        )
        if not targets or side * (entry_hint - episode.stop) <= tick:
            return None
        return base.Episode(
            period=episode.period,
            episode_id=(
                f"{episode.episode_id}:FIRST_RETEST:{zone_kind}:{i}"
            ),
            cluster_id=episode.cluster_id,
            symbol=episode.symbol,
            family=episode.family,
            source=f"{root_source}|FIRST_RETEST_{zone_kind}",
            side=side,
            event_index=i,
            event_time=timestamp,
            level=episode.level,
            event_extreme=episode.event_extreme,
            stop=episode.stop,
            targets=targets,
            target_sources=target_sources,
            max_hold_minutes=episode.max_hold_minutes,
            level_age_hours=episode.level_age_hours,
            event_penetration_atr=episode.event_penetration_atr,
            event_range_atr=episode.event_range_atr,
        )
    return None


def _range_episodes(
    period: str,
    symbol: str,
    frame: pd.DataFrame,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> list[base.Episode]:
    immediate = _original_range(period, symbol, frame, start_time, end_time)
    output = list(immediate)
    for episode in immediate:
        retest = _first_retest(episode, frame, end_time)
        if retest is not None:
            output.append(retest)
    return sorted(output, key=lambda item: (item.event_time, item.cluster_id, item.episode_id))


def _install() -> None:
    v4._install()
    base.FEATURE_COLUMNS = FEATURE_COLUMNS
    base._features = _features
    base._range_episodes = _range_episodes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v5"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install()
    args = parse_args()
    base.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
