#!/usr/bin/env python3
"""Compile accepted external-pool breaks followed by first retest resumption.

This independent scenario corrects the rejected arbitrary-internal-pivot model.
The parent state begins only when one already confirmed causal pivot pool is
actually broken and accepted. The broken pool itself becomes the sole eligible
retest boundary; no unrelated historical pivot is substituted.

Causal sequence:

1. first meaningful penetration of an aged/prominent causal high/low pool;
2. completed close outside with executed flow, return and five-minute basis
   expansion aligned with the break;
3. a later completed bar remains outside with the same alignment and a smaller
   or equal directional return, proving non-climactic acceptance;
4. the first meaningful retest penetrates back through the broken level with
   counter-break flow/return and contracting open interest;
5. within three completed bars the exact level is reclaimed with break-side
   flow/return whose displacement is no larger than the retest shock.

Any close back inside before the first meaningful retest invalidates acceptance.
The stop is beyond the complete retest/reclaim extreme. The compiler emits only
timestamped intents; NautilusTrader owns targets, orders, actual fills, costs,
positions, risk, margin, liquidation and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # warmup-aware loader
import rich_signal_compiler_v24 as v24


Intent = v24.Intent
SCENARIO = "ACCEPTED_EXTERNAL_POOL_BREAK_RETEST_RESUMPTION"
ACCEPTANCE_BARS = 3
RECLAIM_BARS = 3
MAX_RETEST_WAIT_BARS = 180


@dataclass(frozen=True, slots=True)
class DirectionalState:
    flow: float
    return_bps: float
    basis_change_bps: float


def outside_pool(close: float, side: int, level: float) -> bool:
    if side not in (-1, 1):
        return False
    return side * (close - level) > 0.0


def directional_state(row: pd.Series, side: int) -> DirectionalState | None:
    if side not in (-1, 1):
        return None
    state = DirectionalState(
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


def aligned_acceptance_state(state: DirectionalState | None) -> bool:
    return bool(
        state is not None
        and state.flow > 0.0
        and state.return_bps > 0.0
        and state.basis_change_bps > 0.0
    )


def non_climactic(direction_return_bps: float, shock_return_bps: float) -> bool:
    if not all(
        math.isfinite(value)
        for value in (direction_return_bps, shock_return_bps)
    ):
        return False
    return 0.0 < direction_return_bps <= shock_return_bps


def retest_penetration_atr(
    row: pd.Series,
    side: int,
    level: float,
    atr: float,
) -> float:
    if not math.isfinite(atr) or atr <= 0.0:
        return float("nan")
    if side > 0:
        return (level - float(row["low"])) / atr
    if side < 0:
        return (float(row["high"]) - level) / atr
    return float("nan")


def _break_quality(take: v24.PoolTake) -> tuple[float, int, int, float]:
    return (
        float(take.prominence_atr),
        int(take.age_bars),
        int(take.touches),
        float(take.penetration_atr),
    )


def detect_external_break_retest_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    takes_by_index = v24.detect_external_pool_takes(data, config)
    intents: list[Intent] = []
    counts = {
        "eligible_pool_takes": 0,
        "ambiguous_multiple_breaks": 0,
        "break_closed_inside": 0,
        "break_alignment_failed": 0,
        "acceptance_invalidated": 0,
        "no_non_climactic_acceptance": 0,
        "no_retest": 0,
        "retest_without_counter_flow": 0,
        "retest_without_oi_contraction": 0,
        "no_reclaim": 0,
        "confirmed_resumption": 0,
    }

    for break_index, takes in sorted(takes_by_index.items()):
        timestamp = data.index[break_index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        counts["eligible_pool_takes"] += len(takes)
        candidates: list[tuple[v24.PoolTake, int, DirectionalState]] = []
        row = data.iloc[break_index]
        for take in takes:
            side = int(take.pool_side)
            if not outside_pool(float(row["close"]), side, float(take.level)):
                counts["break_closed_inside"] += 1
                continue
            state = directional_state(row, side)
            if not aligned_acceptance_state(state):
                counts["break_alignment_failed"] += 1
                continue
            assert state is not None
            candidates.append((take, side, state))

        if not candidates:
            continue
        if len(candidates) > 1:
            counts["ambiguous_multiple_breaks"] += len(candidates) - 1
        take, side, break_state = max(
            candidates,
            key=lambda item: _break_quality(item[0]),
        )

        acceptance_index: int | None = None
        acceptance_state: DirectionalState | None = None
        upper = min(break_index + ACCEPTANCE_BARS, len(data) - 2)
        invalidated = False
        for index in range(break_index + 1, upper + 1):
            candidate = data.iloc[index]
            close = float(candidate["close"])
            if not outside_pool(close, side, take.level):
                invalidated = True
                break
            state = directional_state(candidate, side)
            if not aligned_acceptance_state(state):
                continue
            assert state is not None
            if not non_climactic(state.return_bps, break_state.return_bps):
                continue
            acceptance_index = index
            acceptance_state = state
            break

        if invalidated:
            counts["acceptance_invalidated"] += 1
            continue
        if acceptance_index is None or acceptance_state is None:
            counts["no_non_climactic_acceptance"] += 1
            continue

        retest_index: int | None = None
        retest_state: DirectionalState | None = None
        retest_oi = float("nan")
        retest_penetration = float("nan")
        state_failed = False
        retest_upper = min(
            acceptance_index + MAX_RETEST_WAIT_BARS,
            len(data) - 2,
        )
        for index in range(acceptance_index + 1, retest_upper + 1):
            candidate = data.iloc[index]
            atr = float(candidate["atr"])
            penetration = retest_penetration_atr(
                candidate,
                side,
                take.level,
                atr,
            )
            close = float(candidate["close"])
            if (
                not outside_pool(close, side, take.level)
                and (
                    not math.isfinite(penetration)
                    or penetration < float(config.sweep_min_atr)
                )
            ):
                state_failed = True
                break
            if (
                not math.isfinite(penetration)
                or penetration < float(config.sweep_min_atr)
            ):
                continue

            counter_state = directional_state(candidate, -side)
            if not aligned_acceptance_state(counter_state):
                counts["retest_without_counter_flow"] += 1
                state_failed = True
                break
            oi_change = float(candidate["oi_change_xday_15m"])
            if not math.isfinite(oi_change) or oi_change >= 0.0:
                counts["retest_without_oi_contraction"] += 1
                state_failed = True
                break
            retest_index = index
            retest_state = counter_state
            retest_oi = oi_change
            retest_penetration = penetration
            break

        if state_failed:
            continue
        if retest_index is None or retest_state is None:
            counts["no_retest"] += 1
            continue

        confirmation_index: int | None = None
        reclaim_state: DirectionalState | None = None
        reclaim_upper = min(retest_index + RECLAIM_BARS, len(data) - 2)
        for index in range(retest_index + 1, reclaim_upper + 1):
            candidate = data.iloc[index]
            if not outside_pool(float(candidate["close"]), side, take.level):
                continue
            state = directional_state(candidate, side)
            if not aligned_acceptance_state(state):
                continue
            assert state is not None
            if not non_climactic(state.return_bps, retest_state.return_bps):
                continue
            confirmation_index = index
            reclaim_state = state
            break

        if confirmation_index is None or reclaim_state is None:
            counts["no_reclaim"] += 1
            continue
        if data.index[confirmation_index] > evaluation_end:
            continue

        segment = data.iloc[retest_index : confirmation_index + 1]
        extreme = float(
            segment["low"].min()
            if side > 0
            else segment["high"].max()
        )
        atr = float(data["atr"].iloc[confirmation_index])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        stop_level = extreme - side * float(impact_parameters.stop_buffer_atr) * atr
        details = {
            "broken_pool_id": take.pool_id,
            "broken_pool_side": take.pool_side,
            "broken_pool_level": take.level,
            "broken_pool_age_bars": take.age_bars,
            "broken_pool_prominence_atr": take.prominence_atr,
            "broken_pool_touches": take.touches,
            "break_index": break_index,
            "break_penetration_atr": take.penetration_atr,
            "break_state": {
                "directional_flow_60s": break_state.flow,
                "directional_return_60s_bps": break_state.return_bps,
                "directional_basis_change_5m_bps": break_state.basis_change_bps,
            },
            "acceptance_index": acceptance_index,
            "acceptance_delay_bars": acceptance_index - break_index,
            "acceptance_state": {
                "directional_flow_60s": acceptance_state.flow,
                "directional_return_60s_bps": acceptance_state.return_bps,
                "directional_basis_change_5m_bps": acceptance_state.basis_change_bps,
                "return_to_break_ratio": (
                    acceptance_state.return_bps / break_state.return_bps
                ),
            },
            "retest_index": retest_index,
            "retest_wait_bars": retest_index - acceptance_index,
            "retest_penetration_atr": retest_penetration,
            "retest_counter_break_state": {
                "directional_flow_60s": retest_state.flow,
                "directional_return_60s_bps": retest_state.return_bps,
                "directional_basis_change_5m_bps": retest_state.basis_change_bps,
                "open_interest_change_15m": retest_oi,
            },
            "confirmation_index": confirmation_index,
            "reclaim_delay_bars": confirmation_index - retest_index,
            "reclaim_state": {
                "directional_flow_60s": reclaim_state.flow,
                "directional_return_60s_bps": reclaim_state.return_bps,
                "directional_basis_change_5m_bps": reclaim_state.basis_change_bps,
                "return_to_retest_ratio": (
                    reclaim_state.return_bps / retest_state.return_bps
                ),
            },
            "compiler": "candidate-04-external-break-retest-v1",
        }
        intents.append(
            Intent(
                scenario=SCENARIO,
                side=side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(break_index, acceptance_index, retest_index, confirmation_index),
                details=details,
            ),
        )
        counts["confirmed_resumption"] += 1

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
    intents, counts = detect_external_break_retest_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-external-break-retest-resumption-v1",
        "compiler": "candidate-04-external-break-retest-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "parent_creation": (
                "first accepted break of an aged/prominent causal pivot pool; "
                "a rolling net-return sign is not a parent state"
            ),
            "acceptance": (
                "later outside close with aligned executed flow, return and "
                "directional basis expansion; confirmation return <= break return"
            ),
            "retest": (
                "first meaningful penetration back through the exact broken pool "
                "with counter-break flow/return and contracting OI"
            ),
            "reclaim": (
                "exact pool reclaimed within three bars with break-side flow, "
                "return and basis; confirmation return <= retest return"
            ),
            "invalidation": (
                "close back inside before meaningful retest, failed first retest, "
                "or complete retest/reclaim extreme after entry"
            ),
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
        "constants": {
            "acceptance_bars": ACCEPTANCE_BARS,
            "reclaim_bars": RECLAIM_BARS,
            "maximum_retest_wait_bars": MAX_RETEST_WAIT_BARS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
