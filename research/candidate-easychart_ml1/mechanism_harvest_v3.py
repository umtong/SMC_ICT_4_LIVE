#!/usr/bin/env python3
"""Corrected mechanism harvester.

This module reuses the complete v2 mechanism grammar while fixing two causal
identity details before running it:

* the nominal four-hour source is a completed, non-overlapping four-hour block,
  not a rolling level whose identity changes every minute;
* a next-bar-open order shares the wall-clock timestamp of the just-completed
  bar, so it is represented one nanosecond later to preserve strict event
  ordering without moving the simulated fill price.
"""
from __future__ import annotations

import argparse
from datetime import date
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v2 as base

FEATURE_COLUMNS = base.FEATURE_COLUMNS
SYMBOLS = base.SYMBOLS


_original_prepare_symbol = base._prepare_symbol
_original_label_action = base._label_action


def _prepare_symbol(frame: pd.DataFrame) -> pd.DataFrame:
    out = _original_prepare_symbol(frame)
    out["completed_4h_block"] = out["time"].dt.floor("4h")
    levels = out.groupby("completed_4h_block", sort=True).agg(
        completed_4h_high=("high", "max"),
        completed_4h_low=("low", "min"),
    )
    levels["completed_4h_mid"] = 0.5 * (
        levels["completed_4h_high"] + levels["completed_4h_low"]
    )
    out = out.join(levels.shift(1), on="completed_4h_block")
    # Keep the v2 feature/target column names while replacing their semantics
    # with a completed block that has one stable causal identity.
    out["rolling_4h_high"] = out["completed_4h_high"]
    out["rolling_4h_low"] = out["completed_4h_low"]
    out["rolling_4h_mid"] = out["completed_4h_mid"]
    return out


def _label_action(
    frame: pd.DataFrame,
    entry_index: int,
    episode: base.Episode,
    target: float,
) -> dict[str, Any] | None:
    result = _original_label_action(frame, entry_index, episode, target)
    if result is not None:
        result["entry_time"] = (
            pd.Timestamp(result["entry_time"]) + pd.Timedelta(nanoseconds=1)
        ).isoformat()
    return result


