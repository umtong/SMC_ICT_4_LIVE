#!/usr/bin/env python3
"""Compile continuation after a directional 8-hour session reclaims its VWAP.

This is the complementary state to balanced-session boundary reversal. A full
previous UTC 8-hour session is directional only when its net/path efficiency is
above the shifted median of prior completed sessions and its close accepts price
more than one realized volume-weighted mean absolute deviation beyond session
VWAP in the same direction.

During the next session the previous VWAP can be consumed once. A trade requires:

1. price first trades on the parent side of VWAP, preserving the inherited state;
2. the first meaningful pullback penetrates VWAP with counter-parent completed
   flow and return;
3. within three later completed bars, price closes back through VWAP in the
   parent direction with aligned flow, return and five-minute basis change; and
4. OI changes from immediately before pullback through reclaim.

OI contraction identifies liquidation of late parent-side positions before
resumption. OI expansion identifies fresh countertrend inventory trapped by the
VWAP reclaim. The routes are separate scenarios. Stops lie beyond the complete
pullback/reclaim excursion. This compiler emits intents only; NautilusTrader
owns targets, actual fills, fees, positions, risk, PnL, margin, liquidation and
NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import session_liquidity_resiliency_compiler as session_base
import balanced_session_liquidity_reversal_compiler as balanced


Intent = v22.Intent
CONFIRMATION_BARS = 3
EFFICIENCY_HISTORY_SESSIONS = 30
EFFICIENCY_MIN_SESSIONS = 6
LIQUIDATION_SCENARIO = "DIRECTIONAL_SESSION_VWAP_LIQUIDATION_RECLAIM"
TRAPPED_COUNTER_SCENARIO = (
    "DIRECTIONAL_SESSION_VWAP_TRAPPED_COUNTERINVENTORY_RECLAIM"
)


@dataclass(frozen=True, slots=True)
class DirectionalSession:
    session_start: pd.Timestamp
    high: float
    low: float
    open: float
    close: float
    vwap: float
    vwap_mad: float
    efficiency: float
    past_efficiency_median: float
    side: int
    directional: bool


def directional_value_acceptance(
    close: float,
    vwap: float,
    mad: float,
    side: int,
) -> bool:
    values = (close, vwap, mad)
    if side not in (-1, 1) or not all(math.isfinite(value) for value in values):
        return False
    if mad < 0.0:
        return False
    return side * (close - vwap) > mad


def _directional_contexts(
    data: pd.DataFrame,
) -> dict[pd.Timestamp, DirectionalSession]:
    starts = pd.Series(
        [session_base.session_start(value) for value in data.index],
        index=data.index,
    )
    groups = list(data.groupby(starts, sort=True))
    states: list[DirectionalSession] = []
    past_efficiencies: list[float] = []
    for start, frame in groups:
        if len(frame) != 480:
            continue
        efficiency = balanced.session_auction_efficiency(frame)
        history = [
            value for value in past_efficiencies[-EFFICIENCY_HISTORY_SESSIONS:]
            if math.isfinite(value)
        ]
        cutoff = (
            float(median(history))
            if len(history) >= EFFICIENCY_MIN_SESSIONS
            else float("nan")
        )
        open_price = float(frame["open"].iloc[0])
        close = float(frame["close"].iloc[-1])
        raw = close - open_price
        side = 1 if raw > 0.0 else -1 if raw < 0.0 else 0
        vwap, mad = balanced.session_vwap_state(frame)
        directional = (
            side in (-1, 1)
            and math.isfinite(efficiency)
            and math.isfinite(cutoff)
            and efficiency > cutoff
            and directional_value_acceptance(close, vwap, mad, side)
        )
        states.append(
            DirectionalSession(
                session_start=start,
                high=float(frame["high"].max()),
                low=float(frame["low"].min()),
                open=open_price,
                close=close,
                vwap=vwap,
                vwap_mad=mad,
                efficiency=efficiency,
                past_efficiency_median=cutoff,
                side=side,
                directional=directional,
            )
        )
        if math.isfinite(efficiency):
            past_efficiencies.append(efficiency)
    return {
        state.session_start + pd.Timedelta(hours=8): state
        for state in states
    }


def inventory_route(interval_oi_change: float) -> str | None:
    if not math.isfinite(interval_oi_change) or interval_oi_change == 0.0:
        return None
    if interval_oi_change < 0.0:
        return LIQUIDATION_SCENARIO
    return TRAPPED_COUNTER_SCENARIO


def detect_directional_session_vwap_reclaims(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    contexts = _directional_contexts(data)
    starts = [session_base.session_start(value) for value in data.index]
    open_interest = data["metric_sum_open_interest"].astype(float)
    observed_parent_side: dict[pd.Timestamp, bool] = {}
    consumed: set[pd.Timestamp] = set()
    intents: list[Intent] = []
    counts = {
        "completed_session_contexts": len(contexts),
        "nondirectional_previous_sessions": 0,
        "parent_side_not_observed_before_retest": 0,
        "first_vwap_retests": 0,
        "pullback_not_counter_aligned": 0,
        "no_parent_reclaim": 0,
        "reclaim_not_aligned": 0,
        "no_state_interval_oi_change": 0,
        "liquidation_reclaims": 0,
        "trapped_counterinventory_reclaims": 0,
        "duplicate_signal_bars": 0,
    }

    for index, timestamp in enumerate(data.index):
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        current_start = starts[index]
        parent = contexts.get(current_start)
        if parent is None:
            continue
        if not parent.directional:
            if index == 0 or starts[index - 1] != current_start:
                counts["nondirectional_previous_sessions"] += 1
            continue
        if current_start in consumed:
            continue

        row = data.iloc[index]
        close = float(row["close"])
        if parent.side * (close - parent.vwap) > 0.0:
            observed_parent_side[current_start] = True

        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        if parent.side > 0:
            penetration = (parent.vwap - float(row["low"])) / atr
        else:
            penetration = (float(row["high"]) - parent.vwap) / atr
        if penetration < float(config.sweep_min_atr):
            continue

        consumed.add(current_start)
        counts["first_vwap_retests"] += 1
        if not observed_parent_side.get(current_start, False):
            counts["parent_side_not_observed_before_retest"] += 1
            continue

        pullback_side = -parent.side
        pullback_flow = pullback_side * float(row["flow_60s"])
        pullback_return = pullback_side * float(row["ret_60s_bps"])
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (pullback_flow, pullback_return)
        ):
            counts["pullback_not_counter_aligned"] += 1
            continue

        confirmation_index: int | None = None
        reclaim_flow = float("nan")
        reclaim_return = float("nan")
        reclaim_basis = float("nan")
        upper = min(index + CONFIRMATION_BARS, len(data) - 2)
        for candidate_index in range(index + 1, upper + 1):
            candidate = data.iloc[candidate_index]
            if parent.side * (float(candidate["close"]) - parent.vwap) <= 0.0:
                continue
            flow = parent.side * float(candidate["flow_60s"])
            ret = parent.side * float(candidate["ret_60s_bps"])
            basis = parent.side * float(candidate["basis_change_5m"])
            if not all(
                math.isfinite(value) and value > 0.0
                for value in (flow, ret, basis)
            ):
                counts["reclaim_not_aligned"] += 1
                break
            confirmation_index = candidate_index
            reclaim_flow = flow
            reclaim_return = ret
            reclaim_basis = basis
            break

        if confirmation_index is None:
            counts["no_parent_reclaim"] += 1
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
            if parent.side > 0
            else segment["high"].max()
        )
        stop_level = (
            extreme
            - parent.side * float(impact_parameters.stop_buffer_atr) * atr
        )
        details = {
            "parent_session_start": parent.session_start.isoformat(),
            "current_session_start": current_start.isoformat(),
            "parent_session_side": parent.side,
            "parent_session_open": parent.open,
            "parent_session_close": parent.close,
            "parent_session_high": parent.high,
            "parent_session_low": parent.low,
            "parent_session_vwap": parent.vwap,
            "parent_session_vwap_mad": parent.vwap_mad,
            "parent_session_efficiency": parent.efficiency,
            "past_only_session_efficiency_median": (
                parent.past_efficiency_median
            ),
            "parent_session_directional": parent.directional,
            "vwap_retest_index": index,
            "vwap_penetration_atr": penetration,
            "pullback_flow_60s": pullback_flow,
            "pullback_return_60s_bps": pullback_return,
            "confirmation_index": confirmation_index,
            "reclaim_delay_bars": confirmation_index - index,
            "reclaim_flow_60s": reclaim_flow,
            "reclaim_return_60s_bps": reclaim_return,
            "reclaim_basis_change_5m_bps": reclaim_basis,
            "pullback_to_reclaim_open_interest_change": interval_oi,
            "inventory_route": (
                "LIQUIDATION"
                if scenario == LIQUIDATION_SCENARIO
                else "TRAPPED_COUNTERINVENTORY"
            ),
            "compiler": "candidate-04-directional-session-vwap-reclaim-v1",
        }
        intents.append(
            Intent(
                scenario=scenario,
                side=parent.side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(index, confirmation_index),
                details=details,
            )
        )
        if scenario == LIQUIDATION_SCENARIO:
            counts["liquidation_reclaims"] += 1
        else:
            counts["trapped_counterinventory_reclaims"] += 1

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
    intents, counts = detect_directional_session_vwap_reclaims(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-directional-session-vwap-reclaim-v1",
        "compiler": "candidate-04-directional-session-vwap-reclaim-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "parent_value_migration": (
                "previous full session efficiency above shifted prior median "
                "and close beyond one realized volume-weighted VWAP MAD"
            ),
            "pullback": (
                "next session first trades on parent side, then first meaningful "
                "VWAP penetration with counter-parent flow and return"
            ),
            "reclaim": (
                "close back through previous session VWAP within three bars "
                "with parent-side flow, return and five-minute basis"
            ),
            "inventory_routes": {
                "liquidation": "OI contracts from pre-pullback through reclaim",
                "trapped_counterinventory": (
                    "OI expands from pre-pullback through reclaim"
                ),
            },
            "invalidation": "complete pullback/reclaim excursion plus ATR buffer",
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
