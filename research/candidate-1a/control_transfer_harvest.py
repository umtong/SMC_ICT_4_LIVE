#!/usr/bin/env python3
"""Causal control-transfer entries for semantic liquidity auction episodes.

The inherited v4-v7/episode stack remains responsible for:
- the unconsumed semantic-liquidity map and direction,
- failed-vs-accepted auction classification,
- event ownership and causal episode independence,
- opposing route obstacles, price/flow state, and realistic barrier accounting.

This overlay changes the part which failed structurally in the inherited system: an
order is no longer left at the original event zone while later bars merely add model
features.  After the event departs, the strategy waits for a *new* completed
control-transfer structure (micro BOS plus fresh FVG/order block), then waits for its
first return and an observed price/flow response.  Only then is a next-minute market
entry armed.  Invalidation is the defended micro swing; the target is the first
opposing unconsumed route obstacle, not an arbitrary RR multiple.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterator
import math

import numpy as np
import pandas as pd

# Importing this applies the narrow v4-v6 compatibility aliases.  No inherited
# strategy logic is copied or replaced.
import causal_auction_harvest as compatibility  # noqa: F401
import auction_episode_harvest as episode

policy = episode.policy
core = episode.core
narrative = core.core
hl = core.hl

EPS = 1e-12
MAX_STRUCTURE_WAIT_MINUTES = 89
MAX_RETEST_WAIT_MINUTES = 45
MAX_RESPONSE_BARS = 3
ENTRY_SLIPPAGE_TICKS = 2
TARGET_INSIDE_TICKS = 1
STOP_BUFFER_ATR = 0.04
MIN_GROSS_RR = 1.0


def _sign(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _signed_quote_flow(frame: pd.DataFrame) -> pd.Series:
    quote = pd.to_numeric(
        frame.get("quote_volume", pd.Series(0.0, index=frame.index)),
        errors="coerce",
    ).fillna(0.0)
    if "signed_quote_flow" in frame:
        return pd.to_numeric(frame.signed_quote_flow, errors="coerce").fillna(0.0)
    if "delta_share" in frame:
        delta = pd.to_numeric(frame.delta_share, errors="coerce").fillna(0.0)
        return delta * quote
    if "taker_buy_quote_volume" in frame:
        buy = pd.to_numeric(frame.taker_buy_quote_volume, errors="coerce").fillna(0.0)
        return 2.0 * buy - quote
    return pd.Series(0.0, index=frame.index)


def _last_opposite_body(
    data: pd.DataFrame,
    start: int,
    end: int,
    side: str,
    tick: float,
) -> tuple[float, float, int, float, float] | None:
    sign = _sign(side)
    for index in range(end - 1, max(start, 0) - 1, -1):
        row = data.iloc[index]
        if sign * float(row.close - row.open) >= 0.0:
            continue
        lower = min(float(row.open), float(row.close))
        upper = max(float(row.open), float(row.close))
        if upper - lower < tick:
            continue
        return lower, upper, index, float(row.low), float(row.high)
    return None


def _aligned_gap(
    data: pd.DataFrame,
    arm: int,
    side: str,
    tick: float,
) -> Any | None:
    # The latest completed aligned imbalance owns the prospective retest.  Looking
    # back two bars captures a displacement whose FVG was observed just before the
    # local-control close without reaching into an old, already-mitigated zone.
    for index in range(arm, max(1, arm - 3), -1):
        gap = hl._gap_at(data, index, tick)
        if gap is None or str(gap.side) != side:
            continue
        later = data.iloc[index + 1 : arm + 1]
        if not later.empty:
            spent = (
                float(later.low.min()) <= float(gap.lower) - tick
                if side == "LONG"
                else float(later.high.max()) >= float(gap.upper) + tick
            )
            if spent:
                continue
        return gap
    return None


def _local_control_break(
    data: pd.DataFrame,
    departure: int,
    arm: int,
    side: str,
    tick: float,
) -> tuple[bool, float]:
    start = max(departure, arm - 8)
    prior = data.iloc[start:arm]
    if len(prior) < 2:
        return False, float("nan")
    close = float(data.iloc[arm].close)
    if side == "LONG":
        control = float(prior.high.max())
        return close >= control + tick, control
    control = float(prior.low.min())
    return close <= control - tick, control


def _control_transfer_zone(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    tick: float,
) -> dict[str, Any] | None:
    side = str(candidate.setup.side)
    sign = _sign(side)
    departure = int(candidate.departure_index)
    row = data.iloc[arm]
    aligned_body = sign * float(row.close - row.open) > 0.0
    broke_control, control_price = _local_control_break(
        data, departure, arm, side, tick
    )
    gap = _aligned_gap(data, arm, side, tick)

    recent = data.iloc[max(departure, arm - 5) : arm + 1]
    quote = pd.to_numeric(
        recent.get("quote_volume", pd.Series(0.0, index=recent.index)),
        errors="coerce",
    ).fillna(0.0)
    flow = _signed_quote_flow(recent)
    signed_flow_share = sign * float(flow.sum()) / max(float(quote.sum()), EPS)
    price_progress = sign * float(recent.close.iloc[-1] - recent.open.iloc[0])
    initiative = signed_flow_share > 0.015
    absorption = signed_flow_share <= 0.0 and price_progress > 0.0
    body_ratio = _finite(row.get("body_ratio"))
    range_ratio = _finite(row.get("range_ratio"))
    activity_ratio = _finite(row.get("activity_ratio"))

    # A zone is created only after an observable transfer of local control.  An FVG
    # is the strongest footprint; otherwise a large efficient control-breaking bar
    # plus initiative/absorption is required.
    displacement = (
        gap is not None
        or (
            body_ratio >= 1.15
            and range_ratio >= 1.0
            and (activity_ratio >= 0.85 or initiative or absorption)
        )
    )
    if not (aligned_body and broke_control and displacement):
        return None

    ob = _last_opposite_body(data, max(departure, arm - 10), arm, side, tick)
    location_kind = ""
    if gap is not None:
        lower, upper = float(gap.lower), float(gap.upper)
        origin = max(departure, int(gap.observed_index) - 2)
        location_kind = "FRESH_DIRECTIONAL_FVG"
        if ob is not None:
            ob_lower, ob_upper, ob_index, _, _ = ob
            overlap_lower = max(lower, ob_lower)
            overlap_upper = min(upper, ob_upper)
            if overlap_upper > overlap_lower + tick:
                lower, upper = overlap_lower, overlap_upper
                origin = min(origin, ob_index)
                location_kind = "FVG_OB_OVERLAP"
    elif ob is not None:
        lower, upper, origin, _, _ = ob
        location_kind = "CONTROL_BREAK_ORDER_BLOCK"
    else:
        return None

    decision = float(row.close)
    behind = (
        upper <= decision - tick if side == "LONG" else lower >= decision + tick
    )
    if not behind or upper <= lower:
        return None

    origin_frame = data.iloc[max(departure, origin) : arm + 1]
    if origin_frame.empty:
        return None
    structural_reference = (
        float(origin_frame.low.min())
        if side == "LONG"
        else float(origin_frame.high.max())
    )
    atr = max(narrative._atr_price(data, arm), EPS)
    buffer = max(2.0 * tick, STOP_BUFFER_ATR * atr)
    stop = (
        structural_reference - buffer
        if side == "LONG"
        else structural_reference + buffer
    )
    valid = stop < lower if side == "LONG" else stop > upper
    if not valid:
        return None

    # Recent-vs-early flow is a causal change-point proxy.  It is a feature rather
    # than a brittle gate: persistent initiative and adverse-flow absorption are
    # different valid ways for price to acquire control.
    late = recent.iloc[-min(3, len(recent)) :]
    early = recent.iloc[: max(1, len(recent) - len(late))]
    late_quote = pd.to_numeric(
        late.get("quote_volume", pd.Series(0.0, index=late.index)),
        errors="coerce",
    ).fillna(0.0)
    early_quote = pd.to_numeric(
        early.get("quote_volume", pd.Series(0.0, index=early.index)),
        errors="coerce",
    ).fillna(0.0)
    late_share = sign * float(_signed_quote_flow(late).sum()) / max(
        float(late_quote.sum()), EPS
    )
    early_share = sign * float(_signed_quote_flow(early).sum()) / max(
        float(early_quote.sum()), EPS
    )

    return {
        "creation_index": int(arm),
        "origin_index": int(origin),
        "lower": float(lower),
        "upper": float(upper),
        "stop": float(stop),
        "location_kind": location_kind,
        "control_price": float(control_price),
        "flow_share_signed": float(signed_flow_share),
        "flow_change_signed": float(late_share - early_share),
        "initiative": float(initiative),
        "absorption": float(absorption),
        "body_ratio": float(body_ratio),
        "range_ratio": float(range_ratio),
        "activity_ratio": float(activity_ratio),
        "gap_present": float(gap is not None),
        "ob_present": float(ob is not None),
        "zone_width_bps": (upper - lower) / max(abs(decision), EPS) * 10_000.0,
        "impulse_distance_bps": sign * (decision - 0.5 * (lower + upper))
        / max(abs(decision), EPS)
        * 10_000.0,
    }


def _first_return_response(
    data: pd.DataFrame,
    zone: dict[str, Any],
    side: str,
    tick: float,
    expiry: int,
) -> dict[str, Any] | None:
    sign = _sign(side)
    touch: int | None = None
    extreme: float | None = None
    start = int(zone["creation_index"]) + 1
    end = min(len(data) - 2, expiry)
    for index in range(start, end + 1):
        row = data.iloc[index]
        invalidated = (
            float(row.low) <= float(zone["stop"])
            if side == "LONG"
            else float(row.high) >= float(zone["stop"])
        )
        if invalidated:
            return None
        overlaps = (
            float(row.low) <= float(zone["upper"])
            and float(row.high) >= float(zone["lower"])
        )
        if touch is None:
            if not overlaps:
                continue
            touch = index
            extreme = float(row.low if side == "LONG" else row.high)
        else:
            extreme = (
                min(float(extreme), float(row.low))
                if side == "LONG"
                else max(float(extreme), float(row.high))
            )
        if index - touch > MAX_RESPONSE_BARS:
            return None
        spent = (
            float(row.close) < float(zone["lower"]) - tick
            if side == "LONG"
            else float(row.close) > float(zone["upper"]) + tick
        )
        if spent:
            return None

        prior = data.iloc[index - 1]
        aligned_body = sign * float(row.close - row.open) > 0.0
        closes_away = (
            float(row.close) >= float(zone["upper"])
            if side == "LONG"
            else float(row.close) <= float(zone["lower"])
        )
        local_control = (
            float(row.close) > float(prior.high)
            if side == "LONG"
            else float(row.close) < float(prior.low)
        )
        q = max(_finite(row.get("quote_volume")), EPS)
        signed = _finite(row.get("signed_quote_flow"), float("nan"))
        if not math.isfinite(signed):
            signed = _finite(row.get("delta_share")) * q
        flow_share = sign * signed / q
        price_progress = sign * float(row.close - row.open)
        initiative = flow_share > 0.0
        absorption = flow_share <= 0.0 and price_progress > 0.0
        if aligned_body and closes_away and local_control and (initiative or absorption):
            return {
                "touch_index": int(touch),
                "response_index": int(index),
                "retest_extreme": float(extreme),
                "response_kind": (
                    "ALIGNED_INITIATIVE"
                    if initiative
                    else "ADVERSE_FLOW_ABSORBED"
                ),
                "response_flow_share_signed": float(flow_share),
                "response_body_ratio": _finite(row.get("body_ratio")),
                "response_range_ratio": _finite(row.get("range_ratio")),
                "response_activity_ratio": _finite(row.get("activity_ratio")),
                "return_wait_minutes": float(touch - int(zone["creation_index"])),
                "response_delay_minutes": float(index - touch),
            }
    return None


def _target_inside_obstacle(obstacle: Any, side: str, tick: float) -> float:
    if side == "LONG":
        return float(obstacle.order_price) - TARGET_INSIDE_TICKS * tick
    return float(obstacle.order_price) + TARGET_INSIDE_TICKS * tick


def _label_next_open(
    data: pd.DataFrame,
    response_index: int,
    side: str,
    stop: float,
    target: float,
    tick: float,
) -> Any:
    fill_index = response_index + 1
    if fill_index >= len(data):
        return policy._empty_label("NO_FUTURE", data, len(data) - 1)
    sign = _sign(side)
    entry = float(data.iloc[fill_index].open) + sign * ENTRY_SLIPPAGE_TICKS * tick
    valid = stop < entry < target if side == "LONG" else target < entry < stop
    if not valid:
        return policy._empty_label("GAP_INVALID_GEOMETRY", data, fill_index)
    gross_rr = abs(target - entry) / max(abs(entry - stop), EPS)
    economics = core._raw_economics(side, entry, stop, target, tick)
    if (
        gross_rr < MIN_GROSS_RR
        or economics is None
        or float(economics["target_net_r"]) <= 0.0
    ):
        return policy._empty_label("NEXT_OPEN_RR_BELOW_ONE", data, fill_index)
    # The inherited first-passage resolver is conservative only when stop and target
    # are both touched in the same minute; a target-only first bar remains a win.
    return policy._copy_label(
        core._resolve_after_fill(
            data, fill_index, response_index, entry, stop, target, side, tick
        )
    )


def _structure_positions(
    data: pd.DataFrame,
    candidate: Any,
    source: Any,
    event_stop: float,
) -> Iterator[int]:
    departure = int(candidate.departure_index)
    expiry = min(
        len(data) - 2,
        core._pending_expiry(candidate, source),
        departure + MAX_STRUCTURE_WAIT_MINUTES,
    )
    side = str(candidate.setup.side)
    for arm in range(departure + 1, expiry + 1):
        row = data.iloc[arm]
        invalidated = (
            float(row.low) <= event_stop
            if side == "LONG"
            else float(row.high) >= event_stop
        )
        if invalidated:
            break
        yield arm


def generate_symbol(symbol, data, levels, metadata, trading_start):
    tick = core.CONTRACTS[symbol].tick_size
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = core.direction_sources(
        levels, metadata, minimum_timeframe=core.MINIMUM_SOURCE_TIMEFRAME
    )
    records: list[dict[str, Any]] = []
    active_until = {"HIGH": -1, "LOW": -1}
    seen: set[tuple[int, str]] = set()
    counts = {
        "semantic_sources": len(sources),
        "source_interactions": 0,
        "causal_departures": 0,
        "control_structures": 0,
        "confirmed_responses": 0,
        "plans": 0,
    }

    for source in sources:
        interaction = int(source.first_penetration_index)
        if (
            interaction >= len(data)
            or int(data.index[interaction].value) < start_ns
            or interaction <= active_until[source.side]
        ):
            continue
        clock = (interaction, source.side)
        if clock in seen:
            continue
        peers = [
            level
            for level in sources
            if level.side == source.side
            and int(level.first_penetration_index) == interaction
        ]
        owner = max(
            peers,
            key=lambda level: (
                metadata[level.level_id].semantic_weight,
                level.timeframe_minutes,
                level.defense_count,
                level.strength_ratio,
            ),
        )
        seen.add(clock)
        if owner.level_id != source.level_id:
            continue
        counts["source_interactions"] += 1
        candidates = core._departure_candidates(data, owner, tick)
        if not candidates:
            continue
        candidate = candidates[0]
        active_until[source.side] = max(
            active_until[source.side], int(candidate.departure_index)
        )
        counts["causal_departures"] += 1
        object.__setattr__(candidate, "source", owner)
        event_stop = core._causal_stop(data, candidate, owner, tick)
        event_ns = int(data.index[candidate.setup.interaction_index].value)
        episode_id = (
            f"CT1:{symbol}:{event_ns}:{core._stable_id(owner.level_id)}"
        )
        used_zones: set[tuple[int, int, int]] = set()

        for arm in _structure_positions(data, candidate, owner, event_stop):
            zone = _control_transfer_zone(data, candidate, arm, tick)
            if zone is None:
                continue
            signature = (
                int(round(float(zone["lower"]) / tick)),
                int(round(float(zone["upper"]) / tick)),
                int(round(float(zone["stop"]) / tick)),
            )
            if signature in used_zones:
                continue
            used_zones.add(signature)
            counts["control_structures"] += 1

            expiry = min(
                len(data) - 2,
                int(arm) + MAX_RETEST_WAIT_MINUTES,
                core._pending_expiry(candidate, owner),
            )
            response = _first_return_response(
                data, zone, str(candidate.setup.side), tick, expiry
            )
            if response is None:
                continue
            counts["confirmed_responses"] += 1
            response_index = int(response["response_index"])
            provisional_entry = float(data.iloc[response_index].close)
            obstacle, route_features = core.v4._first_obstacle(
                data,
                levels,
                metadata,
                response_index,
                provisional_entry,
                candidate.setup.side,
                tick,
            )
            if obstacle is None:
                continue
            target = _target_inside_obstacle(
                obstacle, str(candidate.setup.side), tick
            )
            stop = float(zone["stop"])
            planned_risk = abs(provisional_entry - stop)
            valid = (
                stop < provisional_entry < target
                if candidate.setup.side == "LONG"
                else target < provisional_entry < stop
            )
            if not valid or planned_risk <= EPS:
                continue
            planned_rr = abs(target - provisional_entry) / planned_risk
            economics = core._raw_economics(
                candidate.setup.side,
                provisional_entry,
                stop,
                target,
                tick,
            )
            if (
                planned_rr < MIN_GROSS_RR
                or economics is None
                or float(economics["target_net_r"]) <= 0.0
            ):
                continue

            label = _label_next_open(
                data,
                response_index,
                str(candidate.setup.side),
                stop,
                target,
                tick,
            )
            if label.fill_state in {
                "NO_FUTURE",
                "GAP_INVALID_GEOMETRY",
                "NEXT_OPEN_RR_BELOW_ONE",
            }:
                continue
            state_id = (
                f"CT1STATE:{symbol}:{event_ns}:"
                f"{candidate.event_meta['narrative_branch']}:"
                f"{int(zone['creation_index'])}:{response_index}:"
                f"{core._stable_id(owner.level_id, zone['location_kind'])}"
            )
            action_id = (
                f"{episode_id}:{int(zone['creation_index'])}:"
                f"{response_index}:{zone['location_kind']}:{obstacle.kind}"
            )
            base_features = core._plan_features(
                data,
                levels,
                metadata,
                owner,
                candidate,
                obstacle,
                route_features,
                provisional_entry,
                stop,
            )
            arm_features = policy._arm_metrics(
                data, candidate, response_index, provisional_entry, stop
            )
            micro_features = {
                "control_creation_time_ns": int(
                    data.index[int(zone["creation_index"])].value
                ),
                "control_response_time_ns": int(
                    data.index[response_index].value
                ),
                "control_origin_minutes": float(
                    int(zone["creation_index"]) - int(zone["origin_index"])
                ),
                "control_wait_from_departure_minutes": float(
                    int(zone["creation_index"]) - int(candidate.departure_index)
                ),
                "control_zone_lower": float(zone["lower"]),
                "control_zone_upper": float(zone["upper"]),
                "control_zone_width_bps": float(zone["zone_width_bps"]),
                "control_impulse_distance_bps": float(
                    zone["impulse_distance_bps"]
                ),
                "control_flow_share_signed": float(
                    zone["flow_share_signed"]
                ),
                "control_flow_change_signed": float(
                    zone["flow_change_signed"]
                ),
                "control_initiative": float(zone["initiative"]),
                "control_absorption": float(zone["absorption"]),
                "control_gap_present": float(zone["gap_present"]),
                "control_ob_present": float(zone["ob_present"]),
                "control_break_body_ratio": float(zone["body_ratio"]),
                "control_break_range_ratio": float(zone["range_ratio"]),
                "control_break_activity_ratio": float(
                    zone["activity_ratio"]
                ),
                "control_response_kind": str(response["response_kind"]),
                "control_response_flow_share_signed": float(
                    response["response_flow_share_signed"]
                ),
                "control_response_body_ratio": float(
                    response["response_body_ratio"]
                ),
                "control_response_range_ratio": float(
                    response["response_range_ratio"]
                ),
                "control_response_activity_ratio": float(
                    response["response_activity_ratio"]
                ),
                "control_return_wait_minutes": float(
                    response["return_wait_minutes"]
                ),
                "control_response_delay_minutes": float(
                    response["response_delay_minutes"]
                ),
            }
            actual_entry = (
                float(label.actual_entry)
                if label.actual_entry is not None
                else provisional_entry
            )
            actual_risk = abs(actual_entry - stop)
            actual_gross_rr = (
                abs(target - actual_entry) / max(actual_risk, EPS)
            )
            records.append(
                {
                    "action_id": action_id,
                    "state_id": state_id,
                    "episode_id": episode_id,
                    "symbol": symbol,
                    "side": candidate.setup.side,
                    "family": candidate.event_meta["narrative_branch"],
                    "departure_time_ns": int(
                        data.index[candidate.departure_index].value
                    ),
                    "order_time_ns": int(data.index[response_index].value),
                    "arm_index": response_index,
                    "entry_geometry": (
                        f"CONTROL_RESPONSE_NEXT_OPEN:{zone['location_kind']}"
                    ),
                    "entry": provisional_entry,
                    "stop": stop,
                    "target": target,
                    "gross_rr": float(planned_rr),
                    "risk_bps": planned_risk
                    / max(abs(provisional_entry), EPS)
                    * 10_000.0,
                    "route_kind": obstacle.kind,
                    "route_price": float(obstacle.order_price),
                    "route_rr": float(planned_rr),
                    "planned_target_net_r": float(
                        economics["target_net_r"]
                    ),
                    "actual_fill_gross_rr": float(actual_gross_rr),
                    **base_features,
                    **arm_features,
                    **micro_features,
                    **asdict(label),
                }
            )
            counts["plans"] += 1

    frame = pd.DataFrame(records)
    counts["states"] = (
        int(frame.state_id.nunique()) if not frame.empty else 0
    )
    return frame, counts


core.POLICY = (
    "SEMANTIC_LIQUIDITY_AUCTION_THEN_FRESH_CONTROL_TRANSFER_"
    "FVG_OR_ORDER_BLOCK_THEN_FIRST_RETURN_COMPLETED_PRICE_FLOW_"
    "RESPONSE_THEN_NEXT_MINUTE_ENTRY_WITH_MICRO_STRUCTURE_STOP_"
    "TO_FIRST_OPPOSING_UNCONSUMED_ROUTE"
)
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
