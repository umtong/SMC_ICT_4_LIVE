#!/usr/bin/env python3
"""Compile first liquidity sweeps after an objectively balanced 8-hour session.

Prior completed-session reversal research failed because every session high/low
was treated alike and displayed-depth recovery was allowed to confirm the trade.
This independent scenario first proves that the previous 8-hour auction formed
accepted value rather than directional price discovery.

A completed previous session is BALANCED only when:

1. its net/path auction efficiency is at or below the median of prior completed
   sessions available before it; and
2. its closing price remains within the session's volume-weighted mean absolute
   price deviation around session VWAP.

The next session may consume each previous high/low once. A trade then requires:

1. first meaningful penetration of a balanced previous-session boundary with
   attack-side completed flow and return;
2. a later completed close back inside the exact boundary within three bars;
3. reversal-side completed flow, return and five-minute basis change; and
4. a non-zero OI change from immediately before the attack through reclaim.

OI routes two independent economic mechanisms: contraction identifies forced
position liquidation at accepted value; expansion identifies failed new
breakout inventory. No displayed-depth signal is used. Stops lie beyond the
complete attack/reclaim excursion. The compiler emits intents only;
NautilusTrader owns targets, actual fills, fees, positions, risk, PnL, margin,
liquidation and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # warmup-aware loader
import session_liquidity_resiliency_compiler as session_base


Intent = v22.Intent
CONFIRMATION_BARS = 3
EFFICIENCY_HISTORY_SESSIONS = 30
EFFICIENCY_MIN_SESSIONS = 6
LIQUIDATION_SCENARIO = "BALANCED_SESSION_LIQUIDATION_SWEEP_REVERSAL"
FAILED_INVENTORY_SCENARIO = "BALANCED_SESSION_FAILED_INVENTORY_BREAKOUT_REVERSAL"


@dataclass(frozen=True, slots=True)
class BalancedSession:
    session_start: pd.Timestamp
    high: float
    low: float
    close: float
    vwap: float
    vwap_mean_absolute_deviation: float
    efficiency: float
    past_efficiency_median: float
    balanced: bool


def session_auction_efficiency(frame: pd.DataFrame) -> float:
    if frame.empty:
        return float("nan")
    open_price = float(frame["open"].iloc[0])
    closes = frame["close"].astype(float)
    if not math.isfinite(open_price):
        return float("nan")
    path = abs(float(closes.iloc[0]) - open_price)
    path += float(closes.diff().abs().iloc[1:].sum())
    if not math.isfinite(path) or path <= 0.0:
        return 0.0
    return abs(float(closes.iloc[-1]) - open_price) / path


def session_vwap_state(frame: pd.DataFrame) -> tuple[float, float]:
    typical = (
        frame["high"].astype(float)
        + frame["low"].astype(float)
        + frame["close"].astype(float)
    ) / 3.0
    volume = frame["volume"].astype(float).clip(lower=0.0)
    total = float(volume.sum())
    if not math.isfinite(total) or total <= 0.0:
        return float("nan"), float("nan")
    vwap = float((typical * volume).sum() / total)
    mad = float((typical.sub(vwap).abs() * volume).sum() / total)
    return vwap, mad


def close_is_accepted_value(close: float, vwap: float, mad: float) -> bool:
    values = (close, vwap, mad)
    if not all(math.isfinite(value) for value in values) or mad < 0.0:
        return False
    return abs(close - vwap) <= mad


def _completed_session_contexts(
    data: pd.DataFrame,
) -> dict[pd.Timestamp, BalancedSession]:
    starts = pd.Series(
        [session_base.session_start(value) for value in data.index],
        index=data.index,
    )
    groups = list(data.groupby(starts, sort=True))
    states: list[BalancedSession] = []
    past_efficiencies: list[float] = []
    for start, frame in groups:
        # Only full 8-hour sessions can define the next session's context.
        if len(frame) != 480:
            continue
        efficiency = session_auction_efficiency(frame)
        history = [
            value for value in past_efficiencies[-EFFICIENCY_HISTORY_SESSIONS:]
            if math.isfinite(value)
        ]
        cutoff = (
            float(median(history))
            if len(history) >= EFFICIENCY_MIN_SESSIONS
            else float("nan")
        )
        vwap, mad = session_vwap_state(frame)
        close = float(frame["close"].iloc[-1])
        balanced = (
            math.isfinite(efficiency)
            and math.isfinite(cutoff)
            and efficiency <= cutoff
            and close_is_accepted_value(close, vwap, mad)
        )
        states.append(
            BalancedSession(
                session_start=start,
                high=float(frame["high"].max()),
                low=float(frame["low"].min()),
                close=close,
                vwap=vwap,
                vwap_mean_absolute_deviation=mad,
                efficiency=efficiency,
                past_efficiency_median=cutoff,
                balanced=balanced,
            )
        )
        if math.isfinite(efficiency):
            past_efficiencies.append(efficiency)

    contexts: dict[pd.Timestamp, BalancedSession] = {}
    for state in states:
        contexts[state.session_start + pd.Timedelta(hours=8)] = state
    return contexts


def inventory_route(interval_oi_change: float) -> str | None:
    if not math.isfinite(interval_oi_change) or interval_oi_change == 0.0:
        return None
    if interval_oi_change < 0.0:
        return LIQUIDATION_SCENARIO
    return FAILED_INVENTORY_SCENARIO


def detect_balanced_session_reversals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    contexts = _completed_session_contexts(data)
    starts = [session_base.session_start(value) for value in data.index]
    open_interest = data["metric_sum_open_interest"].astype(float)
    consumed: set[tuple[pd.Timestamp, int]] = set()
    intents: list[Intent] = []
    counts = {
        "completed_session_contexts": len(contexts),
        "unbalanced_previous_sessions": 0,
        "first_balanced_boundary_takes": 0,
        "ambiguous_both_boundaries": 0,
        "attack_not_aligned": 0,
        "no_delayed_reclaim": 0,
        "reclaim_not_aligned": 0,
        "no_state_interval_oi_change": 0,
        "liquidation_reversals": 0,
        "failed_inventory_reversals": 0,
        "duplicate_signal_bars": 0,
    }

    for index, timestamp in enumerate(data.index):
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        current_start = starts[index]
        previous = contexts.get(current_start)
        if previous is None:
            continue
        if not previous.balanced:
            # Count once per current session rather than once per bar.
            if index == 0 or starts[index - 1] != current_start:
                counts["unbalanced_previous_sessions"] += 1
            continue

        row = data.iloc[index]
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        high_key = (current_start, 1)
        low_key = (current_start, -1)
        high_penetration = (float(row["high"]) - previous.high) / atr
        low_penetration = (previous.low - float(row["low"])) / atr
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
        counts["first_balanced_boundary_takes"] += int(high_taken) + int(low_taken)
        if high_taken and low_taken:
            counts["ambiguous_both_boundaries"] += 1
            continue

        pool_side = 1 if high_taken else -1
        trade_side = -pool_side
        level = previous.high if high_taken else previous.low
        penetration = high_penetration if high_taken else low_penetration
        attack_state = geometry_state = (
            pool_side * float(row["flow_60s"]),
            pool_side * float(row["ret_60s_bps"]),
        )
        if not all(math.isfinite(value) and value > 0.0 for value in attack_state):
            counts["attack_not_aligned"] += 1
            continue

        confirmation_index: int | None = None
        reversal_flow = float("nan")
        reversal_return = float("nan")
        reversal_basis = float("nan")
        upper = min(index + CONFIRMATION_BARS, len(data) - 2)
        for candidate_index in range(index + 1, upper + 1):
            candidate = data.iloc[candidate_index]
            if not session_base._inside_boundary(
                float(candidate["close"]),
                pool_side,
                level,
            ):
                continue
            flow = trade_side * float(candidate["flow_60s"])
            ret = trade_side * float(candidate["ret_60s_bps"])
            basis = trade_side * float(candidate["basis_change_5m"])
            if not all(
                math.isfinite(value) and value > 0.0
                for value in (flow, ret, basis)
            ):
                counts["reclaim_not_aligned"] += 1
                break
            confirmation_index = candidate_index
            reversal_flow = flow
            reversal_return = ret
            reversal_basis = basis
            break

        if confirmation_index is None:
            counts["no_delayed_reclaim"] += 1
            continue
        if data.index[confirmation_index] > evaluation_end:
            continue

        oi_start_index = max(index - 1, 0)
        oi_start = float(open_interest.iloc[oi_start_index])
        oi_end = float(open_interest.iloc[confirmation_index])
        if not all(math.isfinite(value) and value > 0.0 for value in (oi_start, oi_end)):
            interval_oi = float("nan")
        else:
            interval_oi = oi_end / oi_start - 1.0
        scenario = inventory_route(interval_oi)
        if scenario is None:
            counts["no_state_interval_oi_change"] += 1
            continue

        segment = data.iloc[index : confirmation_index + 1]
        extreme = float(
            segment["low"].min()
            if trade_side > 0
            else segment["high"].max()
        )
        stop_level = (
            extreme
            - trade_side * float(impact_parameters.stop_buffer_atr) * atr
        )
        details = {
            "liquidity_source": "BALANCED_PREVIOUS_COMPLETED_8H_SESSION",
            "previous_session_start": previous.session_start.isoformat(),
            "current_session_start": current_start.isoformat(),
            "previous_session_high": previous.high,
            "previous_session_low": previous.low,
            "previous_session_close": previous.close,
            "previous_session_vwap": previous.vwap,
            "previous_session_vwap_mad": (
                previous.vwap_mean_absolute_deviation
            ),
            "previous_session_efficiency": previous.efficiency,
            "past_only_session_efficiency_median": (
                previous.past_efficiency_median
            ),
            "previous_session_balanced": previous.balanced,
            "pool_side": pool_side,
            "boundary_level": level,
            "penetration_atr": penetration,
            "attack_index": index,
            "attack_directional_flow_60s": attack_state[0],
            "attack_directional_return_60s_bps": attack_state[1],
            "confirmation_index": confirmation_index,
            "confirmation_delay_bars": confirmation_index - index,
            "reversal_flow_60s": reversal_flow,
            "reversal_return_60s_bps": reversal_return,
            "reversal_basis_change_5m_bps": reversal_basis,
            "attack_to_reclaim_open_interest_change": interval_oi,
            "inventory_route": (
                "LIQUIDATION"
                if scenario == LIQUIDATION_SCENARIO
                else "FAILED_NEW_BREAKOUT_INVENTORY"
            ),
            "compiler": "candidate-04-balanced-session-reversal-v1",
        }
        intents.append(
            Intent(
                scenario=scenario,
                side=trade_side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(index, confirmation_index),
                details=details,
            )
        )
        if scenario == LIQUIDATION_SCENARIO:
            counts["liquidation_reversals"] += 1
        else:
            counts["failed_inventory_reversals"] += 1

    intents.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            counts["duplicate_signal_bars"] += 1
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
    intents, counts = detect_balanced_session_reversals(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-balanced-session-liquidity-reversal-v1",
        "compiler": "candidate-04-balanced-session-reversal-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "parent_value": (
                "previous full 8-hour session efficiency <= shifted prior "
                "session median and close within volume-weighted VWAP MAD"
            ),
            "liquidity": "first next-session penetration of previous high or low",
            "attack": "penetration-side completed flow and return",
            "reclaim": (
                "later exact-boundary reclaim within three completed bars with "
                "reversal flow, return and five-minute basis change"
            ),
            "inventory_routes": {
                "liquidation": "OI contracts from pre-attack through reclaim",
                "failed_inventory": "OI expands from pre-attack through reclaim",
            },
            "excluded": "displayed-depth confirmation",
            "invalidation": "complete attack/reclaim excursion plus ATR buffer",
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
        "constants": {
            "confirmation_bars": CONFIRMATION_BARS,
            "efficiency_history_sessions": EFFICIENCY_HISTORY_SESSIONS,
            "efficiency_min_sessions": EFFICIENCY_MIN_SESSIONS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
