#!/usr/bin/env python3
"""Parent-session VWAP liquidation negotiation and resumption.

A directional completed 8-hour session defines accepted value and one external
liquidity destination. The next session must first preserve the parent side,
then close through the parent VWAP with counter-parent flow and return. This is
the start of a real counterauction, not a wick test.

The counterauction must liquidate a material amount of open interest relative to
its shifted past-only norm. Entry is delayed until a later completed bar:

* closes back on the parent side of VWAP;
* leaves the entire prior close-negotiation range in the parent direction;
* has parent-side executed flow and return; and
* has not rebuilt the liquidated open interest above its pre-pullback level.

The target is the still-untouched high/low of the completed parent session. The
stop is beyond the full pullback-negotiation excursion. This module emits intents
only; NautilusTrader owns orders, fills, costs, risk, positions, PnL and NAV.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import directional_session_vwap_reclaim_compiler as parent_base
import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401


Intent = v22.Intent
SCENARIO = "DIRECTIONAL_SESSION_VWAP_LIQUIDATION_NEGOTIATION_RESUMPTION"
MAX_NEGOTIATION_BARS = 180
OI_NORM_WINDOW = 720
OI_NORM_MIN_PERIODS = 240
OI_NORM_HORIZON = 5


def past_only_oi_change_norm(data: pd.DataFrame) -> pd.Series:
    oi = data["metric_sum_open_interest"].astype(float)
    absolute_change = oi.pct_change(OI_NORM_HORIZON, fill_method=None).abs()
    return (
        absolute_change.shift(1)
        .rolling(OI_NORM_WINDOW, min_periods=OI_NORM_MIN_PERIODS)
        .median()
    )


def counterauction_bar(row: pd.Series, vwap: float, parent_side: int) -> bool:
    if parent_side not in (-1, 1) or not math.isfinite(vwap):
        return False
    close = float(row["close"])
    flow = -parent_side * float(row["flow_60s"])
    return_bps = -parent_side * float(row["ret_60s_bps"])
    return bool(
        all(math.isfinite(value) for value in (close, flow, return_bps))
        and -parent_side * (close - vwap) > 0.0
        and flow > 0.0
        and return_bps > 0.0
    )


def negotiation_break(
    prior_closes: pd.Series,
    close: float,
    parent_side: int,
) -> bool:
    if prior_closes.empty or parent_side not in (-1, 1) or not math.isfinite(close):
        return False
    high = float(prior_closes.max())
    low = float(prior_closes.min())
    if not all(math.isfinite(value) for value in (high, low)):
        return False
    return close > high if parent_side > 0 else close < low


def liquidation_cleared(
    pre_pullback_oi: float,
    minimum_oi: float,
    resolution_oi: float,
    past_only_cutoff: float,
) -> tuple[bool, dict[str, float]]:
    values = (pre_pullback_oi, minimum_oi, resolution_oi, past_only_cutoff)
    if not all(math.isfinite(value) for value in values):
        return False, {}
    if pre_pullback_oi <= 0.0 or minimum_oi <= 0.0 or resolution_oi <= 0.0:
        return False, {}
    if past_only_cutoff <= 0.0:
        return False, {}
    contraction = minimum_oi / pre_pullback_oi - 1.0
    resolution_change = resolution_oi / pre_pullback_oi - 1.0
    passed = contraction <= -past_only_cutoff and resolution_oi <= pre_pullback_oi
    return passed, {
        "pre_pullback_open_interest": pre_pullback_oi,
        "minimum_negotiation_open_interest": minimum_oi,
        "resolution_open_interest": resolution_oi,
        "minimum_open_interest_change": contraction,
        "resolution_open_interest_change": resolution_change,
        "past_only_material_oi_change_cutoff": past_only_cutoff,
        "material_liquidation_occurred": contraction <= -past_only_cutoff,
        "liquidation_not_rebuilt": resolution_oi <= pre_pullback_oi,
    }


def parent_target(
    parent: parent_base.DirectionalSession,
) -> tuple[float, str]:
    if parent.side > 0:
        return parent.high, (
            f"completed_parent_session_{parent.session_start.isoformat()}_high"
        )
    return parent.low, (
        f"completed_parent_session_{parent.session_start.isoformat()}_low"
    )


def target_is_unconsumed(
    data: pd.DataFrame,
    current_session_start_index: int,
    signal_index: int,
    parent_side: int,
    target: float,
) -> bool:
    if not (
        0 <= current_session_start_index <= signal_index < len(data)
        and parent_side in (-1, 1)
        and math.isfinite(target)
    ):
        return False
    observed = data.iloc[current_session_start_index : signal_index + 1]
    if observed.empty:
        return False
    if parent_side > 0:
        return float(observed["high"].max()) < target
    return float(observed["low"].min()) > target


def detect_negotiated_resumptions(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    contexts = parent_base._directional_contexts(data)
    session_starts = [parent_base.session_base.session_start(value) for value in data.index]
    open_interest = data["metric_sum_open_interest"].astype(float)
    oi_norm = past_only_oi_change_norm(data)
    observed_parent_side: dict[pd.Timestamp, bool] = {}
    consumed: set[pd.Timestamp] = set()
    intents: list[Intent] = []
    counts = {
        "completed_parent_contexts": len(contexts),
        "nondirectional_parents": 0,
        "parent_side_not_observed": 0,
        "counterauction_starts": 0,
        "no_negotiation_break": 0,
        "resolution_not_flow_aligned": 0,
        "liquidation_not_material_or_rebuilt": 0,
        "parent_target_already_consumed": 0,
        "target_wrong_side": 0,
        "negotiated_liquidation_resumptions": 0,
        "duplicate_signal_bars": 0,
    }

    for index, timestamp in enumerate(data.index):
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        current_start = session_starts[index]
        parent = contexts.get(current_start)
        if parent is None:
            continue
        if not parent.directional:
            if index == 0 or session_starts[index - 1] != current_start:
                counts["nondirectional_parents"] += 1
            continue
        if current_start in consumed:
            continue

        close = float(data["close"].iloc[index])
        if parent.side * (close - parent.vwap) > 0.0:
            observed_parent_side[current_start] = True
        if not counterauction_bar(data.iloc[index], parent.vwap, parent.side):
            continue

        consumed.add(current_start)
        counts["counterauction_starts"] += 1
        if not observed_parent_side.get(current_start, False):
            counts["parent_side_not_observed"] += 1
            continue
        if index == 0:
            continue
        pre_oi = float(open_interest.iloc[index - 1])
        cutoff = float(oi_norm.iloc[index])
        if not all(math.isfinite(value) for value in (pre_oi, cutoff)):
            counts["liquidation_not_material_or_rebuilt"] += 1
            continue

        current_start_index = data.index.get_loc(current_start)
        if not isinstance(current_start_index, int):
            current_start_index = int(current_start_index.start)
        target, target_source = parent_target(parent)
        parent_observed_index = current_start_index - 1
        if parent.side * (target - close) <= 0.0:
            counts["target_wrong_side"] += 1
            continue

        session_end_index = min(current_start_index + 479, len(data) - 2)
        upper = min(index + MAX_NEGOTIATION_BARS, session_end_index, len(data) - 2)
        resolved = False
        for signal_index in range(index + 1, upper + 1):
            if data.index[signal_index] > evaluation_end:
                break
            row = data.iloc[signal_index]
            resolution_close = float(row["close"])
            if parent.side * (resolution_close - parent.vwap) <= 0.0:
                continue
            prior_closes = data["close"].iloc[index:signal_index].astype(float)
            if not negotiation_break(prior_closes, resolution_close, parent.side):
                continue
            flow = parent.side * float(row["flow_60s"])
            return_bps = parent.side * float(row["ret_60s_bps"])
            basis = parent.side * float(row["basis_change_5m"])
            if not all(math.isfinite(value) for value in (flow, return_bps, basis)):
                counts["resolution_not_flow_aligned"] += 1
                continue
            if flow <= 0.0 or return_bps <= 0.0:
                counts["resolution_not_flow_aligned"] += 1
                continue

            segment_oi = open_interest.iloc[index : signal_index + 1].astype(float)
            minimum_oi = float(segment_oi.min())
            resolution_oi = float(open_interest.iloc[signal_index])
            cleared, oi_details = liquidation_cleared(
                pre_oi,
                minimum_oi,
                resolution_oi,
                cutoff,
            )
            if not cleared:
                counts["liquidation_not_material_or_rebuilt"] += 1
                continue
            if not target_is_unconsumed(
                data,
                current_start_index,
                signal_index,
                parent.side,
                target,
            ):
                counts["parent_target_already_consumed"] += 1
                continue
            if parent.side * (target - resolution_close) <= 0.0:
                counts["target_wrong_side"] += 1
                continue

            negotiation = data.iloc[index : signal_index + 1]
            atr = float(row["atr"])
            if not math.isfinite(atr) or atr <= 0.0:
                continue
            extreme = float(
                negotiation["low"].min()
                if parent.side > 0
                else negotiation["high"].max()
            )
            stop = extreme - parent.side * float(
                impact_parameters.stop_buffer_atr
            ) * atr
            if parent.side * (resolution_close - stop) <= 0.0:
                continue

            details = {
                **oi_details,
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
                "past_only_session_efficiency_median": parent.past_efficiency_median,
                "counterauction_index": index,
                "counterauction_close": close,
                "counterauction_flow_60s": -parent.side * float(data["flow_60s"].iloc[index]),
                "counterauction_return_60s_bps": -parent.side * float(data["ret_60s_bps"].iloc[index]),
                "negotiation_resolution_index": signal_index,
                "negotiation_bars": signal_index - index + 1,
                "resolution_flow_60s": flow,
                "resolution_return_60s_bps": return_bps,
                "resolution_basis_change_5m_bps": basis,
                "full_negotiation_low": float(negotiation["low"].min()),
                "full_negotiation_high": float(negotiation["high"].max()),
                "causal_target_reference": target,
                "causal_target_source": target_source,
                "causal_target_observed_index": parent_observed_index,
                "target_unconsumed_through_signal": True,
                "compiler": "candidate-04-directional-session-vwap-negotiation-v1",
            }
            intents.append(
                Intent(
                    scenario=SCENARIO,
                    side=parent.side,
                    signal_index=signal_index,
                    entry_index=signal_index + 1,
                    stop_level=stop,
                    event_indices=(index, signal_index),
                    details=details,
                )
            )
            counts["negotiated_liquidation_resumptions"] += 1
            resolved = True
            break
        if not resolved:
            counts["no_negotiation_break"] += 1

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
    intents, counts = detect_negotiated_resumptions(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-v47-directional-session-vwap-negotiation",
        "compiler": "candidate-04-directional-session-vwap-negotiation-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "parent": "completed directional 8h value migration beyond VWAP MAD",
            "counterauction": "first parent-VWAP counter-side close with counter flow and return after parent side was observed",
            "inventory": "material past-normalized OI contraction without rebuild above pre-pullback OI",
            "resolution": "parent-side close outside the full prior close-negotiation range with parent flow and return",
            "invalidation": "complete pullback-negotiation excursion plus ATR buffer",
            "target": "still-untouched completed parent-session high or low observed before the signal",
            "execution": "NautilusTrader BacktestNode only",
        },
        "constants": {
            "maximum_negotiation_bars": MAX_NEGOTIATION_BARS,
            "oi_norm_horizon_minutes": OI_NORM_HORIZON,
            "oi_norm_window_minutes": OI_NORM_WINDOW,
            "oi_norm_min_periods": OI_NORM_MIN_PERIODS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
