#!/usr/bin/env python3
"""Candidate 3b research-synthesis harvester.

Research synthesis retained from the strongest prior branches:

* semantic liquidity owns direction and causal-event identity;
* failed and accepted auctions share a coherent event grammar;
* trend/channel/OB/FVG geometry describes context and first-return execution location;
* the stop invalidates the observed event;
* the first pre-existing opposing liquidity obstacle describes structural runway;
* pending orders end only when the first-return opportunity is invalidated, passed or
  causally expires; filled positions end only at immutable TP or SL.

Missing piece implemented here: proof-route coupling.  An accepted auction may arm only
after it has already delivered enough directional distance from the future entry zone.
The target is a conservative revisit/completion of that observed delivered leg, while
the older opposing-liquidity target is used as a runway ceiling rather than forced as
the full-position objective.  A prior visit to the completion target therefore does not
"spend" it before the first-return entry; it is the proof that makes the revisit target
causal.  Future bars label an already immutable order and never create the candidate.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import pandas as pd

import candidate_2c_harvest as inherited
import candidate_3b_logic as logic

# Candidate 2c imports candidate 1k as ``synthesis``.  Importing the lineage first keeps
# its compatibility adapters for the v5/v6 contracts and its event-lifetime arm states.
base = inherited.synthesis
policy = inherited.policy
core = inherited.core
EPS = inherited.EPS
MINIMUM_SOURCE_TIMEFRAME = base.MINIMUM_SOURCE_TIMEFRAME
POLICY = (
    "CANDIDATE_3B_RESEARCH_SYNTHESIS_ACCEPTED_AUCTION_"
    "PROVEN_DELIVERED_LEG_FIRST_RETURN_COMPLETION_DEEP_STRUCTURAL_RUNWAY_"
    "PENDING_CAUSAL_INVALIDATION_OR_FIRST_RETURN_PASSED_FILLED_TP_OR_SL_ONLY"
)


def _pre_arm_alive_without_completion_target(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    tick: float,
) -> bool:
    """Check causal availability while allowing a previously delivered target revisit."""
    setup = candidate.setup
    departure = int(candidate.departure_index)
    side = str(setup.side)
    for position in range(departure + 1, int(arm) + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= float(stop) if side == "LONG" else float(row.high) >= float(stop)
        traded = (
            float(row.low) <= float(entry) - core.LIMIT_TRADE_THROUGH_TICKS * float(tick)
            if side == "LONG"
            else float(row.high) >= float(entry) + core.LIMIT_TRADE_THROUGH_TICKS * float(tick)
        )
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)
        if invalidated or traded or overlaps:
            return False
    return True


def completion_label_from_arm(
    data: pd.DataFrame,
    candidate: Any,
    arm: int,
    entry: float,
    stop: float,
    target: float,
    tick: float,
):
    """Label one immutable revisit plan without pre-fill target-spent cancellation."""
    setup = candidate.setup
    side = str(setup.side)
    expiry = min(len(data) - 1, core._pending_expiry(candidate, candidate.source))
    bounded_arm = min(max(int(arm), 0), len(data) - 1)
    if int(arm) >= expiry or not _pre_arm_alive_without_completion_target(
        data, candidate, arm, entry, stop, tick
    ):
        return policy._empty_label("ARM_NOT_AVAILABLE", data, bounded_arm)

    touched_zone = False
    for position in range(int(arm) + 1, expiry + 1):
        row = data.iloc[position]
        invalidated = float(row.low) <= float(stop) if side == "LONG" else float(row.high) >= float(stop)
        traded = (
            float(row.low) <= float(entry) - core.LIMIT_TRADE_THROUGH_TICKS * float(tick)
            if side == "LONG"
            else float(row.high) >= float(entry) + core.LIMIT_TRADE_THROUGH_TICKS * float(tick)
        )
        overlaps = float(row.low) <= float(setup.upper) and float(row.high) >= float(setup.lower)

        if traded:
            target_same_bar = (
                float(row.high) >= float(target)
                if side == "LONG"
                else float(row.low) <= float(target)
            )
            # Intrabar ordering is unknown.  The inherited conservative convention
            # treats a same-minute entry/barrier collision as the stop outcome.
            if invalidated or target_same_bar:
                return policy._copy_label(
                    core._same_bar_stop_label(
                        data,
                        position,
                        int(arm),
                        float(entry),
                        float(stop),
                        float(target),
                        side,
                        float(tick),
                    )
                )
            return policy._copy_label(
                core._resolve_after_fill(
                    data,
                    position,
                    int(arm),
                    float(entry),
                    float(stop),
                    float(target),
                    side,
                    float(tick),
                )
            )

        if invalidated:
            return policy._empty_label("CANCELED_PRE_FILL_INVALIDATED", data, position)

        if overlaps:
            touched_zone = True
            continue
        if touched_zone:
            close_away = (
                float(row.close) >= float(setup.upper)
                if side == "LONG"
                else float(row.close) <= float(setup.lower)
            )
            if close_away:
                return policy._empty_label("CANCELED_FIRST_RETURN_PASSED", data, position)

    return policy._empty_label("EXPIRED_UNFILLED", data, expiry)


def _scenario_plan(
    *,
    candidate: Any,
    arm_features: dict[str, Any],
    entry: float,
    stop: float,
    route_price: float,
    route_rr: float,
    tick: float,
):
    family = str(candidate.event_meta["narrative_branch"])
    location = str(candidate.event_meta.get("location_kind", "UNKNOWN"))
    phase = str(arm_features.get("auction_phase", "UNKNOWN"))
    best = float(arm_features.get("auction_best_progress_r", 0.0) or 0.0)
    effort = float(arm_features.get("auction_effort_result", 0.0) or 0.0)
    side = str(candidate.setup.side)
    risk = abs(float(entry) - float(stop))
    if risk <= EPS:
        return None

    for tier in logic.evidence_tiers(
        family=family,
        location_kind=location,
        auction_phase=phase,
        best_progress_r=best,
        effort_result=effort,
        route_rr=float(route_rr),
    ):
        target = logic.directional_target(
            entry=float(entry),
            stop=float(stop),
            side=side,
            target_r=float(tier.target_r),
            tick=float(tick),
        )
        valid = (
            float(stop) < float(entry) < float(target)
            if side == "LONG"
            else float(target) < float(entry) < float(stop)
        )
        if not valid or not logic.route_is_clear(
            side=side,
            target=target,
            route_price=float(route_price),
            tick=float(tick),
        ):
            continue
        gross_rr = abs(float(target) - float(entry)) / risk
        if gross_rr + 1e-12 < 1.0:
            continue
        economics = core._raw_economics(side, float(entry), float(stop), float(target), float(tick))
        if economics is None:
            continue
        target_net_r = float(economics["target_net_r"])
        if target_net_r + 1e-12 < logic.minimum_target_net_r(float(tier.target_r)):
            continue
        return {
            "scenario_family": tier.scenario_family,
            "scenario_rank": int(tier.scenario_rank),
            "target_tier_r": float(tier.target_r),
            "target": float(target),
            "gross_rr": float(gross_rr),
            "planned_target_net_r": target_net_r,
            "proof_required_r": float(tier.proof_required_r),
            "proof_margin_r": best - float(tier.target_r),
            "route_required_r": float(tier.route_required_r),
            "route_utilization": float(gross_rr / max(float(route_rr), EPS)),
            "economics": economics,
        }
    return None


def generate_symbol(symbol, data, levels, metadata, trading_start):
    """Enumerate causal candidate-3b plans for one instrument."""
    tick = float(core.CONTRACTS[symbol].tick_size)
    start_ns = int(pd.Timestamp(trading_start, tz="UTC").value)
    sources = base.direction_sources(levels, metadata, MINIMUM_SOURCE_TIMEFRAME)
    sources = sorted(
        sources,
        key=lambda level: (
            int(level.first_penetration_index),
            level.side,
            -float(metadata[level.level_id].semantic_weight),
            level.level_id,
        ),
    )
    records: list[dict[str, Any]] = []
    active_until = {"HIGH": -1, "LOW": -1}
    seen: set[tuple[int, str]] = set()
    counts = {
        "semantic_sources": len(sources),
        "source_interactions": 0,
        "causal_departures": 0,
        "arm_states": 0,
        "plans": 0,
        "no_structural_route": 0,
        "no_eligible_completion": 0,
    }

    for source in sources:
        interaction = int(source.first_penetration_index)
        if (
            interaction >= len(data)
            or int(data.index[interaction].value) < start_ns
            or interaction <= active_until[source.side]
        ):
            continue
        clock = (interaction, str(source.side))
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
                float(metadata[level.level_id].semantic_weight),
                int(level.timeframe_minutes),
                int(level.defense_count),
                float(level.strength_ratio),
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
        active_until[source.side] = max(active_until[source.side], int(candidate.departure_index))
        counts["causal_departures"] += 1
        try:
            object.__setattr__(candidate, "source", owner)
        except (AttributeError, TypeError):
            pass

        stop = float(core._causal_stop(data, candidate, owner, tick))
        event_ns = int(data.index[candidate.setup.interaction_index].value)
        episode_id = f"C3B:{symbol}:{event_ns}:{core._stable_id(owner.level_id)}"

        for entry_name, entry_value in core._entry_variants(data, candidate, tick):
            entry = float(entry_value)
            if not (stop < entry if candidate.setup.side == "LONG" else stop > entry):
                continue

            obstacle, route_features = core.v4._first_obstacle(
                data,
                levels,
                metadata,
                int(candidate.departure_index),
                entry,
                str(candidate.setup.side),
                tick,
            )
            if obstacle is None:
                counts["no_structural_route"] += 1
                continue
            route_price = float(obstacle.order_price)
            route_valid = (
                stop < entry < route_price
                if candidate.setup.side == "LONG"
                else route_price < entry < stop
            )
            if not route_valid:
                counts["no_structural_route"] += 1
                continue
            risk = abs(entry - stop)
            if risk <= EPS:
                continue
            route_rr = abs(route_price - entry) / risk
            base_features = core._plan_features(
                data,
                levels,
                metadata,
                owner,
                candidate,
                obstacle,
                route_features,
                entry,
                stop,
            )

            # The structural route is passed to the inherited event-time iterator so
            # the arm remains available only while the wider route is still alive.
            for arm in policy._arm_positions(
                data,
                candidate,
                owner,
                entry,
                stop,
                route_price,
                tick,
            ):
                arm_features = dict(policy._arm_metrics(data, candidate, arm, entry, stop))
                arm_features.update(
                    {
                        "auction_route_headroom_r": float(route_rr),
                        "structural_route_utilization": 1.0,
                        "exact_structural_route": 1.0,
                    }
                )
                plan = _scenario_plan(
                    candidate=candidate,
                    arm_features=arm_features,
                    entry=entry,
                    stop=stop,
                    route_price=route_price,
                    route_rr=route_rr,
                    tick=tick,
                )
                if plan is None:
                    counts["no_eligible_completion"] += 1
                    continue

                label = completion_label_from_arm(
                    data,
                    candidate,
                    arm,
                    entry,
                    stop,
                    float(plan["target"]),
                    tick,
                )
                if label.fill_state == "ARM_NOT_AVAILABLE":
                    continue

                state_id = (
                    f"C3BSTATE:{symbol}:{event_ns}:"
                    f"{candidate.event_meta['narrative_branch']}:{arm}:"
                    f"{core._stable_id(owner.level_id, candidate.setup.setup_kind)}"
                )
                action_id = (
                    f"{episode_id}:{arm}:{entry_name}:"
                    f"{plan['scenario_family']}:{plan['target_tier_r']:.2f}:"
                    f"{core._stable_id(obstacle.obstacle_id)}"
                )
                economics = plan.pop("economics")
                records.append(
                    {
                        "action_id": action_id,
                        "state_id": state_id,
                        "episode_id": episode_id,
                        "symbol": symbol,
                        "side": candidate.setup.side,
                        "family": candidate.event_meta["narrative_branch"],
                        "departure_time_ns": int(data.index[candidate.departure_index].value),
                        "order_time_ns": int(data.index[arm].value),
                        "arm_index": int(arm),
                        "entry_geometry": entry_name,
                        "entry": entry,
                        "stop": stop,
                        "target": float(plan["target"]),
                        "gross_rr": float(plan["gross_rr"]),
                        "risk_bps": risk / max(abs(entry), EPS) * 10_000.0,
                        "route_kind": obstacle.kind,
                        "route_price": route_price,
                        "route_rr": float(route_rr),
                        "structural_route_target": route_price,
                        "completion_target_origin": "OBSERVED_DELIVERED_LEG_REVISIT",
                        "planned_target_net_r": float(economics["target_net_r"]),
                        **{key: value for key, value in plan.items() if key not in {"target", "gross_rr", "planned_target_net_r"}},
                        **base_features,
                        **arm_features,
                        **asdict(label),
                    }
                )
                counts["plans"] += 1

    frame = pd.DataFrame(records)
    counts["arm_states"] = int(frame.state_id.nunique()) if not frame.empty else 0
    return frame, counts


core.POLICY = POLICY
core.generate_symbol = generate_symbol

if __name__ == "__main__":
    core.main()