def _range_episodes(
    period: str,
    symbol: str,
    frame: pd.DataFrame,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> list[base.Episode]:
    tick = base.TICKS[symbol]
    episodes: list[base.Episode] = []
    consumed: set[tuple[str, int, str]] = set()
    pending: list[dict[str, Any]] = []

    for i in range(360, len(frame) - 2):
        row = frame.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if timestamp > end_time:
            break
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= tick:
            continue
        buffer = max(2.0 * tick, 0.035 * atr)

        survivors: list[dict[str, Any]] = []
        for state in pending:
            if i > int(state["expiry"]):
                continue
            side = int(state["side"])
            level = float(state["level"])
            beyond = (
                float(row["close"]) > level + buffer
                if side > 0
                else float(row["close"]) < level - buffer
            )
            if not beyond:
                continue
            state["accepted"] = int(state["accepted"]) + 1
            touched = (
                float(row["low"]) <= level + 0.18 * atr
                if side > 0
                else float(row["high"]) >= level - 0.18 * atr
            )
            if int(state["accepted"]) >= 2 and touched:
                extreme = (
                    min(float(row["low"]), level)
                    if side > 0
                    else max(float(row["high"]), level)
                )
                stop = (
                    extreme - max(2.0 * tick, 0.055 * atr)
                    if side > 0
                    else extreme + max(2.0 * tick, 0.055 * atr)
                )
                entry_hint = float(row["close"])
                targets, target_sources = base._structural_targets(
                    row,
                    side,
                    entry_hint,
                    "RANGE_ACCEPTANCE_PULLBACK",
                    level,
                    tick,
                )
                if (
                    targets
                    and side * (entry_hint - stop) > tick
                    and start_time <= timestamp <= end_time
                ):
                    cluster = (
                        f"{period}:{symbol}:RANGE:{state['source']}:"
                        f"{state['activation']}:{side}"
                    )
                    episodes.append(
                        base.Episode(
                            period=period,
                            episode_id=f"{cluster}:ACCEPT:{i}",
                            cluster_id=cluster,
                            symbol=symbol,
                            family="RANGE_ACCEPTANCE_PULLBACK",
                            source=str(state["source"]),
                            side=side,
                            event_index=i,
                            event_time=timestamp,
                            level=level,
                            event_extreme=extreme,
                            stop=stop,
                            targets=targets,
                            target_sources=target_sources,
                            max_hold_minutes=240,
                            level_age_hours=float(state["level_age_hours"]),
                            event_penetration_atr=(
                                abs(float(state["break_close"]) - level) / atr
                            ),
                            event_range_atr=(
                                float(row["high"]) - float(row["low"])
                            ) / atr,
                        )
                    )
                continue
            survivors.append(state)
        pending = survivors

        level_specs = [
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
        touched_this_bar: list[
            tuple[float, tuple[str, str, float, pd.Timestamp]]
        ] = []
        for spec in level_specs:
            source, kind, level, activation = spec
            if not math.isfinite(level):
                continue
            key = (source, int(round(level / tick)), str(activation.value))
            if key in consumed:
                continue
            touched = (
                float(row["high"]) >= level
                if kind == "HIGH"
                else float(row["low"]) <= level
            )
            if touched:
                touched_this_bar.append((abs(float(row["open"]) - level), spec))
        if not touched_this_bar:
            continue

        _, (source, kind, level, activation) = min(
            touched_this_bar,
            key=lambda item: item[0],
        )
        key = (source, int(round(level / tick)), str(activation.value))
        consumed.add(key)
        side = -1 if kind == "HIGH" else 1
        penetration = (
            float(row["high"]) - level
            if kind == "HIGH"
            else level - float(row["low"])
        )
        rejected = (
            float(row["close"]) < level - 0.01 * atr
            if kind == "HIGH"
            else float(row["close"]) > level + 0.01 * atr
        )
        accepted = (
            float(row["close"]) > level + buffer
            if kind == "HIGH"
            else float(row["close"]) < level - buffer
        )
        level_age = max(
            0.0,
            float((timestamp - activation) / pd.Timedelta(hours=1)),
        )

        if rejected and penetration >= max(tick, 0.02 * atr):
            extreme = float(row["high"]) if side < 0 else float(row["low"])
            stop = (
                extreme + max(2.0 * tick, 0.055 * atr)
                if side < 0
                else extreme - max(2.0 * tick, 0.055 * atr)
            )
            entry_hint = float(row["close"])
            targets, target_sources = base._structural_targets(
                row,
                side,
                entry_hint,
                "RANGE_SWEEP_REJECTION",
                level,
                tick,
            )
            if (
                targets
                and side * (entry_hint - stop) > tick
                and start_time <= timestamp <= end_time
            ):
                cluster = (
                    f"{period}:{symbol}:RANGE:{source}:{activation.value}:{side}"
                )
                episodes.append(
                    base.Episode(
                        period=period,
                        episode_id=f"{cluster}:SWEEP:{i}",
                        cluster_id=cluster,
                        symbol=symbol,
                        family="RANGE_SWEEP_REJECTION",
                        source=source,
                        side=side,
                        event_index=i,
                        event_time=timestamp,
                        level=level,
                        event_extreme=extreme,
                        stop=stop,
                        targets=targets,
                        target_sources=target_sources,
                        max_hold_minutes=180,
                        level_age_hours=float(level_age),
                        event_penetration_atr=penetration / atr,
                        event_range_atr=(
                            float(row["high"]) - float(row["low"])
                        ) / atr,
                    )
                )
        elif accepted:
            pending.append(
                {
                    "side": 1 if kind == "HIGH" else -1,
                    "level": level,
                    "source": source,
                    "activation": activation.value,
                    "level_age_hours": level_age,
                    "break_close": float(row["close"]),
                    "accepted": 1,
                    "expiry": i + 10,
                }
            )
    return episodes


def _install_corrections() -> None:
    base._prepare_symbol = _prepare_symbol
    base._range_episodes = _range_episodes
    base._label_action = _label_action


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v3"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install_corrections()
    args = parse_args()
    base.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
