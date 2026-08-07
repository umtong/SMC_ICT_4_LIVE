#!/usr/bin/env python3
"""Parent-session liquidity transfer with causal target registry (V43).

This is an SMC/ICT scenario compiler, not a backtest engine.

A completed eight-hour parent auction publishes two external liquidity levels:
its high and low. The next session may resolve a first boundary attack in one of
two mutually exclusive ways.

Reversal / failed auction
-------------------------
1. aggressive flow first penetrates one completed parent-session boundary;
2. price response is weak relative to past flow divided by pre-event opposing
   depth (negative impact innovation), or passive opposing depth replenishes;
3. a later completed minute closes back inside the exact boundary and breaks
   pre-attack internal structure with opposite flow, return and basis change;
4. attack inventory is shown as trapped new OI, liquidation exhaustion, or a
   stronger passive-absorption state; and
5. the target is a still-active right-confirmed pivot pool or the untouched
   opposite parent-session boundary, both observed before the attack.

Continuation / accepted auction
-------------------------------
1. the first boundary attack closes outside with positive impact innovation,
   aligned basis and material OI creation;
2. a separate completed counter-flow/counter-price retest touches the boundary
   but accepts outside it while most created inventory remains; and
3. a later completed resumption minute breaks the retest structure with aligned
   flow, return and basis toward a pre-attack active external pool.

Stops lie beyond the complete attack-to-confirmation excursion. All target
references are declared with their causal observation index and are revalidated
by the NautilusTrader execution strategy. NautilusTrader alone owns orders,
fills, fees, positions, current-NAV 3% sizing, PnL and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Iterable

import numpy as np
import pandas as pd

import rich_signal_compiler_v22 as v22
import rich_signal_compiler_v22b  # noqa: F401
import volume_clock_impact_residual_compiler as v37
from nt_liquidity_strategy import net_r_at_price

Intent = v22.Intent

SESSION_REVERSAL_NEW = "PARENT_SESSION_TRAPPED_INVENTORY_LIQUIDITY_REVERSAL"
SESSION_REVERSAL_LIQUIDATION = "PARENT_SESSION_LIQUIDATION_EXHAUSTION_REVERSAL"
SESSION_REVERSAL_PASSIVE = "PARENT_SESSION_PASSIVE_ABSORPTION_REVERSAL"
SESSION_CONTINUATION = "PARENT_SESSION_BOUNDARY_ACCEPTANCE_CONTINUATION"

SESSION_HOURS = 8
IMPACT_WINDOW = 720
IMPACT_MINIMUM = 240
IMPACT_PRESSURE_QUANTILE = 0.50
ABSORPTION_INNOVATION_Z = -1.00
PASSIVE_ABSORPTION_Z = -1.50
ACCEPTANCE_INNOVATION_Z = 1.00
CONFIRMATION_INNOVATION_Z = 0.00
OI_QUANTILE = 0.65
OI_WINDOW = 720
OI_MINIMUM = 240
INVENTORY_RETENTION_FRACTION = 0.75
INVENTORY_UNWIND_FRACTION = 0.50
LIQUIDATION_REBUILD_FRACTION = 0.20
MINIMUM_TARGET_NET_R = 1.20
COST_RATE = 0.00075
MAX_DEPTH_AGE_SECONDS = 45.0
MIN_IMPACT_SCALE = 1e-6


@dataclass(frozen=True, slots=True)
class ParentSession:
    current_start: pd.Timestamp
    observed_index: int
    high: float
    low: float
    high_origin_index: int
    low_origin_index: int


@dataclass(frozen=True, slots=True)
class CausalPool:
    pool_id: int
    side: int
    level: float
    created_index: int
    observed_index: int
    last_touch_index: int
    touches: int
    prominence_atr: float
    active: bool = True


@dataclass(frozen=True, slots=True)
class TargetReference:
    price: float
    source: str
    observed_index: int
    net_r: float


def finite(value: Any) -> float:
    return v37.finite(value)


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


def impact_state(data: pd.DataFrame) -> pd.DataFrame:
    """Past-only impact slope innovation for each completed minute."""

    flow = data["flow_60s"].astype(float)
    notional = data["notional_60s"].astype(float).clip(lower=0.0)
    age = data["depth_snapshot_age_seconds"].astype(float).shift(1)
    ask = data["ask_depth_1"].astype(float).shift(1)
    bid = data["bid_depth_1"].astype(float).shift(1)
    opposing = pd.Series(
        np.where(flow >= 0.0, ask, bid),
        index=data.index,
        dtype=float,
    )
    opposing = opposing.where(age <= MAX_DEPTH_AGE_SECONDS)
    signed_pressure = flow * notional / opposing.replace(0.0, np.nan)
    absolute_pressure = signed_pressure.abs()
    directional_return = np.sign(signed_pressure) * data["ret_60s_bps"].astype(float)
    slope = directional_return / absolute_pressure.replace(0.0, np.nan)
    center = (
        slope.shift(1)
        .rolling(IMPACT_WINDOW, min_periods=IMPACT_MINIMUM)
        .median()
    )
    deviation = (slope - center).abs()
    scale = (
        deviation.shift(1)
        .rolling(IMPACT_WINDOW, min_periods=IMPACT_MINIMUM)
        .median()
        * 1.4826
    ).clip(lower=MIN_IMPACT_SCALE)
    pressure_cutoff = _shifted_quantile(
        absolute_pressure,
        IMPACT_PRESSURE_QUANTILE,
        IMPACT_WINDOW,
        IMPACT_MINIMUM,
    )
    return pd.DataFrame(
        {
            "signed_pressure": signed_pressure,
            "absolute_pressure": absolute_pressure,
            "impact_slope": slope,
            "impact_slope_center": center,
            "impact_slope_scale": scale,
            "impact_innovation_z": (slope - center) / scale,
            "pressure_cutoff": pressure_cutoff,
        },
        index=data.index,
    )


def completed_parent_sessions(data: pd.DataFrame) -> list[ParentSession | None]:
    """Map each row to the immediately preceding completed eight-hour auction."""

    session_key = data.index.floor(f"{SESSION_HOURS}h")
    groups: dict[pd.Timestamp, list[int]] = {}
    for index, key in enumerate(session_key):
        groups.setdefault(key, []).append(index)
    ordered = sorted(groups)
    context_by_key: dict[pd.Timestamp, ParentSession] = {}
    for position in range(1, len(ordered)):
        current = ordered[position]
        previous = ordered[position - 1]
        previous_indices = groups[previous]
        current_indices = groups[current]
        frame = data.iloc[previous_indices]
        high_origin = int(frame["high"].astype(float).to_numpy().argmax())
        low_origin = int(frame["low"].astype(float).to_numpy().argmin())
        context_by_key[current] = ParentSession(
            current_start=current,
            observed_index=current_indices[0] - 1,
            high=float(frame["high"].max()),
            low=float(frame["low"].min()),
            high_origin_index=previous_indices[high_origin],
            low_origin_index=previous_indices[low_origin],
        )
    return [context_by_key.get(key) for key in session_key]


def _pivot_candidate(
    data: pd.DataFrame,
    center: int,
    left: int,
    right: int,
) -> list[tuple[int, float, float]]:
    if center - left < 0 or center + right >= len(data):
        return []
    window = data.iloc[center - left : center + right + 1]
    atr = finite(data["atr"].iloc[center])
    if not math.isfinite(atr) or atr <= 0.0:
        return []
    high = finite(data["high"].iloc[center])
    low = finite(data["low"].iloc[center])
    result: list[tuple[int, float, float]] = []
    if high >= float(window["high"].max()) and int((window["high"] == high).sum()) == 1:
        neighbour = min(
            high - float(window["high"].iloc[:left].max()),
            high - float(window["high"].iloc[left + 1 :].max()),
        )
        result.append((1, high, neighbour / atr))
    if low <= float(window["low"].min()) and int((window["low"] == low).sum()) == 1:
        neighbour = min(
            float(window["low"].iloc[:left].min()) - low,
            float(window["low"].iloc[left + 1 :].min()) - low,
        )
        result.append((-1, low, neighbour / atr))
    return result


def active_causal_pool_snapshots(
    data: pd.DataFrame,
    config: Any,
) -> list[tuple[CausalPool, ...]]:
    """Register, merge, age and consume right-confirmed pivot liquidity pools."""

    active: list[CausalPool] = []
    snapshots: list[tuple[CausalPool, ...]] = []
    pool_id = 0
    left = int(config.pivot_left)
    right = int(config.pivot_right)
    for index in range(len(data)):
        surviving = [
            pool
            for pool in active
            if pool.active
            and index - pool.last_touch_index <= int(config.pool_max_age_minutes)
        ]
        active = surviving
        center = index - right
        if center >= left:
            atr = finite(data["atr"].iloc[center])
            for side, price, prominence in _pivot_candidate(
                data, center, left, right
            ):
                nearby = [
                    pool
                    for pool in active
                    if pool.side == side
                    and math.isfinite(atr)
                    and abs(pool.level - price)
                    <= float(config.pool_merge_atr) * atr
                ]
                if nearby:
                    selected = min(
                        nearby,
                        key=lambda pool: abs(pool.level - price),
                    )
                    active.remove(selected)
                    merged = replace(
                        selected,
                        level=(selected.level * selected.touches + price)
                        / (selected.touches + 1),
                        last_touch_index=center,
                        touches=selected.touches + 1,
                        prominence_atr=max(
                            selected.prominence_atr,
                            prominence,
                        ),
                    )
                    active.append(merged)
                else:
                    pool_id += 1
                    active.append(
                        CausalPool(
                            pool_id=pool_id,
                            side=side,
                            level=price,
                            created_index=center,
                            observed_index=index,
                            last_touch_index=center,
                            touches=1,
                            prominence_atr=prominence,
                        )
                    )

        atr = finite(data["atr"].iloc[index])
        if math.isfinite(atr) and atr > 0.0:
            high = finite(data["high"].iloc[index])
            low = finite(data["low"].iloc[index])
            kept: list[CausalPool] = []
            for pool in active:
                penetration = (
                    (high - pool.level) / atr
                    if pool.side > 0
                    else (pool.level - low) / atr
                )
                if penetration < float(config.sweep_min_atr):
                    kept.append(pool)
            active = kept
        snapshots.append(tuple(active))
    return snapshots


def parent_boundary_first_takes(
    data: pd.DataFrame,
    sessions: list[ParentSession | None],
    config: Any,
) -> tuple[dict[tuple[pd.Timestamp, int], int], dict[int, tuple[int, float]]]:
    """Return first high/low take per current parent session."""

    first: dict[tuple[pd.Timestamp, int], int] = {}
    attacks: dict[int, tuple[int, float]] = {}
    for index, session in enumerate(sessions):
        if session is None:
            continue
        atr = finite(data["atr"].iloc[index])
        if not math.isfinite(atr) or atr <= 0.0:
            continue
        high_taken = (
            finite(data["high"].iloc[index]) - session.high
        ) / atr >= float(config.sweep_min_atr)
        low_taken = (
            session.low - finite(data["low"].iloc[index])
        ) / atr >= float(config.sweep_min_atr)
        if high_taken and low_taken:
            first.setdefault((session.current_start, 1), index)
            first.setdefault((session.current_start, -1), index)
            continue
        if high_taken:
            key = (session.current_start, 1)
            if key not in first:
                first[key] = index
                attacks[index] = (1, session.high)
        elif low_taken:
            key = (session.current_start, -1)
            if key not in first:
                first[key] = index
                attacks[index] = (-1, session.low)
    return first, attacks


def target_net_r(
    entry: float,
    stop: float,
    target: float,
    side: int,
) -> float:
    price_loss = side * (entry - stop)
    planned_loss = price_loss + COST_RATE * (entry + stop)
    if price_loss <= 0.0 or planned_loss <= 0.0:
        return float("nan")
    return net_r_at_price(entry, target, side, planned_loss, COST_RATE)


def choose_causal_target(
    *,
    data: pd.DataFrame,
    snapshots: list[tuple[CausalPool, ...]],
    sessions: list[ParentSession | None],
    boundary_takes: dict[tuple[pd.Timestamp, int], int],
    attack_index: int,
    signal_index: int,
    side: int,
    stop: float,
    config: Any,
) -> TargetReference | None:
    entry = finite(data["close"].iloc[signal_index])
    candidates: list[tuple[float, str, int]] = []
    for pool in snapshots[signal_index]:
        age_at_attack = attack_index - pool.observed_index
        if (
            pool.observed_index >= attack_index
            or age_at_attack < int(config.pool_min_age_minutes)
            or pool.prominence_atr < float(config.pool_min_prominence_atr)
            or pool.side != side
            or side * (pool.level - entry) <= 0.0
        ):
            continue
        source = (
            f"causal_pivot_pool_{pool.pool_id}_"
            f"{'high' if pool.side > 0 else 'low'}"
        )
        candidates.append((pool.level, source, pool.observed_index))

    session = sessions[attack_index]
    if session is not None:
        target_side = side
        key = (session.current_start, target_side)
        first_take = boundary_takes.get(key)
        if first_take is None or first_take > signal_index:
            price = session.high if target_side > 0 else session.low
            origin = (
                session.high_origin_index
                if target_side > 0
                else session.low_origin_index
            )
            if side * (price - entry) > 0.0 and origin < attack_index:
                source = (
                    "completed_parent_session_high"
                    if target_side > 0
                    else "completed_parent_session_low"
                )
                candidates.append((price, source, session.observed_index))

    ordered = sorted(candidates, key=lambda item: side * (item[0] - entry))
    for price, source, observed in ordered:
        net_r = target_net_r(entry, stop, price, side)
        if math.isfinite(net_r) and net_r >= MINIMUM_TARGET_NET_R:
            return TargetReference(price, source, observed, net_r)
    return None


def _directional_close_location(data: pd.DataFrame, index: int, side: int) -> float:
    high = finite(data["high"].iloc[index])
    low = finite(data["low"].iloc[index])
    close = finite(data["close"].iloc[index])
    width = high - low
    if not math.isfinite(width) or width <= 0.0:
        return float("nan")
    return (close - low) / width if side > 0 else (high - close) / width


def _impact_aligned(
    impact: pd.DataFrame,
    data: pd.DataFrame,
    index: int,
    side: int,
    minimum_z: float,
) -> bool:
    values = (
        finite(impact["signed_pressure"].iloc[index]),
        finite(impact["absolute_pressure"].iloc[index]),
        finite(impact["pressure_cutoff"].iloc[index]),
        finite(impact["impact_innovation_z"].iloc[index]),
        finite(data["flow_60s"].iloc[index]),
        finite(data["ret_60s_bps"].iloc[index]),
        finite(data["basis_change_1m"].iloc[index]),
    )
    if not all(math.isfinite(value) for value in values):
        return False
    pressure, absolute, cutoff, innovation, flow, ret, basis = values
    return (
        side * pressure > 0.0
        and absolute >= cutoff
        and innovation >= minimum_z
        and side * flow > 0.0
        and side * ret > 0.0
        and side * basis > 0.0
    )


def _attack_inventory_route(
    data: pd.DataFrame,
    impact: pd.DataFrame,
    index: int,
    side: int,
    oi_cutoff: pd.Series,
) -> tuple[str | None, dict[str, float]]:
    before_index = max(0, index - 5)
    before = finite(data["metric_sum_open_interest"].iloc[before_index])
    after = finite(data["metric_sum_open_interest"].iloc[index])
    cutoff = finite(oi_cutoff.iloc[index])
    oi_change = after / before - 1.0 if before > 0.0 and after > 0.0 else float("nan")
    innovation = finite(impact["impact_innovation_z"].iloc[index])
    replenishment_column = "ask_chg_1_60s" if side > 0 else "bid_chg_1_60s"
    replenishment = finite(data[replenishment_column].iloc[index])
    route: str | None = None
    if all(math.isfinite(value) for value in (oi_change, cutoff)) and cutoff > 0.0:
        if oi_change >= cutoff:
            route = "NEW_INVENTORY"
        elif oi_change <= -cutoff:
            route = "LIQUIDATION"
    if (
        route is None
        and math.isfinite(innovation)
        and innovation <= PASSIVE_ABSORPTION_Z
        and math.isfinite(replenishment)
        and replenishment >= 0.0
    ):
        route = "PASSIVE_ABSORPTION"
    return route, {
        "attack_oi_before": before,
        "attack_oi_end": after,
        "attack_oi_change": oi_change,
        "attack_oi_cutoff": cutoff,
        "opposing_depth_replenishment_60s": replenishment,
    }


def _inventory_reversal_resolved(
    route: str,
    diagnostics: dict[str, float],
    later_oi: float,
) -> bool:
    before = diagnostics["attack_oi_before"]
    end = diagnostics["attack_oi_end"]
    if route == "PASSIVE_ABSORPTION":
        return True
    if not all(math.isfinite(value) for value in (before, end, later_oi)):
        return False
    if route == "NEW_INVENTORY":
        created = end - before
        return created > 0.0 and later_oi <= end - INVENTORY_UNWIND_FRACTION * created
    if route == "LIQUIDATION":
        depleted = before - end
        return depleted > 0.0 and later_oi <= end + LIQUIDATION_REBUILD_FRACTION * depleted
    return False


def _inventory_continuation_retained(
    diagnostics: dict[str, float],
    later_oi: float,
) -> bool:
    before = diagnostics["attack_oi_before"]
    end = diagnostics["attack_oi_end"]
    if not all(math.isfinite(value) for value in (before, end, later_oi)):
        return False
    created = end - before
    return created > 0.0 and later_oi >= before + INVENTORY_RETENTION_FRACTION * created


def _stop(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: int,
    impact_parameters: Any,
) -> float:
    return v37.structural_stop(data, start, end, side, impact_parameters)


def _target_details(target: TargetReference) -> dict[str, Any]:
    return {
        "causal_target_reference": target.price,
        "causal_target_source": target.source,
        "causal_target_observed_index": target.observed_index,
        "causal_target_net_r_at_compilation": target.net_r,
    }


def resolve_reversal(
    data: pd.DataFrame,
    impact: pd.DataFrame,
    snapshots: list[tuple[CausalPool, ...]],
    sessions: list[ParentSession | None],
    boundary_takes: dict[tuple[pd.Timestamp, int], int],
    attack_index: int,
    attack_side: int,
    boundary: float,
    route: str,
    inventory_details: dict[str, float],
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> Intent | None:
    trade_side = -attack_side
    lookback = int(config.pre_sweep_structure_minutes)
    structure_start = max(0, attack_index - lookback)
    structure_frame = data.iloc[structure_start:attack_index]
    if structure_frame.empty:
        return None
    structure = (
        float(structure_frame["low"].min())
        if trade_side < 0
        else float(structure_frame["high"].max())
    )
    maximum = min(
        attack_index + int(config.sweep_confirmation_minutes),
        len(data) - 2,
    )
    for index in range(attack_index + 1, maximum + 1):
        if data.index[index] > evaluation_end:
            return None
        close = finite(data["close"].iloc[index])
        reclaimed = close < boundary if attack_side > 0 else close > boundary
        structure_broken = close < structure if trade_side < 0 else close > structure
        if not (reclaimed and structure_broken):
            continue
        if not _impact_aligned(
            impact, data, index, trade_side, CONFIRMATION_INNOVATION_Z
        ):
            continue
        later_oi = finite(data["metric_sum_open_interest"].iloc[index])
        if not _inventory_reversal_resolved(route, inventory_details, later_oi):
            continue
        stop = _stop(data, attack_index, index, trade_side, impact_parameters)
        if not math.isfinite(stop) or trade_side * (close - stop) <= 0.0:
            continue
        target = choose_causal_target(
            data=data,
            snapshots=snapshots,
            sessions=sessions,
            boundary_takes=boundary_takes,
            attack_index=attack_index,
            signal_index=index,
            side=trade_side,
            stop=stop,
            config=config,
        )
        if target is None:
            return None
        if route == "NEW_INVENTORY":
            scenario = SESSION_REVERSAL_NEW
        elif route == "LIQUIDATION":
            scenario = SESSION_REVERSAL_LIQUIDATION
        else:
            scenario = SESSION_REVERSAL_PASSIVE
        details = {
            "parent_session_start": str(sessions[attack_index].current_start),
            "parent_boundary_side": attack_side,
            "parent_boundary": boundary,
            "attack_index": attack_index,
            "attack_time": data.index[attack_index],
            "attack_extreme": (
                finite(data["high"].iloc[attack_index])
                if attack_side > 0 else finite(data["low"].iloc[attack_index])
            ),
            "attack_flow_60s": finite(data["flow_60s"].iloc[attack_index]),
            "attack_return_60s_bps": finite(data["ret_60s_bps"].iloc[attack_index]),
            "attack_impact_innovation_z": finite(impact["impact_innovation_z"].iloc[attack_index]),
            "inventory_route": route,
            **inventory_details,
            "internal_structure": structure,
            "confirmation_index": index,
            "confirmation_impact_innovation_z": finite(impact["impact_innovation_z"].iloc[index]),
            "confirmation_close_location": _directional_close_location(data, index, trade_side),
            "auction_outcome": "PARENT_BOUNDARY_FAILED_AND_INTERNAL_STRUCTURE_SHIFTED",
            "compiler": "candidate-04-v43-parent-session-liquidity-transfer",
            **_target_details(target),
        }
        return Intent(
            scenario=scenario,
            side=trade_side,
            signal_index=index,
            entry_index=index + 1,
            stop_level=stop,
            event_indices=(attack_index, index),
            details=details,
        )
    return None


def resolve_continuation(
    data: pd.DataFrame,
    impact: pd.DataFrame,
    snapshots: list[tuple[CausalPool, ...]],
    sessions: list[ParentSession | None],
    boundary_takes: dict[tuple[pd.Timestamp, int], int],
    attack_index: int,
    side: int,
    boundary: float,
    inventory_details: dict[str, float],
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
) -> Intent | None:
    pullback_max = min(
        attack_index + int(config.sweep_confirmation_minutes),
        len(data) - 3,
    )
    attack_close = finite(data["close"].iloc[attack_index])
    displacement = side * (attack_close - boundary)
    if displacement <= 0.0:
        return None
    for pullback_index in range(attack_index + 1, pullback_max + 1):
        if data.index[pullback_index] > evaluation_end:
            return None
        close = finite(data["close"].iloc[pullback_index])
        low = finite(data["low"].iloc[pullback_index])
        high = finite(data["high"].iloc[pullback_index])
        touches = low <= boundary if side > 0 else high >= boundary
        accepts_outside = close > boundary if side > 0 else close < boundary
        counter_price = -side * (close - attack_close)
        counter_flow = -side * finite(data["flow_60s"].iloc[pullback_index])
        if not (
            touches and accepts_outside and counter_price > 0.0 and counter_flow > 0.0
        ):
            continue
        later_oi = finite(data["metric_sum_open_interest"].iloc[pullback_index])
        if not _inventory_continuation_retained(inventory_details, later_oi):
            continue
        structure = high if side > 0 else low
        resume_max = min(
            pullback_index + int(config.trend_structure_minutes),
            len(data) - 2,
        )
        for index in range(pullback_index + 1, resume_max + 1):
            close = finite(data["close"].iloc[index])
            broken = close > structure if side > 0 else close < structure
            if not broken or not _impact_aligned(
                impact, data, index, side, CONFIRMATION_INNOVATION_Z
            ):
                continue
            later_oi = finite(data["metric_sum_open_interest"].iloc[index])
            if not _inventory_continuation_retained(inventory_details, later_oi):
                continue
            stop = _stop(data, attack_index, index, side, impact_parameters)
            if not math.isfinite(stop) or side * (close - stop) <= 0.0:
                continue
            target = choose_causal_target(
                data=data,
                snapshots=snapshots,
                sessions=sessions,
                boundary_takes=boundary_takes,
                attack_index=attack_index,
                signal_index=index,
                side=side,
                stop=stop,
                config=config,
            )
            if target is None:
                return None
            details = {
                "parent_session_start": str(sessions[attack_index].current_start),
                "parent_boundary_side": side,
                "parent_boundary": boundary,
                "attack_index": attack_index,
                "attack_time": data.index[attack_index],
                "attack_impact_innovation_z": finite(impact["impact_innovation_z"].iloc[attack_index]),
                "inventory_route": "NEW_INVENTORY",
                **inventory_details,
                "pullback_index": pullback_index,
                "pullback_structure": structure,
                "resumption_index": index,
                "resumption_impact_innovation_z": finite(impact["impact_innovation_z"].iloc[index]),
                "auction_outcome": "PARENT_BOUNDARY_ACCEPTED_RETESTED_AND_RESUMED",
                "compiler": "candidate-04-v43-parent-session-liquidity-transfer",
                **_target_details(target),
            }
            return Intent(
                scenario=SESSION_CONTINUATION,
                side=side,
                signal_index=index,
                entry_index=index + 1,
                stop_level=stop,
                event_indices=(attack_index, pullback_index, index),
                details=details,
            )
    return None


def collect_signals(
    data: pd.DataFrame,
    evaluation_start: pd.Timestamp,
    evaluation_end: pd.Timestamp,
    config: Any,
    impact_parameters: Any,
    router: Any,
) -> tuple[list[Intent], dict[str, Any]]:
    del router
    impact = impact_state(data)
    sessions = completed_parent_sessions(data)
    snapshots = active_causal_pool_snapshots(data, config)
    boundary_takes, attacks = parent_boundary_first_takes(data, sessions, config)
    oi_5m = data["metric_sum_open_interest"].astype(float).pct_change(5, fill_method=None)
    oi_cutoff = _shifted_quantile(
        oi_5m.abs(), OI_QUANTILE, OI_WINDOW, OI_MINIMUM
    )

    intents: list[Intent] = []
    counts = {
        "parent_boundary_attacks": len(attacks),
        "ambiguous_dual_boundary_attacks": 0,
        "impact_model_not_ready": 0,
        "insufficient_pressure": 0,
        "negative_innovation_attacks": 0,
        "positive_innovation_acceptances": 0,
        "unrouted_inventory_attacks": 0,
        "reversal_confirmations": 0,
        "continuation_confirmations": 0,
        "confirmed_without_causal_target": 0,
        "signals": 0,
    }
    for attack_index, (attack_side, boundary) in sorted(attacks.items()):
        if data.index[attack_index] > evaluation_end:
            break
        values = (
            finite(impact["absolute_pressure"].iloc[attack_index]),
            finite(impact["pressure_cutoff"].iloc[attack_index]),
            finite(impact["impact_innovation_z"].iloc[attack_index]),
            finite(impact["signed_pressure"].iloc[attack_index]),
        )
        if not all(math.isfinite(value) for value in values):
            counts["impact_model_not_ready"] += 1
            continue
        absolute, cutoff, innovation, pressure = values
        if absolute < cutoff or attack_side * pressure <= 0.0:
            counts["insufficient_pressure"] += 1
            continue

        route, inventory_details = _attack_inventory_route(
            data, impact, attack_index, attack_side, oi_cutoff
        )
        close = finite(data["close"].iloc[attack_index])
        accepted_outside = close > boundary if attack_side > 0 else close < boundary
        basis_aligned = attack_side * finite(data["basis_change_1m"].iloc[attack_index]) > 0.0

        intent: Intent | None = None
        if innovation <= ABSORPTION_INNOVATION_Z:
            counts["negative_innovation_attacks"] += 1
            if route is None:
                counts["unrouted_inventory_attacks"] += 1
                continue
            intent = resolve_reversal(
                data, impact, snapshots, sessions, boundary_takes,
                attack_index, attack_side, boundary, route,
                inventory_details, evaluation_end, config, impact_parameters,
            )
            if intent is not None:
                counts["reversal_confirmations"] += 1
        elif (
            innovation >= ACCEPTANCE_INNOVATION_Z
            and accepted_outside
            and basis_aligned
            and route == "NEW_INVENTORY"
        ):
            counts["positive_innovation_acceptances"] += 1
            intent = resolve_continuation(
                data, impact, snapshots, sessions, boundary_takes,
                attack_index, attack_side, boundary, inventory_details,
                evaluation_end, config, impact_parameters,
            )
            if intent is not None:
                counts["continuation_confirmations"] += 1
        if intent is None:
            continue
        intents.append(intent)

    priority = {
        SESSION_REVERSAL_NEW: 0,
        SESSION_REVERSAL_LIQUIDATION: 1,
        SESSION_REVERSAL_PASSIVE: 2,
        SESSION_CONTINUATION: 3,
    }
    intents.sort(key=lambda item: (int(item.signal_index), priority[item.scenario]))
    unique: list[Intent] = []
    seen: set[int] = set()
    for intent in intents:
        index = int(intent.signal_index)
        if index in seen:
            continue
        seen.add(index)
        unique.append(intent)
    counts["signals"] = len(unique)
    return unique, {
        "candidate": "candidate-04-v43-parent-session-liquidity-transfer",
        "compiler": "candidate-04-v43-parent-session-liquidity-transfer",
        "raw_routed_signals": len(intents),
        "unique_signal_bars": len(unique),
        "duplicate_signal_bars": len(intents) - len(unique),
        "route_counts": counts,
        "scenario_contract": {
            "parent_liquidity": "first take of a completed prior eight-hour high or low",
            "impact": "past-only return per signed executed notional divided by pre-event opposing depth",
            "reversal": "negative innovation, exact boundary reclaim, internal structure shift and inventory resolution",
            "continuation": "positive innovation and OI creation, separate counter-auction retest, independent resumption",
            "target": "active right-confirmed pivot pool or untouched opposite completed parent-session boundary observed before attack",
            "invalidation": "complete attack-to-confirmation excursion plus ATR buffer",
            "execution": "NautilusTrader BacktestNode only",
        },
    }


v22.collect_signals = collect_signals

if __name__ == "__main__":
    v22.main()
