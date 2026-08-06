#!/usr/bin/env python3
"""Classify directional parent sessions by a past-only upper-tail threshold.

The original directional-session VWAP reclaim treated any completed 8-hour
session above the shifted median efficiency as directional. Cross-development
showed that marginally-above-median sessions mixed weak rotation with genuine
value migration. This module changes one state boundary only: parent auction
efficiency must exceed the shifted 75th percentile of prior completed sessions.

VWAP/MAD acceptance, first next-session pullback, reclaim timing, flow, return,
basis, state-interval OI routing, stop, target, risk and NautilusTrader execution
remain unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import directional_session_vwap_reclaim_compiler as base


Intent = v22.Intent
LIQUIDATION_SCENARIO = base.LIQUIDATION_SCENARIO
TRAPPED_COUNTER_SCENARIO = base.TRAPPED_COUNTER_SCENARIO
EFFICIENCY_QUANTILE = 0.75


def past_efficiency_cutoff(history: list[float]) -> float:
    finite = [float(value) for value in history if math.isfinite(value)]
    if len(finite) < int(base.EFFICIENCY_MIN_SESSIONS):
        return float("nan")
    return float(pd.Series(finite, dtype=float).quantile(EFFICIENCY_QUANTILE))


def _upper_tail_contexts(
    data: pd.DataFrame,
) -> dict[pd.Timestamp, base.DirectionalSession]:
    starts = pd.Series(
        [base.session_base.session_start(value) for value in data.index],
        index=data.index,
    )
    groups = list(data.groupby(starts, sort=True))
    states: list[base.DirectionalSession] = []
    past_efficiencies: list[float] = []
    for start, frame in groups:
        if len(frame) != 480:
            continue
        efficiency = base.balanced.session_auction_efficiency(frame)
        history = [
            value
            for value in past_efficiencies[-base.EFFICIENCY_HISTORY_SESSIONS :]
            if math.isfinite(value)
        ]
        cutoff = past_efficiency_cutoff(history)
        open_price = float(frame["open"].iloc[0])
        close = float(frame["close"].iloc[-1])
        raw = close - open_price
        side = 1 if raw > 0.0 else -1 if raw < 0.0 else 0
        vwap, mad = base.balanced.session_vwap_state(frame)
        directional = (
            side in (-1, 1)
            and math.isfinite(efficiency)
            and math.isfinite(cutoff)
            and efficiency > cutoff
            and base.directional_value_acceptance(close, vwap, mad, side)
        )
        states.append(
            base.DirectionalSession(
                session_start=start,
                high=float(frame["high"].max()),
                low=float(frame["low"].min()),
                open=open_price,
                close=close,
                vwap=vwap,
                vwap_mad=mad,
                efficiency=efficiency,
                # The base detector reads this field as its causal cutoff. The
                # copied intent below renames it accurately in diagnostics.
                past_efficiency_median=cutoff,
                side=side,
                directional=directional,
            )
        )
        if math.isfinite(efficiency):
            past_efficiencies.append(efficiency)
    return {state.session_start + pd.Timedelta(hours=8): state for state in states}


def _copy_intent(intent: Any) -> Intent:
    details = dict(intent.details)
    cutoff = details.pop("past_only_session_efficiency_median", float("nan"))
    details.update(
        {
            "past_only_session_efficiency_upper_quartile": cutoff,
            "session_efficiency_quantile": EFFICIENCY_QUANTILE,
            "compiler": "candidate-04-directional-session-vwap-upper-tail",
        }
    )
    return Intent(
        scenario=str(intent.scenario),
        side=int(intent.side),
        signal_index=int(intent.signal_index),
        entry_index=int(intent.entry_index),
        stop_level=float(intent.stop_level),
        event_indices=tuple(int(value) for value in intent.event_indices),
        details=details,
    )


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
):
    del router
    original = base._directional_contexts
    base._directional_contexts = _upper_tail_contexts
    try:
        intents, counts = base.detect_directional_session_vwap_reclaims(
            data,
            evaluation_start,
            evaluation_end,
            config,
            impact_parameters,
        )
    finally:
        base._directional_contexts = original
    copied = [_copy_intent(intent) for intent in intents]
    return copied, {
        "candidate": "candidate-04-directional-session-vwap-upper-tail",
        "compiler": "candidate-04-directional-session-vwap-upper-tail",
        "raw_routed_signals": len(copied),
        "unique_signal_bars": len(copied),
        "route_counts": counts,
        "structural_refinement": {
            "changed_variables": 1,
            "removed_boundary": "efficiency above shifted prior median",
            "replacement_boundary": (
                "efficiency above shifted prior completed-session 75th percentile"
            ),
            "reason": (
                "directional value migration must be an upper-tail auction state, "
                "not a marginally above-median rotation"
            ),
            "unchanged": [
                "close beyond one realized VWAP MAD",
                "first next-session VWAP pullback",
                "counter-parent pullback flow and return",
                "three-bar parent-side reclaim",
                "parent-side flow return and basis",
                "state-interval OI routing",
                "causal stop and target",
                "actual-fill guard",
                "current-NAV 3% risk sizing",
                "NautilusTrader execution",
            ],
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
