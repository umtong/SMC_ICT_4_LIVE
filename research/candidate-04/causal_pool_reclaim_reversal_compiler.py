#!/usr/bin/env python3
"""Compile exact causal-pivot pool reclaim reversals.

This independent failed-auction scenario replaces coarse session boundaries and
rolling extremes with the first meaningful penetration of an aged, prominent,
right-confirmed pivot pool. A pool take is not enough. The completed sequence is:

1. aggressive executed flow and price displacement attack through the pool;
2. within three later completed bars price closes back inside the exact pool;
3. executed flow, price return and five-minute basis change all turn in the
   reversal direction; and
4. reversal displacement is no larger than the original attack, excluding a
   second impact event from being mislabeled as orderly failed discovery.

Same-bar displayed-depth recovery is deliberately excluded after its controlled
ablation remained negative. OI is diagnostic only because both liquidation and
failed new inventory can produce a valid failed auction. Stops lie beyond the
complete attack/reclaim extreme. The compiler emits intents only; NautilusTrader
owns targets, actual fills, fees, positions, risk, margin, liquidation and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v24 as v24


Intent = v24.Intent
SCENARIO = "EXACT_CAUSAL_POOL_FAILED_AUCTION_REVERSAL"
CONFIRMATION_BARS = 3


@dataclass(frozen=True, slots=True)
class TurnState:
    flow: float
    return_bps: float
    basis_change_bps: float


def directional_turn_state(row: pd.Series, side: int) -> TurnState | None:
    if side not in (-1, 1):
        return None
    state = TurnState(
        flow=side * float(row["flow_60s"]),
        return_bps=side * float(row["ret_60s_bps"]),
        basis_change_bps=side * float(row["basis_change_5m"]),
    )
    if not all(
        math.isfinite(value)
        for value in (state.flow, state.return_bps, state.basis_change_bps)
    ):
        return None
    return state


def attack_aligned(row: pd.Series, pool_side: int) -> tuple[bool, TurnState | None]:
    state = directional_turn_state(row, pool_side)
    return bool(state is not None and state.flow > 0.0 and state.return_bps > 0.0), state


def reversal_confirmed(state: TurnState | None, attack_return_bps: float) -> bool:
    return bool(
        state is not None
        and state.flow > 0.0
        and state.return_bps > 0.0
        and state.basis_change_bps > 0.0
        and math.isfinite(attack_return_bps)
        and 0.0 < state.return_bps <= attack_return_bps
    )


def _take_quality(take: v24.PoolTake) -> tuple[float, int, int, float]:
    return (
        float(take.prominence_atr),
        int(take.age_bars),
        int(take.touches),
        float(take.penetration_atr),
    )


def detect_causal_pool_reclaim_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    del config
    takes_by_index = v24.detect_external_pool_takes(data, config) if False else None
    # The unreachable expression above keeps static type readers aware that the
    # config belongs to the causal pool detector. Execute it normally below.
    takes_by_index = v24.detect_external_pool_takes(data, config)
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
        candidates: list[tuple[v24.PoolTake, TurnState]] = []
        for take in takes:
            aligned, state = attack_aligned(row, int(take.pool_side))
            if not aligned or state is None:
                counts["attack_not_aligned"] += 1
                continue
            candidates.append((take, state))
        if not candidates:
            continue
        if len(candidates) > 1:
            counts["ambiguous_multiple_takes"] += len(candidates) - 1
        take, attack_state = max(candidates, key=lambda item: _take_quality(item[0]))
        trade_side = int(take.trade_side)

        confirmation_index: int | None = None
        confirmation_state: TurnState | None = None
        upper = min(attack_index + CONFIRMATION_BARS, len(data) - 2)
        for index in range(attack_index + 1, upper + 1):
            candidate = data.iloc[index]
            if not v24.pool_is_reclaimed(take, float(candidate["close"])):
                continue
            state = directional_turn_state(candidate, trade_side)
            if not reversal_confirmed(state, attack_state.return_bps):
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
            "compiler": "candidate-04-causal-pool-reclaim-v1",
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
        "candidate": "candidate-04-causal-pool-reclaim-reversal-v1",
        "compiler": "candidate-04-causal-pool-reclaim-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "liquidity": (
                "first meaningful penetration of an aged/prominent causal "
                "right-confirmed pivot pool"
            ),
            "attack": "executed flow and return aligned through the pool",
            "reclaim": (
                "later close back inside the exact pool within three bars"
            ),
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
