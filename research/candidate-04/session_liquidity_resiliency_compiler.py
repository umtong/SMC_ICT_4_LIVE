#!/usr/bin/env python3
"""Compile completed-session liquidity resiliency reversals for NautilusTrader.

This is an independent SMC/ICT scenario, not a relaxation of V25. Every UTC
8-hour session leaves an objectively completed high and low. During the next
session each boundary is consumable once. A trade requires this sequence:

1. the first meaningful penetration of the previous completed session boundary;
2. aggressive 60-second flow and price displacement aligned with the attack;
3. a close back inside the boundary immediately or within three completed bars;
4. passive depth replenishment on the intended reversal side across all five
   public depth bands; and
5. for delayed reclaims, reversal-side executed flow and price displacement.

Displayed depth is used only as confirmation alongside executed flow and price,
never as a stand-alone predictor. The compiler creates timestamped intents only;
NautilusTrader owns targets, orders, fills, costs, positions, risk and NAV.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # warmup-aware loader


Intent = v22.Intent
SCENARIO = "COMPLETED_SESSION_LIQUIDITY_RESILIENCY_REVERSAL"
CONFIRMATION_BARS = 3
DEPTH_CHANGE_SECONDS = 60
DEPTH_BANDS = (1, 2, 3, 4, 5)
MAX_DEPTH_AGE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class SessionBoundary:
    session_start: pd.Timestamp
    high: float
    low: float


@dataclass(frozen=True, slots=True)
class SessionTake:
    shock_index: int
    pool_side: int
    trade_side: int
    level: float
    penetration_atr: float
    previous_session_start: pd.Timestamp


def session_start(timestamp: pd.Timestamp) -> pd.Timestamp:
    """Return the containing UTC 00:00/08:00/16:00 session start."""

    stamp = pd.Timestamp(timestamp)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.floor("8h")


def passive_replenishment_differential(
    row: pd.Series,
    trade_side: int,
) -> float:
    """Positive means displayed depth replenishes on the intended trade side."""

    if trade_side not in (-1, 1):
        return float("nan")
    differences: list[float] = []
    for band in DEPTH_BANDS:
        bid = float(row[f"bid_chg_{band}_{DEPTH_CHANGE_SECONDS}s"])
        ask = float(row[f"ask_chg_{band}_{DEPTH_CHANGE_SECONDS}s"])
        if not (math.isfinite(bid) and math.isfinite(ask)):
            continue
        differences.append((bid - ask) if trade_side > 0 else (ask - bid))
    return median(differences) if differences else float("nan")


def _session_boundaries(data: pd.DataFrame) -> dict[pd.Timestamp, SessionBoundary]:
    starts = pd.Series([session_start(value) for value in data.index], index=data.index)
    groups: list[tuple[pd.Timestamp, pd.DataFrame]] = list(data.groupby(starts, sort=True))
    result: dict[pd.Timestamp, SessionBoundary] = {}
    for index in range(1, len(groups)):
        current_start, _ = groups[index]
        previous_start, previous = groups[index - 1]
        result[current_start] = SessionBoundary(
            session_start=previous_start,
            high=float(previous["high"].max()),
            low=float(previous["low"].min()),
        )
    return result


def _inside_boundary(close: float, pool_side: int, level: float) -> bool:
    return close < level if pool_side > 0 else close > level


def detect_session_resiliency_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    boundaries = _session_boundaries(data)
    starts = [session_start(value) for value in data.index]
    consumed: set[tuple[pd.Timestamp, int]] = set()
    intents: list[Intent] = []
    counts = {
        "first_boundary_penetrations": 0,
        "ambiguous_both_boundaries": 0,
        "unaligned_attacks": 0,
        "no_resilient_reclaim": 0,
        "same_bar_absorption": 0,
        "delayed_reclaim": 0,
    }

    for index, timestamp in enumerate(data.index):
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        current_start = starts[index]
        boundary = boundaries.get(current_start)
        if boundary is None:
            continue
        row = data.iloc[index]
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue

        high_key = (current_start, 1)
        low_key = (current_start, -1)
        high_penetration = (float(row["high"]) - boundary.high) / atr
        low_penetration = (boundary.low - float(row["low"])) / atr
        high_taken = (
            high_key not in consumed
            and high_penetration >= float(config.sweep_min_atr)
        )
        low_taken = (
            low_key not in consumed
            and low_penetration >= float(config.sweep_min_atr)
        )
        if not (high_taken or low_taken):
            continue
        if high_taken:
            consumed.add(high_key)
        if low_taken:
            consumed.add(low_key)
        counts["first_boundary_penetrations"] += int(high_taken) + int(low_taken)
        if high_taken and low_taken:
            counts["ambiguous_both_boundaries"] += 1
            continue

        take = SessionTake(
            shock_index=index,
            pool_side=1 if high_taken else -1,
            trade_side=-1 if high_taken else 1,
            level=boundary.high if high_taken else boundary.low,
            penetration_atr=high_penetration if high_taken else low_penetration,
            previous_session_start=boundary.session_start,
        )
        attack_flow = take.pool_side * float(row["flow_60s"])
        attack_return = take.pool_side * float(row["ret_60s_bps"])
        if not (
            math.isfinite(attack_flow)
            and math.isfinite(attack_return)
            and attack_flow > 0.0
            and attack_return > 0.0
        ):
            counts["unaligned_attacks"] += 1
            continue

        confirmation_index: int | None = None
        confirmation_mode: str | None = None
        replenishment = float("nan")
        upper = min(index + CONFIRMATION_BARS, len(data) - 2)
        for candidate_index in range(index, upper + 1):
            candidate = data.iloc[candidate_index]
            close = float(candidate["close"])
            if not _inside_boundary(close, take.pool_side, take.level):
                continue
            age = float(candidate["depth_snapshot_age_seconds"])
            replenishment = passive_replenishment_differential(
                candidate,
                take.trade_side,
            )
            if not (
                math.isfinite(age)
                and age <= MAX_DEPTH_AGE_SECONDS
                and math.isfinite(replenishment)
                and replenishment > 0.0
            ):
                continue

            if candidate_index == index:
                confirmation_mode = "SAME_BAR_ATTACK_ABSORPTION"
            else:
                reversal_flow = take.trade_side * float(candidate["flow_60s"])
                reversal_return = take.trade_side * float(candidate["ret_60s_bps"])
                if not (
                    math.isfinite(reversal_flow)
                    and math.isfinite(reversal_return)
                    and reversal_flow > 0.0
                    and reversal_return > 0.0
                ):
                    continue
                confirmation_mode = "DELAYED_FLOW_REVERSAL_RECLAIM"
            confirmation_index = candidate_index
            break

        if confirmation_index is None or confirmation_mode is None:
            counts["no_resilient_reclaim"] += 1
            continue
        if data.index[confirmation_index] > evaluation_end:
            continue

        segment = data.iloc[index : confirmation_index + 1]
        extreme = float(
            segment["low"].min()
            if take.trade_side > 0
            else segment["high"].max()
        )
        stop_level = (
            extreme
            - take.trade_side * float(impact_parameters.stop_buffer_atr) * atr
        )
        details = {
            "liquidity_source": "PREVIOUS_COMPLETED_8H_SESSION_BOUNDARY",
            "previous_session_start": take.previous_session_start.isoformat(),
            "current_session_start": current_start.isoformat(),
            "pool_side": take.pool_side,
            "boundary_level": take.level,
            "penetration_atr": take.penetration_atr,
            "shock_index": index,
            "confirmation_index": confirmation_index,
            "confirmation_delay_bars": confirmation_index - index,
            "confirmation_mode": confirmation_mode,
            "attack_flow_60s": attack_flow,
            "attack_return_60s_bps": attack_return,
            "reversal_side_depth_replenishment_differential": replenishment,
            "depth_change_seconds": DEPTH_CHANGE_SECONDS,
            "depth_bands": list(DEPTH_BANDS),
            "maximum_depth_snapshot_age_seconds": MAX_DEPTH_AGE_SECONDS,
            "compiler": "candidate-04-session-resiliency-v1",
        }
        intents.append(
            Intent(
                scenario=SCENARIO,
                side=take.trade_side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(index, confirmation_index),
                details=details,
            ),
        )
        counts[
            "same_bar_absorption"
            if confirmation_index == index
            else "delayed_reclaim"
        ] += 1

    intents.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            continue
        seen.add(index)
        unique.append(intent)
    return unique, counts


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    intents, counts = detect_session_resiliency_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-session-liquidity-resiliency-v1",
        "compiler": "candidate-04-session-resiliency-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "liquidity": "previous completed UTC 8-hour session high or low",
            "consumption": "first meaningful penetration in the next session only",
            "attack": "executed flow and 60-second return aligned with penetration",
            "reclaim": "close back inside immediately or within three completed bars",
            "resiliency": (
                "positive reversal-side passive depth replenishment differential "
                "across all five public depth bands"
            ),
            "delayed_confirmation": (
                "reversal-side executed flow and return must also align"
            ),
            "risk_and_execution": "unchanged NautilusTrader path",
        },
        "constants": {
            "confirmation_bars": CONFIRMATION_BARS,
            "depth_change_seconds": DEPTH_CHANGE_SECONDS,
            "depth_bands": list(DEPTH_BANDS),
            "maximum_depth_snapshot_age_seconds": MAX_DEPTH_AGE_SECONDS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
