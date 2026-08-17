#!/usr/bin/env python3
"""Failed-acceptance trap family added to the v7 integrated grammar.

A breakout that was genuinely accepted for several completed bars and then
loses the level is not treated as a generic sweep. It contains trapped
continuation inventory and a newly revealed opposite auction. V9 adds that
independent mechanism while preserving the original level cluster, so genuine
continuation, failure reversal, immediate confirmation and first retest are
alternative decisions rather than duplicated trades.
"""
from __future__ import annotations

import argparse
from datetime import date
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v7 as v7

base = v7.base
EXTRA_FEATURES = ("family_failed_acceptance",)
FEATURE_COLUMNS = tuple(v7.FEATURE_COLUMNS) + EXTRA_FEATURES
SYMBOLS = base.SYMBOLS

_previous_range: Any = None
_previous_features: Any = None


def _valid(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def _level_specs(row: pd.Series, timestamp: pd.Timestamp) -> list[tuple[str, str, float, pd.Timestamp]]:
    return [
        (
            "PREVIOUS_8H",
            "HIGH",
            float(row.get("block_high", np.nan)),
            timestamp.floor("8h") - pd.Timedelta(hours=8),
        ),
        (
            "PREVIOUS_8H",
            "LOW",
            float(row.get("block_low", np.nan)),
            timestamp.floor("8h") - pd.Timedelta(hours=8),
        ),
        (
            "PREVIOUS_DAY",
            "HIGH",
            float(row.get("day_high", np.nan)),
            timestamp.floor("D") - pd.Timedelta(days=1),
        ),
        (
            "PREVIOUS_DAY",
            "LOW",
            float(row.get("day_low", np.nan)),
            timestamp.floor("D") - pd.Timedelta(days=1),
        ),
        (
            "ROLLING_4H",
            "HIGH",
            float(row.get("rolling_4h_high", np.nan)),
            timestamp.floor("4h") - pd.Timedelta(hours=4),
        ),
        (
            "ROLLING_4H",
            "LOW",
            float(row.get("rolling_4h_low", np.nan)),
            timestamp.floor("4h") - pd.Timedelta(hours=4),
        ),
    ]


def _failed_acceptance_episodes(
    period: str,
    symbol: str,
    frame: pd.DataFrame,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> list[base.Episode]:
    tick = base.TICKS[symbol]
    episodes: list[base.Episode] = []
    consumed: set[tuple[str, int, str]] = set()
    states: list[dict[str, Any]] = []

    for i in range(360, len(frame) - 2):
        row = frame.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if timestamp > end_time:
            break
        atr = float(row.get("atr", np.nan))
        if not math.isfinite(atr) or atr <= tick:
            continue

        survivors: list[dict[str, Any]] = []
        for state in states:
            if i > int(state["expiry"]):
                continue
            breakout_side = int(state["breakout_side"])
            inward_side = -breakout_side
            level = float(state["level"])
            outside = (
                float(row["close"]) > level + 0.015 * atr
                if breakout_side > 0
                else float(row["close"]) < level - 0.015 * atr
            )
            if outside:
                state["outside_closes"] = int(state["outside_closes"]) + 1
            if breakout_side > 0:
                state["extreme"] = max(float(state["extreme"]), float(row["high"]))
            else:
                state["extreme"] = min(float(state["extreme"]), float(row["low"]))

            failed_inside = (
                float(row["close"]) < level - 0.018 * atr
                if breakout_side > 0
                else float(row["close"]) > level + 0.018 * atr
            )
            aligned_flow = inward_side * float(row.get("flow_3", np.nan))
            aligned_return = inward_side * float(row.get("ret_3", np.nan))
            common = inward_side * float(row.get("common_3", np.nan))
            oi_change = float(row.get("oi_change_5", np.nan))
            confirms = (
                int(state["outside_closes"]) >= 2
                and failed_inside
                and _valid(aligned_flow)
                and aligned_flow > 0.0
                and _valid(aligned_return)
                and aligned_return > 0.02
                and _valid(common)
                and common > -0.65
                and (not _valid(oi_change) or oi_change < 0.02)
            )
            if confirms:
                extreme = float(state["extreme"])
                stop = (
                    extreme - max(2.0 * tick, 0.055 * atr)
                    if inward_side > 0
                    else extreme + max(2.0 * tick, 0.055 * atr)
                )
                entry_hint = float(row["close"])
                targets, target_sources = base._structural_targets(
                    row,
                    inward_side,
                    entry_hint,
                    "FAILED_ACCEPTANCE_TRAP",
                    level,
                    tick,
                )
                if (
                    targets
                    and inward_side * (entry_hint - stop) > tick
                    and start_time <= timestamp <= end_time
                ):
                    cluster = str(state["cluster"])
                    episode = base.Episode(
                        period=period,
                        episode_id=f"{cluster}:FAILED_ACCEPTANCE:{i}",
                        cluster_id=cluster,
                        symbol=symbol,
                        family="FAILED_ACCEPTANCE_TRAP",
                        source=str(state["source"]),
                        side=inward_side,
                        event_index=i,
                        event_time=timestamp,
                        level=level,
                        event_extreme=extreme,
                        stop=stop,
                        targets=targets,
                        target_sources=target_sources,
                        max_hold_minutes=210,
                        level_age_hours=float(state["level_age_hours"]),
                        event_penetration_atr=float(state["penetration_atr"]),
                        event_range_atr=float(state["event_range_atr"]),
                    )
                    episodes.append(episode)
                    retest = v7.v6.v5._first_retest(episode, frame, end_time)
                    if retest is not None:
                        episodes.append(retest)
                continue
            survivors.append(state)
        states = survivors

        touched: list[tuple[float, tuple[str, str, float, pd.Timestamp]]] = []
        for spec in _level_specs(row, timestamp):
            source, kind, level, activation = spec
            if not math.isfinite(level):
                continue
            key = (source, int(round(level / tick)), str(activation.value))
            if key in consumed:
                continue
            hit = (
                float(row["high"]) >= level
                if kind == "HIGH"
                else float(row["low"]) <= level
            )
            if hit:
                touched.append((abs(float(row["open"]) - level), spec))
        if not touched:
            continue
        _, (source, kind, level, activation) = min(touched, key=lambda item: item[0])
        key = (source, int(round(level / tick)), str(activation.value))
        consumed.add(key)
        breakout_side = 1 if kind == "HIGH" else -1
        beyond = (
            float(row["close"]) > level + 0.04 * atr
            if breakout_side > 0
            else float(row["close"]) < level - 0.04 * atr
        )
        aligned_flow = breakout_side * float(row.get("flow_3", np.nan))
        aligned_return = breakout_side * float(row.get("ret_3", np.nan))
        common = breakout_side * float(row.get("common_3", np.nan))
        if not (
            beyond
            and _valid(aligned_flow)
            and aligned_flow > 0.015
            and _valid(aligned_return)
            and aligned_return > 0.05
            and _valid(common)
            and common > -0.30
        ):
            continue
        penetration = (
            float(row["high"]) - level
            if kind == "HIGH"
            else level - float(row["low"])
        )
        cluster = f"{period}:{symbol}:RANGE:{source}:{activation.value}:{kind}"
        states.append(
            {
                "cluster": cluster,
                "source": source,
                "kind": kind,
                "level": level,
                "breakout_side": breakout_side,
                "outside_closes": 1,
                "extreme": float(row["high"]) if breakout_side > 0 else float(row["low"]),
                "level_age_hours": max(
                    0.0,
                    float((timestamp - activation) / pd.Timedelta(hours=1)),
                ),
                "penetration_atr": penetration / atr,
                "event_range_atr": (float(row["high"]) - float(row["low"])) / atr,
                "expiry": i + 15,
            }
        )
    return episodes


def _range_episodes(
    period: str,
    symbol: str,
    frame: pd.DataFrame,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> list[base.Episode]:
    episodes = _previous_range(period, symbol, frame, start_time, end_time)
    episodes.extend(
        _failed_acceptance_episodes(period, symbol, frame, start_time, end_time)
    )
    identities: set[str] = set()
    unique: list[base.Episode] = []
    for episode in sorted(
        episodes,
        key=lambda item: (item.event_time, item.cluster_id, item.episode_id),
    ):
        if episode.episode_id in identities:
            continue
        identities.add(episode.episode_id)
        unique.append(episode)
    return unique


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
    values["family_failed_acceptance"] = float(
        episode.family == "FAILED_ACCEPTANCE_TRAP"
    )
    return values


def _install() -> None:
    global _previous_range, _previous_features
    v7._install()
    _previous_range = base._range_episodes
    _previous_features = base._features
    base.FEATURE_COLUMNS = FEATURE_COLUMNS
    base._range_episodes = _range_episodes
    base._features = _features


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v9"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install()
    args = parse_args()
    base.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
