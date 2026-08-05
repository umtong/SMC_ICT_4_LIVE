"""Causal open-liquidity ledger for the intrinsic external router.

The v2 target selector was logically correct but repeatedly rescanned every bar
between each pivot confirmation and each later signal.  That quadratic path is
unnecessary.  This module processes completed events once in chronological
order, keeps currently unconsumed confirmed pivots in two ordered heaps, and
snapshots the open pool only at requested signal indices.

Processing order at completed event ``i`` is exact v2 parity:

1. consume older high/low pools touched by event ``i``;
2. add directional-change pivots confirmed on event ``i`` (their confirmation
   event cannot consume them because v2 scans from confirmation + 1);
3. snapshot the pool for any signal completed on event ``i``.

No snapshot contains a future-confirmed pivot and no future consumption can
change an earlier snapshot.  This is the same state a live online ledger would
hold at that event close, with O((bars + pivots) log pivots) update cost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from heapq import heappop, heappush
from typing import Iterable

from core import Side
from directional_change_failed_sweep_week import DirectionalChangeEvent
from impact_regime_probe import EventFeature, ScenarioPlan
from intrinsic_external_liquidity_v2_week import (
    LONG_RANGE_HOURS,
    LONG_RANGE_OUTER_FRACTION,
    SHORT_RANGE_HOURS,
    STRONG_AGAINST_DELIVERY_FRACTION,
    RoutingDecision,
    SweepRetestSignal,
    completed_range,
    target_geometry,
)


LiquidityEventKey = tuple[str, int, int, int, float]


@dataclass(frozen=True, slots=True)
class OpenLiquiditySnapshot:
    high_keys: frozenset[LiquidityEventKey]
    low_keys: frozenset[LiquidityEventKey]


def liquidity_event_key(event: DirectionalChangeEvent) -> LiquidityEventKey:
    return (
        event.event_type,
        int(event.confirmation_index),
        int(event.confirmation_time_ns),
        int(event.pivot_index),
        float(event.pivot_price),
    )


def build_open_liquidity_snapshots(
    *,
    features: list[EventFeature],
    events: list[DirectionalChangeEvent],
    signal_indices: Iterable[int],
) -> dict[int, OpenLiquiditySnapshot]:
    """Return the exact causal unconsumed pool at requested completed events."""

    requested = frozenset(int(index) for index in signal_indices)
    if not requested:
        return {}
    if min(requested) < 0 or max(requested) >= len(features):
        raise ValueError("signal index is outside the completed feature stream")

    events_by_confirmation: dict[int, list[DirectionalChangeEvent]] = defaultdict(list)
    for event in events:
        index = int(event.confirmation_index)
        if index < 0 or index >= len(features):
            raise ValueError(
                f"directional-change confirmation index {index} is outside features",
            )
        events_by_confirmation[index].append(event)

    high_heap: list[tuple[float, int, LiquidityEventKey]] = []
    low_heap: list[tuple[float, int, LiquidityEventKey]] = []
    active_high: set[LiquidityEventKey] = set()
    active_low: set[LiquidityEventKey] = set()
    snapshots: dict[int, OpenLiquiditySnapshot] = {}
    serial = 0
    final_requested = max(requested)

    for index, feature in enumerate(features):
        if index > final_requested:
            break
        bar = feature.bar

        # Older high liquidity is consumed once price trades at or above it.
        while high_heap and high_heap[0][0] <= bar.high:
            _, _, key = heappop(high_heap)
            active_high.discard(key)

        # Older low liquidity is consumed once price trades at or below it.
        while low_heap and -low_heap[0][0] >= bar.low:
            _, _, key = heappop(low_heap)
            active_low.discard(key)

        # A pivot confirmed on this completed event becomes observable only now;
        # the confirmation event itself is not a post-confirmation consumption.
        for event in events_by_confirmation.get(index, ()):
            key = liquidity_event_key(event)
            serial += 1
            if event.event_type == "DOWN":
                active_high.add(key)
                heappush(high_heap, (float(event.pivot_price), serial, key))
            elif event.event_type == "UP":
                active_low.add(key)
                heappush(low_heap, (-float(event.pivot_price), serial, key))
            else:
                raise ValueError(f"unknown directional-change event: {event.event_type}")

        if index in requested:
            snapshots[index] = OpenLiquiditySnapshot(
                high_keys=frozenset(active_high),
                low_keys=frozenset(active_low),
            )

    missing = requested.difference(snapshots)
    if missing:
        raise RuntimeError(f"failed to build liquidity snapshots for {sorted(missing)}")
    return snapshots


def select_target_indexed(
    *,
    signal: SweepRetestSignal,
    features: list[EventFeature],
    events: list[DirectionalChangeEvent],
    snapshot: OpenLiquiditySnapshot,
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[ScenarioPlan | None, int | None, float | None, float | None]:
    index = signal.signal_bar_index
    expected_entry = features[index].bar.close
    target_event_type = "DOWN" if signal.side is Side.LONG else "UP"
    open_keys = snapshot.high_keys if signal.side is Side.LONG else snapshot.low_keys
    candidates: list[tuple[float, float, int, float, float]] = []

    for event in events:
        if event.event_type != target_event_type:
            continue
        if event.confirmation_index > index:
            continue
        if liquidity_event_key(event) not in open_keys:
            continue
        target = float(event.pivot_price)
        if signal.side is Side.LONG and target <= expected_entry:
            continue
        if signal.side is Side.SHORT and target >= expected_entry:
            continue
        _, planned_gain, price_fraction, net_rr = target_geometry(
            expected_entry=expected_entry,
            stop=signal.stop_price,
            target=target,
            cost=cost,
        )
        if (
            price_fraction < minimum_price_risk_fraction
            or planned_gain <= 0.0
            or net_rr < minimum_net_reward_risk
        ):
            continue
        candidates.append(
            (
                abs(target - expected_entry),
                target,
                int(event.confirmation_index),
                price_fraction,
                net_rr,
            ),
        )

    if not candidates:
        return None, None, None, None
    _, target, event_index, price_fraction, net_rr = sorted(candidates)[0]
    plan = ScenarioPlan(
        scenario_id=signal.scenario_id + f":open-liquidity:{event_index}",
        response="EXHAUSTION_REVERSAL",
        side=signal.side,
        signal_bar_index=signal.signal_bar_index,
        signal_time_ns=signal.signal_time_ns,
        stop_price=signal.stop_price,
        target_price=target,
        confirmation_hold_price=signal.boundary,
        structure_high=max(signal.path_high, signal.boundary, target),
        structure_low=min(signal.path_low, signal.boundary, target),
        structure_midpoint=0.5 * (signal.boundary + target),
        pulse_high=signal.path_high,
        pulse_low=signal.path_low,
        pulse_flow_score=signal.trend_flow_imbalance,
        pulse_move_atr=0.0,
        pulse_path_efficiency=0.0,
        pulse_close_location=0.0,
        reason_code="TARGET_FREE_SWEEP_RETEST_EXTERNAL_POOL_ROUTED",
    )
    return plan, event_index, price_fraction, net_rr


def route_signal_indexed(
    *,
    signal: SweepRetestSignal,
    features: list[EventFeature],
    end_times: list[int],
    outer_states: list[str],
    events: list[DirectionalChangeEvent],
    snapshot: OpenLiquiditySnapshot,
    cost: float,
    minimum_price_risk_fraction: float,
    minimum_net_reward_risk: float,
) -> tuple[ScenarioPlan | None, RoutingDecision]:
    """Route one completed signal with the frozen v2 state rules."""

    index = signal.signal_bar_index
    short_range = completed_range(
        features,
        end_times,
        end_index=index,
        hours=SHORT_RANGE_HOURS,
    )
    long_range = completed_range(
        features,
        end_times,
        end_index=index,
        hours=LONG_RANGE_HOURS,
    )
    outer_state = outer_states[index]
    if short_range is None or long_range is None:
        return None, RoutingDecision(
            signal.scenario_id,
            index,
            signal.signal_time_ns,
            signal.side.value,
            False,
            "INCOMPLETE_DEALING_RANGE_HISTORY",
            None,
            None,
            None,
            outer_state,
            None,
            None,
            None,
            None,
        )
    if short_range.width <= 0.0 or long_range.width <= 0.0:
        return None, RoutingDecision(
            signal.scenario_id,
            index,
            signal.signal_time_ns,
            signal.side.value,
            False,
            "ZERO_WIDTH_DEALING_RANGE",
            None,
            None,
            None,
            outer_state,
            None,
            None,
            None,
            None,
        )

    signal_close = features[index].bar.close
    short_location = (signal_close - short_range.low) / short_range.width
    long_location = (signal_close - long_range.low) / long_range.width
    aligned_delivery = signal.side.sign * long_range.delivery / long_range.width
    correct_short_half = (
        short_location <= 0.50
        if signal.side is Side.LONG
        else short_location >= 0.50
    )
    correct_long_quartile = (
        long_location <= LONG_RANGE_OUTER_FRACTION
        if signal.side is Side.LONG
        else long_location >= 1.0 - LONG_RANGE_OUTER_FRACTION
    )
    aligned_outer_state = (
        outer_state == "BULL"
        if signal.side is Side.LONG
        else outer_state == "BEAR"
    )
    delivery_ok = (
        aligned_delivery >= -STRONG_AGAINST_DELIVERY_FRACTION
        or aligned_outer_state
    )

    if not correct_short_half:
        reason = "WRONG_24H_PREMIUM_DISCOUNT"
    elif not correct_long_quartile:
        reason = "NOT_72H_EXTERNAL_LIQUIDITY"
    elif not delivery_ok:
        reason = "STRONG_72H_DELIVERY_WITHOUT_OUTER_CHOCH"
    else:
        plan, target_index, price_fraction, net_rr = select_target_indexed(
            signal=signal,
            features=features,
            events=events,
            snapshot=snapshot,
            cost=cost,
            minimum_price_risk_fraction=minimum_price_risk_fraction,
            minimum_net_reward_risk=minimum_net_reward_risk,
        )
        if plan is not None:
            return plan, RoutingDecision(
                signal.scenario_id,
                index,
                signal.signal_time_ns,
                signal.side.value,
                True,
                plan.reason_code,
                short_location,
                long_location,
                aligned_delivery,
                outer_state,
                plan.target_price,
                target_index,
                price_fraction,
                net_rr,
            )
        reason = "NO_UNCONSUMED_EXTERNAL_POOL_WITH_NET_GEOMETRY"

    return None, RoutingDecision(
        signal.scenario_id,
        index,
        signal.signal_time_ns,
        signal.side.value,
        False,
        reason,
        short_location,
        long_location,
        aligned_delivery,
        outer_state,
        None,
        None,
        None,
        None,
    )
