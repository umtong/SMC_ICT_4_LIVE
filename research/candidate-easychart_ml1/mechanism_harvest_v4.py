#!/usr/bin/env python3
"""Sequential-confirmation mechanism grammar.

V4 changes the source of edge rather than adding another score threshold. A
boundary interaction is not tradable merely because a candle resembles a
sweep or a breakout. It becomes an action only after the adverse auction fails,
order flow changes side, and price proves that it can progress without making a
new invalidating extreme. Breakout continuation likewise requires acceptance,
a held first pullback, and resumed flow. Systemic exhaustion and cross-asset
catch-up use the same transition logic.
"""
from __future__ import annotations

import argparse
from datetime import date
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import mechanism_harvest_v3 as v3

base = v3.base
FEATURE_COLUMNS = base.FEATURE_COLUMNS
SYMBOLS = base.SYMBOLS


def _valid(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


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
    rejection_states: list[dict[str, Any]] = []
    acceptance_states: list[dict[str, Any]] = []

    for i in range(360, len(frame) - 2):
        row = frame.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if timestamp > end_time:
            break
        atr = float(row.get("atr", np.nan))
        if not math.isfinite(atr) or atr <= tick:
            continue
        buffer = max(2.0 * tick, 0.04 * atr)

        next_rejections: list[dict[str, Any]] = []
        for state in rejection_states:
            if i > int(state["expiry"]):
                continue
            side = int(state["side"])
            level = float(state["level"])
            extreme = float(state["extreme"])
            invalidated = (
                float(row["low"]) < extreme - 0.08 * atr
                if side > 0
                else float(row["high"]) > extreme + 0.08 * atr
            )
            if invalidated:
                continue
            midpoint = 0.5 * (float(state["event_open"]) + float(state["event_close"]))
            inside = (
                float(row["close"]) > level + 0.015 * atr
                if side > 0
                else float(row["close"]) < level - 0.015 * atr
            )
            beyond_midpoint = (
                float(row["close"]) > midpoint
                if side > 0
                else float(row["close"]) < midpoint
            )
            aligned_return = side * float(row.get("ret_3", np.nan))
            aligned_flow = side * float(row.get("flow_3", np.nan))
            common = side * float(row.get("common_3", np.nan))
            confirms = (
                inside
                and beyond_midpoint
                and _valid(aligned_return)
                and aligned_return > 0.02
                and _valid(aligned_flow)
                and aligned_flow > -0.01
                and (_valid(common) and common > -0.80)
            )
            if confirms:
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
                    "RANGE_SWEEP_REJECTION",
                    level,
                    tick,
                )
                if (
                    targets
                    and side * (entry_hint - stop) > tick
                    and start_time <= timestamp <= end_time
                ):
                    cluster = str(state["cluster"])
                    episodes.append(
                        base.Episode(
                            period=period,
                            episode_id=f"{cluster}:CONFIRMED_REJECTION:{i}",
                            cluster_id=cluster,
                            symbol=symbol,
                            family="RANGE_SWEEP_REJECTION",
                            source=str(state["source"]),
                            side=side,
                            event_index=i,
                            event_time=timestamp,
                            level=level,
                            event_extreme=extreme,
                            stop=stop,
                            targets=targets,
                            target_sources=target_sources,
                            max_hold_minutes=180,
                            level_age_hours=float(state["level_age_hours"]),
                            event_penetration_atr=float(state["penetration_atr"]),
                            event_range_atr=float(state["event_range_atr"]),
                        )
                    )
                continue
            next_rejections.append(state)
        rejection_states = next_rejections

        next_acceptances: list[dict[str, Any]] = []
        for state in acceptance_states:
            if i > int(state["expiry"]):
                continue
            side = int(state["side"])
            level = float(state["level"])
            closes_through = (
                float(row["close"]) < level - 0.055 * atr
                if side > 0
                else float(row["close"]) > level + 0.055 * atr
            )
            if closes_through:
                continue
            phase = str(state["phase"])
            outside = (
                float(row["close"]) > level + 0.02 * atr
                if side > 0
                else float(row["close"]) < level - 0.02 * atr
            )
            if phase == "ACCEPTING":
                if outside:
                    state["outside_closes"] = int(state["outside_closes"]) + 1
                if int(state["outside_closes"]) >= 2:
                    state["phase"] = "WAIT_PULLBACK"
                next_acceptances.append(state)
                continue
            if phase == "WAIT_PULLBACK":
                touched = (
                    float(row["low"]) <= level + 0.20 * atr
                    if side > 0
                    else float(row["high"]) >= level - 0.20 * atr
                )
                if touched:
                    state["phase"] = "WAIT_RESUME"
                    state["pullback_index"] = i
                    state["pullback_extreme"] = (
                        float(row["low"]) if side > 0 else float(row["high"])
                    )
                    state["expiry"] = i + 5
                next_acceptances.append(state)
                continue

            pullback_extreme = float(state["pullback_extreme"])
            if side > 0:
                state["pullback_extreme"] = min(pullback_extreme, float(row["low"]))
            else:
                state["pullback_extreme"] = max(pullback_extreme, float(row["high"]))
            away = (
                float(row["close"]) > level + 0.09 * atr
                if side > 0
                else float(row["close"]) < level - 0.09 * atr
            )
            aligned_return = side * float(row.get("ret_3", np.nan))
            aligned_flow = side * float(row.get("flow_3", np.nan))
            oi_change = float(row.get("oi_change_5", np.nan))
            common = side * float(row.get("common_3", np.nan))
            resumes = (
                away
                and _valid(aligned_return)
                and aligned_return > 0.02
                and _valid(aligned_flow)
                and aligned_flow > 0.0
                and (not _valid(oi_change) or oi_change > -0.012)
                and (_valid(common) and common > -0.45)
            )
            if resumes:
                extreme = float(state["pullback_extreme"])
                stop = (
                    min(extreme, level) - max(2.0 * tick, 0.05 * atr)
                    if side > 0
                    else max(extreme, level) + max(2.0 * tick, 0.05 * atr)
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
                    cluster = str(state["cluster"])
                    episodes.append(
                        base.Episode(
                            period=period,
                            episode_id=f"{cluster}:ACCEPT_PULLBACK_RESUME:{i}",
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
                            event_penetration_atr=float(state["penetration_atr"]),
                            event_range_atr=float(state["event_range_atr"]),
                        )
                    )
                continue
            next_acceptances.append(state)
        acceptance_states = next_acceptances

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
        touched_levels: list[
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
                touched_levels.append((abs(float(row["open"]) - level), spec))
        if not touched_levels:
            continue
        _, (source, kind, level, activation) = min(
            touched_levels,
            key=lambda item: item[0],
        )
        key = (source, int(round(level / tick)), str(activation.value))
        consumed.add(key)
        inward_side = -1 if kind == "HIGH" else 1
        breakout_side = -inward_side
        penetration = (
            float(row["high"]) - level
            if kind == "HIGH"
            else level - float(row["low"])
        )
        event_range_atr = (float(row["high"]) - float(row["low"])) / atr
        level_age = max(0.0, float((timestamp - activation) / pd.Timedelta(hours=1)))
        cluster = f"{period}:{symbol}:RANGE:{source}:{activation.value}:{kind}"

        reclaimed = (
            float(row["close"]) < level - 0.01 * atr
            if kind == "HIGH"
            else float(row["close"]) > level + 0.01 * atr
        )
        event_adverse_flow = inward_side * float(row.get("flow_3", np.nan))
        if (
            reclaimed
            and penetration >= max(tick, 0.025 * atr)
            and _valid(event_adverse_flow)
            and event_adverse_flow < 0.12
        ):
            rejection_states.append(
                {
                    "cluster": cluster,
                    "side": inward_side,
                    "level": level,
                    "source": source,
                    "activation": activation.value,
                    "level_age_hours": level_age,
                    "extreme": float(row["high"]) if inward_side < 0 else float(row["low"]),
                    "event_open": float(row["open"]),
                    "event_close": float(row["close"]),
                    "penetration_atr": penetration / atr,
                    "event_range_atr": event_range_atr,
                    "expiry": i + 8,
                }
            )
            continue

        beyond = (
            float(row["close"]) > level + buffer
            if breakout_side > 0
            else float(row["close"]) < level - buffer
        )
        aligned_flow = breakout_side * float(row.get("flow_3", np.nan))
        aligned_return = breakout_side * float(row.get("ret_3", np.nan))
        common = breakout_side * float(row.get("common_3", np.nan))
        if (
            beyond
            and _valid(aligned_flow)
            and aligned_flow > 0.015
            and _valid(aligned_return)
            and aligned_return > 0.05
            and _valid(common)
            and common > -0.25
        ):
            acceptance_states.append(
                {
                    "cluster": cluster,
                    "side": breakout_side,
                    "level": level,
                    "source": source,
                    "activation": activation.value,
                    "level_age_hours": level_age,
                    "penetration_atr": penetration / atr,
                    "event_range_atr": event_range_atr,
                    "phase": "ACCEPTING",
                    "outside_closes": 1,
                    "expiry": i + 14,
                }
            )
    return episodes


def _systemic_episodes(
    period: str,
    frames: dict[str, pd.DataFrame],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> list[base.Episode]:
    episodes: list[base.Episode] = []
    reference = frames["BTCUSDT"]
    last_cluster: pd.Timestamp | None = None
    for i in range(720, len(reference) - 15):
        row = reference.iloc[i]
        timestamp = pd.Timestamp(row["time"])
        if not (start_time <= timestamp <= end_time):
            continue
        impulse_z = float(row.get("common_impulse_z", np.nan))
        breadth = float(row.get("breadth_3", np.nan))
        if not _valid(impulse_z) or abs(impulse_z) < 2.8 or not _valid(breadth):
            continue
        impulse_side = 1 if impulse_z > 0.0 else -1
        if impulse_side * breadth < 0.45:
            continue
        if last_cluster is not None and timestamp - last_cluster < pd.Timedelta(minutes=120):
            continue

        oi_contracting = 0
        premium_extended = 0
        flow_impulsive = 0
        active = 0
        for frame in frames.values():
            local = frame.iloc[i]
            oi_change = float(local.get("oi_change_5", np.nan))
            premium = impulse_side * float(local.get("premium_z", np.nan))
            flow = impulse_side * float(local.get("flow_3", np.nan))
            activity = float(local.get("activity_z", np.nan))
            oi_contracting += int(_valid(oi_change) and oi_change < 0.0)
            premium_extended += int(_valid(premium) and premium > 0.75)
            flow_impulsive += int(_valid(flow) and flow > 0.06)
            active += int(_valid(activity) and activity > 0.5)
        if oi_contracting < 2 or flow_impulsive < 3 or active < 2:
            continue
        # Premium is useful when present, but older archive gaps must not turn
        # the economic mechanism into a calendar filter.
        available_premium = sum(
            int(_valid(impulse_side * float(frame.iloc[i].get("premium_z", np.nan))))
            for frame in frames.values()
        )
        if available_premium >= 2 and premium_extended < 1:
            continue

        confirmation_index: int | None = None
        for j in range(i + 1, min(i + 13, len(reference) - 2)):
            reversal_side = -impulse_side
            flow_reversed = 0
            price_reversed = 0
            impact_failed = 0
            for frame in frames.values():
                local = frame.iloc[j]
                aligned_flow = reversal_side * float(local.get("flow_3", np.nan))
                aligned_move = reversal_side * float(local.get("ret_3", np.nan))
                flow_reversed += int(_valid(aligned_flow) and aligned_flow > 0.0)
                price_reversed += int(_valid(aligned_move) and aligned_move > 0.0)
                old_flow = impulse_side * float(local.get("flow_3", np.nan))
                old_move = impulse_side * float(local.get("ret_3", np.nan))
                impact_failed += int(
                    _valid(old_flow)
                    and _valid(old_move)
                    and old_flow > 0.02
                    and old_move < 0.08
                )
            common_reversal = reversal_side * float(reference.iloc[j].get("common_3", np.nan))
            if (
                flow_reversed >= 2
                and price_reversed >= 3
                and impact_failed >= 1
                and _valid(common_reversal)
                and common_reversal > 0.0
            ):
                confirmation_index = j
                break
        if confirmation_index is None:
            continue

        side = -impulse_side
        cluster = f"{period}:SYSTEMIC_EXHAUSTION:{timestamp.value}:{side}"
        for symbol, frame in frames.items():
            event = frame.iloc[confirmation_index]
            aligned_flow = side * float(event.get("flow_3", np.nan))
            aligned_return = side * float(event.get("ret_3", np.nan))
            if not (_valid(aligned_flow) and aligned_flow > 0.0 and _valid(aligned_return) and aligned_return > 0.0):
                continue
            atr = float(event.get("atr", np.nan))
            tick = base.TICKS[symbol]
            if not math.isfinite(atr) or atr <= tick:
                continue
            local_path = frame.iloc[max(0, i - 2):confirmation_index + 1]
            extreme = (
                float(local_path["low"].min())
                if side > 0
                else float(local_path["high"].max())
            )
            stop = (
                extreme - max(2.0 * tick, 0.06 * atr)
                if side > 0
                else extreme + max(2.0 * tick, 0.06 * atr)
            )
            entry_hint = float(event["close"])
            targets, target_sources = base._structural_targets(
                event,
                side,
                entry_hint,
                "SYSTEMIC_FORCED_FLOW_EXHAUSTION",
                entry_hint,
                tick,
            )
            if not targets or side * (entry_hint - stop) <= tick:
                continue
            episodes.append(
                base.Episode(
                    period=period,
                    episode_id=f"{cluster}:{symbol}:{confirmation_index}",
                    cluster_id=cluster,
                    symbol=symbol,
                    family="SYSTEMIC_FORCED_FLOW_EXHAUSTION",
                    source="SYSTEMIC",
                    side=side,
                    event_index=confirmation_index,
                    event_time=pd.Timestamp(event["time"]),
                    level=entry_hint,
                    event_extreme=extreme,
                    stop=stop,
                    targets=targets,
                    target_sources=target_sources,
                    max_hold_minutes=180,
                    level_age_hours=0.0,
                    event_penetration_atr=abs(impulse_z),
                    event_range_atr=(
                        float(local_path["high"].max()) - float(local_path["low"].min())
                    ) / atr,
                )
            )
        last_cluster = timestamp
    return episodes


def _residual_episodes(
    period: str,
    symbol: str,
    frame: pd.DataFrame,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
) -> list[base.Episode]:
    episodes: list[base.Episode] = []
    last_event: pd.Timestamp | None = None
    threshold = (
        frame["residual_z"]
        .abs()
        .rolling(2160, min_periods=720)
        .quantile(0.985)
        .shift(1)
    )
    tick = base.TICKS[symbol]
    for i in range(721, len(frame) - 2):
        timestamp = pd.Timestamp(frame.iloc[i]["time"])
        if not (start_time <= timestamp <= end_time):
            continue
        if last_event is not None and timestamp - last_event < pd.Timedelta(minutes=60):
            continue
        previous_z = float(frame.iloc[i - 1].get("residual_z", np.nan))
        current_z = float(frame.iloc[i].get("residual_z", np.nan))
        cutoff = float(threshold.iloc[i])
        if not all(_valid(value) for value in (previous_z, current_z, cutoff)):
            continue
        if (
            abs(previous_z) < max(1.5, cutoff)
            or abs(current_z) >= abs(previous_z)
            or np.sign(current_z) != np.sign(previous_z)
        ):
            continue
        side = -1 if previous_z > 0.0 else 1
        row = frame.iloc[i]
        common = side * float(row.get("common_15", np.nan))
        breadth = side * float(row.get("breadth_15", np.nan))
        flow = side * float(row.get("flow_3", np.nan))
        flow_change = side * (
            float(row.get("flow_3", np.nan))
            - float(frame.iloc[i - 3].get("flow_3", np.nan))
        )
        local_return = side * float(row.get("ret_3", np.nan))
        oi_change = float(row.get("oi_change_5", np.nan))
        # This is lag catch-up in the direction of the still-live common
        # auction, not generic mean reversion against market beta.
        confirms = (
            _valid(common)
            and common > 0.18
            and _valid(breadth)
            and breadth > 0.05
            and _valid(flow)
            and flow > -0.01
            and _valid(flow_change)
            and flow_change > 0.0
            and _valid(local_return)
            and local_return > 0.0
            and (not _valid(oi_change) or oi_change > -0.015)
        )
        if not confirms:
            continue
        atr = float(row.get("atr", np.nan))
        if not math.isfinite(atr) or atr <= tick:
            continue
        path = frame.iloc[max(0, i - 15):i + 1]
        extreme = float(path["low"].min()) if side > 0 else float(path["high"].max())
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
            "COMMON_RESIDUAL_REJOIN",
            float(row["fair_60"]),
            tick,
        )
        if not targets or side * (entry_hint - stop) <= tick:
            continue
        cluster = f"{period}:{symbol}:COMMON_CATCHUP:{timestamp.value}:{side}"
        episodes.append(
            base.Episode(
                period=period,
                episode_id=f"{cluster}:{i}",
                cluster_id=cluster,
                symbol=symbol,
                family="COMMON_RESIDUAL_REJOIN",
                source="COMMON_FACTOR",
                side=side,
                event_index=i,
                event_time=timestamp,
                level=float(row["fair_60"]),
                event_extreme=extreme,
                stop=stop,
                targets=targets,
                target_sources=target_sources,
                max_hold_minutes=150,
                level_age_hours=0.0,
                event_penetration_atr=abs(previous_z),
                event_range_atr=(
                    float(path["high"].max()) - float(path["low"].min())
                ) / atr,
            )
        )
        last_event = timestamp
    return episodes


def _install() -> None:
    v3._install_corrections()
    base._range_episodes = _range_episodes
    base._systemic_episodes = _systemic_episodes
    base._residual_episodes = _residual_episodes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--cache", type=Path, default=Path(".cache/mechanism-v4"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    _install()
    args = parse_args()
    base.harvest(args.period, args.start, args.end, args.cache, args.output)


if __name__ == "__main__":
    main()
