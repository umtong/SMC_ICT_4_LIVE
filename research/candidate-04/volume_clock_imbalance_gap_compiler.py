#!/usr/bin/env python3
"""Causal volume-clock imbalance-gap continuation and reversal candidate.

The strategy translates an ICT fair-value-gap relation into completed
information-time auctions rather than fixed clock candles.  Each bucket uses a
notional target frozen from past data by the V37 volume clock.

Continuation:

1. three independent completed buckets form a non-overlapping directional gap;
2. displacement, path efficiency, flow, basis and material OI creation agree;
3. a later weak counter-flow bucket trades into the gap but accepts beyond its
   midpoint while most newly created inventory remains; and
4. a separate completed bucket breaks the retrace structure with renewed flow,
   return and basis alignment.

Reversal:

1. an attack bucket takes a causal confirmed external pivot pool;
2. later completed buckets reclaim that exact pool and create a directional
   inverse gap away from the failed auction;
3. OI identifies trapped new inventory or liquidation and subsequently resolves;
4. a separate retest bucket trades into the inverse gap and another completed
   bucket rejects it in the reversal direction.

Stops lie beyond the complete causal pattern excursion.  This compiler emits
intents only.  NautilusTrader owns targets, orders, fills, fees, positions,
current-NAV 3% all-in risk sizing, margin, liquidation, PnL and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import rich_signal_compiler_v24 as v24
import volume_clock_impact_residual_compiler as v37


Intent = v22.Intent

INFORMED_GAP_CONTINUATION = (
    "VOLUME_CLOCK_INFORMED_GAP_RETEST_CONTINUATION"
)
TRAPPED_IFVG_REVERSAL = (
    "VOLUME_CLOCK_TRAPPED_INVENTORY_INVERSE_GAP_REVERSAL"
)
LIQUIDATION_IFVG_REVERSAL = (
    "VOLUME_CLOCK_LIQUIDATION_INVERSE_GAP_REVERSAL"
)

GAP_FORMATION_BUCKETS = 3
RETRACE_BUCKETS = 3
RESUMPTION_BUCKETS = 2
GAP_MIN_WIDTH_ATR = 0.05
INVENTORY_RETENTION_FRACTION = 0.75
INVENTORY_UNWIND_FRACTION = 0.50


@dataclass(frozen=True, slots=True)
class ImbalanceGap:
    side: int
    lower: float
    upper: float
    midpoint: float
    first_position: int
    middle_position: int
    final_position: int
    start_index: int
    end_index: int
    formation_low: float
    formation_high: float


@dataclass(frozen=True, slots=True)
class GapState:
    gap: ImbalanceGap
    thresholds: v37.BucketThresholds
    state: str
    inventory_route: str
    source_oi_before: float
    source_oi_end: float
    external_take: v24.PoolTake | None = None


def finite(value: Any) -> float:
    return v37.finite(value)


def form_gap(
    buckets: list[v37.VolumeBucket],
    final_position: int,
    data: pd.DataFrame,
) -> ImbalanceGap | None:
    if final_position < GAP_FORMATION_BUCKETS - 1:
        return None
    first_position = final_position - 2
    middle_position = final_position - 1
    first = buckets[first_position]
    middle = buckets[middle_position]
    final = buckets[final_position]
    atr = finite(data["atr"].iloc[final.end_index])
    if not math.isfinite(atr) or atr <= 0.0:
        return None

    if final.low > first.high:
        side = 1
        lower = first.high
        upper = final.low
    elif final.high < first.low:
        side = -1
        lower = final.high
        upper = first.low
    else:
        return None
    width = upper - lower
    if width < GAP_MIN_WIDTH_ATR * atr:
        return None
    if middle.side != side or final.side != side:
        return None
    return ImbalanceGap(
        side=side,
        lower=lower,
        upper=upper,
        midpoint=(lower + upper) / 2.0,
        first_position=first_position,
        middle_position=middle_position,
        final_position=final_position,
        start_index=first.start_index,
        end_index=final.end_index,
        formation_low=min(first.low, middle.low, final.low),
        formation_high=max(first.high, middle.high, final.high),
    )


def total_oi_change(
    first: v37.VolumeBucket,
    final: v37.VolumeBucket,
) -> float:
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (first.oi_before, final.oi_end)
    ):
        return float("nan")
    return final.oi_end / first.oi_before - 1.0


def informed_gap_state(
    gap: ImbalanceGap,
    buckets: list[v37.VolumeBucket],
    thresholds: v37.BucketThresholds,
) -> GapState | None:
    first = buckets[gap.first_position]
    middle = buckets[gap.middle_position]
    final = buckets[gap.final_position]
    oi_change = total_oi_change(first, final)
    values = (
        middle.imbalance,
        final.imbalance,
        final.return_bps,
        final.efficiency,
        final.basis_end,
        first.basis_before,
        oi_change,
        thresholds.imbalance_q75,
        thresholds.absolute_return_q65,
        thresholds.efficiency_q60,
        thresholds.positive_oi_median,
    )
    if not all(math.isfinite(value) for value in values):
        return None
    aligned = (
        gap.side * middle.imbalance >= thresholds.imbalance_q75
        and gap.side * final.imbalance >= thresholds.imbalance_q50
        and gap.side * final.return_bps >= thresholds.absolute_return_q65
        and final.efficiency >= thresholds.efficiency_q60
        and gap.side * (final.basis_end - first.basis_before) > 0.0
        and oi_change >= thresholds.positive_oi_median
    )
    if not aligned:
        return None
    return GapState(
        gap=gap,
        thresholds=thresholds,
        state="INFORMED_GAP",
        inventory_route="NEW_INVENTORY",
        source_oi_before=first.oi_before,
        source_oi_end=final.oi_end,
    )


def _external_takes(
    gap: ImbalanceGap,
    buckets: list[v37.VolumeBucket],
) -> list[tuple[int, v24.PoolTake]]:
    result: list[tuple[int, v24.PoolTake]] = []
    for position in range(gap.first_position, gap.final_position + 1):
        for take in buckets[position].external_takes:
            result.append((position, take))
    return result


def inverse_gap_state(
    gap: ImbalanceGap,
    buckets: list[v37.VolumeBucket],
    thresholds: v37.BucketThresholds,
) -> GapState | None:
    final = buckets[gap.final_position]
    candidates = [
        (position, take)
        for position, take in _external_takes(gap, buckets)
        if int(take.pool_side) == -gap.side
        and v24.pool_is_reclaimed(take, final.close)
    ]
    if not candidates:
        return None
    attack_position, take = max(
        candidates,
        key=lambda item: (
            item[1].age_bars,
            item[1].prominence_atr,
            item[1].touches,
        ),
    )
    attack = buckets[attack_position]
    route = v37.material_inventory_route(
        attack.oi_change,
        thresholds.positive_oi_median,
    )
    if route is None:
        return None
    aligned = (
        gap.side * final.imbalance >= thresholds.imbalance_q50
        and gap.side * final.return_bps > 0.0
        and gap.side * (final.basis_end - final.basis_before) > 0.0
    )
    if not aligned:
        return None
    return GapState(
        gap=gap,
        thresholds=thresholds,
        state="INVERSE_GAP_AFTER_EXTERNAL_TAKE",
        inventory_route=route,
        source_oi_before=attack.oi_before,
        source_oi_end=attack.oi_end,
        external_take=take,
    )


def bucket_touches_gap(bucket: v37.VolumeBucket, gap: ImbalanceGap) -> bool:
    return bucket.low <= gap.upper and bucket.high >= gap.lower


def midpoint_accepted(bucket: v37.VolumeBucket, gap: ImbalanceGap) -> bool:
    return (
        bucket.close >= gap.midpoint
        if gap.side > 0
        else bucket.close <= gap.midpoint
    )


def weak_retrace(
    bucket: v37.VolumeBucket,
    state: GapState,
) -> bool:
    directional_counter = -state.gap.side * bucket.imbalance
    if directional_counter <= 0.0:
        return abs(bucket.imbalance) <= state.thresholds.imbalance_q75
    return directional_counter <= state.thresholds.imbalance_q50


def inventory_retained(
    state: GapState,
    bucket: v37.VolumeBucket,
) -> bool:
    if not all(
        math.isfinite(value)
        for value in (
            state.source_oi_before,
            state.source_oi_end,
            bucket.oi_end,
        )
    ):
        return False
    created = state.source_oi_end - state.source_oi_before
    if created <= 0.0:
        return False
    floor = (
        state.source_oi_before
        + INVENTORY_RETENTION_FRACTION * created
    )
    return bucket.oi_end >= floor


def inventory_resolved(
    state: GapState,
    bucket: v37.VolumeBucket,
) -> bool:
    if not all(
        math.isfinite(value)
        for value in (
            state.source_oi_before,
            state.source_oi_end,
            bucket.oi_end,
        )
    ):
        return False
    change = state.source_oi_end - state.source_oi_before
    if state.inventory_route == "NEW_INVENTORY":
        if change <= 0.0:
            return False
        return bucket.oi_end <= (
            state.source_oi_end
            - INVENTORY_UNWIND_FRACTION * change
        )
    if state.inventory_route == "LIQUIDATION":
        if change >= 0.0:
            return False
        return bucket.oi_end <= state.source_oi_end * v37.OI_REBUILD_TOLERANCE
    return False


def retrace_structure(bucket: v37.VolumeBucket, side: int) -> float:
    return bucket.high if side > 0 else bucket.low


def structure_broken(
    bucket: v37.VolumeBucket,
    structure: float,
    side: int,
) -> bool:
    return bucket.close > structure if side > 0 else bucket.close < structure


def aligned_resumption(
    bucket: v37.VolumeBucket,
    state: GapState,
) -> bool:
    side = state.gap.side
    return bool(
        side * bucket.imbalance >= state.thresholds.imbalance_q50
        and side * bucket.return_bps > 0.0
        and side * (bucket.basis_end - bucket.basis_before) > 0.0
    )


def _gap_details(state: GapState) -> dict[str, Any]:
    gap = state.gap
    details: dict[str, Any] = {
        "gap_side": gap.side,
        "gap_lower": gap.lower,
        "gap_upper": gap.upper,
        "gap_midpoint": gap.midpoint,
        "gap_first_position": gap.first_position,
        "gap_middle_position": gap.middle_position,
        "gap_final_position": gap.final_position,
        "gap_start_index": gap.start_index,
        "gap_end_index": gap.end_index,
        "gap_formation_low": gap.formation_low,
        "gap_formation_high": gap.formation_high,
        "inventory_route": state.inventory_route,
        "source_oi_before": state.source_oi_before,
        "source_oi_end": state.source_oi_end,
        "past_only_imbalance_q50": state.thresholds.imbalance_q50,
        "past_only_imbalance_q75": state.thresholds.imbalance_q75,
        "past_only_absolute_return_q65": state.thresholds.absolute_return_q65,
        "past_only_efficiency_q60": state.thresholds.efficiency_q60,
        "past_only_positive_oi_median": state.thresholds.positive_oi_median,
        "compiler": "candidate-04-volume-clock-imbalance-gap",
    }
    if state.external_take is not None:
        details.update(
            {
                "external_pool_id": state.external_take.pool_id,
                "external_pool_side": state.external_take.pool_side,
                "external_pool_level": state.external_take.level,
                "external_pool_age_bars": state.external_take.age_bars,
                "external_pool_prominence_atr": (
                    state.external_take.prominence_atr
                ),
            }
        )
    return details


def resolve_gap(
    data: pd.DataFrame,
    buckets: list[v37.VolumeBucket],
    state: GapState,
    evaluation_end: pd.Timestamp,
    impact_parameters: Any,
) -> tuple[Intent | None, int]:
    gap = state.gap
    maximum_retrace = min(
        gap.final_position + RETRACE_BUCKETS,
        len(buckets) - 2,
    )
    for retrace_position in range(gap.final_position + 1, maximum_retrace + 1):
        retrace = buckets[retrace_position]
        if data.index[retrace.end_index] > evaluation_end:
            return None, retrace_position
        if not (
            bucket_touches_gap(retrace, gap)
            and midpoint_accepted(retrace, gap)
            and weak_retrace(retrace, state)
        ):
            continue
        if state.state == "INFORMED_GAP":
            inventory_condition = inventory_retained(state, retrace)
        else:
            inventory_condition = inventory_resolved(state, retrace)
        if not inventory_condition:
            continue
        structure = retrace_structure(retrace, gap.side)
        upper = min(
            retrace_position + RESUMPTION_BUCKETS,
            len(buckets) - 1,
        )
        for resume_position in range(retrace_position + 1, upper + 1):
            resume = buckets[resume_position]
            if data.index[resume.end_index] > evaluation_end:
                return None, resume_position
            if resume.end_index + 1 >= len(data):
                continue
            if not (
                structure_broken(resume, structure, gap.side)
                and aligned_resumption(resume, state)
            ):
                continue
            if state.state == "INFORMED_GAP":
                final_inventory = inventory_retained(state, resume)
            else:
                final_inventory = inventory_resolved(state, resume)
            if not final_inventory:
                continue
            stop = v37.structural_stop(
                data,
                gap.start_index,
                resume.end_index,
                gap.side,
                impact_parameters,
            )
            if not math.isfinite(stop) or gap.side * (resume.close - stop) <= 0.0:
                continue
            if state.state == "INFORMED_GAP":
                scenario = INFORMED_GAP_CONTINUATION
                outcome = "INFORMED_GAP_RETRACE_ACCEPTED_AND_RESUMED"
            elif state.inventory_route == "NEW_INVENTORY":
                scenario = TRAPPED_IFVG_REVERSAL
                outcome = "EXTERNAL_BREAKOUT_TRAPPED_AND_INVERSE_GAP_REJECTED"
            else:
                scenario = LIQUIDATION_IFVG_REVERSAL
                outcome = "EXTERNAL_LIQUIDATION_EXHAUSTED_AND_INVERSE_GAP_REJECTED"
            details = {
                **_gap_details(state),
                "auction_outcome": outcome,
                "retrace_bucket_id": retrace.bucket_id,
                "retrace_start_index": retrace.start_index,
                "retrace_end_index": retrace.end_index,
                "retrace_close": retrace.close,
                "retrace_imbalance": retrace.imbalance,
                "retrace_structure": structure,
                "resumption_bucket_id": resume.bucket_id,
                "resumption_start_index": resume.start_index,
                "resumption_end_index": resume.end_index,
                "resumption_close": resume.close,
                "resumption_imbalance": resume.imbalance,
                "resumption_return_bps": resume.return_bps,
                "resumption_oi_end": resume.oi_end,
            }
            return (
                Intent(
                    scenario=scenario,
                    side=gap.side,
                    signal_index=resume.end_index,
                    entry_index=resume.end_index + 1,
                    stop_level=stop,
                    event_indices=(
                        gap.start_index,
                        gap.end_index,
                        retrace.start_index,
                        retrace.end_index,
                        resume.start_index,
                        resume.end_index,
                    ),
                    details=details,
                ),
                resume_position,
            )
    return None, maximum_retrace


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    takes = v24.detect_external_pool_takes(data, config)
    buckets = v37.build_volume_buckets(data, takes)
    history: list[v37.VolumeBucket] = []
    intents: list[Intent] = []
    counts = {
        "volume_buckets": len(buckets),
        "formed_gaps": 0,
        "informed_gap_states": 0,
        "inverse_gap_states": 0,
        "informed_gap_continuations": 0,
        "trapped_inventory_inverse_gap_reversals": 0,
        "liquidation_inverse_gap_reversals": 0,
        "unresolved_gap_states": 0,
        "history_not_ready": 0,
    }
    position = 0
    while position < len(buckets):
        bucket = buckets[position]
        timestamp = data.index[bucket.end_index]
        thresholds = v37.bucket_thresholds(history)
        history.append(bucket)
        if timestamp < evaluation_start:
            position += 1
            continue
        if timestamp > evaluation_end:
            break
        if thresholds is None:
            counts["history_not_ready"] += 1
            position += 1
            continue
        gap = form_gap(buckets, position, data)
        if gap is None:
            position += 1
            continue
        counts["formed_gaps"] += 1
        state = informed_gap_state(gap, buckets, thresholds)
        if state is not None:
            counts["informed_gap_states"] += 1
        else:
            state = inverse_gap_state(gap, buckets, thresholds)
            if state is not None:
                counts["inverse_gap_states"] += 1
        if state is None:
            position += 1
            continue
        intent, resolved_position = resolve_gap(
            data,
            buckets,
            state,
            evaluation_end,
            impact_parameters,
        )
        next_position = max(position + 1, resolved_position + 1)
        history.extend(buckets[position + 1 : next_position])
        position = next_position
        if intent is None:
            counts["unresolved_gap_states"] += 1
            continue
        intents.append(intent)
        if intent.scenario == INFORMED_GAP_CONTINUATION:
            counts["informed_gap_continuations"] += 1
        elif intent.scenario == TRAPPED_IFVG_REVERSAL:
            counts["trapped_inventory_inverse_gap_reversals"] += 1
        else:
            counts["liquidation_inverse_gap_reversals"] += 1

    intents.sort(key=lambda item: int(item.signal_index))
    unique: list[Intent] = []
    seen: set[int] = set()
    duplicates = 0
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            duplicates += 1
            continue
        seen.add(index)
        unique.append(intent)
    return unique, {
        "candidate": "candidate-04-v38-volume-clock-imbalance-gap",
        "compiler": "candidate-04-volume-clock-imbalance-gap",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": duplicates,
        "route_counts": counts,
        "scenario_contract": {
            "clock": "past-only notional-target volume buckets",
            "gap": "three separate completed buckets with non-overlapping price ranges",
            "continuation": (
                "material OI creation, weak separate gap retrace and later "
                "structure-breaking resumption"
            ),
            "reversal": (
                "causal external pool take, exact reclaim, inverse gap, routed "
                "inventory resolution, separate retest and rejection"
            ),
            "stop": "outside complete formation-retrace-resumption excursion",
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
