#!/usr/bin/env python3
"""Compile parent-aligned internal-liquidity liquidation resumptions.

This independent scenario extends the mechanism that survived V24/V25 without
relaxing the impact-shock thresholds. It asks whether a directional parent
auction resumes after liquidating counter-position inventory at an already
confirmed internal pivot pool.

Causal sequence:

1. the completed 480-minute parent auction ends before the shock;
2. the first meaningful penetration consumes an aged/prominent causal pivot
   pool in the parent's discount half for longs or premium half for shorts;
3. executed flow and price displacement attack against the parent while open
   interest contracts, identifying position liquidation rather than new
   counter-parent inventory;
4. within three later completed bars, price reclaims the exact pool with
   parent-side executed flow and price displacement;
5. the confirmation displacement is no larger than the liquidation shock, and
   the signed parent displacement is larger than the shock magnitude.

The pool is internal only when its level remains inside the completed parent
high/low. Stops lie beyond the complete shock/reclaim extreme. This compiler
emits timestamped intents only; NautilusTrader owns targets, orders, fills,
costs, positions, risk, margin, liquidation and NAV.
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
SCENARIO = "PARENT_ALIGNED_INTERNAL_LIQUIDATION_RESUMPTION"
PARENT_BARS = 480
CONFIRMATION_BARS = 3


@dataclass(frozen=True, slots=True)
class ParentAuction:
    side: int
    start_price: float
    end_price: float
    return_bps: float
    high: float
    low: float
    midpoint: float


def completed_parent_auction(
    data: pd.DataFrame,
    shock_index: int,
    bars: int = PARENT_BARS,
) -> ParentAuction | None:
    """Return the pre-shock parent auction; the shock cannot define its bias."""

    if shock_index <= bars:
        return None
    selected = data.iloc[shock_index - bars : shock_index]
    if len(selected) != bars:
        return None
    start = float(data["close"].iloc[shock_index - bars - 1])
    end = float(data["close"].iloc[shock_index - 1])
    high = float(selected["high"].max())
    low = float(selected["low"].min())
    values = (start, end, high, low)
    if not all(math.isfinite(value) for value in values):
        return None
    if start <= 0.0 or high <= low:
        return None
    return_bps = (end / start - 1.0) * 10_000.0
    if not math.isfinite(return_bps) or return_bps == 0.0:
        return None
    return ParentAuction(
        side=1 if return_bps > 0.0 else -1,
        start_price=start,
        end_price=end,
        return_bps=return_bps,
        high=high,
        low=low,
        midpoint=(high + low) / 2.0,
    )


def pool_is_internal_discount_or_premium(
    take: v24.PoolTake,
    parent: ParentAuction,
) -> bool:
    """Require the counter-parent pool to lie inside the parent dealing range."""

    if take.trade_side != parent.side:
        return False
    if not parent.low <= take.level <= parent.high:
        return False
    if parent.side > 0:
        return take.pool_side < 0 and take.level <= parent.midpoint
    return take.pool_side > 0 and take.level >= parent.midpoint


def non_climactic_confirmation(
    confirmation_return_bps: float,
    trade_side: int,
    shock_absolute_return_bps: float,
) -> bool:
    if trade_side not in (-1, 1):
        return False
    if not all(
        math.isfinite(value)
        for value in (confirmation_return_bps, shock_absolute_return_bps)
    ):
        return False
    directional = trade_side * confirmation_return_bps
    return 0.0 < directional <= shock_absolute_return_bps


def _take_quality(take: v24.PoolTake) -> tuple[float, int, int, float]:
    """Prefer the most established consumed pool when several overlap."""

    return (
        float(take.prominence_atr),
        int(take.age_bars),
        int(take.touches),
        float(take.penetration_atr),
    )


def detect_parent_internal_liquidation_intents(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    pool_takes = v24.detect_external_pool_takes(data, config)
    intents: list[Intent] = []
    counts = {
        "eligible_pool_takes": 0,
        "not_parent_internal": 0,
        "parent_weaker_than_shock": 0,
        "attack_not_counter_parent": 0,
        "open_interest_not_contracting": 0,
        "no_parent_reclaim": 0,
        "confirmed_resumption": 0,
        "duplicate_pool_candidates": 0,
    }

    for shock_index, takes in sorted(pool_takes.items()):
        timestamp = data.index[shock_index]
        if timestamp < evaluation_start or timestamp > evaluation_end:
            continue
        parent = completed_parent_auction(data, shock_index)
        if parent is None:
            continue

        shock_row = data.iloc[shock_index]
        shock_return = float(shock_row["ret_60s_bps"])
        shock_absolute_return = abs(shock_return)
        if not math.isfinite(shock_absolute_return) or shock_absolute_return <= 0.0:
            continue

        internal = [
            take
            for take in takes
            if pool_is_internal_discount_or_premium(take, parent)
        ]
        counts["eligible_pool_takes"] += len(takes)
        counts["not_parent_internal"] += len(takes) - len(internal)
        if not internal:
            continue
        if len(internal) > 1:
            counts["duplicate_pool_candidates"] += len(internal) - 1
        take = max(internal, key=_take_quality)

        if not v24.parent_dominates_shock(
            parent.return_bps,
            parent.side,
            shock_absolute_return,
        ):
            counts["parent_weaker_than_shock"] += 1
            continue

        attack_flow = take.pool_side * float(shock_row["flow_60s"])
        attack_return = take.pool_side * shock_return
        if not (
            math.isfinite(attack_flow)
            and math.isfinite(attack_return)
            and attack_flow > 0.0
            and attack_return > 0.0
        ):
            counts["attack_not_counter_parent"] += 1
            continue

        oi_change = float(shock_row["oi_change_xday_15m"])
        if not math.isfinite(oi_change) or oi_change >= 0.0:
            counts["open_interest_not_contracting"] += 1
            continue

        confirmation_index: int | None = None
        confirmation_flow = float("nan")
        confirmation_return = float("nan")
        upper = min(shock_index + CONFIRMATION_BARS, len(data) - 2)
        for index in range(shock_index + 1, upper + 1):
            row = data.iloc[index]
            close = float(row["close"])
            if not v24.pool_is_reclaimed(take, close):
                continue
            flow = parent.side * float(row["flow_60s"])
            signed_return = float(row["ret_60s_bps"])
            if not (
                math.isfinite(flow)
                and flow > 0.0
                and non_climactic_confirmation(
                    signed_return,
                    parent.side,
                    shock_absolute_return,
                )
            ):
                continue
            confirmation_index = index
            confirmation_flow = flow
            confirmation_return = signed_return
            break

        if confirmation_index is None:
            counts["no_parent_reclaim"] += 1
            continue
        if data.index[confirmation_index] > evaluation_end:
            continue

        segment = data.iloc[shock_index : confirmation_index + 1]
        extreme = float(
            segment["low"].min()
            if parent.side > 0
            else segment["high"].max()
        )
        atr = float(data["atr"].iloc[confirmation_index])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        stop_level = (
            extreme
            - parent.side * float(impact_parameters.stop_buffer_atr) * atr
        )
        details = {
            "parent_bars": PARENT_BARS,
            "parent_side": parent.side,
            "parent_start_price": parent.start_price,
            "parent_end_price": parent.end_price,
            "parent_return_bps": parent.return_bps,
            "parent_high": parent.high,
            "parent_low": parent.low,
            "parent_midpoint": parent.midpoint,
            "parent_displacement_to_shock_ratio": (
                parent.side * parent.return_bps / shock_absolute_return
            ),
            "pool_id": take.pool_id,
            "pool_side": take.pool_side,
            "pool_level": take.level,
            "pool_age_bars": take.age_bars,
            "pool_prominence_atr": take.prominence_atr,
            "pool_touches": take.touches,
            "pool_penetration_atr": take.penetration_atr,
            "pool_location": "PARENT_DISCOUNT" if parent.side > 0 else "PARENT_PREMIUM",
            "shock_index": shock_index,
            "shock_absolute_return_bps": shock_absolute_return,
            "counter_parent_attack_flow_60s": attack_flow,
            "counter_parent_attack_return_60s_bps": attack_return,
            "shock_open_interest_change_15m": oi_change,
            "confirmation_index": confirmation_index,
            "confirmation_delay_bars": confirmation_index - shock_index,
            "parent_reclaim_flow_60s": confirmation_flow,
            "parent_reclaim_return_60s_bps": confirmation_return,
            "confirmation_to_shock_return_ratio": (
                abs(confirmation_return) / shock_absolute_return
            ),
            "compiler": "candidate-04-parent-internal-liquidation-v1",
        }
        intents.append(
            Intent(
                scenario=SCENARIO,
                side=parent.side,
                signal_index=confirmation_index,
                entry_index=confirmation_index + 1,
                stop_level=stop_level,
                event_indices=(shock_index, confirmation_index),
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
    intents, counts = detect_parent_internal_liquidation_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    return intents, {
        "candidate": "candidate-04-parent-internal-liquidation-resumption-v1",
        "compiler": "candidate-04-parent-internal-liquidation-v1",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(intents),
        "route_counts": counts,
        "scenario_contract": {
            "parent": "completed pre-shock 480-minute auction",
            "liquidity": (
                "first penetration of an aged/prominent causal pivot inside "
                "parent discount for longs or premium for shorts"
            ),
            "liquidation": (
                "counter-parent executed flow and return with negative "
                "15-minute open-interest change"
            ),
            "reclaim": (
                "exact pool reclaimed within three completed bars with "
                "parent-side executed flow and return"
            ),
            "relative_strength": (
                "parent displacement > shock magnitude and confirmation "
                "displacement <= shock magnitude"
            ),
            "invalidation": "complete shock/reclaim extreme plus ATR buffer",
            "target_and_execution": "causal external liquidity through NautilusTrader",
        },
        "constants": {
            "parent_bars": PARENT_BARS,
            "confirmation_bars": CONFIRMATION_BARS,
            "stop_buffer_atr": float(impact_parameters.stop_buffer_atr),
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
