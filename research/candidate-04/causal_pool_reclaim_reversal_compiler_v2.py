#!/usr/bin/env python3
"""Implementation-correct V2 wrapper for exact causal-pool reclaim reversal.

The V1 source defined the intended market state correctly but accidentally
deleted the ``config`` local before passing it to the causal pool detector. This
file changes no scenario logic. It reuses every V1 helper and implements only the
configuration-safe detector/entrypoint so the same intended state can be tested.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import causal_pool_reclaim_reversal_compiler as base
import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401


Intent = base.Intent
SCENARIO = base.SCENARIO
CONFIRMATION_BARS = base.CONFIRMATION_BARS


def detect_causal_pool_reclaim_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    """Run the unchanged V1 scenario with the supplied pool configuration."""

    takes_by_index = base.v24.detect_external_pool_takes(data, config)
    intents: list[Intent] = []
    counts = {
        "eligible_pool_takes": 0,
        "ambiguous_multiple_takes": 0,
        "attack_not_aligned": 0,
        "no_exact_reclaim_turn": 0,
        "confirmed_reversal": 0,
    }

    for attack_index, takes in sorted(takes_by_index.items()):
        timestamp = data.index[attack_index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        counts["eligible_pool_takes"] += len(takes)
        row = data.iloc[attack_index]
        candidates: list[tuple[Any, Any]] = []
        for take in takes:
            aligned, state = base.attack_aligned(row, int(take.pool_side))
            if not aligned or state is None:
                counts["attack_not_aligned"] += 1
                continue
            candidates.append((take, state))
        if not candidates:
            continue
        if len(candidates) > 1:
            counts["ambiguous_multiple_takes"] += len(candidates) - 1
        take, attack_state = max(
            candidates,
            key=lambda item: base._take_quality(item[0]),
        )
        trade_side = int(take.trade_side)

        confirmation_index: int | None = None
        confirmation_state: Any | None = None
        upper = min(attack_index + CONFIRMATION_BARS, len(data) - 2)
        for index in range(attack_index + 1, upper + 1):
            candidate = data.iloc[index]
            if not base.v24.pool_is_reclaimed(take, float(candidate["close"])):
                continue
            state = base.directional_turn_state(candidate, trade_side)
            if not base.reversal_confirmed(state, attack_state.return_bps):
                continue
            confirmation_index = index
            confirmation_state = state
            break

        if confirmation_index is None or confirmation_state is None:
            counts["no_exact_reclaim_turn"] += 1
            continue
        if data.index[confirmation_index] > evaluation_end:
            continue

        segment = data.iloc[attack_index : confirmation_index + 1]
        extreme = float(
            segment["low"].min()
            if trade_side > 0
            else segment["high"].max()
        )
        atr = float(data["atr"].iloc[confirmation_index])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        stop_level = (
            extreme
            - trade_side * float(impact_parameters.stop_buffer_atr) * atr
        )
        oi_change = float(data["oi_change_xday_15m"].iloc[attack_index])
        details = {
            "pool_id": take.pool_id,
            "pool_side": take.pool_side,
            "pool_level": take.level,
            "pool_age_bars": take.age_bars,
            "pool_prominence_atr": take.prominence_atr,
            "pool_touches": take.touches,
            "pool_penetration_atr": take.penetration_atr,
            "attack_index": attack_index,
            "attack_state": {
                "directional_flow_60s": attack_state.flow,
                "directional_return_60s_bps": attack_state.return_bps,
                "directional_basis_change_5m_bps": attack_state.basis_change_bps,
                "open_interest_change_15m": oi_change,
            },
            "confirmation_index": confirmation_index,
            "confirmation_delay_bars": confirmation_index - attack_index,
            "reversal_state": {
                "directional_flow_60s": confirmation_state.flow,
                "directional_return_60s_bps": confirmation_state.return_bps,
                "directional_basis_change_5m_bps": confirmation_state.basis_change_bps,
                "return_to_attack_ratio": (
                    confirmation_state.return_bps / attack_state.return_bps
                ),
            },
            "same_bar_depth_confirmation_allowed": False,
            "compiler": "candidate-04-causal-pool-reclaim-v2",
        }
        intents.append(
            Intent(
                scenario=SCENARIO,
                side=trade_side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(attack_index, confirmation_index),
                details=details,
            ),
        )
        counts["confirmed_reversal"] += 1

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
    intents, counts = detect_causal_pool_reclaim_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-causal-pool-reclaim-reversal-v2",
        "compiler": "candidate-04-causal-pool-reclaim-v2",
        "implementation_fix": (
            "preserve the supplied config local when invoking the unchanged "
            "causal pivot-pool detector"
        ),
        "scenario_logic_changed_from_v1": false,
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "liquidity": (
                "first meaningful penetration of an aged/prominent causal "
                "right-confirmed pivot pool"
            ),
            "attack": "executed flow and return aligned through the pool",
            "reclaim": "later close back inside the exact pool within three bars",
            "turn": (
                "reversal-side executed flow, return and basis change with "
                "reversal return no larger than the attack"
            ),
            "excluded": "same-bar displayed-depth confirmation",
            "invalidation": "complete attack/reclaim extreme plus ATR buffer",
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
        "constants": {
            "confirmation_bars": CONFIRMATION_BARS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
