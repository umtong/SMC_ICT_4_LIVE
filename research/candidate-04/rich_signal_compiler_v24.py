#!/usr/bin/env python3
"""V24 causal rich-state compiler: separate pool failure from parent resumption.

The compiler emits completed-data scenario intents only. NautilusTrader remains
sole owner of orders, fills, positions, fees, PnL, margin, liquidation and NAV.

V23's ``PARENT_AUCTION_SHOCK_REACCEPTANCE`` branch was removed by a controlled
ablation: both observed trades stopped within five minutes, and deleting only
that state improved both affected weeks without changing any execution input.
V24 therefore makes impact events mutually exclusive by their economic cause:

* external-pool failed discovery: the shock is the first meaningful penetration
  of an already confirmed, aged and prominent pivot pool, and the completed
  confirmation closes back inside that exact pool;
* parent-auction interruption/resumption: no eligible external pool is taken,
  and the completed 480-minute parent move in the intended trade direction is
  larger than the shock itself.

A rolling high/low is not treated as liquidity merely because it is extreme.
Pools use the same causal pivot observation, merge, age, prominence, expiry and
first-penetration semantics as the frozen V5 detector. All thresholds remain
past-only; the parent state ends before the shock.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401  # installs warmup-aware loader
import rich_signal_compiler_v23 as v23


Intent = v22.Intent


@dataclass(slots=True)
class _Pool:
    side: int
    level: float
    created_index: int
    observed_index: int
    last_touch_index: int
    touches: int
    pool_id: int
    prominence_atr: float
    active: bool = True


@dataclass(frozen=True, slots=True)
class PoolTake:
    shock_index: int
    pool_id: int
    pool_side: int
    trade_side: int
    level: float
    extreme: float
    penetration_atr: float
    age_bars: int
    prominence_atr: float
    touches: int


def _copy_intent(parent: Intent, scenario: str, details: dict[str, Any]) -> Intent:
    return Intent(
        scenario=scenario,
        side=int(parent.side),
        signal_index=int(parent.signal_index),
        entry_index=int(parent.entry_index),
        stop_level=float(parent.stop_level),
        event_indices=tuple(int(value) for value in parent.event_indices),
        details=details,
    )


def _shifted_quantile(
    series: pd.Series,
    quantile: float,
    window: int,
    minimum: int,
) -> pd.Series:
    return (
        series.astype(float)
        .replace([math.inf, -math.inf], float("nan"))
        .shift(1)
        .rolling(window, min_periods=minimum)
        .quantile(quantile)
    )


def detect_external_pool_takes(
    data: pd.DataFrame,
    config: Any,
) -> dict[int, list[PoolTake]]:
    """Return eligible first penetrations of causal confirmed pivot pools.

    The current bar cannot create the pool it takes: a pivot becomes observable
    only after ``pivot_right`` completed bars. The first meaningful penetration
    consumes the pool even when it is too young or insufficiently prominent,
    matching the frozen V5 semantics and preventing repeated hindsight reuse.
    """

    pools: list[_Pool] = []
    active: list[_Pool] = []
    takes: dict[int, list[PoolTake]] = {}
    pool_id = 0
    left = int(config.pivot_left)
    right = int(config.pivot_right)

    for index in range(left + right, len(data)):
        for pool in active:
            if (
                pool.active
                and index - pool.last_touch_index > int(config.pool_max_age_minutes)
            ):
                pool.active = False

        center = index - right
        window = data.iloc[center - left : center + right + 1]
        pivot_atr = float(data["atr"].iloc[center])
        if math.isfinite(pivot_atr) and pivot_atr > 0.0:
            high = float(data["high"].iloc[center])
            low = float(data["low"].iloc[center])
            candidates: list[tuple[int, float, float]] = []
            if (
                high >= float(window["high"].max())
                and int((window["high"] == high).sum()) == 1
            ):
                neighbour = min(
                    high - float(window["high"].iloc[:left].max()),
                    high - float(window["high"].iloc[left + 1 :].max()),
                )
                candidates.append((1, high, neighbour / pivot_atr))
            if (
                low <= float(window["low"].min())
                and int((window["low"] == low).sum()) == 1
            ):
                neighbour = min(
                    float(window["low"].iloc[:left].min()) - low,
                    float(window["low"].iloc[left + 1 :].min()) - low,
                )
                candidates.append((-1, low, neighbour / pivot_atr))

            for side, price, prominence in candidates:
                nearby = [
                    pool
                    for pool in active
                    if pool.active
                    and pool.side == side
                    and abs(pool.level - price)
                    <= float(config.pool_merge_atr) * pivot_atr
                ]
                if nearby:
                    pool = min(nearby, key=lambda item: abs(item.level - price))
                    pool.level = (
                        pool.level * pool.touches + price
                    ) / (pool.touches + 1)
                    pool.touches += 1
                    pool.last_touch_index = center
                    pool.prominence_atr = max(pool.prominence_atr, prominence)
                else:
                    pool_id += 1
                    pool = _Pool(
                        side=side,
                        level=price,
                        created_index=center,
                        observed_index=index,
                        last_touch_index=center,
                        touches=1,
                        pool_id=pool_id,
                        prominence_atr=prominence,
                    )
                    pools.append(pool)
                    active.append(pool)

        row = data.iloc[index]
        atr = float(row["atr"])
        if not math.isfinite(atr) or atr <= 0.0:
            continue

        for pool in list(active):
            if not pool.active:
                continue
            age = index - pool.observed_index
            eligible = (
                age >= int(config.pool_min_age_minutes)
                and pool.prominence_atr >= float(config.pool_min_prominence_atr)
            )
            if pool.side > 0:
                penetration = (float(row["high"]) - pool.level) / atr
                extreme = float(row["high"])
                trade_side = -1
            else:
                penetration = (pool.level - float(row["low"])) / atr
                extreme = float(row["low"])
                trade_side = 1
            taken = penetration >= float(config.sweep_min_atr)
            if not taken:
                continue
            if eligible:
                takes.setdefault(index, []).append(
                    PoolTake(
                        shock_index=index,
                        pool_id=pool.pool_id,
                        pool_side=pool.side,
                        trade_side=trade_side,
                        level=pool.level,
                        extreme=extreme,
                        penetration_atr=penetration,
                        age_bars=age,
                        prominence_atr=pool.prominence_atr,
                        touches=pool.touches,
                    ),
                )
            pool.active = False

    return takes


def pool_is_reclaimed(take: PoolTake, confirmation_close: float) -> bool:
    return (
        confirmation_close < take.level
        if take.pool_side > 0
        else confirmation_close > take.level
    )


def parent_dominates_shock(
    parent_return_bps: float,
    trade_side: int,
    shock_absolute_return_bps: float,
) -> bool:
    """Require intended parent displacement to exceed the interruption."""

    values = (parent_return_bps, shock_absolute_return_bps)
    if not all(math.isfinite(value) for value in values):
        return False
    if shock_absolute_return_bps <= 0.0 or trade_side not in (-1, 1):
        return False
    return trade_side * parent_return_bps > shock_absolute_return_bps


def _select_reclaimed_pool(
    candidates: list[PoolTake],
    trade_side: int,
    confirmation_close: float,
) -> PoolTake | None:
    matching = [
        take
        for take in candidates
        if take.trade_side == trade_side
        and pool_is_reclaimed(take, confirmation_close)
    ]
    if not matching:
        return None
    return max(
        matching,
        key=lambda item: (item.age_bars, item.prominence_atr, item.touches),
    )


def _collect_v23_non_impact(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> tuple[list[Intent], dict[str, int]]:
    """Preserve V23's validated non-impact states unchanged."""

    close = data["close"].astype(float)
    one_minute_path = close.pct_change(fill_method=None).abs()
    path_240 = one_minute_path.rolling(240, min_periods=240).sum() * 10_000.0
    window = int(config.stress_inventory_quantile_window_minutes)
    minimum = int(config.stress_inventory_quantile_min_periods)
    path_median = _shifted_quantile(path_240, 0.50, window, minimum)
    basis = data["trade_index_basis_bps"].astype(float)
    basis_q10 = _shifted_quantile(basis, 0.10, window, minimum)
    basis_q90 = _shifted_quantile(basis, 0.90, window, minimum)
    abs_return_q80 = _shifted_quantile(
        data["ret_60s_bps"].astype(float).abs(),
        0.80,
        int(impact_parameters.quantile_window_minutes),
        int(impact_parameters.quantile_min_periods),
    )

    swing, _ = v22.v9.v8.v7.v6.detect_swing_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    v22.v9.v8.detect_mesoscale_inventory_intents.original_detector = (
        v22.v9.v8.v7.v6.detect_trend_intents
    )
    trend, _ = v22.v9.v8.detect_mesoscale_inventory_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
    )
    v22.v9.filter_reversal_failure_intents.original_detector = (
        v22.v9.v8.v7.v6.detect_stress_failure_intents
    )
    stress_failure, _ = v22.v9.filter_reversal_failure_intents(
        data,
        swing,
        config,
    )

    routed: list[Intent] = []
    counts = {
        "normal_failed_auction": 0,
        "orderly_inventory": 0,
        "normal_inventory_climactic_rejected": 0,
        "stress_tail_acceptance": 0,
        "stress_weak_terminal_rejected": 0,
    }

    for parent in swing:
        index = int(parent.signal_index)
        regime = float(v22.v9.v8.v7.v6.basis_regime(data, index, config))
        if regime < float(config.basis_stress_threshold_bps):
            continue
        routed.append(
            _copy_intent(
                parent,
                "NORMAL_FAILED_AUCTION_RESUMPTION",
                {
                    **parent.details,
                    "basis_regime_bps": regime,
                    "trade_index_basis_bps": float(basis.iloc[index]),
                    "compiler": "candidate-04-v24",
                },
            ),
        )
        counts["normal_failed_auction"] += 1

    oi_change = data["oi_change_xday_15m"].astype(float)
    for parent in trend:
        index = int(parent.signal_index)
        regime = float(v22.v9.v8.v7.v6.basis_regime(data, index, config))
        if regime < float(config.basis_stress_threshold_bps):
            continue
        values = (
            float(path_240.iloc[index]),
            float(path_median.iloc[index]),
            float(basis.iloc[index]),
            float(basis_q10.iloc[index]),
            float(basis_q90.iloc[index]),
            float(oi_change.iloc[index]),
        )
        finite = all(math.isfinite(value) for value in values)
        orderly = (
            finite
            and values[0] <= values[1]
            and values[3] <= values[2] <= values[4]
        )
        details = {
            **parent.details,
            "basis_regime_bps": regime,
            "auction_path_240m_bps": values[0],
            "past_only_path_240m_median_bps": values[1],
            "trade_index_basis_bps": values[2],
            "past_only_basis_q10_bps": values[3],
            "past_only_basis_q90_bps": values[4],
            "raw_oi_change_15m": values[5],
            "orderly_parent_auction": orderly,
            "compiler": "candidate-04-v24",
        }
        if orderly:
            routed.append(
                _copy_intent(parent, "ORDERLY_INVENTORY_DISPLACEMENT", details),
            )
            counts["orderly_inventory"] += 1
        else:
            counts["normal_inventory_climactic_rejected"] += 1

    for parent in stress_failure:
        index = int(parent.signal_index)
        current_basis = float(basis.iloc[index])
        directional_return = int(parent.side) * float(
            data["ret_60s_bps"].iloc[index]
        )
        cutoff = float(abs_return_q80.iloc[index])
        passed = (
            math.isfinite(current_basis)
            and current_basis < 0.0
            and math.isfinite(directional_return)
            and math.isfinite(cutoff)
            and directional_return >= cutoff
        )
        details = {
            **parent.details,
            "trade_index_basis_bps": current_basis,
            "terminal_directional_return_60s_bps": directional_return,
            "past_only_absolute_return_q80_bps": cutoff,
            "terminal_tail_acceptance": passed,
            "compiler": "candidate-04-v24",
        }
        if passed:
            routed.append(
                _copy_intent(
                    parent,
                    "TAIL_CONFIRMED_STRESS_FAILED_AUCTION",
                    details,
                ),
            )
            counts["stress_tail_acceptance"] += 1
        else:
            counts["stress_weak_terminal_rejected"] += 1

    return routed, counts


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    routed, counts = _collect_v23_non_impact(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )
    counts.update(
        {
            "external_pool_failed_discovery": 0,
            "parent_auction_interruption_resumption": 0,
            "external_pool_not_reclaimed": 0,
            "parent_weaker_than_shock": 0,
            "positive_basis_impact_rejected": 0,
        },
    )

    basis = data["trade_index_basis_bps"].astype(float)
    close = data["close"].astype(float)
    pool_takes = detect_external_pool_takes(data, config)
    impact, _ = v22.v10.detect_impact_exhaustion_intents(
        data,
        evaluation_start,
        evaluation_end,
        config,
        impact_parameters,
    )

    for parent in impact:
        index = int(parent.signal_index)
        current_basis = float(basis.iloc[index])
        if not math.isfinite(current_basis) or current_basis >= 0.0:
            counts["positive_basis_impact_rejected"] += 1
            continue

        shock_index = int(parent.details["shock_index"])
        trade_side = int(parent.side)
        shock_return = float(parent.details["absolute_return_bps"])
        confirmation_close = float(close.iloc[index])
        shock_pool_takes = pool_takes.get(shock_index, [])
        matching_pool_takes = [
            take for take in shock_pool_takes if take.trade_side == trade_side
        ]
        selected_pool = _select_reclaimed_pool(
            shock_pool_takes,
            trade_side,
            confirmation_close,
        )

        parent_return = v23._impact_parent_return_bps(close, shock_index)
        if selected_pool is not None:
            details = {
                **parent.details,
                "trade_index_basis_bps": current_basis,
                "impact_route": "EXTERNAL_POOL_FAILED_DISCOVERY",
                "external_pool_id": selected_pool.pool_id,
                "external_pool_side": selected_pool.pool_side,
                "external_pool_level": selected_pool.level,
                "external_pool_age_bars": selected_pool.age_bars,
                "external_pool_prominence_atr": selected_pool.prominence_atr,
                "external_pool_touches": selected_pool.touches,
                "external_pool_penetration_atr": selected_pool.penetration_atr,
                "external_pool_reclaimed_close": confirmation_close,
                "pre_shock_parent_480m_return_bps": parent_return,
                "impact_absolute_return_bps": shock_return,
                "compiler": "candidate-04-v24",
            }
            routed.append(
                _copy_intent(
                    parent,
                    "EXTERNAL_POOL_FAILED_DISCOVERY_REVERSAL",
                    details,
                ),
            )
            counts["external_pool_failed_discovery"] += 1
            continue

        if matching_pool_takes:
            counts["external_pool_not_reclaimed"] += 1
            continue

        if parent_dominates_shock(parent_return, trade_side, shock_return):
            details = {
                **parent.details,
                "trade_index_basis_bps": current_basis,
                "impact_route": "PARENT_AUCTION_INTERRUPTION_RESUMPTION",
                "eligible_external_pool_taken": False,
                "pre_shock_parent_480m_return_bps": parent_return,
                "trade_side_parent_return_bps": trade_side * parent_return,
                "impact_absolute_return_bps": shock_return,
                "parent_displacement_to_shock_ratio": (
                    trade_side * parent_return / shock_return
                ),
                "compiler": "candidate-04-v24",
            }
            routed.append(
                _copy_intent(
                    parent,
                    "PARENT_AUCTION_INTERRUPTION_RESUMPTION",
                    details,
                ),
            )
            counts["parent_auction_interruption_resumption"] += 1
        else:
            counts["parent_weaker_than_shock"] += 1

    priority = {
        "EXTERNAL_POOL_FAILED_DISCOVERY_REVERSAL": 0,
        "PARENT_AUCTION_INTERRUPTION_RESUMPTION": 1,
        "NORMAL_FAILED_AUCTION_RESUMPTION": 2,
        "TAIL_CONFIRMED_STRESS_FAILED_AUCTION": 3,
        "ORDERLY_INVENTORY_DISPLACEMENT": 4,
    }
    routed.sort(
        key=lambda item: (
            int(item.signal_index),
            priority.get(item.scenario, 99),
        ),
    )
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in routed:
        index = int(intent.signal_index)
        if index in seen:
            continue
        seen.add(index)
        unique.append(intent)

    return unique, {
        "candidate": "candidate-04-v24-causal-impact-split",
        "compiler": "candidate-04-v24",
        "raw_routed_signals": len(routed),
        "unique_signal_bars": len(unique),
        "route_counts": counts,
        "impact_parameters": asdict(impact_parameters),
        "impact_contract": {
            "external_pool": (
                "first eligible causal pivot-pool penetration plus completed "
                "close back inside the exact pool"
            ),
            "parent_resumption": (
                "no eligible external pool taken and signed completed 480m "
                "parent displacement greater than shock magnitude"
            ),
            "removed": "PARENT_AUCTION_SHOCK_REACCEPTANCE",
        },
    }


v22.collect_signals = collect_signals


if __name__ == "__main__":
    v22.main()
