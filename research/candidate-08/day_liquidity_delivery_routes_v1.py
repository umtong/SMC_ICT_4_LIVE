"""Completed-session route selector beneath an already-established H4 draw."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from typing import Any

from day_liquidity_delivery_context_v1 import (
    ACCEPTANCE_FAMILY,
    RAID_FAMILY,
    DayLiquidityDeliveryConfig,
    DrawContext,
    FifteenMinuteBar,
    RouteCandidate,
    aggregate_fifteen_minute_bars,
    build_session_ranges,
    day_start_ns,
    route_window_for_bar,
)
from day_liquidity_delivery_htf_v1 import (
    levels_after_five_bar,
    same_draw,
    select_htf_target,
    target_still_active,
)
from range_fvg_logic import ExternalLevel, FiveMinuteBar


@dataclass(slots=True)
class _AcceptancePending:
    scenario_id: str
    route_name: str
    source_name: str
    route_start_ns: int
    route_end_ns: int
    direction: int
    draw: DrawContext
    target: ExternalLevel
    boundary_id: str
    boundary_source: str
    boundary_level: float
    acceptance_time_ns: int
    acceptance_five_index: int
    acceptance_high: float
    acceptance_low: float


def _acceptance_displacement(
    bar: FifteenMinuteBar,
    *,
    direction: int,
    boundary: float,
    config: DayLiquidityDeliveryConfig,
) -> bool:
    if not all(
        isfinite(value)
        for value in (bar.atr, bar.prior_body_median, bar.prior_range_median)
    ):
        return False
    close_location_ok = (
        bar.close_location >= config.acceptance_close_location
        if direction > 0
        else bar.close_location <= 1.0 - config.acceptance_close_location
    )
    return (
        direction * (bar.close - boundary) >= config.session_boundary_excursion_atr * bar.atr
        and direction * (bar.close - bar.open) > 0.0
        and bar.body >= bar.prior_body_median
        and bar.range >= bar.prior_range_median
        and close_location_ok
    )


def build_route_candidates(
    *,
    bars: tuple[FiveMinuteBar, ...],
    draw_by_five: tuple[DrawContext | None, ...],
    snapshots: tuple[tuple[ExternalLevel, ...], ...],
    config: DayLiquidityDeliveryConfig,
    symbol: str,
    diagnostics: Counter[str],
    rejected: list[dict[str, Any]],
) -> tuple[RouteCandidate, ...]:
    """Arm at most one causal route for each completed source-session window."""

    fifteen = aggregate_fifteen_minute_bars(bars, config)
    diagnostics["FIFTEEN_MINUTE_BARS"] = len(fifteen)
    sessions = build_session_ranges(bars)
    state: dict[tuple[int, str], str] = {}
    pending_by_route: dict[tuple[int, str], _AcceptancePending] = {}
    candidates: list[RouteCandidate] = []
    scenario_counter = 0

    for bar in fifteen:
        route = route_window_for_bar(bar)
        if route is None:
            continue
        day = day_start_ns(bar.start_time_ns)
        key = day, route.name
        if state.get(key) == "LOCKED":
            continue
        source = sessions.get((day, route.source_name))
        if source is None:
            diagnostics["MISSING_COMPLETE_SOURCE_SESSION"] += 1
            state[key] = "LOCKED"
            continue
        draw = draw_by_five[bar.last_five_index]
        pending = pending_by_route.get(key)

        if pending is not None:
            if not isfinite(bar.atr) or bar.atr <= 0.0:
                diagnostics["NO_CAUSAL_FIFTEEN_MINUTE_ATR_FOR_RETEST"] += 1
                continue
            if not same_draw(draw, pending.draw):
                diagnostics["ACCEPTANCE_DRAW_INVALIDATED_BEFORE_RETEST"] += 1
                rejected.append(
                    {
                        "scenario_id": pending.scenario_id,
                        "symbol": symbol,
                        "reason": "ACCEPTANCE_DRAW_INVALIDATED_BEFORE_RETEST",
                        "acceptance_time_ns": pending.acceptance_time_ns,
                    }
                )
                pending_by_route.pop(key, None)
                state[key] = "LOCKED"
                continue
            if not target_still_active(snapshots, bar.last_five_index, pending.target.level_id):
                diagnostics["TARGET_CONSUMED_BEFORE_ACCEPTANCE_RETEST"] += 1
                pending_by_route.pop(key, None)
                state[key] = "LOCKED"
                continue
            tolerance = config.session_boundary_excursion_atr * bar.atr
            if pending.direction > 0:
                held = bar.low <= pending.boundary_level + tolerance and bar.close >= pending.boundary_level
                reclaimed = bar.close < pending.boundary_level - tolerance
                structural_reference = bar.low
            else:
                held = bar.high >= pending.boundary_level - tolerance and bar.close <= pending.boundary_level
                reclaimed = bar.close > pending.boundary_level + tolerance
                structural_reference = bar.high
            if held:
                diagnostics["DRAW_ALIGNED_ACCEPTANCE_RETEST_HELD"] += 1
                candidates.append(
                    RouteCandidate(
                        scenario_id=pending.scenario_id,
                        family=ACCEPTANCE_FAMILY,
                        route_name=pending.route_name,
                        source_name=pending.source_name,
                        route_start_ns=pending.route_start_ns,
                        route_end_ns=pending.route_end_ns,
                        direction=pending.direction,
                        draw=pending.draw,
                        target=pending.target,
                        boundary_id=pending.boundary_id,
                        boundary_source=pending.boundary_source,
                        boundary_level=pending.boundary_level,
                        interaction_time_ns=pending.acceptance_time_ns,
                        trigger_time_ns=bar.end_time_ns,
                        trigger_five_index=bar.last_five_index,
                        structural_reference=structural_reference,
                        interaction_details={
                            "acceptance_high": pending.acceptance_high,
                            "acceptance_low": pending.acceptance_low,
                            "acceptance_five_index": pending.acceptance_five_index,
                            "retest_high": bar.high,
                            "retest_low": bar.low,
                        },
                    )
                )
                pending_by_route.pop(key, None)
                state[key] = "LOCKED"
            elif reclaimed:
                diagnostics["DRAW_ALIGNED_ACCEPTANCE_RECLAIMED"] += 1
                pending_by_route.pop(key, None)
                state[key] = "LOCKED"
            continue

        if draw is None or not isfinite(bar.atr) or bar.atr <= 0.0:
            diagnostics["NO_ACTIVE_HTF_DRAW_IN_ROUTE"] += 1
            continue
        target = select_htf_target(
            levels_after_five_bar(snapshots, bar.last_five_index),
            direction=draw.direction,
            reference=bar.close,
            h4_atr=draw.h4_atr,
            config=config,
        )
        if target is None:
            diagnostics["NO_ACTIVE_HTF_EXTERNAL_TARGET"] += 1
            continue

        excursion = config.session_boundary_excursion_atr * bar.atr
        if draw.direction > 0:
            raid = bar.low <= source.low - excursion and bar.close > source.low
            acceptance = _acceptance_displacement(bar, direction=1, boundary=source.high, config=config)
            raid_boundary, acceptance_boundary = source.low, source.high
            structural_reference = bar.low
        else:
            raid = bar.high >= source.high + excursion and bar.close < source.high
            acceptance = _acceptance_displacement(bar, direction=-1, boundary=source.low, config=config)
            raid_boundary, acceptance_boundary = source.high, source.low
            structural_reference = bar.high
        if raid and acceptance:
            diagnostics["AMBIGUOUS_BOTH_SOURCE_BOUNDARIES_INTERACTED"] += 1
            state[key] = "LOCKED"
            continue
        if not raid and not acceptance:
            continue

        scenario_counter += 1
        scenario_id = f"{symbol}-day-delivery-{scenario_counter:05d}-{bar.end_time_ns}"
        route_start_ns = day + route.start_minute * 60 * 1_000_000_000
        route_end_ns = day + route.end_minute * 60 * 1_000_000_000
        if raid:
            diagnostics["DRAW_ALIGNED_SOURCE_SESSION_RAID_RECLAIM"] += 1
            side = "LOW" if draw.direction > 0 else "HIGH"
            candidates.append(
                RouteCandidate(
                    scenario_id=scenario_id,
                    family=RAID_FAMILY,
                    route_name=route.name,
                    source_name=source.name,
                    route_start_ns=route_start_ns,
                    route_end_ns=route_end_ns,
                    direction=draw.direction,
                    draw=draw,
                    target=target,
                    boundary_id=f"{source.name}-{day}-{side}",
                    boundary_source=f"{source.name}_{side}",
                    boundary_level=raid_boundary,
                    interaction_time_ns=bar.end_time_ns,
                    trigger_time_ns=bar.end_time_ns,
                    trigger_five_index=bar.last_five_index,
                    structural_reference=structural_reference,
                    interaction_details={"raid_high": bar.high, "raid_low": bar.low},
                )
            )
            state[key] = "LOCKED"
        else:
            diagnostics["DRAW_ALIGNED_SOURCE_SESSION_ACCEPTANCE"] += 1
            side = "HIGH" if draw.direction > 0 else "LOW"
            pending_by_route[key] = _AcceptancePending(
                scenario_id=scenario_id,
                route_name=route.name,
                source_name=source.name,
                route_start_ns=route_start_ns,
                route_end_ns=route_end_ns,
                direction=draw.direction,
                draw=draw,
                target=target,
                boundary_id=f"{source.name}-{day}-{side}",
                boundary_source=f"{source.name}_{side}",
                boundary_level=acceptance_boundary,
                acceptance_time_ns=bar.end_time_ns,
                acceptance_five_index=bar.last_five_index,
                acceptance_high=bar.high,
                acceptance_low=bar.low,
            )
            state[key] = "ACCEPTANCE_PENDING"

    for pending in pending_by_route.values():
        diagnostics["ACCEPTANCE_ROUTE_ENDED_WITHOUT_RETEST"] += 1
        rejected.append(
            {
                "scenario_id": pending.scenario_id,
                "symbol": symbol,
                "reason": "ACCEPTANCE_ROUTE_ENDED_WITHOUT_RETEST",
                "acceptance_time_ns": pending.acceptance_time_ns,
            }
        )
    diagnostics["ROUTE_CANDIDATES"] = len(candidates)
    return tuple(candidates)


__all__ = ["build_route_candidates"]
